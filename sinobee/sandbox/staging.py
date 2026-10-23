"""Review-mode folders: the sandbox writes to a copy, you decide what reaches the original (sandbox plan E3).

    ~/.sinobee/sandbox/<conversation>/host/<name>/        the copy, mounted at /host/<name>
    ~/.sinobee/sandbox/<conversation>/base/<name>.json    the baseline: {relative path: [size, mtime_ns, sha256]} of
                                                          the original when the copy was made (or last applied)
    ~/.sinobee/sandbox/<conversation>/applied/<id>/       what an apply replaced, for 復原 (undo)

- **The copy** is an APFS clone on macOS (`cp -c`, copy-on-write: instant and no extra space until a file changes),
  a reflink on Linux file systems that have them, a plain copy otherwise. Made the first time the folder is used.
- **Changes** compare the copy with the baseline: added, modified, deleted (text files get a diff). A change whose
  original was edited after the copy was made (its hash is no longer the baseline's) is a **conflict**: applying it
  needs an explicit choice (`force`), never happens by itself.
- **Apply** writes the copy's version over the original after saving what it replaces; a deleted file goes to the
  Trash, never deleted outright. The baseline then follows. **Discard** puts the original back into the copy.
  **Undo** restores what an apply replaced (and removes files it added).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from .folders import conv_dir

SKIP = {".DS_Store", ".git", "node_modules", "__pycache__", ".venv", "venv"}   # not compared, not copied back
TEXT_MAX = 400_000
DIFF_LINES = 400


class StagingError(Exception):
    pass


def copy_dir(config, conversation: str, name: str) -> Path:
    return conv_dir(config, conversation) / "host" / name


def _base_file(config, conversation: str, name: str) -> Path:
    return conv_dir(config, conversation) / "base" / f"{name}.json"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path) -> dict[str, Path]:
    out = {}
    if not root.is_dir():
        return out
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            if f in SKIP:
                continue
            p = Path(dirpath) / f
            if p.is_symlink() or not p.is_file():
                continue
            out[p.relative_to(root).as_posix()] = p
    return out


def _manifest(root: Path) -> dict[str, list]:
    out = {}
    for rel, p in _walk(root).items():
        st = p.stat()
        out[rel] = [st.st_size, st.st_mtime_ns, _sha(p)]
    return out


def clone_tree(src: Path, dst: Path) -> str:
    """Copy a folder as cheaply as the file system allows; returns how ("clone", "reflink", "copy")."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    if platform.system() == "Darwin":
        r = subprocess.run(["cp", "-c", "-R", str(src), str(dst)], capture_output=True, timeout=1800)
        if r.returncode == 0:
            return "clone"
    elif shutil.which("cp"):
        r = subprocess.run(["cp", "-a", "--reflink=auto", str(src), str(dst)], capture_output=True, timeout=1800)
        if r.returncode == 0:
            return "reflink"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    return "copy"


def _clone_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        if subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True, timeout=600).returncode == 0:
            return
    shutil.copy2(src, dst)


def ensure_copy(config, conversation: str, folder: dict) -> Path:
    """The review copy of folder ({"path", "name"}), made (with its baseline) the first time."""
    dst = copy_dir(config, conversation, folder["name"])
    base = _base_file(config, conversation, folder["name"])
    if dst.is_dir() and base.is_file():
        return dst
    src = Path(folder["path"])
    if not src.is_dir():
        raise StagingError(f"找不到資料夾 {src}")
    clone_tree(src, dst)
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text(json.dumps({"path": str(src), "made": time.time(), "files": _manifest(src)}), encoding="utf-8")
    return dst


def _baseline(config, conversation: str, name: str) -> dict:
    try:
        return json.loads(_base_file(config, conversation, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}}


def _save_baseline(config, conversation: str, name: str, data: dict) -> None:
    _base_file(config, conversation, name).write_text(json.dumps(data), encoding="utf-8")


def _is_text(p: Path) -> bool:
    try:
        if p.stat().st_size > TEXT_MAX:
            return False
        head = p.read_bytes()[:8192]
    except OSError:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        return e.start > len(head) - 4          # a character cut at the 8 KiB boundary


def _diff(a: Path | None, b: Path | None, rel: str) -> str:
    def lines(p):
        return p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if p and p.is_file() else []
    out = list(difflib.unified_diff(lines(a), lines(b), f"原本/{rel}", f"修改後/{rel}", n=3))
    if len(out) > DIFF_LINES:
        out = out[:DIFF_LINES] + [f"…（差異太長，只顯示前 {DIFF_LINES} 行）\n"]
    return "".join(out)


def changes(config, conversation: str, folder: dict, with_diff: bool = True) -> list[dict]:
    """What the sandbox changed in the copy of folder, against the baseline; each with whether its original moved on."""
    name = folder["name"]
    copy = copy_dir(config, conversation, name)
    if not copy.is_dir():
        return []
    base = _baseline(config, conversation, name)
    files = base.get("files") or {}
    orig_root = Path(base.get("path") or folder["path"])
    now = _walk(copy)
    out = []
    for rel in sorted(set(files) | set(now)):
        b, p = files.get(rel), now.get(rel)
        if p is not None and b is not None:
            st = p.stat()
            if st.st_size == b[0] and st.st_mtime_ns == b[1]:
                continue
            if _sha(p) == b[2]:
                continue
            kind = "modified"
        elif p is not None:
            kind = "added"
        else:
            kind = "deleted"
        orig = orig_root / rel
        if b is None:
            conflict = orig.exists()                        # you made a file of that name meanwhile
        else:
            conflict = not orig.is_file() or _sha(orig) != b[2]
        item = {"folder": name, "path": rel, "kind": kind, "conflict": conflict,
                "size": p.stat().st_size if p is not None else None, "original": str(orig)}
        if with_diff:
            texty = (p is None or _is_text(p)) and (not orig.is_file() or _is_text(orig))
            item["binary"] = not texty
            if texty:
                item["diff"] = _diff(orig if kind != "added" else None, p, rel)
        out.append(item)
    return out


def _trash(p: Path) -> Path:
    """Move a file to the Trash (macOS ~/.Trash, freedesktop on Linux); returns where it went."""
    home = Path.home()
    trash = home / ".Trash" if platform.system() == "Darwin" else home / ".local" / "share" / "Trash" / "files"
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / p.name
    k = 2
    while dst.exists():
        dst = trash / f"{p.stem} {k}{p.suffix}"
        k += 1
    shutil.move(str(p), str(dst))
    return dst


def apply(config, conversation: str, folder: dict, paths: list[str] | None = None,
          force: list[str] | None = None) -> dict:
    """Bring the copy's changes (all, or `paths`) to the original. Conflicts are skipped unless listed in `force`.
    Returns {"id", "applied": [...], "conflicts": [...], "trashed": [...]}."""
    name = folder["name"]
    todo = [c for c in changes(config, conversation, folder, with_diff=False) if paths is None or c["path"] in paths]
    force = set(force or [])
    base = _baseline(config, conversation, name)
    files = base.setdefault("files", {})
    orig_root = Path(base.get("path") or folder["path"])
    copy = copy_dir(config, conversation, name)
    bid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    store = conv_dir(config, conversation) / "applied" / bid
    record = {"id": bid, "folder": name, "origin": str(orig_root), "t": time.time(), "items": []}
    applied, conflicts, trashed = [], [], []
    for c in todo:
        rel = c["path"]
        if c["conflict"] and rel not in force:
            conflicts.append(rel)
            continue
        orig, src = orig_root / rel, copy / rel
        saved = None
        if orig.is_file():                                   # what is replaced, for undo
            saved = store / "files" / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(orig, saved)
        if c["kind"] == "deleted":
            if orig.exists():
                trashed.append(str(_trash(orig)))
            files.pop(rel, None)
        else:
            orig.parent.mkdir(parents=True, exist_ok=True)
            tmp = orig.with_name(f".{orig.name}.sinobee-tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, orig)
            st = orig.stat()
            files[rel] = [st.st_size, st.st_mtime_ns, _sha(orig)]
            # the copy's timestamp follows, so the file is not reported again
            os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
            files[rel][1] = src.stat().st_mtime_ns
        record["items"].append({"path": rel, "kind": c["kind"], "saved": bool(saved)})
        applied.append(rel)
    if applied:
        store.mkdir(parents=True, exist_ok=True)
        (store / "record.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        _save_baseline(config, conversation, name, base)
    return {"id": bid if applied else None, "applied": applied, "conflicts": conflicts, "trashed": trashed}


def discard(config, conversation: str, folder: dict, paths: list[str] | None = None) -> list[str]:
    """Put the original back into the copy for these changes (all by default)."""
    name = folder["name"]
    base = _baseline(config, conversation, name)
    files = base.setdefault("files", {})
    orig_root = Path(base.get("path") or folder["path"])
    copy = copy_dir(config, conversation, name)
    done = []
    for c in changes(config, conversation, folder, with_diff=False):
        rel = c["path"]
        if paths is not None and rel not in paths:
            continue
        orig, dst = orig_root / rel, copy / rel
        if dst.exists():
            dst.unlink()
        if orig.is_file():
            _clone_file(orig, dst)
            st = dst.stat()
            files[rel] = [st.st_size, st.st_mtime_ns, _sha(orig)]
        else:
            files.pop(rel, None)
        done.append(rel)
    _save_baseline(config, conversation, name, base)
    return done


def applied_batches(config, conversation: str) -> list[dict]:
    root = conv_dir(config, conversation) / "applied"
    out = []
    for d in sorted(root.iterdir() if root.is_dir() else [], reverse=True):
        try:
            out.append(json.loads((d / "record.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def undo(config, conversation: str, batch: str) -> list[str]:
    """Restore what apply `batch` replaced: modified files get their old content, added files go to the Trash,
    deleted files come back. The copy keeps the sandbox's version (it shows up as a change again)."""
    d = conv_dir(config, conversation) / "applied" / batch
    try:
        rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StagingError("找不到這次套用的紀錄") from None
    if rec.get("undone"):
        return []
    orig_root = Path(rec["origin"])
    base = _baseline(config, conversation, rec["folder"])
    files = base.setdefault("files", {})
    done = []
    for it in rec["items"]:
        rel = it["path"]
        orig, saved = orig_root / rel, d / "files" / rel
        if it["saved"] and saved.is_file():
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, orig)
            st = orig.stat()
            files[rel] = [st.st_size, st.st_mtime_ns, _sha(orig)]
        elif it["kind"] == "added" and orig.exists():
            _trash(orig)
            files.pop(rel, None)
        done.append(rel)
    _save_baseline(config, conversation, rec["folder"], base)
    rec["undone"] = time.time()
    (d / "record.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return done


def review_folders(config, conversation: str) -> list[dict]:
    from .folders import folders_for
    return [f for f in folders_for(config, conversation) if f["mode"] == "review"]


def ensure_copies(config, conversation: str) -> list[str]:
    """Make the review copies this conversation's sandbox needs (folders added in Settings since); returns errors."""
    errs = []
    for f in review_folders(config, conversation):
        try:
            ensure_copy(config, conversation, f)
        except (StagingError, OSError, subprocess.SubprocessError) as e:
            errs.append(f"{f['path']}: {e}")
    return errs


def pending(config, conversation: str, with_diff: bool = False) -> list[dict]:
    """Every change waiting for review in this conversation's copies."""
    out = []
    for f in review_folders(config, conversation):
        try:
            out += [{**c, "origin": f["path"]} for c in changes(config, conversation, f, with_diff)]
        except OSError:
            continue
    return out


def remember_pending(config, conversation: str, items: list[dict]) -> None:
    """The count the sidebar shows next to the conversation (pending.json)."""
    d = conv_dir(config, conversation)
    if not items and not d.exists():
        return
    d.mkdir(parents=True, exist_ok=True)
    (d / "pending.json").write_text(json.dumps({"conversation": conversation, "count": len(items), "t": time.time()}), encoding="utf-8")


def pending_counts(config) -> dict[str, int]:
    root = Path(config.home) / "sandbox"
    out = {}
    for d in root.iterdir() if root.is_dir() else []:
        try:
            data = json.loads((d / "pending.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("count"):
            out[str(data.get("conversation") or d.name)] = int(data["count"])
    return out
