"""Shell execution tool."""
from __future__ import annotations

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
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    timeout = max(1, min(int(timeout or 120), 1800))
    workdir = ex.paths.to_host(cwd) if cwd else (ex.paths.workspace or ctx.config.workspace)   # cwd may be a sandbox path
    if not ex.exists(workdir):
        return f"ERROR: cwd {ex.paths.to_exec(workdir)} does not exist"
    env = {"PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}
    sandbox = getattr(ctx.config, "sandbox", None)
    if sandbox is not None:                 # an evaluation trial: decoy home, no network
        env.update(sandbox.env())
    res = await ex.run(command, workdir, timeout, env)
    text = res.output.strip()
    header = (f"[exit {res.code}{' (timed out after %ss)' % timeout if res.timed_out else ''}] "
              f"cwd={ex.paths.to_exec(workdir)}")
    return f"{header}\n{text}" if text else header
