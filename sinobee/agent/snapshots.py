"""File snapshots: before write_file / edit_file change a file, a copy of the original is kept, so the user can undo
everything a turn did to their files with one click ("復原本輪的檔案變更").

    $SINOBEE_HOME/snapshots/<session>/<turn key>/manifest.json   {"created", "files": [{"path", "existed", "blob"}]}
                                                 /<n>.bin         the original content

Only the first change of a file in a turn is snapshotted (that is the state to go back to). A file the turn created
is removed on restore. Files over 20 MB are not copied (and say so). Old snapshots are pruned after
agent.snapshot_days. Shell commands can change files too; those are not covered - the restore result says which
files it put back.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger("sinobee.snapshots")
MAX_BYTES = 20 * 1024 * 1024
SAFE = re.compile(r"^[\w.-]{1,80}$")


class SnapshotError(ValueError):
    pass


def _dir(home, session: str, key: str) -> Path:
    if not SAFE.match(session or "") or not SAFE.match(key or ""):
        raise SnapshotError("bad snapshot id")
    return Path(home) / "snapshots" / session / key


def _manifest(d: Path) -> dict:
    try:
        return json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"created": time.time(), "files": []}


def take(home, session: str, key: str, path: Path) -> bool:
    """Keep the current state of `path` (once per turn). Returns True when a new entry was recorded."""
    d = _dir(home, session, key)
    path = Path(path).resolve()
    m = _manifest(d)
    if any(f["path"] == str(path) for f in m["files"]):
        return False
    d.mkdir(parents=True, exist_ok=True)
    entry = {"path": str(path), "existed": path.is_file(), "blob": None, "ts": time.time()}
    if entry["existed"]:
        size = path.stat().st_size
        if size > MAX_BYTES:
            entry["skipped"] = f"too large to keep ({size} bytes)"
        else:
            blob = f"{len(m['files'])}.bin"
            shutil.copy2(path, d / blob)
            entry["blob"] = blob
    m["files"].append(entry)
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def info(home, session: str, key: str) -> dict | None:
    d = _dir(home, session, key)
    if not (d / "manifest.json").is_file():
        return None
    m = _manifest(d)
    return {"key": key, "created": m.get("created"), "restored": m.get("restored"),
            "files": [{"path": f["path"], "existed": f["existed"], "skipped": f.get("skipped")} for f in m["files"]]}


def restore(home, session: str, key: str) -> dict:
    """Put every snapshotted file back as it was before the turn; files the turn created are removed."""
    d = _dir(home, session, key)
    if not (d / "manifest.json").is_file():
        raise SnapshotError("no snapshot for this turn")
    m = _manifest(d)
    restored, removed, failed = [], [], []
    for f in m["files"]:
        p = Path(f["path"])
        try:
            if f["existed"]:
                if not f.get("blob"):
                    failed.append({"path": str(p), "why": f.get("skipped") or "no copy"})
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(d / f["blob"], p)
                restored.append(str(p))
            elif p.exists():
                p.unlink()
                removed.append(str(p))
        except OSError as e:
            failed.append({"path": str(p), "why": str(e)})
    m["restored"] = time.time()
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"restored": restored, "removed": removed, "failed": failed}


def prune(home, days: float = 14) -> int:
    root = Path(home) / "snapshots"
    if not root.is_dir():
        return 0
    cutoff, n = time.time() - days * 86400, 0
    for sess in root.iterdir():
        for d in sess.iterdir() if sess.is_dir() else []:
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
            except OSError:
                continue
        try:
            if sess.is_dir() and not any(sess.iterdir()):
                sess.rmdir()
        except OSError:
            pass
    return n
