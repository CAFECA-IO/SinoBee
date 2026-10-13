"""Curated local models (Ollama tags) and hardware-fit scoring.

Only models whose Ollama build supports tool calling are offered for the agent (SinoBee drives the computer through
tools). Sizes are the default (≈Q4) download sizes from ollama.com, checked CATALOG_CHECKED; once a model is
downloaded, the runtime's own report (size, capabilities, context length) replaces these numbers.
Any other Ollama tag can still be pulled by name from the UI.
"""
from __future__ import annotations

CATALOG_CHECKED = "2026-10-13"

# quality: rough agentic/tool-use ability on a 1-10 scale, used only to rank recommendations
CATALOG: list[dict] = [
    # ---- tiny / laptop ----
    {"id": "qwen3.5:0.8b", "title": "Qwen 3.5 0.8B", "params_b": 0.8, "size_gb": 1.0, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 2, "note": "極輕量，適合測試或很舊的電腦；複雜任務能力有限"},
    {"id": "granite4.1:3b", "title": "IBM Granite 4.1 3B", "params_b": 3, "size_gb": 2.1, "ctx_k": 128,
     "caps": ["tools"], "quality": 3, "note": "專為工具呼叫訓練，小而穩"},
    {"id": "qwen3.5:4b", "title": "Qwen 3.5 4B", "params_b": 4, "size_gb": 3.4, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 4, "note": "8GB 記憶體電腦的首選，可看截圖"},
    {"id": "qwen3:8b", "title": "Qwen 3 8B", "params_b": 8, "size_gb": 5.2, "ctx_k": 40,
     "caps": ["tools", "thinking"], "quality": 5, "note": "工具呼叫穩定、中文好的通用選擇"},
    {"id": "qwen3.5:9b", "title": "Qwen 3.5 9B", "params_b": 9, "size_gb": 6.6, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 6, "note": "16GB 電腦最佳平衡：會看圖、會用工具"},
    {"id": "gemma4:12b", "title": "Gemma 4 12B", "params_b": 12, "size_gb": 7.6, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 6, "note": "Google 開放模型，多語與視覺能力佳"},
    # ---- workstation ----
    {"id": "gpt-oss:20b", "title": "gpt-oss 20B", "params_b": 20, "size_gb": 14, "ctx_k": 128,
     "caps": ["tools", "thinking"], "quality": 7, "note": "OpenAI 開放權重 MoE，推理與代理任務強，16GB 可跑"},
    {"id": "mistral-small3.2:24b", "title": "Mistral Small 3.2 24B", "params_b": 24, "size_gb": 15, "ctx_k": 128,
     "caps": ["tools", "vision"], "quality": 7, "note": "回應快、函式呼叫可靠，支援圖片"},
    {"id": "qwen3.8:27b", "title": "Qwen 3.8 27B", "params_b": 27, "size_gb": 18, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 8, "note": "24GB 顯卡／32GB Mac 的最強通用代理模型"},
    {"id": "qwen3-coder:30b", "title": "Qwen 3 Coder 30B-A3B", "params_b": 30, "size_gb": 19, "ctx_k": 256,
     "caps": ["tools"], "quality": 7, "note": "程式與終端操作專長，MoE 速度快"},
    {"id": "qwen3.6:35b", "title": "Qwen 3.6 35B", "params_b": 35, "size_gb": 24, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 8, "note": "32GB 以上記憶體，長任務更穩"},
    # ---- server ----
    {"id": "llama3.3:70b", "title": "Llama 3.3 70B", "params_b": 70, "size_gb": 43, "ctx_k": 128,
     "caps": ["tools"], "quality": 8, "note": "需要 48GB 以上"},
    {"id": "gpt-oss:120b", "title": "gpt-oss 120B", "params_b": 120, "size_gb": 65, "ctx_k": 128,
     "caps": ["tools", "thinking"], "quality": 9, "note": "需要 80GB 等級 GPU 或 96GB 以上 Mac"},
    {"id": "qwen3.5:122b", "title": "Qwen 3.5 122B", "params_b": 122, "size_gb": 81, "ctx_k": 256,
     "caps": ["tools", "vision", "thinking"], "quality": 9, "note": "工作站／伺服器等級"},
    # ---- embeddings (long-term memory search) ----
    {"id": "nomic-embed-text", "title": "Nomic Embed Text", "params_b": 0.14, "size_gb": 0.27, "ctx_k": 8,
     "caps": ["embedding"], "kind": "embedding", "quality": 5, "note": "長期記憶語意搜尋用，幾乎任何電腦都能跑"},
    {"id": "qwen3-embedding:0.6b", "title": "Qwen 3 Embedding 0.6B", "params_b": 0.6, "size_gb": 0.64, "ctx_k": 32,
     "caps": ["embedding"], "kind": "embedding", "quality": 6, "note": "中文語意搜尋較佳"},
]

for _m in CATALOG:
    _m.setdefault("kind", "chat")


def kv_cache_gb(params_b: float, num_ctx: int) -> float:
    """Rough KV-cache size at q8/f16 for typical GQA models (grows with context)."""
    return round(min(8.0, 0.06 * (params_b ** 0.5) * num_ctx / 4096), 2)


def need_gb(m: dict, num_ctx: int) -> float:
    if m.get("kind") == "embedding":
        return round(m["size_gb"] * 1.2 + 0.2, 1)
    return round(m["size_gb"] * 1.1 + kv_cache_gb(m.get("params_b", 7), num_ctx) + 0.6, 1)


def fit(m: dict, b: dict, num_ctx: int, disk_free_gb: float | None = None) -> dict:
    """-> {level, label, need_gb}; level: great | ok | slow | no"""
    need = need_gb(m, num_ctx)
    if disk_free_gb is not None and disk_free_gb and m["size_gb"] > disk_free_gb - 1:
        return {"level": "no", "label": "磁碟空間不足", "need_gb": need}
    if b["fast_gb"] and need <= b["fast_gb"]:
        return {"level": "great", "label": "GPU 加速，順暢" if b["accel"] != "metal" else "Metal 加速，順暢",
                "need_gb": need}
    if need <= b["total_gb"]:
        if b["fast_gb"]:
            return {"level": "slow", "label": "部分在 CPU 執行，較慢", "need_gb": need}
        if m.get("kind") == "embedding" or m.get("params_b", 0) <= 4:
            return {"level": "ok", "label": "CPU 可順利執行", "need_gb": need}
        return {"level": "slow" if m.get("params_b", 0) > 9 else "ok",
                "label": "CPU 執行，速度偏慢" if m.get("params_b", 0) > 9 else "CPU 可執行", "need_gb": need}
    return {"level": "no", "label": f"記憶體不足（約需 {need:g} GB）", "need_gb": need}


def recommend(rows: list[dict]) -> dict[str, str | None]:
    """Best chat + embedding model id among fitting rows (rows carry 'fit')."""
    def best(kind: str):
        order = {"great": 0, "ok": 1, "slow": 2}
        cands = [r for r in rows if r.get("kind") == kind and r["fit"]["level"] in order]
        if not cands:
            return None
        top = min(order[r["fit"]["level"]] for r in cands)
        cands = [r for r in cands if order[r["fit"]["level"]] == top]
        if kind == "chat" and top > 0:  # CPU-only: prefer the smallest model that is still useful
            cands = [r for r in cands if r.get("params_b", 0) <= 9] or cands
        return max(cands, key=lambda r: (r.get("quality", 0), "vision" in r.get("caps", []), -r["size_gb"]))["id"]
    return {"chat": best("chat"), "embedding": best("embedding")}
