"""Writing reviewed changes back to your folder so that an interruption can always be finished or undone.

A write-back of many files can be cut off anywhere - SinoBee quits, the Mac sleeps or loses power, the disk fills.
Each file is replaced atomically (written beside it, then renamed over it), but a batch stopped halfway leaves your
folder half old, half new. So a write-back is a small transaction, kept in

    ~/.sinobee/sandbox/<conversation>/applied/<id>/
        record.json     what is written: items with each original's size and time when it was prepared; state
        files/<path>    the originals it replaces (for 復原 and for going back after an interruption)
        new/<path>      the new contents, frozen from the copy when prepared
        progress.log    one line per file done, flushed to disk as it goes
        changes.patch | changes.diff    the difference, to keep or hand on (git folders: a binary git patch)

1. **prepare** - nothing in your folder changes: conflicts, write permission and free space are checked for the whole
   batch, the originals are saved and the new contents frozen (APFS clones: almost no space). Anything wrong stops
   here. The sandbox may go on changing its copy: what is written is what you reviewed.
2. **write** - file by file; right before each, its original is checked again (you edited it in the meantime: it is
   skipped as a conflict, never overwritten); each file done is logged.
3. **done** - the baseline follows, leftovers are removed; 復原 undoes the whole batch.

A batch found in `prepared` or `writing` was interrupted: `resume` finishes it from the frozen contents (a file
already written but not yet logged is recognised by its content), `rollback` puts back every file it touched (made
ones go to the Trash) and the changes are waiting for review again. The review panel offers both.

`export_current` / `export_batch` give the difference as a file: a git patch (`git apply`) for a git folder, a zip
(changes.diff for `patch -p1`, the new files, the deleted list, a README) otherwise. Names that usually hold secrets
(.env, keys, credentials) are left out of exports, and the README says so.
"""
from __future__ import annotations

import fnmatch
import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from . import clone
from .folders import conv_dir

SECRET_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                ".npmrc", ".pypirc", ".netrc", "credentials*", "*.keystore", "*.jks", "secrets.*")
MARGIN = 16 << 20


class _Stop(Exception):
    """Tests: the write stops here as if the power went off."""


def secret(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, p) for p in SECRET_NAMES)


def _store(config, conversation: str, bid: str) -> Path:
    return conv_dir(config, conversation) / "applied" / bid


def _save(store: Path, rec: dict) -> None:
    tmp = store / "record.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, store / "record.json")


def load(config, conversation: str, bid: str) -> dict:
    from .staging import StagingError
    try:
        return json.loads((_store(config, conversation, bid) / "record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StagingError("找不到這次寫回的紀錄") from None


def _log(store: Path, entry: dict) -> None:
    with open(store / "progress.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _progress(store: Path) -> dict[str, dict]:
    out = {}
    try:
        for line in (store / "progress.log").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                out[e["path"]] = e
            except (ValueError, KeyError):
                continue                       # a line cut by the interruption itself
    except OSError:
        pass
    return out


def _stat(p: Path) -> list | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns] if p.is_file() else ["dir", 0]


def _tmp(orig: Path) -> Path:
    return orig.with_name(f".{orig.name}.sinobee-tmp")


def _writable(p: Path) -> bool:
    q = p.parent
    while not q.exists() and q != q.parent:
        q = q.parent
    return os.access(q, os.W_OK)


def _same(a: Path, b: Path) -> bool:
    import filecmp
    try:
        return a.is_file() and b.is_file() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


# ----------------------------------------------------------------- the difference as text
def _udiff(a: Path | None, b: Path | None, rel: str) -> str:
    import difflib
    from .staging import _is_text

    def lines(p):
        return p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if p and p.is_file() else []
    if (a and a.is_file() and not _is_text(a)) or (b and b.is_file() and not _is_text(b)):
        return f"Binary files {'a/' + rel if a else '/dev/null'} and {'b/' + rel if b else '/dev/null'} differ\n"
    out = difflib.unified_diff(lines(a), lines(b), f"a/{rel}" if a else "/dev/null", f"b/{rel}" if b else "/dev/null")
    return "".join(x if x.endswith("\n") else x + "\n\\ No newline at end of file\n" for x in out)


def _git_patch(config, conversation: str, name: str, paths: list[str]) -> str:
    from . import gitreview
    from .staging import _base_file, _baseline, copy_dir
    base = _baseline(config, conversation, name)
    stem = _base_file(config, conversation, name).with_suffix("")
    out = []
    for i in range(0, len(paths), 200):
        out.append(gitreview._git(copy_dir(config, conversation, name), "diff", "--cached", "--binary", "--no-color",
                                  "--no-ext-diff", base["base"], "--", *paths[i:i + 200],
                                  index=Path(f"{stem}.review-index")))
    return "".join(out)


# ----------------------------------------------------------------- write back
def apply(config, conversation: str, folder: dict, paths: list[str] | None = None, force: list[str] | None = None,
          stop_after: int | None = None) -> dict:
    """Write the copy's changes (all, or `paths`) to the original, as a transaction. Conflicts are skipped unless
    listed in `force`. Returns {"id", "applied", "conflicts", "trashed"}."""
    from .staging import StagingError, _baseline, changes, copy_dir
    name = folder["name"]
    if any(x["folder"] == name for x in interrupted(config, conversation)):
        raise StagingError("上次寫回這個資料夾時中斷了：請先「繼續完成」或「全部還原」")
    todo = [c for c in changes(config, conversation, folder, with_diff=False) if paths is None or c["path"] in paths]
    force = set(force or [])
    conflicts = [c["path"] for c in todo if c["conflict"] and c["path"] not in force]
    todo = [c for c in todo if c["path"] not in conflicts]
    if not todo:
        return {"id": None, "applied": [], "conflicts": conflicts, "trashed": []}
    base = _baseline(config, conversation, name)
    orig_root = Path(base.get("path") or folder["path"])
    copy = copy_dir(config, conversation, name)
    bid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    store = _store(config, conversation, bid)
    # 1. prepare: nothing in your folder changes yet
    items, need = [], 0
    try:
        store.mkdir(parents=True)
        for c in todo:
            rel = c["path"]
            orig, src = orig_root / rel, copy / rel
            pre = _stat(orig)
            if pre and pre[0] == "dir":
                raise StagingError(f"{rel} 在你的資料夾裡是資料夾，不能用檔案取代")
            if not _writable(orig):
                raise StagingError(f"沒有權限寫入 {orig.parent}")
            if pre:
                (store / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                clone.clone_file(orig, store / "files" / rel)
            if c["kind"] != "deleted":
                if not src.is_file():
                    raise StagingError(f"副本裡的 {rel} 不見了（沙盒還在修改？）請重新整理後再寫回")
                (store / "new" / rel).parent.mkdir(parents=True, exist_ok=True)
                clone.clone_file(src, store / "new" / rel)
                need += src.stat().st_size
            items.append({"path": rel, "kind": c["kind"], "saved": bool(pre), "pre": pre})
        free = shutil.disk_usage(orig_root).free
        if need + MARGIN > free:
            raise StagingError(f"磁碟空間不足：需要約 {need / 1e6:.0f} MB，只剩 {free / 1e6:.0f} MB")
        try:
            if base.get("via") == "git":
                (store / "changes.patch").write_text(_git_patch(config, conversation, name, [
                    i["path"] for i in items if not secret(i["path"])]), encoding="utf-8")
            else:
                (store / "changes.diff").write_text("".join(
                    _udiff(store / "files" / i["path"] if i["saved"] else None,
                           store / "new" / i["path"] if i["kind"] != "deleted" else None, i["path"])
                    for i in items if not secret(i["path"])), encoding="utf-8")
        except Exception:  # noqa: BLE001 - the difference file is a convenience, never a reason not to write
            pass
    except BaseException:
        shutil.rmtree(store, ignore_errors=True)
        raise
    rec = {"id": bid, "folder": name, "origin": str(orig_root), "via": base.get("via", "copy"), "t": time.time(),
           "state": "prepared", "items": items, "conflicts": conflicts}
    _save(store, rec)
    # 2. write, 3. done
    return _run(config, conversation, folder, store, rec, stop_after)


def _write_one(store: Path, orig_root: Path, it: dict) -> dict:
    from .staging import _trash
    rel = it["path"]
    orig = orig_root / rel
    if it["kind"] == "deleted":
        if not os.path.lexists(orig):
            return {"path": rel, "ok": True}
        if _stat(orig) != it["pre"]:
            return {"path": rel, "conflict": True}
        return {"path": rel, "ok": True, "trashed": str(_trash(orig))}
    new = store / "new" / rel
    if _same(orig, new):                       # already written (cut off before it was logged)
        return {"path": rel, "ok": True}
    if _stat(orig) != it["pre"]:               # you changed it since it was prepared: never overwritten
        return {"path": rel, "conflict": True}
    orig.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(orig)
    clone.clone_file(new, tmp)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, orig)
    return {"path": rel, "ok": True}


def execute(store: Path, rec: dict, stop_after: int | None = None) -> dict[str, dict]:
    """Write a prepared batch file by file with its log (also what is left of an interrupted one); returns the log
    {path: {"ok"|"conflict", "trashed"?}}. Shared by write-backs and checkpoint restores (checkpoints.py)."""
    orig_root = Path(rec["origin"])
    if rec["state"] != "writing":
        rec["state"] = "writing"
        _save(store, rec)
    done = _progress(store)
    n = 0
    for it in rec["items"]:
        if it["path"] in done:
            continue
        if stop_after is not None and n >= stop_after:
            raise _Stop(it["path"])
        _log(store, _write_one(store, orig_root, it))
        n += 1
    for it in rec["items"]:
        _tmp(orig_root / it["path"]).unlink(missing_ok=True)
    return _progress(store)


def _run(config, conversation: str, folder: dict, store: Path, rec: dict, stop_after: int | None = None) -> dict:
    from .staging import _baseline, _follow, copy_dir
    orig_root = Path(rec["origin"])
    prog = execute(store, rec, stop_after)
    applied = [it for it in rec["items"] if prog.get(it["path"], {}).get("ok")]
    late = [it["path"] for it in rec["items"] if prog.get(it["path"], {}).get("conflict")]
    name = rec["folder"]
    _follow(config, conversation, name, _baseline(config, conversation, name), copy_dir(config, conversation, name),
            {it["path"]: (orig_root / it["path"] if it["kind"] != "deleted" else None) for it in applied})
    rec.update(state="done", items=applied, late_conflicts=late, finished=time.time())
    _save(store, rec)
    return {"id": rec["id"] if applied else None, "applied": [it["path"] for it in applied],
            "conflicts": rec.get("conflicts", []) + late,
            "trashed": [prog[it["path"]]["trashed"] for it in applied if prog[it["path"]].get("trashed")]}


# ----------------------------------------------------------------- after an interruption
def interrupted(config, conversation: str) -> list[dict]:
    """Write-backs cut off before they finished: [{"id", "folder", "t", "total", "done"}]."""
    root = conv_dir(config, conversation) / "applied"
    out = []
    for d in sorted(root.iterdir() if root.is_dir() else []):
        try:
            rec = json.loads((d / "record.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("state") in ("prepared", "writing"):
            prog = _progress(d)
            out.append({"id": rec["id"], "folder": rec["folder"], "t": rec["t"], "total": len(rec["items"]),
                        "done": sum(1 for e in prog.values() if e.get("ok"))})
    return out


def _unfinished(config, conversation: str, bid: str) -> tuple[Path, dict]:
    from .staging import StagingError
    rec = load(config, conversation, bid)
    if rec.get("state") not in ("prepared", "writing"):
        raise StagingError("這次寫回沒有中斷，不需要處理")
    return _store(config, conversation, bid), rec


def resume(config, conversation: str, bid: str) -> dict:
    """Finish an interrupted write-back from the contents frozen when it was prepared."""
    store, rec = _unfinished(config, conversation, bid)
    return _run(config, conversation, {"name": rec["folder"], "path": rec["origin"]}, store, rec)


def rollback(config, conversation: str, bid: str) -> dict:
    """Undo an interrupted write-back: every file it already wrote gets its original back (files it made go to the
    Trash, files it deleted come back). The changes wait for review again."""
    store, rec = _unfinished(config, conversation, bid)
    return rollback_store(store, rec)


def rollback_store(store: Path, rec: dict) -> dict:
    """Put back every file an unfinished batch wrote (from its saved originals); made files go to the Trash."""
    from .staging import _trash
    orig_root = Path(rec["origin"])
    prog = _progress(store)
    restored, trashed = [], []
    for it in rec["items"]:
        rel = it["path"]
        orig, saved, new = orig_root / rel, store / "files" / rel, store / "new" / rel
        _tmp(orig).unlink(missing_ok=True)
        if it["kind"] == "deleted":
            touched = prog.get(rel, {}).get("ok") or (it["pre"] and not os.path.lexists(orig))
        else:
            touched = prog.get(rel, {}).get("ok") or _same(orig, new) and _stat(orig) != it["pre"]
        if not touched:
            continue
        if it["saved"] and saved.is_file():
            orig.parent.mkdir(parents=True, exist_ok=True)
            tmp = _tmp(orig)
            clone.clone_file(saved, tmp)
            os.replace(tmp, orig)
            restored.append(rel)
        elif os.path.lexists(orig):
            trashed.append(str(_trash(orig)))
    rec.update(state="rolled_back", finished=time.time())
    _save(store, rec)
    return {"restored": restored, "trashed": trashed}


# ----------------------------------------------------------------- the difference as a file
def export_current(config, conversation: str, folder: dict, paths: list[str] | None = None) -> tuple[str, bytes, str]:
    """The changes waiting for review (all, or `paths`) as (file name, bytes, media type)."""
    from .staging import changes, copy_dir, _baseline
    name = folder["name"]
    items = [c for c in changes(config, conversation, folder, with_diff=False) if paths is None or c["path"] in paths]
    left = [c["path"] for c in items if secret(c["path"])]
    items = [c for c in items if not secret(c["path"])]
    stamp = time.strftime("%Y%m%d-%H%M")
    base = _baseline(config, conversation, name)
    orig_root = Path(base.get("path") or folder["path"])
    if base.get("via") == "git":
        text = _git_patch(config, conversation, name, [c["path"] for c in items]) if items else ""
        head = (f"# SinoBee：{name} 的變更（{stamp}），套用：cd {orig_root} && git apply --3way 這個檔案\n"
                + (f"# 未包含可能含機密的檔案：{', '.join(left)}\n" if left else ""))
        return f"{name}-{stamp}.patch", (head + text).encode("utf-8"), "text/x-diff"
    copy = copy_dir(config, conversation, name)
    files = {c["path"]: (orig_root / c["path"] if c["kind"] != "added" and (orig_root / c["path"]).is_file() else None,
                         copy / c["path"] if c["kind"] != "deleted" else None, c["kind"]) for c in items}
    return f"{name}-{stamp}.zip", _zip(name, orig_root, files, left), "application/zip"


def export_batch(config, conversation: str, bid: str) -> tuple[str, bytes, str]:
    """What write-back `bid` wrote, from its own record (also after the copy changed)."""
    rec = load(config, conversation, bid)
    store = _store(config, conversation, bid)
    files = {}
    for it in rec["items"]:
        rel = it["path"]
        if secret(rel):
            continue
        files[rel] = (store / "files" / rel if it.get("saved") else None,
                      store / "new" / rel if it["kind"] != "deleted" else None, it["kind"])
    left = [it["path"] for it in rec["items"] if secret(it["path"])]
    data = _zip(rec["folder"], Path(rec["origin"]), files, left,
                patch=(store / "changes.patch") if (store / "changes.patch").is_file() else None)
    return f"{rec['folder']}-{rec['id']}.zip", data, "application/zip"


def _zip(name: str, origin: Path, files: dict, left: list[str], patch: Path | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        diff = "".join(_udiff(a, b, rel) for rel, (a, b, _k) in sorted(files.items()))
        z.writestr("changes.diff", diff)
        if patch is not None:
            z.write(patch, "changes.patch")
        deleted = [rel for rel, (_a, _b, k) in sorted(files.items()) if k == "deleted"]
        z.writestr("deleted.txt", "".join(f"{rel}\n" for rel in deleted))
        for rel, (_a, b, k) in sorted(files.items()):
            if k != "deleted" and b is not None and b.is_file():
                z.write(b, f"files/{rel}")
        z.writestr("README.txt", (
            f"SinoBee 匯出的變更：{name}（原資料夾 {origin}）\n\n"
            "files/        新增或修改後的檔案，依原本的路徑排列\n"
            "deleted.txt   要刪除的檔案\n"
            "changes.diff  文字差異（統一格式）；在原資料夾執行 patch -p1 < changes.diff 也可以套用\n"
            + ("changes.patch git 格式的差異（含二進位檔）：git apply --3way changes.patch\n" if patch else "")
            + "\n套用方式：把 files/ 的內容複製到原資料夾的同一位置，再刪除 deleted.txt 列出的檔案。\n"
            + (f"\n為避免外洩，以下可能含機密的檔案沒有包含在內：\n" + "".join(f"  {r}\n" for r in left) if left else "")))
    return buf.getvalue()
