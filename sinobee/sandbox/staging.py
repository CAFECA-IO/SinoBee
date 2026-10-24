"""Review-mode folders: the sandbox writes to a copy, you decide what reaches the original (sandbox plan E3).

    ~/.sinobee/sandbox/<conversation>/host/<name>/        the copy, mounted at /host/<name>
    ~/.sinobee/sandbox/<conversation>/base/<name>.json    the baseline: what the original was when the copy was made
                                                          (or last applied) - see the two ways below
    ~/.sinobee/sandbox/<conversation>/applied/<id>/       what an apply replaced, for 復原 (undo)

A review folder is reviewed one of two ways (folder["via"]):

- **copy** (any folder): the copy is made by clone.py - an APFS clone per file, without node_modules, virtualenvs and
  build caches (the sandbox installs its own). The baseline is {path: [size, mtime_ns, sha256 | None]} taken from the
  same walk, with no hashing: a file whose size or time moved is compared with the original byte by byte, only then.
  At the start of each task `sync` brings into the copy what you changed in your folder meanwhile (files the sandbox
  has not touched), so the sandbox never works on stale files.
- **git** (a git repository; gitreview.py): the copy carries the repository, and the baseline is a commit of your
  working tree. Changes are `git diff` against it - only what changed is looked at, so the size of the project does not
  matter; files git ignores are not reviewed and never written back. `sync` brings your edits in the same way, and
  `to_branch` delivers reviewed changes as a commit on a branch of your repository instead of your working tree.

Either way a change whose original was edited after the copy was made is a **conflict**: applying it needs an explicit
choice (`force`), never happens by itself. **Apply** writes the copy's version over the original after saving what it
replaces; a deleted file goes to the Trash, never deleted outright; the baseline then follows. **Discard** puts the
original back into the copy. **Undo** restores what an apply replaced (and removes files it added).
"""
from __future__ import annotations

import difflib
import filecmp
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from . import clone
from .folders import conv_dir

SKIP = clone.DEPS | {".git"} | clone.NOISE      # not compared, not copied back
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


def _via(folder: dict) -> str:
    return "git" if folder.get("via") == "git" else "copy"


def clone_tree(src: Path, dst: Path) -> str:
    """Copy a folder as cheaply as the file system allows (clone.py); kept for callers that only want the copy."""
    clone.clone_tree(src, dst)
    return "clone"


def ensure_copy(config, conversation: str, folder: dict) -> Path:
    """The review copy of folder ({"path", "name", "via"}), made (with its baseline) the first time."""
    dst = copy_dir(config, conversation, folder["name"])
    base = _base_file(config, conversation, folder["name"])
    if dst.is_dir() and base.is_file():
        return dst
    src = Path(folder["path"])
    if not src.is_dir():
        raise StagingError(f"找不到資料夾 {src}")
    base.parent.mkdir(parents=True, exist_ok=True)
    if _via(folder) == "git":
        from . import gitreview
        meta = gitreview.make(src, dst, base.with_suffix(""))
    else:
        meta = {"via": "copy", "files": clone.clone_tree(src, dst)}
    base.write_text(json.dumps({"path": str(src), "made": time.time(), **meta}), encoding="utf-8")
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


def _stat_is(st: os.stat_result, b: list) -> bool:
    return st.st_size == b[0] and st.st_mtime_ns == b[1]


def _orig_moved(orig: Path, b: list) -> bool:
    """Whether the original changed since the baseline b = [size, mtime_ns, sha | None]."""
    try:
        st = orig.stat()
    except OSError:
        return True
    if not orig.is_file():
        return True
    if _stat_is(st, b):
        return False
    return _sha(orig) != b[2] if b[2] else True


def _align(copy_file: Path, b: list) -> None:
    """The copy has the baseline's content after all: give it the baseline's time so it is not compared again."""
    try:
        os.utime(copy_file, ns=(time.time_ns(), b[1]))
    except OSError:
        pass


def changes(config, conversation: str, folder: dict, with_diff: bool = True) -> list[dict]:
    """What the sandbox changed in the copy of folder, against the baseline; each with whether its original moved on."""
    name = folder["name"]
    copy = copy_dir(config, conversation, name)
    if not copy.is_dir():
        return []
    base = _baseline(config, conversation, name)
    orig_root = Path(base.get("path") or folder["path"])
    if base.get("via") == "git":
        from . import gitreview
        return gitreview.changes(copy, orig_root, base, _base_file(config, conversation, name).with_suffix(""),
                                 name, with_diff, DIFF_LINES)
    files = base.get("files") or {}
    now = clone.walk(copy, SKIP)
    out = []
    for rel in sorted(set(files) | set(now)):
        b, s = files.get(rel), now.get(rel)
        p, orig = copy / rel, orig_root / rel
        if s is not None and b is not None:
            if _stat_is(s, b):
                continue
            if b[2]:
                if _sha(p) == b[2]:
                    continue
            elif not _orig_moved(orig, b) and filecmp.cmp(p, orig, shallow=False):
                _align(p, b)
                continue
            kind = "modified"
        elif s is not None:
            kind = "added"
        else:
            kind = "deleted"
        conflict = orig.exists() if b is None else _orig_moved(orig, b)
        item = {"folder": name, "path": rel, "kind": kind, "conflict": conflict,
                "size": s.st_size if s is not None else None, "original": str(orig)}
        if with_diff:
            texty = (s is None or _is_text(p)) and (not orig.is_file() or _is_text(orig))
            item["binary"] = not texty
            if texty:
                item["diff"] = _diff(orig if kind != "added" else None, p if s is not None else None, rel)
        out.append(item)
    return out


def sync(config, conversation: str, folder: dict) -> int:
    """Bring into a copy what you changed in your folder since it was made - files the sandbox has not touched
    (both ways of review; a file changed on both sides stays a conflict). Returns how many files moved."""
    name = folder["name"]
    copy = copy_dir(config, conversation, name)
    base = _baseline(config, conversation, name)
    if base.get("via") == "git":
        orig_root = Path(base.get("path") or folder["path"])
        if not copy.is_dir() or not orig_root.is_dir() or not base.get("base"):
            return 0
        from . import gitreview
        n = gitreview.sync(copy, orig_root, base, _base_file(config, conversation, name).with_suffix(""))
        if n:
            _save_baseline(config, conversation, name, base)
        return n
    if not copy.is_dir() or not base.get("files") and not base.get("path"):
        return 0
    files = base.setdefault("files", {})
    orig_root = Path(base.get("path") or folder["path"])
    if not orig_root.is_dir():
        return 0
    orig_now, copy_now = clone.walk(orig_root, SKIP), clone.walk(copy, SKIP)
    n = 0
    for rel in set(orig_now) | set(files):
        b, o, c = files.get(rel), orig_now.get(rel), copy_now.get(rel)
        untouched = (c is None) if b is None else (c is not None and _stat_is(c, b))
        if not untouched:
            continue                                       # the sandbox made or changed it: leave it to the review
        try:
            if o is None:
                if b is not None:                          # you deleted it
                    if c is not None:
                        (copy / rel).unlink()
                    files.pop(rel, None)
                    n += 1
            elif b is None or not _stat_is(o, b):          # you added or changed it
                if b is not None and b[2] and _sha(orig_root / rel) == b[2]:
                    files[rel] = [o.st_size, o.st_mtime_ns, b[2]]
                    continue
                (copy / rel).parent.mkdir(parents=True, exist_ok=True)
                clone.clone_file(orig_root / rel, copy / rel)
                os.utime(copy / rel, ns=(o.st_atime_ns, o.st_mtime_ns))
                files[rel] = [o.st_size, o.st_mtime_ns, None]
                n += 1
        except OSError:
            continue
    if n:
        _save_baseline(config, conversation, name, base)
    return n


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


def _follow(config, conversation: str, name: str, base: dict, copy: Path, now: dict[str, Path | None]) -> None:
    """The baseline takes these files' new content (None: the file is gone) - after an apply, discard or undo."""
    if base.get("via") == "git":
        from . import gitreview
        gitreview.rebase(copy, base, _base_file(config, conversation, name).with_suffix(""), now)
    else:
        files = base.setdefault("files", {})
        for rel, f in now.items():
            if f is None or not f.is_file():
                files.pop(rel, None)
                continue
            st = f.stat()
            files[rel] = [st.st_size, st.st_mtime_ns, None]
            if (copy / rel).is_file() and filecmp.cmp(copy / rel, f, shallow=False):
                _align(copy / rel, files[rel])        # same content: the copy is not reported again
    _save_baseline(config, conversation, name, base)


def apply(config, conversation: str, folder: dict, paths: list[str] | None = None,
          force: list[str] | None = None) -> dict:
    """Bring the copy's changes (all, or `paths`) to the original - prepared first, then written file by file with a
    log, so an interruption can be finished or undone (writeback.py). Conflicts are skipped unless listed in `force`.
    Returns {"id", "applied": [...], "conflicts": [...], "trashed": [...]}."""
    from . import writeback
    return writeback.apply(config, conversation, folder, paths, force)


def to_branch(config, conversation: str, folder: dict, paths: list[str] | None = None, branch: str | None = None,
              message: str | None = None) -> dict:
    """A git folder's reviewed changes (all, or `paths`) as a commit on a branch of your repository instead of your
    working tree (gitreview.to_branch); then they count as delivered (the baseline follows the copy)."""
    name = folder["name"]
    base = _baseline(config, conversation, name)
    if base.get("via") != "git":
        raise StagingError("只有 git 專案能交付成分支")
    orig_root = Path(base.get("path") or folder["path"])
    copy = copy_dir(config, conversation, name)
    items = [c for c in changes(config, conversation, folder, with_diff=False) if paths is None or c["path"] in paths]
    from . import gitreview
    branch = (branch or f"sinobee/{conversation[:12]}").strip()
    msg = (message or "").strip() or f"SinoBee：{len(items)} 個檔案的變更（對話 {conversation[:12]}）"
    res = gitreview.to_branch(copy, orig_root, base, _base_file(config, conversation, name).with_suffix(""), items,
                              branch, msg)
    _follow(config, conversation, name, base, copy, {c["path"]: (copy / c["path"]) for c in items})
    return res


def discard(config, conversation: str, folder: dict, paths: list[str] | None = None) -> list[str]:
    """Put the original back into the copy for these changes (all by default)."""
    name = folder["name"]
    base = _baseline(config, conversation, name)
    orig_root = Path(base.get("path") or folder["path"])
    copy = copy_dir(config, conversation, name)
    done, now = [], {}
    for c in changes(config, conversation, folder, with_diff=False):
        rel = c["path"]
        if paths is not None and rel not in paths:
            continue
        orig, dst = orig_root / rel, copy / rel
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        if orig.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            clone.clone_file(orig, dst)
            st = orig.stat()
            os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
            now[rel] = orig
        else:
            now[rel] = None
        done.append(rel)
    if done:
        _follow(config, conversation, name, base, copy, now)
    return done


def applied_batches(config, conversation: str) -> list[dict]:
    root = conv_dir(config, conversation) / "applied"
    out = []
    for d in sorted(root.iterdir() if root.is_dir() else [], reverse=True):
        try:
            rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("state", "done") == "done":          # interrupted ones: writeback.interrupted
            out.append(rec)
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
    if rec.get("state", "done") != "done":
        raise StagingError("這次寫回沒有完成：請用「繼續完成」或「全部還原」處理")
    orig_root = Path(rec["origin"])
    base = _baseline(config, conversation, rec["folder"])
    done, now = [], {}
    for it in rec["items"]:
        rel = it["path"]
        orig, saved = orig_root / rel, d / "files" / rel
        if it["saved"] and saved.is_file():
            orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, orig)
            now[rel] = orig
        elif it["kind"] == "added" and orig.exists():
            _trash(orig)
            now[rel] = None
        done.append(rel)
    _follow(config, conversation, rec["folder"], base, copy_dir(config, conversation, rec["folder"]), now)
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
        except (OSError, StagingError, subprocess.SubprocessError):
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
