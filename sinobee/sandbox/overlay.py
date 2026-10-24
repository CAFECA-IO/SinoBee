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
