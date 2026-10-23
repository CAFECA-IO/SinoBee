"""Sandbox containers: one per conversation, behind the egress proxy (sandbox plan B2 + C1).

    networks   sinobee-int  (internal: no route out)      sinobee-ext (ordinary)
    egress     sinobee-egress on both; proxy port 3128 inside, control API published on 127.0.0.1:<random>
    sandbox    sinobee-<conversation> on sinobee-int only:
               --read-only, --tmpfs /tmp, --user 1000:1000, --cap-drop ALL, no-new-privileges,
               CPU / memory / process limits,
               /workspace = the SinoBee workspace (shared by all conversations),
               /home/bee  = the volume sinobee-home-<conversation> (pip --user, npm -g, caches: a bind mount
                            is ~10x slower for many small files on macOS),
               /sinobee/skills, /sinobee/bundled_skills read-only (skill_run runs the scripts in place),
               HTTP(S)_PROXY=http://egress:3128

The container starts the first time a tool needs it, stops after `sandbox.idle_minutes` (30) without use, and
is removed with its volume after `sandbox.prune_days` (14) stopped. Everything goes through the docker CLI,
so there is no Python dependency and the same code drives Docker Desktop and Linux Docker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

INTERNAL, EXTERNAL, EGRESS = "sinobee-int", "sinobee-ext", "sinobee-egress"
EGRESS_IMAGE = "python:3.12-alpine"
ROLE = "org.sinobee.role"
REMEMBERED = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]
_managers: dict[str, "SandboxManager"] = {}

# a byte pipe to a TCP port inside the sandbox over `docker exec -i` stdio: the sandbox is on an internal network,
# which Docker does not publish ports from, so the live view, the browser's DevTools and page previews come this way
BRIDGE = r"""import socket, sys, threading
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=15)
s.settimeout(None)
def up():
    try:
        while True:
            d = sys.stdin.buffer.read1(65536)
            if not d:
                break
            s.sendall(d)
    except OSError:
        pass
    finally:
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
threading.Thread(target=up, daemon=True).start()
out = sys.stdout.buffer
while True:
    d = s.recv(65536)
    if not d:
        break
    out.write(d)
    out.flush()
"""


@dataclass
class Box:
    name: str
    conversation: str
    ip: str


def safe(conversation: str) -> str:
    return (re.sub(r"[^a-zA-Z0-9_.-]", "-", conversation or "default").strip("-.") or "default")[:48]


def bundled_skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "bundled_skills"


def image_name(config, conversation: str | None = None) -> str:
    """The sandbox image: Settings' sandbox.image, or the conversation's own choice (convconf overrides.image)."""
    from .. import __version__
    from .convconf import image
    flavour = image(config, conversation)
    return f"sinobee-sandbox{'-office' if flavour == 'office' else ''}:{__version__}"


class DockerError(RuntimeError):
    pass


def docker_sync(*args: str, timeout: float = 20) -> tuple[int, str, str]:
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


async def docker(*args: str, timeout: float = 120, stdin: bytes | None = None) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        return 127, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"docker {' '.join(args[:2])} timed out after {timeout}s"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


class SandboxManager:
    def __init__(self, config):
        self.config = config
        self.state_dir = Path(config.home) / "sandbox"
        self.last_used: dict[str, float] = {}
        self.boxes: dict[str, Box] = {}          # conversation -> running box
        self.grants: dict[str, set[str]] = {}    # conversation -> domains allowed for it (plan C2)
        self.open_all: set[str] = set()          # conversations allowed all network this task (plan C2)
        self._locks: dict = {}                   # one asyncio.Lock per event loop
        self._avail: tuple[float, str | None] = (0.0, "not checked")
        self._egress: dict | None = None
        self._log_seq = 0
        self._checked: dict[str, float] = {}     # conversation -> when its box was last confirmed running
        self.takeover: dict[str, float] = {}     # conversation -> since when you control its desktop (plan D1)
        self._bridges: dict[tuple[str, int], asyncio.base_events.Server] = {}
        self.connections: list[dict] = []       # recent connection log from the egress proxy

    # ----------------------------------------------------------------- can we run at all
    def unavailable(self, max_age: float = 60) -> str | None:
        """None when sandboxes can run; otherwise why not (checked at most once a minute)."""
        t, why = self._avail
        if time.time() - t < max_age:
            return why
        if not shutil.which("docker"):
            why = "Docker is not installed"
        else:
            rc, _, err = docker_sync("info", "--format", "{{.ServerVersion}}", timeout=8)
            if rc != 0:
                why = "Docker is not running" + (f" ({err.strip()[:120]})" if err.strip() else "")
            else:
                rc, _, _ = docker_sync("image", "inspect", image_name(self.config), timeout=8)
                why = None if rc == 0 else (f"the sandbox image {image_name(self.config)} is not built yet "
                                            "(sinobee sandbox build)")
        self._avail = (time.time(), why)
        return why

    def limits(self) -> tuple[str, str]:
        """(cpus, memory) for a sandbox: settings, or 4 CPUs / 60% of Docker's memory up to 8 GB."""
        cpus = self.config.get("sandbox.cpus", "auto")
        mem = self.config.get("sandbox.memory", "auto")
        if cpus in (None, "", "auto") or mem in (None, "", "auto"):
            rc, out, _ = docker_sync("info", "--format", "{{.NCPU}} {{.MemTotal}}", timeout=8)
            try:
                ncpu, total = (int(x) for x in out.split())
            except ValueError:
                ncpu, total = 4, 8 << 30
            if cpus in (None, "", "auto"):
                cpus = str(min(4, ncpu))
            if mem in (None, "", "auto"):
                mem = f"{min(8 << 30, int(total * 0.6)) // (1 << 20)}m"
        return str(cpus), str(mem)

    # ----------------------------------------------------------------- networks and the egress proxy
    async def _networks(self) -> None:
        for name, internal in ((INTERNAL, True), (EXTERNAL, False)):
            rc, _, _ = await docker("network", "inspect", name, timeout=20)
            if rc != 0:
                args = ["network", "create", "--label", f"{ROLE}=network"] + (["--internal"] if internal else []) + [name]
                rc, _, err = await docker(*args, timeout=30)
                if rc != 0 and "already exists" not in err:
                    raise DockerError(f"cannot create network {name}: {err.strip()}")

    def _egress_file(self) -> Path:
        return self.state_dir / "egress.json"

    async def _egress_ready(self) -> dict:
        """The running egress proxy {token, port}; started (or restarted) when needed."""
        if self._egress and await self._health(self._egress):
            return self._egress
        saved = {}
        try:
            saved = json.loads(self._egress_file().read_text())
        except (OSError, ValueError):
            pass
        rc, out, _ = await docker("inspect", "-f", "{{.State.Running}}", EGRESS, timeout=20)
        if rc == 0 and out.strip() == "true" and saved.get("token"):
            port = await self._published(EGRESS, 8080)
            info = {"token": saved["token"], "port": port}
            if port and await self._health(info):
                self._egress = info
                await self._push_all()
                return info
        await docker("rm", "-f", EGRESS, timeout=30)
        token = secrets.token_urlsafe(24)
        src = (Path(__file__).resolve().parent / "egress.py").read_text()
        image = str(self.config.get("sandbox.egress_image", EGRESS_IMAGE) or EGRESS_IMAGE)
        rc, _, err = await docker(
            "run", "-d", "--name", EGRESS, "--label", f"{ROLE}=egress", "--network", EXTERNAL,
            "--restart", "unless-stopped", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65534:65534", "--memory", "256m",
            "-p", "127.0.0.1::8080", "-e", f"SINOBEE_EGRESS_TOKEN={token}",
            image, "python", "-c", src, timeout=180)
        if rc != 0:
            raise DockerError(f"cannot start the egress proxy: {err.strip()[:300]}")
        rc, _, err = await docker("network", "connect", "--alias", "egress", INTERNAL, EGRESS, timeout=30)
        if rc != 0 and "already exists" not in err:
            raise DockerError(f"cannot connect the egress proxy to {INTERNAL}: {err.strip()}")
        port = await self._published(EGRESS, 8080)
        info = {"token": token, "port": port}
        for _ in range(50):
            if port and await self._health(info):
                break
            await asyncio.sleep(0.2)
        else:
            raise DockerError("the egress proxy did not answer")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._egress_file().write_text(json.dumps({"token": token}))
        self._egress_file().chmod(0o600)
        self._egress = info
        await self._push_all()
        return info

    async def _published(self, name: str, port: int) -> int | None:
        rc, out, _ = await docker("port", name, f"{port}/tcp", timeout=20)
        for line in out.splitlines():
            if line.startswith("127.0.0.1:"):
                return int(line.rsplit(":", 1)[1])
        return None

    async def _control(self, info: dict, method: str, path: str, body: dict | None = None):
        def call():
            req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"X-SinoBee-Token": info["token"], "Content-Type": "application/json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a proxy
            with opener.open(req, timeout=5) as r:
                return json.loads(r.read() or b"null")
        return await asyncio.to_thread(call)

    async def _health(self, info: dict) -> bool:
        try:
            return bool((await self._control(info, "GET", "/health")).get("ok"))
        except Exception:  # noqa: BLE001
            return False

    def policy(self, conversation: str) -> dict:
        remembered = self.config.get("sandbox.network.remembered")
        domains = list(REMEMBERED if remembered is None else remembered)
        domains += sorted(self.grants.get(conversation, set()))
        from .convconf import domains as kept
        mine, mine_all = kept(self.config, conversation)          # allowed for this conversation and kept
        domains += [d for d in mine if d not in domains]
        open_all = (self.config.get("sandbox.network.mode", "ask") == "open") or conversation in self.open_all \
            or mine_all
        return {"conversation": conversation, "domains": domains, "all": open_all}

    async def push_policy(self, conversation: str) -> None:
        box = self.boxes.get(conversation)
        if not box or not self._egress:
            return
        await self._control(self._egress, "PUT", f"/policy/{box.ip}", self.policy(conversation))

    async def _push_all(self) -> None:
        for conv in list(self.boxes):
            try:
                await self.push_policy(conv)
            except Exception as e:  # noqa: BLE001
                log.warning("egress policy for %s: %s", conv, e)

    async def connection_log(self) -> list[dict]:
        """New entries of the egress log (also kept in self.connections, last 2,000)."""
        if not self._egress:
            return []
        try:
            new = await self._control(self._egress, "GET", f"/log?since={self._log_seq}") or []
        except Exception:  # noqa: BLE001
            return []
        if new:
            self._log_seq = max(e["seq"] for e in new)
            self.connections = (self.connections + new)[-2000:]
        return new

    # ----------------------------------------------------------------- sandboxes
    def name_of(self, conversation: str) -> str:
        return f"sinobee-{safe(conversation)}"

    def mounts(self) -> list[tuple[Path, str, str]]:
        """(this computer, in the sandbox, rw|ro) for every sandbox."""
        out = [(Path(self.config.workspace).expanduser().resolve(), "/workspace", "rw")]
        skills = Path(self.config.home) / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        out.append((skills.resolve(), "/sinobee/skills", "ro"))
        out.append((bundled_skills_dir(), "/sinobee/bundled_skills", "ro"))
        return out

    def folder_mounts(self, conversation: str) -> list[tuple[Path, str, str]]:
        """This conversation's folders of this computer (folders.py): /host is its staging folder (review copies are
        made there and show up at once); read-only and read/write folders are bind mounts over /host/<name>."""
        from .folders import conv_dir, folders_for
        stage = conv_dir(self.config, conversation) / "host"
        stage.mkdir(parents=True, exist_ok=True)
        out = [(stage.resolve(), "/host", "rw")]
        for f in folders_for(self.config, conversation):
            if f["mode"] in ("ro", "rw") and Path(f["path"]).is_dir():
                out.append((Path(f["path"]), f"/host/{f['name']}", f["mode"]))
        return out

    def mount_key(self, conversation: str) -> str:
        """Changes when the container's mounts must change (a folder granted read-only or read/write)."""
        import hashlib
        spec = [(str(h), i, m) for h, i, m in self.folder_mounts(conversation)]
        return hashlib.sha1(json.dumps(spec).encode()).hexdigest()[:12]

    async def ensure(self, conversation: str) -> Box:
        """The conversation's sandbox, running and allowed out as its policy says."""
        box = self.boxes.get(conversation)
        if box and time.time() - self._checked.get(conversation, 0) < 20:     # checked a moment ago
            self.last_used[box.name] = time.time()
            return box
        loop = asyncio.get_running_loop()
        lock = self._locks.get(id(loop))
        if lock is None:
            lock = self._locks[id(loop)] = asyncio.Lock()
        async with lock:
            why = await asyncio.to_thread(self.unavailable)
            if why:
                raise DockerError(why)
            await self._networks()
            await self._egress_ready()
            name = self.name_of(conversation)
            rc, out, _ = await docker("inspect", "-f",
                                      "{{.State.Running}} {{.Image}} {{index .Config.Labels \"org.sinobee.mounts\"}}",
                                      name, timeout=20)
            parts = out.split()
            if rc == 0 and len(parts) > 1 and (parts[1] != await self._image_id(conversation)
                                               or (parts[2] if len(parts) > 2 else "") != self.mount_key(conversation)):
                # the image was rebuilt (a new SinoBee) or a folder was granted: a new container; the home volume
                # stays, what ran in the old one stops
                await docker("rm", "-f", name, timeout=60)
                self._close_bridges(conversation)
                rc = 1
            from .staging import ensure_copies          # review folders added since: their copies appear at once
            for err in await asyncio.to_thread(ensure_copies, self.config, conversation):
                log.warning("review copy: %s", err)
            if rc != 0:
                await self._create(conversation, name)
            elif out.split()[0] != "true":
                rc, _, err = await docker("start", name, timeout=60)
                if rc != 0:
                    raise DockerError(f"cannot start {name}: {err.strip()[:300]}")
            rc, out, _ = await docker("inspect", "-f",
                                      "{{with index .NetworkSettings.Networks \"" + INTERNAL + "\"}}{{.IPAddress}}{{end}}",
                                      name, timeout=20)
            ip = out.strip()
            if not ip:
                raise DockerError(f"{name} has no address on {INTERNAL}")
            box = Box(name, conversation, ip)
            self.boxes[conversation] = box
            self.last_used[name] = self._checked[conversation] = time.time()
            await self.push_policy(conversation)
            return box

    async def _image_id(self, conversation: str | None = None) -> str:
        rc, out, _ = await docker("image", "inspect", "-f", "{{.Id}}", image_name(self.config, conversation), timeout=20)
        return out.strip() if rc == 0 else ""

    # ----------------------------------------------------------------- reaching ports inside a sandbox
    async def open_stream(self, conversation: str, port: int):
        """(reader, writer, process): a byte stream to 127.0.0.1:<port> inside the conversation's sandbox."""
        box = await self.ensure(conversation)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", box.name, "python3", "-u", "-c", BRIDGE, str(int(port)),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        return proc.stdout, proc.stdin, proc

    async def bridge(self, conversation: str, port: int) -> int:
        """A port on 127.0.0.1 of this computer that reaches <port> inside the sandbox (one docker exec per
        connection). Kept until the sandbox stops."""
        key = (conversation, int(port))
        srv = self._bridges.get(key)
        loop = asyncio.get_running_loop()
        if srv is not None and srv.is_serving() and getattr(srv, "_sb_loop", None) is loop:
            return srv.sockets[0].getsockname()[1]

        async def handle(reader, writer):
            try:
                r2, w2, proc = await self.open_stream(conversation, port)
            except Exception:  # noqa: BLE001
                writer.close()
                return

            async def pump(src, dst):
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                except (ConnectionError, OSError, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        dst.close()
                    except Exception:  # noqa: BLE001
                        pass
            await asyncio.gather(pump(reader, w2), pump(r2, writer))
            if proc.returncode is None:
                proc.kill()
        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        srv._sb_loop = loop
        self._bridges[key] = srv
        return srv.sockets[0].getsockname()[1]

    async def recreate(self, conversation: str) -> None:
        """Replace the conversation's container on its next use (another image); the home volume stays."""
        from ..tools import browser
        await browser.forget_sandbox(conversation)
        self.boxes.pop(conversation, None)
        self._checked.pop(conversation, None)
        self._close_bridges(conversation)
        await docker("rm", "-f", self.name_of(conversation), timeout=60)

    async def refresh(self, conversation: str) -> bool:
        """After a folder grant: check the container again now; True when it had to be replaced (its programs and
        browser stopped). Review copies need nothing: they appear under /host at once."""
        self._checked.pop(conversation, None)
        name = self.name_of(conversation)
        rc, out, _ = await docker("inspect", "-f", "{{index .Config.Labels \"org.sinobee.mounts\"}}", name, timeout=20)
        if rc != 0 or out.strip() == self.mount_key(conversation):
            return False
        from ..tools import browser
        await browser.forget_sandbox(conversation)
        self.boxes.pop(conversation, None)
        await self.ensure(conversation)
        return True

    def _close_bridges(self, conversation: str) -> None:
        for key in [k for k in self._bridges if k[0] == conversation]:
            try:
                self._bridges.pop(key).close()
            except RuntimeError:          # its event loop is already closed
                pass

    def set_takeover(self, conversation: str, on: bool) -> None:
        if on:
            self.takeover[conversation] = time.time()
        else:
            self.takeover.pop(conversation, None)

    async def _create(self, conversation: str, name: str) -> None:
        img = image_name(self.config, conversation)
        if not await self._image_id(conversation):
            raise DockerError(f"the sandbox image {img} is not built yet: run "
                              + ("sinobee sandbox build --office" if "-office" in img else "sinobee sandbox build"))
        cpus, mem = self.limits()
        args = ["run", "-d", "--name", name, "--hostname", "sandbox",
                "--label", f"{ROLE}=sandbox", "--label", f"org.sinobee.conversation={conversation}",
                "--network", INTERNAL, "--read-only", "--tmpfs", "/tmp:rw,exec,nosuid,size=2g",
                "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--cpus", cpus, "--memory", mem, "--pids-limit", str(self.config.get("sandbox.pids", 1024) or 1024),
                "--shm-size", "512m",
                "--label", f"org.sinobee.mounts={self.mount_key(conversation)}",
                "-v", f"sinobee-home-{safe(conversation)}:/home/bee"]
        for host, inner, mode in self.mounts() + self.folder_mounts(conversation):
            args += ["-v", f"{host}:{inner}" + (":ro" if mode == "ro" else "")]
        proxy = "http://egress:3128"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            args += ["-e", f"{k}={proxy}"]
        args += ["-e", "NO_PROXY=localhost,127.0.0.1", "-e", "no_proxy=localhost,127.0.0.1",
                 image_name(self.config, conversation)]
        rc, _, err = await docker(*args, timeout=120)
        if rc != 0:
            raise DockerError(f"cannot create the sandbox {name}: {err.strip()[:400]}")

    def touch(self, conversation: str) -> None:
        self.last_used[self.name_of(conversation)] = time.time()

    async def list(self) -> list[dict]:
        rc, out, _ = await docker("ps", "-a", "--filter", f"label={ROLE}=sandbox", "--format",
                                  "{{.Names}}\t{{.State}}\t{{.Label \"org.sinobee.conversation\"}}\t{{.Status}}",
                                  timeout=30)
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                rows.append({"name": parts[0], "state": parts[1], "conversation": parts[2], "status": parts[3],
                             "last_used": self.last_used.get(parts[0])})
        return rows

    def forget(self, conversation: str) -> None:
        """The box may be gone (an exec failed): look again next time."""
        self._checked.pop(conversation, None)

    async def stop(self, conversation: str) -> None:
        self._checked.pop(conversation, None)
        self._close_bridges(conversation)
        self.takeover.pop(conversation, None)
        await docker("stop", "-t", "3", self.name_of(conversation), timeout=60)
        box = self.boxes.pop(conversation, None)
        if box and self._egress:
            try:
                await self._control(self._egress, "DELETE", f"/policy/{box.ip}")
            except Exception:  # noqa: BLE001
                pass

    async def remove(self, conversation: str) -> None:
        await self.stop(conversation)
        await docker("rm", "-f", self.name_of(conversation), timeout=60)
        await docker("volume", "rm", "-f", f"sinobee-home-{safe(conversation)}", timeout=60)

    async def sweep(self, known=None) -> dict:
        """Stop sandboxes idle for sandbox.idle_minutes; remove ones stopped for sandbox.prune_days; once an hour
        tidy the conversations' files (convconf.sweep_files). known(conversation) -> bool says it still exists."""
        if time.time() - getattr(self, "_files_swept", 0) > 3600:
            self._files_swept = time.time()
            from .convconf import sweep_files
            try:
                await asyncio.to_thread(sweep_files, self.config, known)
            except Exception:  # noqa: BLE001
                log.exception("sandbox files sweep failed")
        idle = float(self.config.get("sandbox.idle_minutes", 30) or 30) * 60
        prune = float(self.config.get("sandbox.prune_days", 14) or 14) * 86400
        stopped, removed = [], []
        now = time.time()
        for row in await self.list():
            name, conv = row["name"], row["conversation"]
            if row["state"] == "running":
                last = self.last_used.setdefault(name, now)        # unknown to this process: count from now
                if now - last > idle:
                    await self.stop(conv)
                    stopped.append(name)
            elif row["state"] in ("exited", "created"):
                rc, out, _ = await docker("inspect", "-f", "{{.State.FinishedAt}}", name, timeout=20)
                finished = _parse_time(out.strip())
                if finished and now - finished > prune:
                    await self.remove(conv)
                    removed.append(name)
        if self._egress:
            await self.connection_log()
        return {"stopped": stopped, "removed": removed}


def _parse_time(s: str) -> float | None:
    from datetime import datetime
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", s or "")
    if not m or s.startswith("0001"):
        return None
    try:
        from datetime import timezone
        return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def manager_for(config) -> SandboxManager:
    key = str(Path(config.home).resolve())
    m = _managers.get(key)
    if m is None or m.config is not config:
        old = m
        m = _managers[key] = SandboxManager(config)
        if old is not None:                      # keep what a reload must not forget
            m.last_used, m.boxes, m.grants, m.open_all = old.last_used, old.boxes, old.grants, old.open_all
            m._egress, m._log_seq, m.connections = old._egress, old._log_seq, old.connections
            m.takeover, m._bridges = old.takeover, old._bridges
    return m


def active_managers() -> list[SandboxManager]:
    return list(_managers.values())
