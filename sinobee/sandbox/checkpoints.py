"""Checkpoints of folders the sandbox writes directly (mode rw): for fixing a real environment or a project too big to
review, every task starts from a point you can go back to.

    ~/.sinobee/sandbox/<conversation>/checkpoints/<name>/index.json   [{"id", "t", "label", "kind", ...}], newest last

- **git** (the folder is the top of a git repository): a commit of the whole working tree - tracked, changed and
  untracked files, not ignored ones - written into your repository's object store with a temporary index started from
  your own index, so only files whose size or time changed are read, and your branch, index and files are not touched.
  It is kept by the ref refs/sinobee/checkpoints/<conversation>/<id>.
- **copy** (any other folder): a clone of the folder (clone.py: on APFS it costs nothing until files change) with its
  manifest; node_modules, virtualenvs and caches are left out. A folder with more than sandbox.review_max_files files
  gets no copy checkpoint (the task is told).

A checkpoint is made before each task (`before_task`, which also brings review copies up to date - staging.sync), by
the agent before a risky step (folder_checkpoint), or by you; nothing new is made when nothing changed. Restoring puts
back the files that differ (files made since go to the Trash) after taking one more checkpoint, so a restore can be
undone too. sandbox.checkpoints_keep (10) are kept per folder.
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import clone
from .folders import conv_dir

SKIP = clone.DEPS | clone.NOISE


def _dir(config, conversation: str, name: str) -> Path:
    return conv_dir(config, conversation) / "checkpoints" / name


def listing(config, conversation: str, name: str) -> list[dict]:
    try:
        data = json.loads((_dir(config, conversation, name) / "index.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(config, conversation: str, name: str, items: list[dict]) -> None:
    d = _dir(config, conversation, name)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "index.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "index.json")


def _ref(conversation: str, cid: str) -> str:
    from .manager import safe
    return f"refs/sinobee/checkpoints/{safe(conversation)}/{cid}"


def _git_tree(config, conversation: str, folder: dict) -> str:
    """The folder's working tree as a git tree, without touching the user's index."""
    from .gitreview import _snapshot
    root = Path(folder["path"])
    tmp = _dir(config, conversation, folder["name"]) / "tmp-index"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if (root / ".git" / "index").is_file():
        shutil.copyfile(root / ".git" / "index", tmp)
    else:
        tmp.unlink(missing_ok=True)
    try:
        return _snapshot(root, tmp)
    finally:
        tmp.unlink(missing_ok=True)


def make(config, conversation: str, folder: dict, label: str = "") -> dict | None:
    """A checkpoint of the folder now (or the last one, when nothing changed since). None: too big to copy."""
    from .gitreview import _commit, _git, is_repo
    name, root = folder["name"], Path(folder["path"])
    items = listing(config, conversation, name)
    last = items[-1] if items else None
    cid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    label = (label or "").strip().replace("\n", " ")[:80]
    if is_repo(root):
        tree = _git_tree(config, conversation, folder)
        if last and last.get("kind") == "git" and last.get("tree") == tree:
            return last
        head = _git(root, "rev-parse", "-q", "--verify", "HEAD^{commit}", check=False).strip() or None
        commit = _commit(root, tree, head, f"SinoBee 檢查點：{label or cid}", ref=_ref(conversation, cid))
        item = {"id": cid, "t": time.time(), "label": label, "kind": "git", "commit": commit, "tree": tree}
    else:
        from .folders import count_files, review_max
        if count_files(root, review_max(config)) > review_max(config):
            return None
        now = clone.walk(root, SKIP | {".git"})
        if last and last.get("kind") == "copy":
            try:
                old = json.loads((_dir(config, conversation, name) / f"{last['id']}.json").read_text(encoding="utf-8"))
                if {r: [s.st_size, s.st_mtime_ns] for r, s in now.items()} == {r: v[:2] for r, v in old.items()}:
                    return last
            except (OSError, ValueError):
                pass
        d = _dir(config, conversation, name)
        manifest = clone.clone_tree(root, d / cid, skip=SKIP, whole=frozenset({".git"}))
        (d / f"{cid}.json").write_text(json.dumps(manifest), encoding="utf-8")
        item = {"id": cid, "t": time.time(), "label": label, "kind": "copy", "files": len(manifest)}
    items.append(item)
    keep = max(1, int(config.get("sandbox.checkpoints_keep", 10) or 10))
    for old in items[:-keep]:
        _drop(config, conversation, folder, old)
    _save(config, conversation, name, items[-keep:])
    return item


def _drop(config, conversation: str, folder: dict, item: dict) -> None:
    d = _dir(config, conversation, folder["name"])
    if item.get("kind") == "git":
        from .gitreview import _git
        _git(Path(folder["path"]), "update-ref", "-d", _ref(conversation, item["id"]), check=False)
    else:
        shutil.rmtree(d / item["id"], ignore_errors=True)
        (d / f"{item['id']}.json").unlink(missing_ok=True)


def _find(config, conversation: str, name: str, cid: str) -> dict:
    from .staging import StagingError
    item = next((x for x in listing(config, conversation, name) if x["id"] == cid), None)
    if item is None:
        raise StagingError("找不到這個檢查點")
    return item


def diff(config, conversation: str, folder: dict, cid: str) -> list[dict]:
    """What going back to checkpoint cid would change: [{"path", "kind"}] - modified (gets its old content), added
    (made since: goes to the Trash), deleted (deleted since: comes back)."""
    item = _find(config, conversation, folder["name"], cid)
    root = Path(folder["path"])
    if item["kind"] == "git":
        from .gitreview import _git
        tree = _git_tree(config, conversation, folder)
        raw = _git(root, "diff-tree", "-r", "-z", "--name-status", "--no-renames", item["commit"], tree).split("\0")
        kinds = {"A": "added", "D": "deleted"}
        return [{"path": raw[i + 1], "kind": kinds.get(raw[i][0], "modified")}
                for i in range(0, len(raw) - 1, 2) if raw[i]]
    d = _dir(config, conversation, folder["name"])
    old = json.loads((d / f"{cid}.json").read_text(encoding="utf-8"))
    now = clone.walk(root, SKIP | {".git"})
    out = []
    for rel in sorted(set(old) | set(now)):
        o, s = old.get(rel), now.get(rel)
        if o is not None and s is not None:
            if s.st_size == o[0] and s.st_mtime_ns == o[1]:
                continue
            if filecmp.cmp(root / rel, d / cid / rel, shallow=False):
                continue
            out.append({"path": rel, "kind": "modified"})
        elif s is not None:
            out.append({"path": rel, "kind": "added"})
        else:
            out.append({"path": rel, "kind": "deleted"})
    return out


def _blob(root: Path, commit: str, rel: str) -> tuple[bytes, bool]:
    from .gitreview import ENV
    env = {**os.environ, **ENV}
    mode = subprocess.run(["git", "-C", str(root), "ls-tree", commit, "--", rel], capture_output=True, text=True,
                          env=env, timeout=60).stdout.split(" ", 1)[0]
    data = subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{commit}:{rel}"], capture_output=True,
                          env=env, timeout=600, check=True).stdout
    return data, mode == "100755"


def _restores(config, conversation: str, name: str) -> Path:
    return _dir(config, conversation, name) / "restores"


def restore(config, conversation: str, folder: dict, cid: str, paths: list[str] | None = None,
            stop_after: int | None = None) -> dict:
    """Put the folder back to checkpoint cid (all its differences, or `paths`) as a transaction, like a write-back
    (writeback.py): a checkpoint of now is taken, everything to write is prepared first (old contents taken out of
    the checkpoint, current files saved, space and permissions checked - nothing changes if anything is wrong), then
    files are replaced one by one with a log, each checked right before (changed since it was prepared: skipped).
    Interrupted, it can be finished or rolled back (resume_restore / rollback_restore).
    Returns {"id", "restored", "trashed", "conflicts", "before"}."""
    from . import writeback as wb
    from .staging import StagingError
    item = _find(config, conversation, folder["name"], cid)
    name, root = folder["name"], Path(folder["path"])
    if any(x["folder"] == name for x in interrupted_restores(config, conversation)):
        raise StagingError("上次還原這個資料夾時中斷了：請先「繼續完成」或「全部復原」")
    todo = [c for c in diff(config, conversation, folder, cid) if paths is None or c["path"] in paths]
    before = make(config, conversation, folder, f"還原到 {time.strftime('%m/%d %H:%M', time.localtime(item['t']))} 之前")
    rid = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    store = _restores(config, conversation, name) / rid
    items, need = [], 0
    try:
        store.mkdir(parents=True)
        for c in todo:
            rel = c["path"]
            target = root / rel
            pre = wb._stat(target)
            if pre and pre[0] == "dir":
                raise StagingError(f"{rel} 現在是資料夾，不能用檔案取代")
            if not wb._writable(target):
                raise StagingError(f"沒有權限寫入 {target.parent}")
            if pre:
                (store / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                clone.clone_file(target, store / "files" / rel)
            if c["kind"] == "added":                       # made after the checkpoint: goes to the Trash
                items.append({"path": rel, "kind": "deleted", "saved": bool(pre), "pre": pre})
                continue
            dst = store / "new" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if item["kind"] == "git":
                data, exe = _blob(root, item["commit"], rel)
                dst.write_bytes(data)
                os.chmod(dst, 0o755 if exe else 0o644)
            else:
                clone.clone_file(_dir(config, conversation, name) / cid / rel, dst)
            need += dst.stat().st_size
            items.append({"path": rel, "kind": "modified" if pre else "added", "saved": bool(pre), "pre": pre})
        free = shutil.disk_usage(root).free
        if need + wb.MARGIN > free:
            raise StagingError(f"磁碟空間不足：需要約 {need / 1e6:.0f} MB，只剩 {free / 1e6:.0f} MB")
    except BaseException:
        shutil.rmtree(store, ignore_errors=True)
        raise
    rec = {"id": rid, "type": "restore", "folder": name, "origin": str(root), "cid": cid, "t": time.time(),
           "state": "prepared", "items": items, "before": (before or {}).get("id")}
    wb._save(store, rec)
    return _finish_restore(store, rec, stop_after)


def _finish_restore(store: Path, rec: dict, stop_after: int | None = None) -> dict:
    from . import writeback as wb
    prog = wb.execute(store, rec, stop_after)
    ok = [it for it in rec["items"] if prog.get(it["path"], {}).get("ok")]
    rec.update(state="done", finished=time.time())
    wb._save(store, rec)
    return {"id": rec["id"], "before": rec.get("before"),
            "restored": [it["path"] for it in ok if it["kind"] != "deleted"],
            "trashed": [prog[it["path"]]["trashed"] for it in ok if prog[it["path"]].get("trashed")],
            "conflicts": [it["path"] for it in rec["items"] if prog.get(it["path"], {}).get("conflict")]}


def interrupted_restores(config, conversation: str) -> list[dict]:
    """Restores cut off before they finished: [{"id", "folder", "cid", "t", "total", "done"}]."""
    from . import writeback as wb
    out = []
    root = conv_dir(config, conversation) / "checkpoints"
    for rec_path in sorted(root.glob("*/restores/*/record.json")) if root.is_dir() else []:
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("state") in ("prepared", "writing"):
            prog = wb._progress(rec_path.parent)
            out.append({"id": rec["id"], "folder": rec["folder"], "cid": rec.get("cid"), "t": rec["t"],
                        "total": len(rec["items"]), "done": sum(1 for e in prog.values() if e.get("ok"))})
    return out


def _unfinished_restore(config, conversation: str, name: str, rid: str) -> tuple[Path, dict]:
    from .staging import StagingError
    store = _restores(config, conversation, name) / rid
    try:
        rec = json.loads((store / "record.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StagingError("找不到這次還原的紀錄") from None
    if rec.get("state") not in ("prepared", "writing"):
        raise StagingError("這次還原沒有中斷，不需要處理")
    return store, rec


def resume_restore(config, conversation: str, name: str, rid: str) -> dict:
    """Finish an interrupted restore from the contents prepared for it."""
    store, rec = _unfinished_restore(config, conversation, name, rid)
    return _finish_restore(store, rec)


def rollback_restore(config, conversation: str, name: str, rid: str) -> dict:
    """Undo an interrupted restore: the files it already wrote get what they were before it started."""
    from . import writeback as wb
    store, rec = _unfinished_restore(config, conversation, name, rid)
    return wb.rollback_store(store, rec)


def before_task(config, conversation: str, label: str = "") -> dict:
    """At the start of a task: review copies take what you changed in your folders meanwhile (staging.sync), folders
    written directly get a checkpoint. Returns {"synced": {name: n}, "checkpoints": {name: id | None}}."""
    from . import staging
    from .folders import folders_for
    out: dict = {"synced": {}, "checkpoints": {}}
    for f in folders_for(config, conversation):
        try:
            if f["mode"] == "review" and f.get("via") != "git":
                n = staging.sync(config, conversation, f)
                if n:
                    out["synced"][f["name"]] = n
            elif f["mode"] == "rw" and Path(f["path"]).is_dir():
                c = make(config, conversation, f, label)
                out["checkpoints"][f["name"]] = c["id"] if c else None
        except Exception:  # noqa: BLE001 - a task is never blocked by its safety net; the next one tries again
            continue
    return out
