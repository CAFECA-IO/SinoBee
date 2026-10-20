"""Shell execution tool."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .approval import shell_danger
from .registry import ToolContext, tool


def _danger(args: dict) -> str | None:
    return shell_danger(str(args.get("command", "")))


@tool("shell", "shell",
      "Run a shell command (bash) on the computer and return stdout/stderr and exit code. Use for file "
      "operations, running programs, installing packages, git, scripts. Non-interactive only: pass flags like -y. "
      "Long-running servers should be started with `nohup ... &`.",
      {"properties": {
          "command": {"type": "string", "description": "The bash command to run"},
          "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120, max 1800)"},
          "cwd": {"type": "string", "description": "Working directory (default: the SinoBee workspace)"},
      }, "required": ["command"]},
      writes=True, dangerous=_danger)
async def shell(ctx: ToolContext, command: str, timeout: int = 120, cwd: str | None = None) -> str:
    timeout = max(1, min(int(timeout or 120), 1800))
    workdir = Path(cwd).expanduser() if cwd else ctx.config.workspace
    if not workdir.exists():
        return f"ERROR: cwd {workdir} does not exist"
    env = {**os.environ, "PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}
    sandbox = getattr(ctx.config, "sandbox", None)
    if sandbox is not None:                 # an evaluation trial: decoy home, no network
        env.update(sandbox.env())
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash", "-lc", command, cwd=str(workdir), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        code = proc.returncode
        timed_out = False
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        out, code, timed_out = b"", -9, True
    except asyncio.CancelledError:  # user pressed "stop": don't leave the command running
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        raise
    text = out.decode("utf-8", errors="replace").strip()
    header = f"[exit {code}{' (timed out after %ss)' % timeout if timed_out else ''}] cwd={workdir}"
    return f"{header}\n{text}" if text else header
