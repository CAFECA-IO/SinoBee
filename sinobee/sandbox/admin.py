"""Sandbox health, images and housekeeping for the settings page and the hint above the input box (stage 1).

- `health`  what stops sandboxes from running and what can be done about it, as one problem with actions:
  docker_missing (install Docker Desktop), docker_down (start it), image_missing / image_outdated (build the image
  - SinoBee was updated), building (progress), ok.
- `build`   builds a sandbox image in the background (standard, or office with LibreOffice), with its output;
  the same `docker build` as `sinobee sandbox build [--office]`.
- `usage`   per conversation: its container (running / stopped), home volume (installed packages and caches), review
  copies, checkpoints and backups, and own workspace; with totals. Files cloned on APFS share their space, so the
  copies' size is an upper bound.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .manager import ROLE, docker, docker_sync, image_name, manager_for, safe

DOCKER_URL = "https://www.docker.com/products/docker-desktop/"
BUILDS: dict[str, dict] = {}           # "standard" | "office" -> {"state", "lines", "started", "finished", "rc"}
_UNITS = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1 << 20, "gib": 1 << 30}


def _bytes(s: str) -> int:
    m = re.match(r"\s*([\d.]+)\s*([kKmMgGtT]?i?[bB])", str(s or ""))
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS.get(m.group(2).lower(), 1))


def _dockerfile() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "docker" / "sandbox.Dockerfile"


def _image_kind_name(config, kind: str) -> str:
    from .. import __version__
    return f"sinobee-sandbox{'-office' if kind == 'office' else ''}:{__version__}"


def _get(data: dict, key: str, default=None):
    cur = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def images(config) -> dict:
    """{"standard": {"name", "built"}, "office": {...}}."""
    out = {}
    for kind in ("standard", "office"):
        name = _image_kind_name(config, kind)
        rc, _, _ = docker_sync("image", "inspect", name, timeout=8)
        out[kind] = {"name": name, "built": rc == 0, "building": BUILDS.get(kind, {}).get("state") == "running"}
    return out


def health(config) -> dict:
    """{"ok", "problem", "title", "message", "actions": [{"id", "label", "url"?}], ...}."""
    from . import wanted_kind
    m = manager_for(config)
    base = {"wanted": wanted_kind(config) == "sandbox"}
    if not shutil.which("docker"):
        return {**base, "ok": False, "problem": "docker_missing", "title": "沒有安裝 Docker",
                "message": "沙盒需要 Docker Desktop。安裝並開啟後，SinoBee 會自動使用它。",
                "actions": [{"id": "install", "label": "下載 Docker Desktop", "url": DOCKER_URL},
                            {"id": "retry", "label": "重新檢查"}]}
    rc, _, err = docker_sync("info", "--format", "{{.ServerVersion}}", timeout=8)
    if rc != 0:
        acts = [{"id": "start_docker", "label": "開啟 Docker"}] if platform.system() == "Darwin" else []
        return {**base, "ok": False, "problem": "docker_down", "title": "Docker 沒有在執行",
                "message": "開啟 Docker Desktop 後，沙盒就能使用（約需 10–30 秒）。" + (f"（{err.strip()[:120]}）" if err.strip() else ""),
                "actions": acts + [{"id": "retry", "label": "重新檢查"}]}
    kind = "office" if _get(config.data, "sandbox.image") == "office" else "standard"
    if BUILDS.get(kind, {}).get("state") == "running":
        job = BUILDS[kind]
        return {**base, "ok": False, "problem": "building", "title": "正在建立沙盒映像",
                "message": (job["lines"][-1] if job["lines"] else "準備中…")[:200], "actions": [],
                "started": job["started"]}
    name = image_name(config)
    rc, _, _ = docker_sync("image", "inspect", name, timeout=8)
    if rc != 0:
        rc2, out, _ = docker_sync("images", "--format", "{{.Repository}}:{{.Tag}}", name.split(":")[0], timeout=8)
        older = [x for x in out.split() if x and x != name]
        failed = BUILDS.get(kind, {}).get("state") == "failed"
        return {**base, "ok": False, "problem": "image_outdated" if older else "image_missing",
                "title": "SinoBee 更新了，需要重建沙盒映像" if older else "沙盒映像還沒建立",
                "message": ("上次建立失敗：" + " ".join(BUILDS[kind]["lines"][-2:])[:240] if failed else
                            "第一次建立會下載約 2 GB，需要幾分鐘；之後的任務都會用它。"),
                "actions": [{"id": "build", "label": "重建映像" if older else "建立映像", "kind": kind}]}
    m._avail = (0.0, "rechecked")          # all good: let the manager see it at once
    cap = m.capacity()
    return {**base, "ok": True, "problem": None, "title": "沙盒可以使用", "message": "", "actions": [],
            "capacity": cap}


def start_docker() -> dict:
    if platform.system() != "Darwin":
        return {"ok": False, "message": "請自行啟動 Docker（例如 systemctl start docker）"}
    try:
        subprocess.Popen(["open", "-a", "Docker"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return {"ok": False, "message": f"無法開啟 Docker：{e}"}
    return {"ok": True, "message": "正在開啟 Docker Desktop…"}


def build(config, kind: str = "standard") -> dict:
    """Build the sandbox image in the background; returns the job."""
    kind = "office" if kind == "office" else "standard"
    job = BUILDS.get(kind)
    if job and job["state"] == "running":
        return job
    dockerfile = _dockerfile()
    if not dockerfile.exists():
        BUILDS[kind] = {"state": "failed", "lines": [f"找不到 {dockerfile}：請在 SinoBee 原始碼資料夾執行"],
                        "started": time.time(), "finished": time.time(), "rc": 1, "kind": kind}
        return BUILDS[kind]
    tag = _image_kind_name(config, kind)
    cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag] + (["--build-arg", "OFFICE=1"] if kind == "office"
                                                                    else []) + [str(dockerfile.parent.parent)]
    job = {"state": "running", "lines": [], "started": time.time(), "finished": None, "rc": None, "kind": kind,
           "image": tag}
    BUILDS[kind] = job

    def run():
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                 env={**os.environ, "DOCKER_BUILDKIT": "1", "BUILDKIT_PROGRESS": "plain"})
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    job["lines"] = (job["lines"] + [line])[-200:]
            job["rc"] = p.wait()
        except OSError as e:
            job["lines"].append(str(e))
            job["rc"] = 1
        job["state"] = "done" if job["rc"] == 0 else "failed"
        job["finished"] = time.time()
        m = manager_for(config)
        m._avail = (0.0, "rebuilt")
        from .manager import emit
        emit("sandbox_health", {"build": kind, "state": job["state"]})
    threading.Thread(target=run, daemon=True).start()
    return job


def _du(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for root, dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_blocks * 512
            except OSError:
                continue
    return total


async def usage(config, titles: dict | None = None) -> dict:
    """Per conversation: container state and size, home volume, copies/backups/checkpoints, own workspace."""
    import asyncio
    import json
    m = manager_for(config)
    rows: dict[str, dict] = {}

    def row(conv: str) -> dict:
        return rows.setdefault(conv, {"conversation": conv, "title": (titles or {}).get(conv), "state": "none",
                                      "container": 0, "volume": 0, "files": 0, "workspace": 0,
                                      "last_used": m.last_used.get(m.name_of(conv)), "busy": m._busy(conv)})
    rc, out, _ = await docker("system", "df", "-v", "--format", "{{json .}}", timeout=90)
    try:
        df = json.loads(out) if rc == 0 else {}
    except ValueError:
        df = {}
    for c in df.get("Containers") or []:
        labels = dict(x.split("=", 1) for x in str(c.get("Labels") or "").split(",") if "=" in x)
        if labels.get(ROLE) != "sandbox" or not labels.get("org.sinobee.conversation"):
            continue
        r = row(labels["org.sinobee.conversation"])
        r["state"] = "running" if str(c.get("State")) == "running" else "stopped"
        r["container"] = _bytes(str(c.get("Size") or "").split(" ")[0])
    for v in df.get("Volumes") or []:
        name = str(v.get("Name") or "")
        if name.startswith("sinobee-home-"):
            conv = name[len("sinobee-home-"):]
            rows.get(conv) or row(conv)
            rows[conv]["volume"] = _bytes(v.get("Size"))
    sb = Path(config.home) / "sandbox"
    wsroot = Path(config.workspace) / "conversations"

    def host_sizes():
        for d in sorted(sb.iterdir()) if sb.is_dir() else []:
            if d.is_dir():
                row(d.name)["files"] = _du(d)
        for d in sorted(wsroot.iterdir()) if wsroot.is_dir() else []:
            if d.is_dir():
                row(d.name)["workspace"] = _du(d)
    await asyncio.to_thread(host_sizes)
    images_size = sum(_bytes(i.get("Size")) for i in df.get("Images") or []
                      if str(i.get("Repository") or "").startswith("sinobee-sandbox"))
    for r in rows.values():
        r["total"] = r["container"] + r["volume"] + r["files"] + r["workspace"]
    items = sorted(rows.values(), key=lambda r: (r["state"] != "running", -(r["last_used"] or 0), -r["total"]))
    return {"items": items, "images": images_size, "total": sum(r["total"] for r in items) + images_size,
            "running": sum(1 for r in items if r["state"] == "running"), "capacity": m.capacity(),
            "waiting": list(m.waiting)}


async def clear(config, conversation: str) -> dict:
    """Remove a conversation's sandbox: its container and home volume (installed packages, caches). Its files -
    workspace, review copies with changes, checkpoints - stay."""
    m = manager_for(config)
    if m._busy(conversation):
        raise RuntimeError("這個對話的任務正在執行")
    await m.remove(conversation)
    return {"ok": True, "conversation": conversation, "volume": f"sinobee-home-{safe(conversation)}"}
