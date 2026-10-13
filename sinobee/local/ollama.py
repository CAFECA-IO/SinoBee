"""Ollama runtime: discovery, start/stop of a managed `ollama serve`, model pull / load / unload / delete.

Discovery order (first that answers GET /api/version):
  settings local.base_url > $SINOBEE_OLLAMA_URL > $OLLAMA_HOST > localhost:11434 > ollama:11434 (compose sidecar)
  > host.docker.internal:11434 (native Ollama on the Docker host, e.g. a Mac using Metal)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("sinobee.local")

DEFAULT_PORT = 11434
_URL_CACHE: dict = {}


def _norm_url(u: str) -> str:
    u = u.strip().rstrip("/")
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    if u.endswith("/v1"):
        u = u[:-3]
    p = urlparse(u)
    if not p.port and p.scheme == "http":
        u = f"{u}:{DEFAULT_PORT}"
    return u.replace("0.0.0.0", "127.0.0.1")


def candidates(config) -> list[str]:
    out: list[str] = []
    for u in ((config.get("local.base_url") if config else None), os.environ.get("SINOBEE_OLLAMA_URL"),
              os.environ.get("OLLAMA_HOST"), "http://127.0.0.1:11434", "http://ollama:11434",
              "http://host.docker.internal:11434"):
        if u:
            n = _norm_url(u)
            if n not in out:
                out.append(n)
    return out


def location(url: str) -> str:
    """container | host | sidecar | remote — where the runtime runs relative to SinoBee."""
    h = urlparse(url).hostname or ""
    if h in ("127.0.0.1", "localhost", "::1"):
        return "local"
    if h == "host.docker.internal":
        return "host"
    if h == "ollama":
        return "sidecar"
    return "remote"


def resolve_url(config, refresh: bool = False) -> tuple[str, bool]:
    """(url, reachable) — synchronous, cached 15s; used when building the provider."""
    explicit = config.get("local.base_url") if config else None
    key = tuple(candidates(config))
    c = _URL_CACHE.get(key)
    if c and not refresh and c["t"] > time.time() - 15:
        return c["url"], c["ok"]
    found, ok = None, False
    for u in key:
        try:
            r = httpx.get(f"{u}/api/version", timeout=0.8)
            if r.status_code == 200:
                found, ok = u, True
                break
        except httpx.HTTPError:
            continue
    if not found:
        found = _norm_url(explicit) if explicit else "http://127.0.0.1:11434"
    _URL_CACHE[key] = {"t": time.time(), "url": found, "ok": ok}
    return found, ok


def forget_url() -> None:
    _URL_CACHE.clear()


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    async def _req(self, method: str, path: str, timeout: float = 10, **kw):
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, self.url + path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", r.text)
            except ValueError:
                msg = r.text
            raise OllamaError(f"Ollama HTTP {r.status_code}: {msg[:400]}")
        return r.json() if r.content else {}

    async def version(self) -> str | None:
        try:
            return (await self._req("GET", "/api/version", timeout=2)).get("version")
        except (httpx.HTTPError, OllamaError):
            return None

    async def tags(self) -> list[dict]:
        return (await self._req("GET", "/api/tags")).get("models") or []

    async def ps(self) -> list[dict]:
        return (await self._req("GET", "/api/ps")).get("models") or []

    async def show(self, name: str) -> dict:
        return await self._req("POST", "/api/show", json={"model": name}, timeout=15)

    async def load(self, name: str, num_ctx: int, keep_alive: str = "30m") -> None:
        # an empty prompt loads the model into memory without generating
        await self._req("POST", "/api/generate", timeout=900,
                        json={"model": name, "keep_alive": keep_alive, "options": {"num_ctx": num_ctx}})

    async def embed_load(self, name: str, keep_alive: str = "30m") -> None:
        await self._req("POST", "/api/embed", timeout=600, json={"model": name, "input": "ok", "keep_alive": keep_alive})

    async def unload(self, name: str) -> None:
        await self._req("POST", "/api/generate", timeout=60, json={"model": name, "keep_alive": 0})

    async def delete(self, name: str) -> None:
        await self._req("DELETE", "/api/delete", json={"model": name}, timeout=60)

    async def pull(self, name: str):
        """Yield progress dicts: {status, total?, completed?}."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=600)) as c:
            async with c.stream("POST", self.url + "/api/pull", json={"model": name, "stream": True}) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")
                    raise OllamaError(f"Ollama HTTP {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("error"):
                        raise OllamaError(d["error"])
                    yield d


class LocalRuntime:
    """Process-wide manager: managed server process, pull jobs, load state; emits events through `emit`."""

    def __init__(self, config, emit=None):
        self.config = config
        self.emit = emit or (lambda kind, data: None)
        self.proc: subprocess.Popen | None = None
        self.jobs: dict[str, dict] = {}      # model -> {state, pct, completed, total, status, error, task}
        self.loading: dict[str, str] = {}    # model -> "loading" | "unloading" | error text

    # ---------- server ----------
    @property
    def binary(self) -> str | None:
        return shutil.which("ollama")

    @property
    def models_dir(self) -> Path:
        d = self.config.get("local.models_dir")
        return Path(d).expanduser() if d else Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models"))

    def managed(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    async def start_server(self) -> dict:
        if not self.binary:
            raise OllamaError("這台機器沒有安裝 Ollama")
        if self.managed():
            return {"started": False, "pid": self.proc.pid}
        env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434"}
        if self.config.get("local.models_dir"):
            env["OLLAMA_MODELS"] = str(self.models_dir)
        logdir = self.config.home / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        logf = open(logdir / "ollama.log", "ab")
        self.proc = subprocess.Popen([self.binary, "serve"], env=env, stdout=logf, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):
            await asyncio.sleep(0.25)
            if await Ollama("http://127.0.0.1:11434").version():
                forget_url()
                self.emit("local_changed", {"reason": "server_started"})
                return {"started": True, "pid": self.proc.pid}
            if self.proc.poll() is not None:
                break
        tail = ""
        try:
            tail = (logdir / "ollama.log").read_text(errors="replace")[-600:]
        except OSError:
            pass
        raise OllamaError("Ollama 服務啟動失敗：" + tail)

    async def stop_server(self) -> None:
        if self.managed():
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        forget_url()
        self.emit("local_changed", {"reason": "server_stopped"})

    def client(self) -> tuple[Ollama, bool]:
        url, ok = resolve_url(self.config)
        return Ollama(url), ok

    # ---------- pulls ----------
    def start_pull(self, name: str) -> dict:
        job = self.jobs.get(name)
        if job and job["state"] == "pulling":
            return job
        job = {"model": name, "state": "pulling", "pct": 0.0, "completed": 0, "total": 0, "status": "準備下載",
               "error": None, "started": time.time()}
        self.jobs[name] = job
        job["task"] = asyncio.get_running_loop().create_task(self._pull(name, job))
        return job

    async def _pull(self, name: str, job: dict) -> None:
        cli, _ = self.client()
        last = 0.0
        layers: dict[str, tuple[int, int]] = {}
        try:
            async for d in cli.pull(name):
                st = d.get("status", "")
                if d.get("digest") and d.get("total"):
                    layers[d["digest"]] = (int(d.get("completed") or 0), int(d["total"]))
                total = sum(t for _, t in layers.values())
                done = sum(c for c, _ in layers.values())
                job.update(status=_status_zh(st), total=total, completed=done,
                           pct=round(done * 100 / total, 1) if total else job["pct"])
                if time.time() - last > 0.5:
                    last = time.time()
                    self.emit("local_progress", _public(job))
            job.update(state="done", pct=100.0, status="下載完成")
        except asyncio.CancelledError:
            job.update(state="cancelled", status="已取消")
        except Exception as e:  # noqa: BLE001
            job.update(state="error", error=str(e)[:400], status="下載失敗")
        self.emit("local_progress", _public(job))
        self.emit("local_changed", {"reason": "pull", "model": name})

    def cancel_pull(self, name: str) -> bool:
        job = self.jobs.get(name)
        if job and job["state"] == "pulling" and job.get("task"):
            job["task"].cancel()
            return True
        return False

    # ---------- load / unload ----------
    def start_load(self, name: str, num_ctx: int, embedding: bool = False) -> None:
        if self.loading.get(name) == "loading":
            return
        self.loading[name] = "loading"
        self.emit("local_changed", {"reason": "loading", "model": name})

        async def run():
            cli, _ = self.client()
            keep = str(self.config.get("local.keep_alive", "30m"))
            try:
                if embedding:
                    await cli.embed_load(name, keep)
                else:
                    await cli.load(name, num_ctx, keep)
                self.loading.pop(name, None)
            except Exception as e:  # noqa: BLE001
                self.loading[name] = f"載入失敗：{str(e)[:300]}"
            self.emit("local_changed", {"reason": "loaded", "model": name})

        asyncio.get_running_loop().create_task(run())

    async def unload(self, name: str) -> None:
        cli, _ = self.client()
        await cli.unload(name)
        self.loading.pop(name, None)
        self.emit("local_changed", {"reason": "unloaded", "model": name})

    def pulls(self) -> dict[str, dict]:
        return {k: _public(v) for k, v in self.jobs.items()}


def _public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "task"}


def _status_zh(st: str) -> str:
    s = st.lower()
    if s.startswith("pulling manifest"):
        return "取得模型資訊"
    if s.startswith("pulling") or s.startswith("downloading"):
        return "下載中"
    if "verifying" in s:
        return "驗證檔案"
    if "writing manifest" in s:
        return "寫入中"
    if s == "success":
        return "下載完成"
    return st
