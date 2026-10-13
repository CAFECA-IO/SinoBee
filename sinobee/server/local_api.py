"""Local model endpoints: hardware analysis, runtime status, download / load / unload / delete, select."""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException

from .. import local
from ..local import LocalRuntime, OllamaError, full_tag

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}(:[A-Za-z0-9._-]{1,80})?$")


def _name(body: dict) -> str:
    name = str((body or {}).get("name") or "").strip()
    if not NAME_RE.match(name):
        raise HTTPException(400, "模型名稱格式不正確（例如 qwen3:8b）")
    return name


def register(app: FastAPI, rt_getter, emit) -> LocalRuntime:
    lr = LocalRuntime(None, emit)  # config is attached per request (it is rebuilt on reload)

    def runtime() -> LocalRuntime:
        lr.config = rt_getter().config
        return lr

    async def client():
        cli, ok = runtime().client()
        if not ok and not await cli.version():
            raise HTTPException(409, "本地模型服務沒有在執行")
        return cli

    @app.get("/api/local/status")
    async def status():
        return await local.status(rt_getter(), runtime())

    @app.post("/api/local/runtime/start")
    async def start():
        try:
            return await runtime().start_server()
        except OllamaError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/local/runtime/stop")
    async def stop():
        await runtime().stop_server()
        return {"ok": True}

    @app.post("/api/local/pull")
    async def pull(body: dict):
        name = _name(body)
        await client()
        job = runtime().start_pull(name)
        return {k: v for k, v in job.items() if k != "task"}

    @app.delete("/api/local/pull/{name:path}")
    async def cancel_pull(name: str):
        return {"cancelled": runtime().cancel_pull(name)}

    @app.post("/api/local/load")
    async def load(body: dict):
        name = _name(body)
        await client()
        cfg = rt_getter().config
        emb = bool(body.get("embedding"))
        runtime().start_load(name, local.num_ctx(cfg), embedding=emb)
        return {"ok": True}

    @app.post("/api/local/unload")
    async def unload(body: dict):
        name = _name(body)
        await client()
        try:
            await runtime().unload(name)
        except OllamaError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    @app.post("/api/local/delete")
    async def delete(body: dict):
        name = _name(body)
        cli = await client()
        rt = rt_getter()
        for role in ("default", "embedding", "auxiliary"):
            spec = rt.config.get(f"model.{role}") or ""
            if spec.startswith("ollama/") and full_tag(spec[7:]) == full_tag(name):
                raise HTTPException(400, f"這個模型正被設為{ {'default': '主要模型', 'embedding': 'Embedding 模型', 'auxiliary': '輔助模型'}[role]}，請先改選其他模型")
        try:
            await cli.delete(name)
        except OllamaError as e:
            raise HTTPException(400, str(e)) from None
        runtime().jobs.pop(name, None)
        emit("local_changed", {"reason": "deleted", "model": name})
        return {"ok": True}

    @app.post("/api/local/use")
    async def use(body: dict):
        """Select a local model as the main (or embedding / auxiliary) model; no API key needed."""
        name = _name(body)
        role = body.get("role", "default")
        if role not in ("default", "embedding", "auxiliary"):
            raise HTTPException(400, "role must be default | embedding | auxiliary")
        rt = rt_getter()
        key = f"model.{role}"
        if key in rt.config.locked:
            raise HTTPException(409, f"{key} 由啟動參數指定，無法在介面修改")
        ignored = rt.config.save_gui_settings({"model": {role: f"ollama/{name}"}})
        rt.reload()
        cli, ok = runtime().client()
        if ok:
            runtime().start_load(name, local.num_ctx(rt.config), embedding=(role == "embedding"))
        emit("local_changed", {"reason": "selected", "model": name, "role": role})
        return {"ok": True, "model": f"ollama/{name}", "ignored_locked": ignored}

    @app.put("/api/local/settings")
    async def settings(body: dict):
        rt = rt_getter()
        upd: dict = {}
        if "base_url" in body:
            upd["base_url"] = (body["base_url"] or "").strip() or None
        if "num_ctx" in body:
            v = str(body["num_ctx"] or "auto").strip()
            if v != "auto" and not (v.isdigit() and 2048 <= int(v) <= 1_048_576):
                raise HTTPException(400, "上下文長度需為 auto 或 2048 以上的整數")
            upd["num_ctx"] = v if v == "auto" else int(v)
        if "keep_alive" in body:
            v = str(body["keep_alive"] or "30m").strip()
            if not re.match(r"^-?\d+[smh]?$", v):
                raise HTTPException(400, "保留時間格式例如 30m、2h、-1（永久）")
            upd["keep_alive"] = v
        if "memory_gb" in body:
            v = body["memory_gb"]
            upd["memory_gb"] = float(v) if v not in (None, "") else None
        rt.config.save_gui_settings({"local": upd})
        from ..local.ollama import forget_url
        forget_url()
        rt.reload()
        emit("local_changed", {"reason": "settings"})
        return await local.status(rt, runtime())

    return lr
