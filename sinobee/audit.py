"""What a conversation's tools did, kept per conversation (security plan stage 2): commands, files written, sites
opened, network and key decisions, and whether each was allowed.

    ~/.sinobee/sandbox/<conversation>/audit.jsonl    one JSON object per line, newest last (cut at 4 MB: the older
                                                      half goes to audit.1.jsonl)

The tools cannot read or change it (tools/guard.py keeps SinoBee's data folder out of their reach). Keys are
replaced by [key …] before anything is written.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX = 4 << 20
_lock = threading.Lock()
KEEP = {"shell", "skill_run", "write_file", "edit_file", "web_fetch", "web_search", "browser", "request_network",
        "request_secret", "desktop", "db_query", "db_execute"}
READ_ONLY_BROWSER = {"snapshot", "screenshot", "get_text", "tabs", "wait", "scroll", "switch_tab"}


def path_of(config, conversation: str) -> Path:
    from .sandbox.convconf import conv_dir
    return conv_dir(config, conversation) / "audit.jsonl"


def wanted(tool, args: dict) -> bool:
    if tool.name == "browser":
        return str(args.get("action", "")).lower() not in READ_ONLY_BROWSER
    return tool.name in KEEP or tool.writes


def summary(name: str, args: dict) -> str:
    a = args or {}
    if name in ("shell",):
        return str(a.get("command", ""))[:600]
    if name == "skill_run":
        return f"{a.get('name', '')} {a.get('script', '')} {a.get('args', '')}".strip()[:600]
    if name in ("write_file", "edit_file"):
        return str(a.get("path", ""))
    if name == "web_fetch":
        return str(a.get("url", ""))[:600]
    if name == "web_search":
        return str(a.get("query", ""))[:300]
    if name == "browser":
        return " ".join(str(x) for x in (a.get("action"), a.get("url") or a.get("path") or a.get("key") or "") if x)[:600]
    if name == "request_network":
        return ", ".join(a.get("domains") or [])[:300]
    if name == "request_secret":
        return str(a.get("name", ""))
    return json.dumps({k: v for k, v in a.items() if k not in ("content", "new_string", "old_string")},
                      ensure_ascii=False)[:400]


def record(config, conversation: str | None, entry: dict, ctx=None) -> None:
    if not conversation:
        return
    from .task_secrets import redact
    line = {"t": round(time.time(), 3), **entry}
    for k in ("what", "detail", "reason"):
        if isinstance(line.get(k), str):
            line[k] = redact(line[k], ctx, config)[:800]
    p = path_of(config, conversation)
    try:
        with _lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > MAX:
                os.replace(p, p.with_name("audit.1.jsonl"))
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(config, conversation: str, limit: int = 500) -> list[dict]:
    out = []
    for p in (path_of(config, conversation).with_name("audit.1.jsonl"), path_of(config, conversation)):
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    continue
        except OSError:
            continue
    return out[-limit:]
