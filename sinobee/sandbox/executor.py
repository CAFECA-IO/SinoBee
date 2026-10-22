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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .paths import PathMap

log = logging.getLogger(__name__)

IMPLEMENTED = {"host"}          # B3 adds "sandbox"


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


def executor_kind(config) -> str:
    kind = str((config.get("sandbox.executor", "host") if config is not None else "host") or "host")
    if kind not in IMPLEMENTED:
        log.warning("sandbox.executor=%s is not available yet; the tools run on this computer", kind)
        return "host"
    return kind


def make_executor(config) -> Executor:
    from .paths import for_config
    return HostExecutor(config, for_config(config))


def executor_of(ctx) -> Executor:
    """The executor of a tool's session (created on first use and kept in ctx.state)."""
    ex = ctx.state.get("executor") if isinstance(getattr(ctx, "state", None), dict) else None
    if ex is None or ex.config is not ctx.config:
        ex = make_executor(ctx.config)
        if isinstance(getattr(ctx, "state", None), dict):
            ctx.state["executor"] = ex
    return ex
