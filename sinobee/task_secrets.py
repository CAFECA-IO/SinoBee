"""Keys a task may be given, one task at a time (security plan stage 2, F3).

You keep keys in SinoBee (Settings → 權限與安全 → 任務金鑰): a name, the environment variable it becomes, the sites
it is for, and the value (in secrets.json, 0600; never shown again). A task asks with `request_secret(name, reason)`;
you allow it for this task or not. Then:

- the value is in the environment of the task's commands (`env_for`) - the model never sees it, and any tool output
  that contains it shows `[key NAME]` instead (`redact`);
- in the sandbox its sites are reachable for the task, and the egress proxy refuses a request that carries it to
  any other site (sandbox/egress.py);
- the conversation counts as having seen a secret (tools/guard.py): tools that could send it out ask first;
- when the task ends it is taken back (`end_task`).
"""
from __future__ import annotations

import base64
import re
import time
from urllib.parse import quote

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def clean_domains(domains) -> list[str]:
    from .tools.sandbox_tools import clean
    return clean(domains)


def listing(config) -> list[dict]:
    """What the settings page and the tool may know: never the value."""
    out = []
    for name, v in sorted(config.secrets.task_secrets().items()):
        val = str(v.get("value") or "")
        out.append({"name": name, "env": v.get("env") or name.upper().replace("-", "_"),
                    "domains": list(v.get("domains") or []), "note": v.get("note") or "",
                    "set": bool(val), "hint": ("…" + val[-4:]) if len(val) > 8 else "", "updated": v.get("updated")})
    return out


def save(config, name: str, *, value: str | None = None, env: str | None = None, domains=None,
         note: str | None = None) -> dict:
    if not NAME.match(name or ""):
        raise ValueError("名稱只能用英文字母、數字、- 與 _，且以字母開頭")
    cur = config.secrets.task_secrets().get(name, {})
    env = (env or cur.get("env") or name.upper().replace("-", "_")).strip()
    if not ENV.match(env):
        raise ValueError("環境變數名稱只能用大寫英文字母、數字與 _")
    doms = clean_domains(domains) if domains is not None else list(cur.get("domains") or [])
    if not doms:
        raise ValueError("至少要指定一個這把金鑰會用到的網域（例如 api.stripe.com）")
    val = value if value else cur.get("value")
    if not val:
        raise ValueError("請輸入金鑰的值")
    entry = {"value": val, "env": env, "domains": doms, "note": (note if note is not None else cur.get("note")) or "",
             "updated": time.time()}
    config.secrets.set_task_secret(name, entry)
    return next(x for x in listing(config) if x["name"] == name)


def delete(config, name: str) -> None:
    config.secrets.set_task_secret(name, None)


# ----------------------------------------------------------------- one task
def held(ctx) -> dict[str, dict]:
    return ctx.state.setdefault("task_secrets", {})


def grant(ctx, name: str) -> dict:
    """Give the stored key to this task (after you allowed it)."""
    entry = dict(ctx.config.secrets.task_secrets()[name])
    held(ctx)[name] = entry
    from .tools.guard import note_secret
    note_secret(ctx, f"the key {name}")
    from .sandbox import executor_of
    ex = executor_of(ctx)
    if ex.kind == "sandbox":
        ex.manager.task_secrets.setdefault(ex.conversation, {})[name] = {"value": entry["value"],
                                                                        "domains": entry["domains"]}
    return entry


def env_for(ctx) -> dict[str, str]:
    return {v.get("env") or k.upper(): v["value"] for k, v in (ctx.state.get("task_secrets") or {}).items()
            if v.get("value")}


def _variants(value: str) -> list[str]:
    out = {value, quote(value, safe=""), base64.b64encode(value.encode()).decode().rstrip("=")}
    return sorted((x for x in out if len(x) >= 6), key=len, reverse=True)


def redact(text: str, ctx=None, config=None) -> str:
    """Keys given to this task and every stored key or token never reach the model, the transcript or a log."""
    if not text:
        return text
    names: list[tuple[str, str]] = []
    for name, v in ((ctx.state.get("task_secrets") if ctx is not None else None) or {}).items():
        names += [(x, f"[key {name}]") for x in _variants(str(v.get("value") or ""))]
    cfg = config or (ctx.config if ctx is not None else None)
    if cfg is not None and hasattr(cfg, "secrets"):
        try:
            names += [(x, "[key]") for x in cfg.secrets.all_values(min_len=8)]
        except Exception:  # noqa: BLE001
            pass
    for value, label in sorted(names, key=lambda t: len(t[0]), reverse=True):
        if value and value in text:
            text = text.replace(value, label)
    return text


def end_task(ctx) -> bool:
    """Take back what this task was given. True when something was held."""
    had = bool(ctx.state.pop("task_secrets", None))
    from .sandbox import executor_of
    try:
        ex = executor_of(ctx)
    except Exception:  # noqa: BLE001
        return had
    if ex.kind == "sandbox":
        had = (ex.manager.task_secrets.pop(ex.conversation, None) is not None) or had
    return had
