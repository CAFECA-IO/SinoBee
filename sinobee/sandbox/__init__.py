"""Running the tools somewhere other than directly on this computer (the sandbox plan).

executor.py   where commands, files and the screen are reached: HostExecutor (this computer) or SandboxExecutor
manager.py    the Docker containers: one per conversation, behind the egress proxy
egress.py     the proxy, the sandbox's only way out (runs in its own container)
paths.py      paths of this computer <-> paths the tools see
"""
from .executor import (Executor, HostExecutor, RunResult, SandboxExecutor, executor_kind, executor_of, make_executor,
                       sandbox_unavailable, wanted_kind)
from .paths import Mount, PathMap, for_config, sandbox_map

__all__ = ["Executor", "HostExecutor", "RunResult", "SandboxExecutor", "executor_kind", "executor_of",
           "make_executor", "sandbox_unavailable", "wanted_kind", "Mount", "PathMap", "for_config", "sandbox_map"]
