"""Which files the UI may show: the workspace, the project and allowed paths - never SinoBee's private files."""
from __future__ import annotations

from pathlib import Path

PRIVATE_NAMES = {"secrets.json", "settings.json", "config.yaml", "sinobee.db", "sinobee.db-wal", "sinobee.db-shm",
                 "trusted_projects.json", ".vncpass"}


class FileAccessError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def allowed_roots(config) -> list[Path]:
    roots = [config.workspace, config.project_dir] + [Path(p) for p in (config.get("permissions.allowed_paths") or [])]
    # folders given to conversations' sandboxes, and the review copies of them
    roots.append(Path(config.home) / "sandbox")
    import json as _json
    for g in (Path(config.home) / "sandbox").glob("*/grants.json"):      # folders one conversation was given
        try:
            roots += [Path(str(x["path"])) for x in _json.loads(g.read_text(encoding="utf-8")) if x.get("path")]
        except (OSError, ValueError, TypeError, KeyError):
            continue
    out = []
    for r in roots:
        try:
            out.append(Path(r).expanduser().resolve())
        except OSError:
            pass
    return out


def resolve_file(config, path: str) -> Path:
    from .sandbox import for_config
    try:                           # a sandbox path (/workspace/..., /host/<name>/...) means a file on this computer
        p = for_config(config).to_host(path, config.workspace)
    except RuntimeError:           # "~name" of a user that does not exist (e.g. "~250kg" in a sentence)
        raise FileAccessError(404, "file not found") from None
    p = p.resolve()
    home = config.home.resolve()
    copies = home / "sandbox"
    shown = config.workspace.resolve() in [p, *p.parents] or (copies in p.parents and "host" in p.relative_to(copies).parts[1:2])
    if p.name in PRIVATE_NAMES or (home in p.parents and not shown):
        raise FileAccessError(403, "this file is private to SinoBee")
    if not any(p == r or r in p.parents for r in allowed_roots(config)):
        raise FileAccessError(403, "file is outside the workspace / allowed paths")
    if not p.is_file():
        raise FileAccessError(404, "file not found")
    return p
