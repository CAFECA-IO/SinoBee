"""Settings (models, API keys, permissions, budgets, pricing) and usage/cost endpoints."""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

from ..accounting import scope
from ..pricing import PRICES_CHECKED, table
from ..providers import (PRESETS, build_provider, key_env_for, needs_key, provider_names, resolve_key,
                         split_model, usable_now)
from ..secrets import mask
from ..types import Message

ALL_TOOLSETS = ["shell", "files", "desktop", "browser", "web", "memory", "skills", "usage", "database", "work"]
PROVIDER_LABELS = {
    "sinobee": "SinoBee.ai 訂閱",
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
        needs_setup = not usable_now(prov, cfg)
    return {
        "model": {k: cfg.get(f"model.{k}") for k in ("default", "fallbacks", "auxiliary", "thinking", "reviewer", "embedding",
                                                      "vision", "context_window")},
        "agent": {"verify": cfg.get("agent.verify", True), "quality": cfg.get("agent.quality", "auto"),
                  "reviewer": cfg.get("agent.reviewer", True), "plan": cfg.get("agent.plan", "auto")},
        "context": _context_info(cfg),
        "permissions": {"approval": cfg.get("permissions.approval"),
                        "toolsets": sorted(set(cfg.get("permissions.toolsets") or [])
                                           | ({"work"} if int(cfg.get("permissions.toolsets_v") or 1) < 2 else set())),
                        "allowed_paths": cfg.get("permissions.allowed_paths")},
        "usage": cfg.get("usage"),
        "skills": {"auto_create": cfg.get("skills.auto_create"), "project_dir": str(cfg.project_dir),
                   "interop": cfg.get("skills.interop", True), "hub_url": cfg.get("skills.hub_url")},
        "pricing": cfg.get("pricing") or {},
        "providers": providers,
        "locked": sorted(cfg.locked),
        "all_toolsets": ALL_TOOLSETS,
        "needs_setup": needs_setup,
        "selected": _selected(cfg),
        "provider_error": rt.provider_error,
    }


def _role_usable(spec: str, cfg) -> bool:
    return usable_now(split_model(spec)[0], cfg)


def _selected(cfg) -> dict:
    """The model chosen for each role and whether its provider can be used (key present)."""
    out = {}
    for role in ("default", "auxiliary", "thinking", "reviewer", "embedding"):
        spec = cfg.get(f"model.{role}")
        out[role] = {"spec": spec, "ok": bool(spec) and _valid_spec(spec) and _role_usable(spec, cfg)}
    out["fallbacks"] = [{"spec": f, "ok": _valid_spec(f) and _role_usable(f, cfg)}
                        for f in (cfg.get("model.fallbacks") or [])]
    return out


def _context_info(cfg) -> dict:
    """The context window SinoBee assumes for the default model and the resulting request budget."""
    from ..agent.context import context_budget, context_window
    try:
        window, source = context_window(cfg)
        out = int(cfg.get("model.max_tokens", 8192))
        return {"window": window, "source": source, "budget": context_budget(cfg, out)}
    except Exception:  # noqa: BLE001
        return {}


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
            if "escalate" in body["model"] and "thinking" not in body["model"]:
                body["model"]["thinking"] = body["model"]["escalate"]          # the old name
            for k in ("default", "auxiliary", "thinking", "reviewer", "embedding"):
                if k in body["model"]:
                    v = (body["model"][k] or "").strip() or None
                    if v and not _valid_spec(v):
                        errors.append(f"model.{k}: use provider/model format, e.g. openai/gpt-5.4")
                    m[k] = v
            if "thinking" in m:
                m["escalate"] = None          # replaced by model.thinking
            if "fallbacks" in body["model"]:
                fb = body["model"]["fallbacks"]
                fb = [x.strip() for x in (fb.split(",") if isinstance(fb, str) else fb or []) if x and x.strip()]
                errors += [f"fallback '{x}' must be provider/model" for x in fb if not _valid_spec(x)]
                m["fallbacks"] = fb
            if "vision" in body["model"]:
                m["vision"] = bool(body["model"]["vision"])
            if "context_window" in body["model"]:
                v = str(body["model"]["context_window"] or "").strip().lower().replace(",", "").replace("_", "")
                if v in ("", "auto"):
                    m["context_window"] = "auto"
                else:
                    mult = 1000 if v.endswith("k") else 1_000_000 if v.endswith("m") else 1
                    try:
                        n = int(float(v.rstrip("km")) * mult)
                        if n < 2048:
                            raise ValueError
                        m["context_window"] = n
                    except ValueError:
                        errors.append("model.context_window: a number of tokens (e.g. 200000 or 200k), or auto")
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
                perm["toolsets_v"] = 2
            if "allowed_paths" in p:
                perm["allowed_paths"] = [x for x in p["allowed_paths"] if x]
            updates["permissions"] = perm
        if isinstance(body.get("agent"), dict):
            a = body["agent"]
            if "verify" in a:
                updates.setdefault("agent", {})["verify"] = bool(a["verify"])
            if a.get("quality") in ("auto", "fast", "standard", "strict"):
                updates.setdefault("agent", {})["quality"] = a["quality"]
            if "reviewer" in a:
                updates.setdefault("agent", {})["reviewer"] = bool(a["reviewer"])
            if a.get("plan") in ("auto", "always", "off"):
                updates.setdefault("agent", {})["plan"] = a["plan"]
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
        if body.get("api_keys"):
            from ..providers import catalog
            catalog.forget()
        rt.reload()
        payload = settings_payload(rt)
        names = {"default": "執行模型", "auxiliary": "輔助模型", "thinking": "思維模型", "reviewer": "審查模型",
                 "embedding": "Embedding 模型"}
        why = lambda spec: "需要登入 SinoBee.ai" if str(spec).startswith("sinobee/") else "的供應商還沒有設定金鑰"  # noqa: E731
        warnings = [f"{names[r]} {v['spec']} {why(v['spec'])}" for r, v in payload["selected"].items()
                    if r in names and v["spec"] and not v["ok"]]
        warnings += [f"備援模型 {f['spec']} {why(f['spec'])}" for f in payload["selected"]["fallbacks"] if not f["ok"]]
        return {**payload, "ignored_locked": ignored + [f"api_key:{n}" for n in key_ignored], "warnings": warnings}

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

    @app.get("/api/models/catalog")
    async def models_catalog(refresh: bool = False):
        """Every usable provider with its models (live list, cached), for the model pickers."""
        from ..providers import catalog
        rt = rt_getter()
        return {**await catalog.catalog(rt.config, refresh=refresh), "selected": _selected(rt.config)}

    @app.put("/api/providers/{name}/key")
    async def set_provider_key(name: str, body: dict):
        """Save (or clear) one provider's key right away, then verify it by listing that provider's models."""
        from ..providers import ALIASES, catalog
        rt = rt_getter()
        cfg = rt.config
        name = ALIASES.get(name.strip().lower(), name.strip().lower())
        if name not in provider_names(cfg):
            raise HTTPException(404, f"unknown provider {name}")
        if resolve_key(name, cfg)[1] == "env":
            raise HTTPException(409, "這個金鑰由啟動參數提供，無法在介面修改")
        key = (body.get("key") or "").strip()
        if key and (len(key) < 8 or any(c.isspace() for c in key)):
            raise HTTPException(400, "金鑰格式看起來不正確（太短或含空白）")
        cfg.secrets.set(name, key or None)
        catalog.forget()
        rt.reload()
        entry = await catalog.provider_models(name, rt.config, refresh=True)
        out = {"provider": entry, "selected": _selected(rt.config)}
        main = rt.config.get("model.default")
        main_ok = _valid_spec(main) and _role_usable(main, rt.config)
        if key and entry["status"] == "ready" and not main_ok:
            out["suggest_default"] = catalog.suggest_default(entry)
        return out

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
