"""Path mapping between this computer and where the tools run (sandbox plan A2).

The agent, the tools and the GUI all talk about files. With the tools on this computer (HostExecutor) a path is
a path. In a sandbox the same files appear at other places: the workspace at /workspace, an authorised folder at
/host/<name>. A `PathMap` is the table of those mounts and turns a path either way:

- `to_host(path)`   whatever the agent or a tool output says (a sandbox path, a path on this computer, a path
                    relative to the workspace, "~/...") -> the file on this computer, which SinoBee reads, serves
                    and checks;
- `to_exec(path)`   a file on this computer -> how the tools see it (what goes into a command, a cwd, a script path);
- `visible(path)`   whether the tools can see a file of this computer at all;
- `describe()`      the table for the system prompt (empty when there is nothing to explain).

With no mounts (`PathMap()`, the host executor) every conversion is the identity, so nothing changes until a
sandbox gives the map its mounts.
"""
from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MODES = ("rw", "ro", "review")      # review = written to a copy that you review before it reaches the original


@dataclass(frozen=True)
class Mount:
    host: Path               # on this computer (absolute, resolved)
    inner: PurePosixPath     # where the tools see it
    mode: str = "rw"
    label: str = ""

    def contains_host(self, p: Path) -> bool:
        return p == self.host or self.host in p.parents

    def contains_inner(self, p: PurePosixPath) -> bool:
        return p == self.inner or self.inner in p.parents


def _norm_host(p: Path) -> Path:
    try:
        return p.resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(p))


class PathMap:
    def __init__(self, mounts: list[Mount] | None = None, workspace: Path | None = None):
        # longest first, so /host/a/b wins over /host/a
        self.mounts = sorted(mounts or [], key=lambda m: len(m.inner.parts), reverse=True)
        self.workspace = Path(workspace) if workspace else None

    @property
    def identity(self) -> bool:
        return not self.mounts

    # ----------------------------------------------------------------- conversions
    def to_host(self, path: str | os.PathLike, base: Path | None = None) -> Path:
        """A path as the agent or a tool wrote it -> the file on this computer (not resolved: callers decide)."""
        s = os.fspath(path)
        p = Path(s).expanduser()
        if not p.is_absolute():
            return Path(base or self.workspace or Path.cwd()) / p
        if self.mounts:
            inner = PurePosixPath(posixpath.normpath(s))     # /workspace/../etc is /etc, not in the workspace
            for m in self.mounts:
                if m.contains_inner(inner):
                    return m.host.joinpath(*inner.relative_to(m.inner).parts)
            return Path(str(inner))
        return p

    def to_exec(self, path: str | os.PathLike) -> str:
        """A file on this computer -> how the tools see it; unchanged when no mount covers it."""
        p = Path(os.fspath(path))
        if not self.mounts:
            return str(p)
        h = _norm_host(p)
        for m in sorted(self.mounts, key=lambda m: len(m.host.parts), reverse=True):
            if m.contains_host(h):
                rel = h.relative_to(m.host)
                return str(m.inner.joinpath(*rel.parts)) if rel.parts else str(m.inner)
        return str(p)

    def visible(self, path: str | os.PathLike) -> bool:
        if not self.mounts:
            return True
        h = _norm_host(Path(os.fspath(path)))
        return any(m.contains_host(h) for m in self.mounts)

    def mount_of(self, path: str | os.PathLike) -> Mount | None:
        h = _norm_host(Path(os.fspath(path)))
        for m in sorted(self.mounts, key=lambda m: len(m.host.parts), reverse=True):
            if m.contains_host(h):
                return m
        return None

    def to_host_text(self, text: str) -> str:
        """Sandbox paths inside a tool output written as paths of this computer (for the GUI and the artifact
        scan). Only whole path prefixes are replaced."""
        if not self.mounts or not text:
            return text
        for m in self.mounts:
            inner = str(m.inner)
            if inner in text:
                text = _replace_prefix(text, inner, str(m.host))
        return text

    def describe(self) -> str:
        if not self.mounts:
            return ""
        home = str(Path.home())
        rows = []
        for m in sorted(self.mounts, key=lambda m: str(m.inner)):
            host = str(m.host)
            host = "~" + host[len(home):] if host.startswith(home + "/") or host == home else host
            how = {"rw": "read/write", "ro": "read-only", "review": "writes go to a copy the user reviews"}[m.mode]
            rows.append(f"- {m.inner} = {host} ({how})")
        return ("Your tools run in a sandbox. Paths there differ from the user's computer:\n" + "\n".join(rows) +
                "\nUse the sandbox paths in commands; paths of the user's computer given to file tools are "
                "translated. Anything else on the user's computer is not visible.")


def _replace_prefix(text: str, old: str, new: str) -> str:
    """`old` as a whole path prefix (not inside another path or word) -> `new`."""
    return re.sub(rf"(?<![\w./-]){re.escape(old)}(?=$|[/\s\"'`),:;\]>])", lambda _: new, text)


def sandbox_map(workspace: Path, folders: list[dict] | None = None) -> PathMap:
    """The table a sandbox uses: the workspace at /workspace and each authorised folder at /host/<name>.
    folders: [{"path": "~/Documents/報告", "name": "報告", "mode": "review"}, ...]"""
    ws = _norm_host(Path(workspace).expanduser())
    mounts = [Mount(ws, PurePosixPath("/workspace"), "rw", "workspace")]
    used = set()
    for f in folders or []:
        host = _norm_host(Path(str(f.get("path", ""))).expanduser())
        name = str(f.get("name") or host.name or "folder").replace("/", "_")
        base, k = name, 2
        while name in used:
            name, k = f"{base}-{k}", k + 1
        used.add(name)
        mode = f.get("mode") if f.get("mode") in MODES else "review"
        mounts.append(Mount(host, PurePosixPath("/host") / name, mode, name))
    return PathMap(mounts, ws)


def for_config(config) -> PathMap:
    """The map the tools of this configuration use: no mounts while they run on this computer."""
    from .executor import executor_kind
    ws = getattr(config, "workspace", None)
    if executor_kind(config) == "sandbox":
        return sandbox_map(ws, config.get("sandbox.folders") or [])
    return PathMap(workspace=ws)
