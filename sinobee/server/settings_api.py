"""Settings (models, API keys, permissions, budgets, pricing) and usage/cost endpoints."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

from ..accounting import scope
from ..pricing import PRICES_CHECKED, table
from ..providers import (PRESETS, build_provider, key_env_for, needs_key, provider_names, resolve_key,
                         split_model)
from ..secrets import mask
from ..types import Message

ALL_TOOLSETS = ["shell", "files", "desktop", "browser", "web", "memory", "skills", "usage", "database"]
PROVIDER_LABELS = {
    "openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Google Gemini", "openrouter": "OpenRouter",
    "deepseek": "DeepSeek", "groq": "Groq", "mistral": "Mistral", "xai": "xAI", "together": "Together",
    "fireworks": "Fireworks", "moonshot": "Moonshot (Kimi)", "qwen": "Qwen (DashScope)", "zhipu": "Zhipu (GLM)",
    "ollama": "Ollama（本機）", "lmstudio": "LM Studio（本機）", "vllm": "vLLM（自架）",
}


def _valid_spec(spec) -> bool:
    try:
        return bool(spec) and all(split_model(spec))
    except ValueError:
        return False


def settings_payload(rt) -> dict:
    cfg = rt.config
    custom = cfg.get("providers") or {}
    providers = []
    for name in provider_names(cfg):
        key, source = resolve_key(name, cfg)
        preset = PRESETS.get(name)
        providers.append({
            "name": name, "label": PROVIDER_LABELS.get(name, name),
            "key_env": key_env_for(name, cfg), "needs_key": needs_key(name, cfg),
            "has_key": bool(key), "key_masked": mask(key), "key_source": source,
            "base_url": (custom.get(name) or {}).get("base_url") or (preset[1] if preset else ""),
            "custom": preset is None,
        })
    default = cfg.get("model.default")
    needs_setup = not default
    if default and _valid_spec(default):
        prov = split_model(default)[0]
        needs_setup = needs_key(prov, cfg) and not resolve_key(prov, cfg)[0]
    return {
        "model": {k: cfg.get(f"model.{k}") for k in ("default", "fallbacks", "auxiliary", "embedding", "vision")},
        "permissions": {"approval": cfg.get("permissions.approval"), "toolsets": cfg.get("permissions.toolsets"),
                        "allowed_paths": cfg.get("permissions.allowed_paths")},
        "usage": cfg.get("usage"),
        "skills": {"auto_create": cfg.get("skills.auto_create"), "project_dir": str(cfg.project_dir),
                   "interop": cfg.get("skills.interop", True), "hub_url": cfg.get("skills.hub_url")},
        "pricing": cfg.get("pricing") or {},
        "providers": providers,
        "locked": sorted(cfg.locked),
        "all_toolsets": ALL_TOOLSETS,
        "needs_setup": needs_setup,
        "provider_error": rt.provider_error,
    }


def register(app: FastAPI, rt_getter) -> None:
    @app.get("/api/settings")
    async def get_settings():
        return settings_payload(rt_getter())

    @app.put("/api/settings")
    async def put_settings(body: dict):
        rt = rt_getter()
        cfg = rt.config
        updates: dict = {}
        errors: list[str] = []
        if "model" in body:
            m = {}
            for k in ("default", "auxiliary", "embedding"):
                if k in body["model"]:
                    v = (body["model"][k] or "").strip() or None
                    if v and not _valid_spec(v):
                        errors.append(f"model.{k}: use provider/model format, e.g. openai/gpt-5.4")
                    m[k] = v
            if "fallbacks" in body["model"]:
                fb = body["model"]["fallbacks"]
                fb = [x.strip() for x in (fb.split(",") if isinstance(fb, str) else fb or []) if x and x.strip()]
                errors += [f"fallback '{x}' must be provider/model" for x in fb if not _valid_spec(x)]
                m["fallbacks"] = fb
            if "vision" in body["model"]:
                m["vision"] = bool(body["model"]["vision"])
            updates["model"] = m
        if "permissions" in body:
            p = body["permissions"]
            perm = {}
            if "approval" in p:
                if p["approval"] not in ("auto", "confirm", "readonly"):
                    errors.append("approval must be auto | confirm | readonly")
                perm["approval"] = p["approval"]
            if "toolsets" in p:
                perm["toolsets"] = [t for t in p["toolsets"] if t in ALL_TOOLSETS]
            if "allowed_paths" in p:
                perm["allowed_paths"] = [x for x in p["allowed_paths"] if x]
            updates["permissions"] = perm
        if "usage" in body:
            u = {}
            for k in ("daily_budget_usd", "monthly_budget_usd", "currency_rate"):
                if k in body["usage"]:
                    v = body["usage"][k]
                    u[k] = float(v) if v not in (None, "") else None
            for k in ("enforce_budget",):
                if k in body["usage"]:
                    u[k] = bool(body["usage"][k])
            if "currency" in body["usage"]:
                u["currency"] = str(body["usage"]["currency"] or "USD")[:8]
            updates["usage"] = u
        if "skills" in body:
            sk = {}
            b = body["skills"]
            if "auto_create" in b:
                if str(b["auto_create"]) not in ("auto", "suggest", "off"):
                    errors.append("skills.auto_create must be auto | suggest | off")
                sk["auto_create"] = str(b["auto_create"])
            if "project_dir" in b:
                sk["project_dir"] = (b["project_dir"] or "").strip() or None
            if "interop" in b:
                sk["interop"] = bool(b["interop"])
            updates["skills"] = sk
        if "pricing" in body:
            clean = {}
            for model, price in (body["pricing"] or {}).items():
                try:
                    clean[model.strip()] = {k: float(price[k]) for k in ("input", "output", "cache_read", "cache_write")
                                            if price.get(k) not in (None, "")}
                except (TypeError, ValueError):
                    errors.append(f"pricing for {model} must be numbers")
            updates["pricing"] = clean
        if "custom_providers" in body:
            prov = {}
            for name, d in (body["custom_providers"] or {}).items():
                name = name.strip().lower()
                if not name or not d.get("base_url"):
                    continue
                prov[name] = {"base_url": d["base_url"].strip()}
                if d.get("transport") in ("openai", "anthropic"):
                    prov[name]["transport"] = d["transport"]
            updates["providers"] = prov
        if errors:
            raise HTTPException(400, "; ".join(errors))
        ignored = cfg.save_gui_settings(updates) if updates else []
        key_ignored = []
        for name, key in (body.get("api_keys") or {}).items():
            if resolve_key(name, cfg)[1] == "env":
                key_ignored.append(name)  # provided at startup; GUI cannot override
                continue
            cfg.secrets.set(name, (key or "").strip() or None)
        rt.reload()
        return {**settings_payload(rt), "ignored_locked": ignored + [f"api_key:{n}" for n in key_ignored]}

    @app.post("/api/settings/test")
    async def test_model(body: dict | None = None):
        rt = rt_getter()
        spec = (body or {}).get("model") or rt.config.get("model.default")
        if not _valid_spec(spec):
            raise HTTPException(400, "select a model first")
        t0 = time.time()
        try:
            p = build_provider(spec, rt.config)
            p.recorder = rt._record
            with scope(None, "test"):
                resp = await p.chat([Message("user", "Reply with exactly: OK")], temperature=0, max_tokens=16,
                                    retries=0)
                rt._record(p, resp.usage)
            return {"ok": True, "model": spec, "latency_ms": int((time.time() - t0) * 1000),
                    "reply": resp.message.text()[:200]}
        except Exception as e:
            return {"ok": False, "model": spec, "error": str(e)[:600]}

    @app.get("/api/providers/{name}/models")
    async def provider_models(name: str):
        rt = rt_getter()
        try:
            return {"models": (await build_provider(f"{name}/_", rt.config).list_models())[:300]}
        except Exception as e:
            return {"models": [], "error": str(e)[:300]}

    @app.get("/api/pricing")
    async def pricing():
        rt = rt_getter()
        return {"checked": PRICES_CHECKED, "rows": table(rt.config.get("pricing") or {})}

    @app.get("/api/usage")
    async def usage(days: int = 30):
        rt = rt_getter()
        mem = rt.memory
        days = max(1, min(days, 366))
        since = time.time() - days * 86400
        series = {r["key"]: r for r in mem.usage_grouped("day", since, limit=400)}
        by_day = []
        for i in range(days - 1, -1, -1):
            d = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
            r = series.get(d, {})
            by_day.append({"day": d, "cost_usd": r.get("cost_usd", 0), "input_tokens": r.get("input_tokens", 0),
                           "output_tokens": r.get("output_tokens", 0), "calls": r.get("calls", 0)})
        return {
            "totals": {p: mem.usage_totals(rt.period_start(p)) for p in ("today", "month", "all")}
                      | {"range": mem.usage_totals(since)},
            "by_day": by_day,
            "by_model": mem.usage_grouped("model", since),
            "by_purpose": mem.usage_grouped("purpose", since),
            "by_session": mem.usage_grouped("session", since, limit=15),
            "budget": rt.budget_status(),
            "unpriced_models": mem.unpriced_models(),
            "currency": rt.config.get("usage.currency", "USD"),
            "currency_rate": rt.config.get("usage.currency_rate"),
            "days": days,
        }
