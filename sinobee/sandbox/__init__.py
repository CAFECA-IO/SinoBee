"""Running the tools somewhere other than directly on this computer (the sandbox plan).

executor.py   where commands, files and the screen are reached (HostExecutor now; SandboxExecutor in plan B)
paths.py      paths of this computer <-> paths the tools see
"""
from .executor import Executor, HostExecutor, RunResult, executor_kind, executor_of, make_executor
from .paths import Mount, PathMap, for_config, sandbox_map

__all__ = ["Executor", "HostExecutor", "RunResult", "executor_kind", "executor_of", "make_executor",
           "Mount", "PathMap", "for_config", "sandbox_map"]
