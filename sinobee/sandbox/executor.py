"""Where the tools run (sandbox plan A1).

Every tool that touches the computer - shell, the file tools, skill_run, the desktop tools - goes through an
`Executor` instead of calling subprocess, the file system or the screen itself:

    run(command, cwd, timeout, env)        a bash command -> RunResult(code, output, timed_out)
    read_bytes / read_text / write_text    files (host paths; a sandbox mounts them, see paths.py)
    exists / is_file / is_dir / stat / walk / mkdir
    screenshot(max_width)                  (png, width, height, raw_width, raw_height)
    input()                                the mouse and keyboard backend
    desktop_available()
    python()                               the interpreter a skill's .py scripts run with

`HostExecutor` does exactly what the tools did before, on this computer. A `SandboxExecutor` (plan B3) will run
the same calls in the conversation's container. `executor_of(ctx)` gives a tool its executor (one per session,
kept in ctx.state); `executor_kind(config)` says which one this configuration uses: `sandbox.executor`
("host" - the default - or "sandbox" once it exists; an unknown or unavailable kind falls back to "host").
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .paths import PathMap

log = logging.getLogger(__name__)

IMPLEMENTED = {"host", "sandbox"}


@dataclass
class RunResult:
    code: int
    output: str
    timed_out: bool = False


class Executor:
    kind = "base"

    def __init__(self, config, paths: PathMap | None = None):
        self.config = config
        self.paths = paths if paths is not None else PathMap(workspace=getattr(config, "workspace", None))

    # ----------------------------------------------------------------- commands
    async def run(self, command: str, cwd: Path, timeout: int, env: dict | None = None) -> RunResult:
        raise NotImplementedError

    def python(self) -> str:
        return sys.executable

    def check(self, p: Path, write: bool = False) -> None:
        """Raise PermissionError when the tools may not reach (or write) this file of the computer."""

    # ----------------------------------------------------------------- files (paths on this computer)
    def read_bytes(self, p: Path) -> bytes:
        return Path(p).read_bytes()

    def read_text(self, p: Path, errors: str | None = None) -> str:
        return Path(p).read_text(encoding="utf-8", errors=errors) if errors else Path(p).read_text(encoding="utf-8")

    def write_text(self, p: Path, text: str, append: bool = False) -> None:
        p = Path(p)
        if append:
            with p.open("a", encoding="utf-8") as f:
                f.write(text)
        else:
            p.write_text(text, encoding="utf-8")

    def mkdir(self, p: Path) -> None:
        Path(p).mkdir(parents=True, exist_ok=True)

    def exists(self, p: Path) -> bool:
        return Path(p).exists()

    def is_file(self, p: Path) -> bool:
        return Path(p).is_file()

    def is_dir(self, p: Path) -> bool:
        return Path(p).is_dir()

    def stat(self, p: Path) -> os.stat_result:
        return Path(p).stat()

    def walk(self, root: Path) -> Iterator[tuple[str, list[str], list[str]]]:
        return os.walk(root)

    # ----------------------------------------------------------------- screen
    def desktop_available(self) -> bool:
        from ..tools import desktop
        return desktop.available()

    def input(self):
        from ..tools import desktop
        return desktop.get_backend(self.config)

    def screenshot(self, max_width: int = 1280) -> tuple[bytes, int, int, int, int]:
        from ..tools import desktop
        return desktop.grab_screen(max_width)


class HostExecutor(Executor):
    """The tools on this computer: what SinoBee always did."""
    kind = "host"

    async def run(self, command: str, cwd: Path, timeout: int, env: dict | None = None) -> RunResult:
        full = {**os.environ, **(env or {})}
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-lc", command, cwd=str(cwd), env=full,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return RunResult(proc.returncode, out.decode("utf-8", errors="replace"))
        except asyncio.TimeoutError:
            _kill(proc)
            return RunResult(-9, "", True)
        except asyncio.CancelledError:      # the user pressed "stop": don't leave the command running
            _kill(proc)
            raise


def _kill(proc) -> None:
    try:
        os.killpg(proc.pid, 9)
    except ProcessLookupError:
        pass


class SandboxExecutor(Executor):
    """The tools in the conversation's Docker container (sandbox plan B3). Files are the same files: the container
    mounts them (paths.py), so the file methods stay those of this computer, limited to what is mounted."""
    kind = "sandbox"

    def __init__(self, config, paths: PathMap, conversation: str, manager):
        super().__init__(config, paths)
        self.conversation = conversation or "default"
        self.manager = manager

    def python(self) -> str:
        return "python3"

    def check(self, p: Path, write: bool = False) -> None:
        m = self.paths.mount_of(p)
        if m is None:
            raise PermissionError(f"{p} is not in the sandbox: only /workspace and folders the user authorised are "
                                  "there. Ask the user to attach the file or to authorise its folder.")
        if write and m.mode == "ro":
            raise PermissionError(f"{self.paths.to_exec(p)} is read-only in the sandbox")

    async def run(self, command: str, cwd: Path, timeout: int, env: dict | None = None) -> RunResult:
        from .manager import DockerError
        try:
            box = await self.manager.ensure(self.conversation)
        except DockerError as e:
            return RunResult(1, f"ERROR: the sandbox is not available: {e}")
        if not self.paths.visible(cwd):
            return RunResult(1, f"ERROR: {cwd} is not in the sandbox (use /workspace)")
        inner = self.paths.to_exec(cwd)
        pidf = f"/tmp/.sbx-{secrets.token_hex(6)}"
        script = f"echo $$ > {pidf}; exec bash -lc {shlex.quote(command)}"
        args = ["exec", "-w", inner]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        # setsid makes the command its own process group (a timeout or stop kills all of it); --wait, because a
        # process-group leader's setsid forks and returns at once - docker exec would end before the output does
        args += [box.name, "setsid", "--wait", "bash", "-c", script]
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._kill(box.name, pidf, proc)
            return RunResult(-9, "", True)
        except asyncio.CancelledError:      # the user pressed "stop": end the command inside the sandbox too
            await asyncio.shield(self._kill(box.name, pidf, proc))
            raise
        text = out.decode("utf-8", errors="replace")
        if proc.returncode in (125, 126, 127) and ("No such container" in text or "is not running" in text):
            self.manager.forget(self.conversation)          # the box went away: start it again next time
        asyncio.ensure_future(_quiet("exec", box.name, "rm", "-f", pidf))
        return RunResult(proc.returncode, text)

    @staticmethod
    async def _kill(name: str, pidf: str, proc) -> None:
        await _quiet("exec", name, "bash", "-c", f"kill -KILL -- -$(cat {pidf}) 2>/dev/null; rm -f {pidf}")
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _quiet(*args: str) -> None:
    try:
        p = await asyncio.create_subprocess_exec("docker", *args, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), 20)
    except Exception:  # noqa: BLE001
        pass


_warned: set[str] = set()


def wanted_kind(config) -> str:
    """What the settings ask for: sandbox.executor (auto | host | sandbox); auto = the sandbox when the approval
    mode is "sandbox"."""
    if config is None:
        return "host"
    want = str(config.get("sandbox.executor", "auto") or "auto")
    if want == "auto":
        want = "sandbox" if config.get("permissions.approval") == "sandbox" else "host"
    return want


def executor_kind(config) -> str:
    """Where the tools of this configuration run now: "sandbox" when asked for and Docker can run it."""
    kind = wanted_kind(config)
    if kind not in IMPLEMENTED:
        return "host"
    if kind == "sandbox":
        from .manager import manager_for
        why = manager_for(config).unavailable()
        if why:
            if why not in _warned:
                _warned.add(why)
                log.warning("sandbox requested but not available (%s); the tools run on this computer", why)
            return "host"
    return kind


def sandbox_unavailable(config) -> str | None:
    """Why a requested sandbox is not used (None: not requested, or running)."""
    if wanted_kind(config) != "sandbox":
        return None
    from .manager import manager_for
    return manager_for(config).unavailable()


def make_executor(config, conversation: str = "") -> Executor:
    from .paths import for_config
    if executor_kind(config) == "sandbox":
        from .manager import manager_for
        return SandboxExecutor(config, for_config(config), conversation, manager_for(config))
    return HostExecutor(config, for_config(config))


def executor_of(ctx) -> Executor:
    """The executor of a tool's session (created on first use and kept in ctx.state; made again when the
    settings switch between this computer and the sandbox)."""
    state = ctx.state if isinstance(getattr(ctx, "state", None), dict) else None
    ex = state.get("executor") if state is not None else None
    if ex is None or ex.config is not ctx.config or (ex.kind in ("host", "sandbox")
                                                     and ex.kind != executor_kind(ctx.config)):
        ex = make_executor(ctx.config, getattr(ctx, "session_id", "") or "")
        if state is not None:
            state["executor"] = ex
    return ex
