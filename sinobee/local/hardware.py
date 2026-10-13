"""Hardware analysis for running local models.

Reports CPU, RAM, GPU (NVIDIA / AMD / Apple Silicon unified memory) and free disk, then turns that into a memory
"budget" used to judge which models fit:

  fast_gb   memory the model can live in at full speed (GPU VRAM, or ~75% of Apple unified memory)
  total_gb  memory it can use at all (fast + spill-over to system RAM on CPU)

When SinoBee runs in Docker but the model runtime runs on the host (e.g. native Ollama on a Mac for Metal), the
container cannot see the host's hardware; scripts/docker-run.sh passes it in SINOBEE_HOST_HARDWARE
("os=darwin;arch=arm64;cpu=Apple M3 Pro;cores=12;mem_gb=36;gpu=Apple M3 Pro;vram_gb=0;unified=1").
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

_CACHE: dict = {}


def _run(cmd: list[str], timeout: float = 4) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _gb(n_bytes: float) -> float:
    return round(n_bytes / 1024 ** 3, 1)


def in_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text() or "containerd" in Path("/proc/1/cgroup").read_text()
    except OSError:
        return False


def _cgroup_limit_gb() -> float | None:
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(p).read_text().strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < 1 << 50:
            return _gb(int(v))
    return None


def _cpu_name() -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.processor()
    if sysname == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith(("model name", "hardware", "cpu model")):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def _memory() -> tuple[float, float]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return _gb(vm.total), _gb(vm.available)
    except Exception:
        pass
    try:
        info = {k.strip(): int(v.split()[0]) * 1024 for k, v in
                (l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)}
        return _gb(info["MemTotal"]), _gb(info.get("MemAvailable", info.get("MemFree", 0)))
    except Exception:
        pass
    if platform.system() == "Darwin":
        total = int(_run(["sysctl", "-n", "hw.memsize"]) or 0)
        return _gb(total), _gb(total * 0.5)
    return 0.0, 0.0


def _gpus() -> list[dict]:
    gpus: list[dict] = []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"])
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].replace(".", "").isdigit():
            gpus.append({"vendor": "nvidia", "name": parts[0], "vram_gb": round(float(parts[1]) / 1024, 1),
                         "vram_free_gb": round(float(parts[2]) / 1024, 1), "accel": "cuda"})
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--showproductname", "--json"])
    if out.strip().startswith("{"):
        try:
            for card, d in json.loads(out).items():
                total = next((int(v) for k, v in d.items() if "Total Memory" in k and "VRAM" in k), 0)
                used = next((int(v) for k, v in d.items() if "Used Memory" in k and "VRAM" in k), 0)
                gpus.append({"vendor": "amd", "name": d.get("Card series") or d.get("Card SKU") or card,
                             "vram_gb": _gb(total), "vram_free_gb": _gb(total - used), "accel": "rocm"})
        except (ValueError, AttributeError):
            pass
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        total, _ = _memory()
        gpus.append({"vendor": "apple", "name": _cpu_name(), "vram_gb": total, "vram_free_gb": None,
                     "accel": "metal", "unified": True})
    return gpus


def _disk_free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return _gb(shutil.disk_usage(p).free)
    except OSError:
        return 0.0


def parse_host_hardware(raw: str | None) -> dict | None:
    """SINOBEE_HOST_HARDWARE -> hardware dict (hardware of the Docker host, collected by scripts/docker-run.sh)."""
    if not raw:
        return None
    kv = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
    def num(k):
        try:
            return float(kv.get(k, "") or 0)
        except ValueError:
            return 0.0
    gpus = []
    if kv.get("gpu"):
        unified = kv.get("unified") in ("1", "true")
        gpus.append({"vendor": "apple" if unified else kv.get("gpu_vendor", "nvidia"), "name": kv["gpu"],
                     "vram_gb": num("mem_gb") if unified else num("vram_gb"), "vram_free_gb": None,
                     "accel": "metal" if unified else ("cuda" if kv.get("gpu_vendor", "nvidia") == "nvidia" else "rocm"),
                     "unified": unified})
    return {"source": "host", "os": kv.get("os", ""), "arch": kv.get("arch", ""), "cpu": kv.get("cpu", ""),
            "cores": int(num("cores")), "ram_gb": num("mem_gb"), "ram_available_gb": None, "gpus": gpus,
            "disk_free_gb": num("disk_gb") or None, "in_docker": False}


def detect(models_dir: Path | None = None, refresh: bool = False) -> dict:
    """Hardware of the machine SinoBee runs on (cached for 30s)."""
    if not refresh and _CACHE.get("t", 0) > time.time() - 30:
        hw = dict(_CACHE["hw"])
    else:
        total, avail = _memory()
        limit = _cgroup_limit_gb()
        hw = {"source": "local", "os": platform.system().lower(), "arch": platform.machine(), "cpu": _cpu_name(),
              "cores": os.cpu_count() or 1, "ram_gb": min(total, limit) if limit else total,
              "ram_available_gb": min(avail, limit) if limit else avail, "cgroup_limit_gb": limit,
              "gpus": _gpus(), "in_docker": in_docker()}
        _CACHE.update(t=time.time(), hw=hw)
    hw["disk_free_gb"] = _disk_free_gb(models_dir or Path.home())
    return hw


def budget(hw: dict, override_gb: float | None = None) -> dict:
    """Memory budget for model weights + KV cache."""
    gpus = hw.get("gpus") or []
    ram = float(hw.get("ram_gb") or 0)
    unified = next((g for g in gpus if g.get("unified")), None)
    discrete = [g for g in gpus if not g.get("unified")]
    if override_gb:
        fast = float(override_gb) if (unified or discrete) else 0.0
        return {"accel": (unified or (discrete[0] if discrete else {})).get("accel", "cpu"),
                "fast_gb": round(fast, 1), "total_gb": round(float(override_gb), 1),
                "label": f"手動設定 {override_gb:g} GB", "override": True}
    if unified:
        # macOS lets the GPU wire roughly 2/3 (<=36GB) to 3/4 of unified memory
        fast = ram * (0.75 if ram > 36 else 0.66)
        return {"accel": "metal", "fast_gb": round(fast, 1), "total_gb": round(fast, 1),
                "label": f"Apple Silicon 統一記憶體 {ram:g} GB（GPU 可用約 {fast:.0f} GB）"}
    if discrete:
        vram = sum(float(g.get("vram_gb") or 0) for g in discrete)
        accel = discrete[0]["accel"]
        return {"accel": accel, "fast_gb": round(vram * 0.92, 1), "total_gb": round(vram * 0.92 + ram * 0.6, 1),
                "label": f"{discrete[0]['name']}{' ×%d' % len(discrete) if len(discrete) > 1 else ''} "
                         f"VRAM {vram:g} GB + 系統記憶體 {ram:g} GB"}
    usable = ram * 0.7
    return {"accel": "cpu", "fast_gb": 0.0, "total_gb": round(usable, 1),
            "label": f"僅 CPU（{hw.get('cores')} 核心），系統記憶體 {ram:g} GB"}


def auto_num_ctx(b: dict) -> int:
    """Context window to request from the runtime: large enough for SinoBee's prompt + tools, small enough to fit."""
    room = b["fast_gb"] or b["total_gb"] * 0.5   # CPU-only: long prompts are slow, keep the window modest
    if room >= 40:
        return 65536
    if room >= 20:
        return 32768
    return 16384  # SinoBee's system prompt + tool schemas alone take ~5k tokens


def summary_zh(hw: dict) -> str:
    gpu = ", ".join(f"{g['name']}" + (f" {g['vram_gb']:g}GB" if not g.get("unified") else "") for g in hw.get("gpus") or [])
    return (f"{hw.get('cpu') or hw.get('arch')} · {hw.get('cores')} 核心 · 記憶體 {hw.get('ram_gb'):g} GB"
            + (f" · GPU {gpu}" if gpu else " · 無 GPU 加速"))


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)
