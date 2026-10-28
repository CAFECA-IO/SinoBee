"""Local model hosting: hardware analysis, curated catalog and an Ollama runtime manager.

Selecting a local model sets model.default to "ollama/<tag>"; no API key is needed and usage is priced at $0.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import re

from .catalog import CATALOG, CATALOG_CHECKED, fit, need_gb, recommend
from .hardware import auto_num_ctx, budget, detect, parse_host_hardware, summary_zh
from .ollama import LocalRuntime, Ollama, OllamaError, location, resolve_url

_SHOW_CACHE: dict[str, dict] = {}

# The larger window a task asked for (larger_ctx): its prompt, or an answer with its hidden thinking, did not fit.
# One value per model for the whole process, only ever growing: tasks that run at the same time on the same model
# send the same num_ctx - a different num_ctx makes Ollama load the model again, so two tasks taking turns with 16k
# and 32k reloaded it on every step. GROWN_CTX (per task) is kept for callers that set a window for one task only.
GROWN_CTX: contextvars.ContextVar = contextvars.ContextVar("grown_num_ctx", default=None)
_GROWN: dict[str, int] = {}


def _model_key(model: str | None) -> str:
    return full_tag(str(model).split("/", 1)[-1]) if model else ""


def grown(model: str | None) -> int:
    """The window enlarged for this model (0: none), or for this task (GROWN_CTX)."""
    return max(_GROWN.get(_model_key(model), 0) if model else 0, GROWN_CTX.get() or 0)


def set_grown(model: str, n: int) -> int:
    """Enlarge the shared window of this model (never shrinks it); returns the window now in use."""
    k = _model_key(model)
    _GROWN[k] = max(_GROWN.get(k, 0), int(n))
    return _GROWN[k]


def reset_grown() -> None:
    _GROWN.clear()
CTX_STEPS = (16384, 32768, 65536, 131072, 262144)


def full_tag(name: str) -> str:
    return name if ":" in name else name + ":latest"


def runtime_hardware(config, url: str | None = None, models_dir=None) -> dict:
    """Hardware of the machine the model runtime runs on (the Docker host when Ollama runs natively there)."""
    url = url or resolve_url(config)[0]
    host = parse_host_hardware(os.environ.get("SINOBEE_HOST_HARDWARE"))
    loc = location(url)
    # native Ollama on the Docker host, or (Linux) an Ollama container that shares the host's RAM/GPU
    if host and (loc == "host" or (loc == "sidecar" and host.get("os") == "linux")):
        if loc == "sidecar":
            host = {**host, "disk_free_gb": None}
        return host
    hw = detect(models_dir)
    if location(url) != "local":
        hw = {**hw, "disk_free_gb": None}   # models are stored where the runtime runs
    if location(url) == "remote":
        hw = {**hw, "source": "unknown-remote"}
    return hw


def model_budget(config, hw: dict | None = None) -> dict:
    hw = hw or runtime_hardware(config)
    return budget(hw, config.get("local.memory_gb"))


def num_ctx(config, b: dict | None = None, model: str | None = None) -> int:
    v = config.get("local.num_ctx", "auto")
    try:
        if v not in (None, "", "auto"):
            return max(2048, int(v), grown(model))
    except (TypeError, ValueError):
        pass
    return max(auto_num_ctx(b or model_budget(config)), grown(model))


def _params_b(tag: str, params: str | None) -> float:
    """Billions of parameters from Ollama's report ("8.0B") or the tag ("gemma4:e4b" -> 4); 8 when neither says."""
    for text in (params or "", tag.split(":")[-1]):
        m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return 8.0


async def larger_ctx(config, tag: str, current: int, need: int) -> int | None:
    """A larger window for a task whose answer (with its hidden thinking) does not fit beside the prompt: the smallest
    step in CTX_STEPS that holds `need` tokens - or the largest that still fits, when none does - within the model's
    own context length and the memory the runtime has (weights + KV cache, catalog.need_gb). None: no larger one fits.
    Ollama reloads the model when the window changes (tens of seconds)."""
    tag = full_tag(tag.split("/", 1)[-1])
    url = (await asyncio.to_thread(resolve_url, config))[0]
    try:
        show = await _caps(Ollama(url), {"name": tag})
    except Exception:  # noqa: BLE001
        show = {}
    b = await asyncio.to_thread(model_budget, config)
    entry = next((m for m in CATALOG if full_tag(m["id"]) == tag), None)
    params = entry["params_b"] if entry else _params_b(tag, show.get("params"))
    size_gb = entry["size_gb"] if entry else round(params * 0.65, 1)
    try:                                   # the downloaded size, when Ollama reports it, beats the estimates
        size = next((m.get("size") for m in await Ollama(url).tags() if full_tag(m.get("name", "")) == tag), None)
        if size:
            size_gb = size / 1e9
    except Exception:  # noqa: BLE001
        pass
    model = {"size_gb": size_gb, "params_b": params}
    model_max = int(show.get("ctx") or (entry or {}).get("ctx_k", 0) * 1024 or 131072)
    memory = b.get("fast_gb") or b.get("total_gb") or 0
    best = None
    for n in CTX_STEPS:
        if n <= current or n > model_max:
            continue
        if need_gb(model, n) > memory:
            break
        best = n
        if n >= need:
            break
    return best


def is_local_spec(spec: str | None) -> bool:
    return bool(spec) and str(spec).lower().startswith("ollama/")


async def _caps(cli: Ollama, m: dict) -> dict:
    key = m.get("digest") or m["name"]
    if key not in _SHOW_CACHE:
        try:
            d = await cli.show(m["name"])
            info = d.get("model_info") or {}
            ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
            _SHOW_CACHE[key] = {"caps": d.get("capabilities") or [], "ctx": ctx,
                                "params": (d.get("details") or {}).get("parameter_size"),
                                "quant": (d.get("details") or {}).get("quantization_level")}
        except Exception:  # noqa: BLE001
            _SHOW_CACHE[key] = {"caps": [], "ctx": None}
    return _SHOW_CACHE[key]


INSTALL_HINTS = {
    "docker": [
        "建議：在 Docker 外的主機直接安裝 Ollama（Mac 可用 Metal GPU 加速），SinoBee 會自動透過 host.docker.internal 連線。",
        "或用內建的 Ollama 容器：docker compose --profile local-llm up -d（NVIDIA GPU 請見 docker-compose.yml 註解）。",
        "或啟動時加參數：scripts/docker-run.sh --local-llm",
    ],
    "darwin": ["到 https://ollama.com/download 下載 Mac 版並開啟，或 brew install ollama。"],
    "linux": ["curl -fsSL https://ollama.com/install.sh | sh", "安裝後回到這裡按「啟動本地服務」。"],
    "windows": ["到 https://ollama.com/download 下載 Windows 版並執行。"],
}


async def status(rt, lr: LocalRuntime) -> dict:
    cfg = rt.config
    url, ok = await asyncio.to_thread(resolve_url, cfg, True)
    cli = Ollama(url)
    version = await cli.version() if ok else None
    ok = bool(version)
    installed: dict[str, dict] = {}
    running: dict[str, dict] = {}
    error = None
    if ok:
        try:
            tags, ps = await asyncio.gather(cli.tags(), cli.ps())
            for m in tags:
                m["_caps"] = await _caps(cli, m)
                installed[full_tag(m["name"])] = m
            running = {full_tag(m["name"]): m for m in ps}
        except Exception as e:  # noqa: BLE001
            error = str(e)[:300]
    hw = await asyncio.to_thread(runtime_hardware, cfg, url, lr.models_dir)
    b = budget(hw, cfg.get("local.memory_gb"))
    nctx = num_ctx(cfg, b)
    pulls = lr.pulls()
    default, embedding = cfg.get("model.default") or "", cfg.get("model.embedding") or ""

    def state_of(tag: str) -> dict:
        ft = full_tag(tag)
        job = pulls.get(tag) or pulls.get(ft)
        load = lr.loading.get(tag) or lr.loading.get(ft)
        if job and job["state"] == "pulling":
            return {"state": "pulling", "progress": job}
        if load == "loading":
            return {"state": "loading"}
        if ft in running:
            r = running[ft]
            size, vram = r.get("size") or 0, r.get("size_vram") or 0
            gpu_pct = round(vram * 100 / size) if size else 0
            return {"state": "running", "gpu_pct": gpu_pct, "mem_gb": round(size / 1024 ** 3, 1),
                    "expires_at": r.get("expires_at"), "ctx": r.get("context_length")}
        if ft in installed:
            return {"state": "downloaded", "error": load if load and load != "loading" else None}
        if job and job["state"] == "error":
            return {"state": "error", "error": job["error"]}
        return {"state": "absent"}

    rows = []
    for m in CATALOG:
        ft = full_tag(m["id"])
        inst = installed.get(ft)
        size = round(inst["size"] / 1024 ** 3, 1) if inst else m["size_gb"]
        caps = (inst["_caps"]["caps"] if inst and inst["_caps"]["caps"] else m["caps"])
        row = {**m, "size_gb": size, "caps": caps, "fit": fit({**m, "size_gb": size}, b, nctx, hw.get("disk_free_gb")),
               **state_of(m["id"])}
        row["selected"] = [r for r, spec in (("default", default), ("embedding", embedding))
                           if spec and full_tag(spec.split("/", 1)[-1]) == ft and spec.startswith("ollama/")]
        rows.append(row)
    known = {full_tag(m["id"]) for m in CATALOG}
    extras = []
    for ft, inst in installed.items():
        if ft in known:
            continue
        caps = inst["_caps"]["caps"]
        size = round(inst["size"] / 1024 ** 3, 1)
        kind = "embedding" if "embedding" in caps else "chat"
        m = {"id": inst["name"], "title": inst["name"], "params_b": _params(inst["_caps"].get("params")),
             "size_gb": size, "caps": caps, "kind": kind, "custom": True,
             "note": " · ".join(x for x in (inst["_caps"].get("params"), inst["_caps"].get("quant")) if x)}
        m["fit"] = fit(m, b, nctx)
        m.update(state_of(inst["name"]))
        m["selected"] = [r for r, spec in (("default", default), ("embedding", embedding))
                         if spec.startswith("ollama/") and full_tag(spec.split("/", 1)[1]) == ft]
        extras.append(m)
    for name, job in pulls.items():  # custom pulls in progress
        if full_tag(name) not in known and full_tag(name) not in installed and job["state"] in ("pulling", "error"):
            extras.append({"id": name, "title": name, "size_gb": round((job.get("total") or 0) / 1024 ** 3, 1),
                           "caps": [], "kind": "chat", "custom": True, "note": "",
                           "fit": {"level": "ok", "label": "", "need_gb": 0},
                           **({"state": "pulling", "progress": job} if job["state"] == "pulling"
                              else {"state": "error", "error": job["error"]})})
    plat = "docker" if hw.get("in_docker") and location(url) in ("local", "sidecar") else (hw.get("os") or "linux")
    return {
        "runtime": {"kind": "ollama", "url": url, "reachable": ok, "version": version, "location": location(url),
                    "managed": lr.managed(), "binary": lr.binary, "error": error,
                    "can_start": bool(lr.binary) and not ok,
                    "install_hints": [] if ok or lr.binary else INSTALL_HINTS.get(
                        "docker" if hw.get("in_docker") else plat, INSTALL_HINTS["linux"])},
        "hardware": {**hw, "summary": summary_zh(hw)}, "budget": b, "num_ctx": nctx,
        "settings": {k: cfg.get(f"local.{k}") for k in ("base_url", "num_ctx", "keep_alive", "memory_gb")},
        "models": rows, "extras": extras, "recommended": recommend(rows),
        "selected": {"default": default, "embedding": embedding}, "catalog_checked": CATALOG_CHECKED,
    }


def _params(s) -> float:
    try:
        s = str(s or "").upper().rstrip("B")
        return float(s[:-1]) / 1000 if s.endswith("M") else float(s)
    except ValueError:
        return 7.0


__all__ = ["LocalRuntime", "Ollama", "OllamaError", "status", "num_ctx", "is_local_spec", "model_budget",
           "runtime_hardware", "resolve_url", "full_tag", "CATALOG"]
