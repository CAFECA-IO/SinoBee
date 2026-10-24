"""Plan C experiment (sandbox plan stage 3): review a huge non-git folder without copying it - an overlay mount.

    lower  your folder, read-only                      (no copy at all, however many files)
    upper  a Docker volume in Docker's own disk         (only what the sandbox writes; deletions are whiteouts)
    view   lower + upper, mounted in the sandbox        (it sees your folder and its own changes)

The review would then be the upper layer alone. Whether it works depends on where Docker runs: on Linux your folder is
on the same machine; on Docker Desktop (Mac) the folder reaches Docker's VM through file sharing (virtiofs) and an
overlay may or may not accept that as its lower layer. `probe` tries it and measures it:

    sinobee sandbox probe-overlay ~/Documents/some-folder

Nothing in your folder is written: the probe writes, changes and deletes only in the view, then checks the upper layer
caught it all and your folder did not change.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .manager import EGRESS_IMAGE, docker_sync


def _run(*args: str, timeout: float = 120) -> tuple[int, str, str]:
    return docker_sync(*args, timeout=timeout)


def probe(folder: str | Path, image: str | None = None) -> dict:
    """Try an overlay of `folder` in Docker; {"ok", "steps": [...], "lower", "timings"}."""
    lower = Path(folder).expanduser().resolve()
    out: dict = {"ok": False, "lower": str(lower), "steps": [], "timings": {}}
    img = image or EGRESS_IMAGE

    def step(name: str, ok: bool, detail: str = "") -> bool:
        out["steps"].append({"step": name, "ok": ok, "detail": detail.strip()[:400]})
        return ok
    if not lower.is_dir():
        step("folder", False, f"{lower} is not a folder")
        return out
    tag = uuid.uuid4().hex[:8]
    upvol, ovl = f"sinobee-ovl-up-{tag}", f"sinobee-ovl-{tag}"
    try:
        rc, _, err = _run("volume", "create", upvol)
        if not step("upper volume", rc == 0, err):
            return out
        rc, mp, err = _run("volume", "inspect", "-f", "{{.Mountpoint}}", upvol)
        mp = mp.strip()
        if not step("upper path in Docker", rc == 0 and bool(mp), err or mp):
            return out
        rc, _, err = _run("run", "--rm", "-v", f"{upvol}:/u", img, "sh", "-c", "mkdir -p /u/upper /u/work")
        if not step("upper/work folders", rc == 0, err):
            return out
        # the lower layer as Docker's VM sees your folder: the same path (Linux, Docker Desktop's shared /Users),
        # or under /host_mnt (older Docker Desktop)
        tried = []
        for low in (str(lower), f"/host_mnt{lower}"):
            opts = f"lowerdir={low},upperdir={mp}/upper,workdir={mp}/work"
            _run("volume", "rm", "-f", ovl)
            rc, _, err = _run("volume", "create", "--driver", "local", "--opt", "type=overlay", "--opt",
                              "device=overlay", "--opt", f"o={opts}", ovl)
            if rc != 0:
                tried.append(f"{low}: {err.strip()[:200]}")
                continue
            t = time.time()
            rc, listing, err = _run("run", "--rm", "-v", f"{ovl}:/w", img, "sh", "-c", "ls -A /w | head -5; "
                                    "find /w -xdev -type f 2>/dev/null | wc -l", timeout=600)
            if rc == 0:
                out["timings"]["mount_and_count_s"] = round(time.time() - t, 2)
                out["lower_seen_as"] = low
                out["files_seen"] = int((listing.strip().splitlines() or ["0"])[-1] or 0)
                break
            tried.append(f"{low}: {err.strip()[:200]}")
        else:
            step("overlay mount", False, " | ".join(tried))
            return out
        step("overlay mount", True, f"lower {out['lower_seen_as']}: {out['files_seen']} files")
        before = sorted(p.name for p in lower.iterdir())
        first = next((p.name for p in sorted(lower.iterdir()) if p.is_file()), None)
        script = ("echo probe > /w/__sinobee_probe__.txt && "
                  + (f"echo changed >> '/w/{first}' && rm -f '/w/{first}.gone' ; " if first else "")
                  + (f"cp '/w/{first}' '/w/{first}.gone' && rm '/w/{first}.gone' && " if first else "")
                  + "ls -A /w | wc -l")
        t = time.time()
        rc, _, err = _run("run", "--rm", "-v", f"{ovl}:/w", img, "sh", "-c", script)
        out["timings"]["write_s"] = round(time.time() - t, 2)
        if not step("write in the view", rc == 0, err):
            return out
        rc, up, err = _run("run", "--rm", "-v", f"{upvol}:/u", img, "sh", "-c", "cd /u/upper && find . | sort")
        caught = rc == 0 and "__sinobee_probe__.txt" in up and (first is None or f"./{first}" in up)
        step("upper layer caught the changes", caught, up[:300] + err)
        untouched = sorted(p.name for p in lower.iterdir()) == before and \
            not (lower / "__sinobee_probe__.txt").exists()
        step("your folder unchanged", untouched)
        if first:
            data = (lower / first).read_bytes()
            step("your file unchanged", not data.endswith(b"changed\n"))
        out["ok"] = all(s["ok"] for s in out["steps"])
        return out
    finally:
        _run("volume", "rm", "-f", ovl)
        _run("volume", "rm", "-f", upvol)


def report(res: dict) -> str:
    lines = [f"overlay of {res['lower']}: {'works' if res['ok'] else 'does NOT work'}"]
    for s in res["steps"]:
        lines.append(f"  {'✓' if s['ok'] else '✗'} {s['step']}{(' - ' + s['detail']) if s['detail'] else ''}")
    if res.get("timings"):
        lines.append("  timings: " + json.dumps(res["timings"]))
    return "\n".join(lines)


# ================================================================= the third way of review (stage 3b)
"""Review by overlay (folder via="overlay"): a folder that is not a git project and has too many files to copy
(more than `sandbox.overlay_min_files`, 20,000 by default).

    sinobee-ovu-<conversation>-<id>   upper volume: upper/ (what the sandbox wrote) and work/
    sinobee-ovl-<conversation>-<id>   the view: an overlay volume, your folder below, upper above - /host/<name>
    sandbox/<conversation>/host/<name>/  a cache of the upper layer's files on this computer (what the review
                                          compares and writes back from; the sandbox never sees it)

A change is an upper entry whose content differs from your file: a file (added / modified), a whiteout (deleted),
an opaque directory (everything of yours under it that the sandbox did not write again is deleted). A conflict is a
file of yours changed after the folder was mounted (or after its last write-back). Discard puts your version back
through the view, never by editing the upper layer under a mounted overlay.
"""

UPPER, VIEW = "sinobee-ovu-", "sinobee-ovl-"
LIST_PY = r"""
import json, os, stat, sys
root, skip = "/u/upper", set(json.loads(sys.argv[1]))
for cur, dirs, files in os.walk(root):
    rel = os.path.relpath(cur, root)
    rel = "" if rel == "." else rel
    for d in list(dirs):
        p = os.path.join(cur, d)
        if d in skip:
            dirs.remove(d)
            continue
        st = os.lstat(p)
        try:
            opaque = os.getxattr(p, "trusted.overlay.opaque", follow_symlinks=False) == b"y"
        except OSError:
            opaque = False
        if opaque:
            print(json.dumps({"p": os.path.join(rel, d), "t": "o"}))
    for f in files:
        p = os.path.join(cur, f)
        st = os.lstat(p)
        r = os.path.join(rel, f)
        if stat.S_ISCHR(st.st_mode) and st.st_rdev == 0:
            print(json.dumps({"p": r, "t": "w"}))
        elif stat.S_ISREG(st.st_mode):
            print(json.dumps({"p": r, "t": "f", "s": st.st_size, "m": st.st_mtime_ns}))
        elif stat.S_ISLNK(st.st_mode):
            print(json.dumps({"p": r, "t": "l"}))
"""


def vol_names(conversation: str, name: str) -> tuple[str, str]:
    import hashlib

    from .manager import safe
    tag = f"{safe(conversation)[:40]}-{hashlib.sha1(name.encode()).hexdigest()[:8]}"
    return UPPER + tag, VIEW + tag


def _state_file(config) -> Path:
    return Path(config.home) / "sandbox" / "overlay.json"


def capability(config, refresh: bool = False) -> dict:
    """Can this machine's Docker review by overlay? {"ok", "prefix", "why", "docker"} - probed once per Docker
    version on a small folder of SinoBee's own, the way the sandbox writes (as uid 1000: change a file, add one in a
    subfolder, delete one)."""
    rc, ver, _ = _run("version", "-f", "{{.Server.Version}}", timeout=20)
    ver = ver.strip() if rc == 0 else ""
    f = _state_file(config)
    try:
        saved = json.loads(f.read_text())
    except (OSError, ValueError):
        saved = {}
    if not refresh and saved.get("docker") == ver and ver:
        return saved
    out = {"ok": False, "prefix": "", "why": "", "docker": ver, "t": time.time()}
    if not ver:
        out["why"] = "Docker is not running"
        return out
    import shutil
    lower = Path(config.home) / "sandbox" / "_overlay_probe"
    shutil.rmtree(lower, ignore_errors=True)
    (lower / "sub").mkdir(parents=True)
    (lower / "keep.txt").write_text("keep\n")
    (lower / "gone.txt").write_text("gone\n")
    (lower / "sub" / "a.txt").write_text("a\n")
    up, view = vol_names("probe", uuid.uuid4().hex)
    try:
        if not _make_volumes(up, view, lower, out):
            return out
        rc, so, err = _run("run", "--rm", "--user", "1000:1000", "--network", "none", "-v", f"{view}:/w", EGRESS_IMAGE,
                           "sh", "-c", "echo changed >> /w/sub/a.txt && echo new > /w/sub/b.txt && rm /w/gone.txt "
                           "&& cat /w/keep.txt /w/sub/a.txt", timeout=120)
        if rc != 0:
            out["why"] = f"the sandbox user cannot write through the overlay: {err.strip()[:200]}"
            return out
        untouched = (lower / "sub" / "a.txt").read_text() == "a\n" and (lower / "gone.txt").exists() \
            and not (lower / "sub" / "b.txt").exists()
        out["ok"] = untouched and "changed" in so
        out["why"] = "" if out["ok"] else "the overlay wrote into the folder below"
        return out
    finally:
        _run("volume", "rm", "-f", view)
        _run("volume", "rm", "-f", up)
        shutil.rmtree(lower, ignore_errors=True)
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(out))
        except OSError:
            pass


def available(config) -> bool:
    try:
        return bool(capability(config).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _make_volumes(up: str, view: str, lower: Path, out: dict) -> bool:
    """The upper volume (upper/ owned by the sandbox user) and the overlay view of `lower` on it."""
    rc, _, err = _run("volume", "create", "--label", "org.sinobee.role=overlay", up)
    if rc != 0:
        out["why"] = f"volume: {err.strip()[:200]}"
        return False
    rc, mp, err = _run("volume", "inspect", "-f", "{{.Mountpoint}}", up)
    mp = mp.strip()
    rc2, _, err2 = _run("run", "--rm", "--network", "none", "-v", f"{up}:/u", EGRESS_IMAGE, "sh", "-c",
                        "mkdir -p /u/upper /u/work && chown 1000:1000 /u/upper")
    if rc != 0 or not mp or rc2 != 0:
        out["why"] = f"upper: {(err or err2).strip()[:200]}"
        return False
    tried = []
    prefixes = [out["prefix"]] if out.get("prefix") else ["", "/host_mnt"]
    for pre in prefixes:
        low = f"{pre}{lower}"
        _run("volume", "rm", "-f", view)
        rc, _, err = _run("volume", "create", "--driver", "local", "--label", "org.sinobee.role=overlay",
                          "--opt", "type=overlay", "--opt", "device=overlay",
                          "--opt", f"o=lowerdir={low},upperdir={mp}/upper,workdir={mp}/work", view)
        if rc == 0:
            rc, _, err = _run("run", "--rm", "--network", "none", "-v", f"{view}:/w", EGRESS_IMAGE, "ls", "/w")
        if rc == 0:
            out["prefix"] = pre
            return True
        tried.append(f"{low}: {err.strip()[:160]}")
    out["why"] = "overlay: " + " | ".join(tried)
    return False


def make(config, conversation: str, folder: dict) -> dict:
    """The two volumes of a folder reviewed by overlay; returns what base/<name>.json keeps."""
    from .staging import StagingError
    cap = capability(config)
    if not cap.get("ok"):
        raise StagingError(f"這台電腦的 Docker 不能用疊加方式審核：{cap.get('why') or '未知原因'}")
    up, view = vol_names(conversation, folder["name"])
    rc, _, _ = _run("volume", "inspect", view)
    if rc != 0:
        out = {"prefix": cap.get("prefix", "")}
        if not _make_volumes(up, view, Path(folder["path"]), out):
            raise StagingError(f"無法建立疊加檢視：{out.get('why')}")
    return {"via": "overlay", "upper": up, "view": view, "made_ns": time.time_ns(), "seen": {}}


def remove(conversation: str, name: str | None = None) -> None:
    """Drop a folder's (or all of a conversation's) overlay volumes - after the container that used them is gone."""
    from .manager import safe
    if name is not None:
        names = list(vol_names(conversation, name))
    else:
        rc, out, _ = _run("volume", "ls", "-q", "--filter", "label=org.sinobee.role=overlay")
        mine = safe(conversation)[:40] + "-"
        names = [v for v in out.split() if any(v.startswith(p + mine) and len(v) == len(p + mine) + 8
                                                 for p in (UPPER, VIEW))]
    for v in sorted(names, key=lambda v: not v.startswith(VIEW)):      # the view before its upper volume
        _run("volume", "rm", "-f", v)


def prune(config, conversation: str) -> list[str]:
    """Remove this conversation's overlay volumes that no folder given to it uses any more (a volume still mounted
    by a container stays until it is not)."""
    from .folders import folders_for
    from .manager import safe
    keep = set()
    for f in folders_for(config, conversation):
        if f["mode"] == "review" and f.get("via") == "overlay":
            keep.update(vol_names(conversation, f["name"]))
    rc, out, _ = _run("volume", "ls", "-q", "--filter", "label=org.sinobee.role=overlay")
    mine = safe(conversation)[:40] + "-"
    gone = []
    for v in sorted(out.split(), key=lambda v: not v.startswith(VIEW)):
        if v not in keep and any(v.startswith(p + mine) and len(v) == len(p + mine) + 8 for p in (UPPER, VIEW)):
            if _run("volume", "rm", v)[0] == 0:
                gone.append(v)
    return gone


def _list_upper(up: str) -> list[dict]:
    from .staging import SKIP
    rc, out, err = _run("run", "--rm", "--network", "none", "--cap-add", "SYS_ADMIN", "-v", f"{up}:/u:ro",
                        EGRESS_IMAGE, "python", "-c", LIST_PY, json.dumps(sorted(SKIP)), timeout=300)
    if rc != 0:
        from .staging import StagingError
        raise StagingError(f"無法讀取沙盒的變更：{err.strip()[:200]}")
    return [json.loads(x) for x in out.splitlines() if x.strip()]


def _fetch(up: str, cache: Path, files: list[dict]) -> None:
    """The upper layer's files into the cache (only those whose size or time moved); the rest of the cache goes."""
    import io
    import subprocess
    import tarfile
    man_f = cache / ".sinobee-overlay.json"
    try:
        man = json.loads(man_f.read_text())
    except (OSError, ValueError):
        man = {}
    want = {f["p"]: [f["s"], f["m"]] for f in files}
    stale = [p for p, v in want.items() if man.get(p) != v or not (cache / p).is_file()]
    for p in set(man) - set(want):
        (cache / p).unlink(missing_ok=True)
    for i in range(0, len(stale), 500):
        part = stale[i:i + 500]
        r = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "-v", f"{up}:/u:ro", EGRESS_IMAGE,
                            "tar", "-C", "/u/upper", "-cf", "-", "-T", "-"], input="\n".join(part).encode(),
                           capture_output=True, timeout=600)
        if r.returncode != 0:
            from .staging import StagingError
            raise StagingError(f"無法取出沙盒的變更：{r.stderr.decode(errors='replace')[:200]}")
        with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
            for m in t.getmembers():
                if not m.isfile():
                    continue
                dst = cache / m.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(m) as src, open(dst, "wb") as out:
                    out.write(src.read())
    cache.mkdir(parents=True, exist_ok=True)
    man_f.write_text(json.dumps(want))


def _same(a: Path, b: Path) -> bool:
    import filecmp
    try:
        return a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def changes(config, conversation: str, folder: dict, base: dict, cache: Path, with_diff: bool) -> list[dict]:
    from .staging import SKIP, _diff, _is_text
    name = folder["name"]
    orig_root = Path(base.get("path") or folder["path"])
    entries = _list_upper(base["upper"])
    files = [e for e in entries if e["t"] == "f"]
    _fetch(base["upper"], cache, files)
    made, seen = int(base.get("made_ns") or 0), base.get("seen") or {}
    written = {e["p"] for e in files}
    found: dict[str, str] = {}
    for e in files:
        orig, mine = orig_root / e["p"], cache / e["p"]
        if _same(orig, mine):
            continue
        found[e["p"]] = "modified" if orig.is_file() else "added"

    def gone(rel: str) -> None:
        orig = orig_root / rel
        if orig.is_file():
            found.setdefault(rel, "deleted")
        elif orig.is_dir():
            for cur, dirs, fs in os.walk(orig):
                dirs[:] = [d for d in dirs if d not in SKIP]
                for f in fs:
                    r = os.path.relpath(os.path.join(cur, f), orig_root)
                    if r not in written:
                        found.setdefault(r, "deleted")
    for e in entries:
        if e["t"] == "w":
            gone(e["p"])
        elif e["t"] == "o":
            gone(e["p"])
    out = []
    for rel in sorted(found):
        kind, orig, mine = found[rel], orig_root / rel, cache / rel
        if kind == "added":
            conflict = os.path.lexists(orig)
        else:
            try:
                conflict = orig.stat().st_mtime_ns > max(made, int(seen.get(rel, 0)))
            except OSError:
                conflict = True
        item = {"folder": name, "path": rel, "kind": kind, "conflict": conflict,
                "size": mine.stat().st_size if kind != "deleted" and mine.is_file() else None, "original": str(orig)}
        if with_diff:
            texty = (kind == "deleted" or _is_text(mine)) and (not orig.is_file() or _is_text(orig))
            item["binary"] = not texty
            if texty:
                item["diff"] = _diff(orig if kind != "added" else None, mine if kind != "deleted" else None, rel)
        out.append(item)
    return out


def follow(base: dict, orig_root: Path, now: dict) -> None:
    """After a write-back / undo: these files of yours are the new reference for conflicts."""
    seen = base.setdefault("seen", {})
    for rel in now:
        try:
            seen[rel] = (orig_root / rel).stat().st_mtime_ns
        except OSError:
            seen.pop(rel, None)


def put_back(config, conversation: str, folder: dict, base: dict, items: list[dict]) -> list[str]:
    """Discard through the view: your version written again (or the sandbox's new file removed) - in the running
    sandbox, or in a short-lived container on the view volume when it is not running."""
    import shlex
    import subprocess

    from .manager import manager_for
    from .staging import StagingError
    orig_root = Path(base.get("path") or folder["path"])
    box = manager_for(config).name_of(conversation)
    rc, st, _ = _run("inspect", "-f", "{{.State.Running}}", box)
    if rc == 0 and st.strip() == "true":
        prefix, root = ["docker", "exec", "-i", box], f"/host/{folder['name']}"
    else:
        prefix, root = ["docker", "run", "--rm", "-i", "--user", "1000:1000", "--network", "none", "-v",
                        f"{base['view']}:/w", EGRESS_IMAGE], "/w"
    done = []
    for c in items:
        dst = f"{root}/{c['path']}"
        if c["kind"] == "added":
            cmd, data = f"rm -f -- {shlex.quote(dst)}", b""
        else:
            src = orig_root / c["path"]
            data = src.read_bytes() if src.is_file() else b""
            cmd = f"mkdir -p -- {shlex.quote(os.path.dirname(dst))} && cat > {shlex.quote(dst)}"
        r = subprocess.run(prefix + ["sh", "-c", cmd], input=data, capture_output=True, timeout=600)
        if r.returncode != 0:
            raise StagingError(f"無法換回 {c['path']}：{r.stderr.decode(errors='replace')[:200]}")
        done.append(c["path"])
    return done
