"""Share a conversation through sinobee.ai.

Everything sensitive is removed here, before upload, and the user sees the exact result first:
stored secrets (API keys, DB passwords, the sinobee.ai token), passwords inside connection strings,
things shaped like API keys / tokens / private keys, `password=` style fields and the home directory.
Images the answer shows from the workspace are attached (max 20 files, 5 MiB in total).
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path

import httpx

from . import account
from .db import DSN_RE

MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 20
TOOL_IO = 2000

PATTERNS = [
    ("privatekey", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("apikey", re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{20}|sbh_[A-Za-z0-9_-]{20,})\b")),
    ("token", re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{20,}")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),   # JWT
    ("password", re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|api[_-]?key|access[_-]?token|token)(['\"]?\s*[:=]\s*)(['\"]?)([^\s'\",;}]{4,})\3")),
]
IMG_REF = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


class ShareError(RuntimeError):
    pass


class Redactor:
    def __init__(self, config):
        self.secrets = sorted({v for v in config.secrets.all_values(min_len=6)}, key=len, reverse=True)
        self.home = str(Path.home())
        self.counts: dict[str, int] = {}

    def _hit(self, kind: str, n: int = 1):
        if n:
            self.counts[kind] = self.counts.get(kind, 0) + n

    def __call__(self, text: str) -> str:
        if not text:
            return text
        for v in self.secrets:
            if v in text:
                self._hit("secret", text.count(v))
                text = text.replace(v, "••••")

        def dsn(m):
            if m.group(3) and m.group(3) != "••••":
                self._hit("dsn")
                return m.group(0).replace(":" + m.group(3) + "@", ":••••@")
            return m.group(0)
        text = DSN_RE.sub(dsn, text)
        for kind, rx in PATTERNS:
            if kind == "password":
                def pw(m):
                    if m.group(4) == "••••":
                        return m.group(0)
                    self._hit(kind)
                    return f"{m.group(1)}{m.group(2)}{m.group(3)}••••{m.group(3)}"
                text = rx.sub(pw, text)
            elif kind == "token" and rx.pattern.startswith("(?i)"):
                text, n = rx.subn(lambda m: m.group(1) + "••••", text)
                self._hit(kind, n)
            else:
                text, n = rx.subn("••••", text)
                self._hit(kind, n)
        if self.home and len(self.home) > 1 and self.home in text:
            self._hit("path", text.count(self.home))
            text = text.replace(self.home, "~")
        return text

    def summary(self) -> list[dict]:
        return [{"kind": k, "count": n} for k, n in sorted(self.counts.items())]


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "") for p in content
                         if (isinstance(p, dict) and p.get("type") == "text") or hasattr(p, "text"))
    return ""


SECRET_KEY = re.compile(r"(?i)(pass(word|wd)?|pwd|secret|token|api[_-]?key|private[_-]?key|credential|authorization)")


def _scrub(v, red: "Redactor"):
    """Structured tool arguments: values under secret-looking keys are replaced outright."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if SECRET_KEY.search(str(k)) and x not in (None, "", False):
                red._hit("password")
                out[k] = "••••"
            else:
                out[k] = _scrub(x, red)
        return out
    if isinstance(v, list):
        return [_scrub(x, red) for x in v]
    return v


def _clip(s: str, n: int = TOOL_IO) -> str:
    return s if len(s) <= n else s[:n] + f"\n…（已截斷，共 {len(s)} 字元）"


def build(config, messages: list[dict], *, include_tools: bool = True, title: str | None = None) -> dict:
    """messages: session messages as dicts (Message.to_dict()). Returns the share body (redacted) + a preview."""
    red = Redactor(config)
    out: list[dict] = []
    pending: dict[str, dict] = {}
    for m in messages:
        role = m.get("role")
        if role == "user":
            t = _text(m.get("content"))
            if t.strip():
                out.append({"role": "user", "text": red(t)})
        elif role == "assistant":
            entry = {"role": "assistant", "text": red(_text(m.get("content"))), "tools": []}
            for tc in m.get("tool_calls") or []:
                tool = {"name": str(tc.get("name"))[:80]}
                if include_tools:
                    args = _scrub(tc.get("arguments"), red)
                    tool["input"] = red(_clip(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)))
                entry["tools"].append(tool)
                pending[str(tc.get("id"))] = tool
            if not entry["tools"]:
                entry.pop("tools")
            if entry["text"].strip() or entry.get("tools"):
                # merge consecutive assistant steps into one bubble
                if out and out[-1]["role"] == "assistant":
                    prev = out[-1]
                    prev["text"] = "\n\n".join(x for x in (prev["text"], entry["text"]) if x.strip())
                    if entry.get("tools"):
                        prev.setdefault("tools", []).extend(entry["tools"])
                else:
                    out.append(entry)
        elif role == "tool" and include_tools:
            t = pending.get(str(m.get("tool_call_id")))
            if t is not None:
                t["output"] = red(_clip(_text(m.get("content"))))
    if not out:
        raise ShareError("這段對話沒有可分享的內容")
    files = attach_images(config, out)
    first = next((x["text"].strip() for x in out if x["role"] == "user" and x["text"].strip()), "")
    body = {"title": (title or (first.splitlines()[0] if first else "") or "對話")[:160],
            "messages": out, "files": files, "redactions": red.summary(),
            "model": config.get("model.default") or None}
    size = len(json.dumps(body))
    if size > MAX_BYTES * 1.37:
        raise ShareError(f"分享內容太大（{size // 1024} KiB），請取消附上工具細節或減少圖片")
    return body


def attach_images(config, messages: list[dict]) -> list[dict]:
    """Workspace images referenced by the answers become share files; refs are rewritten to files/<n>."""
    ws = Path(config.workspace).resolve()
    files: list[dict] = []
    seen: dict[str, str] = {}
    total = 0

    def repl(m):
        nonlocal total
        alt, ref = m.group(1), m.group(2)
        if re.match(r"^(https?:|data:)", ref):
            return m.group(0)
        raw = ref.split("?")[0]
        if raw.startswith("/api/files/"):
            raw = raw[len("/api/files/"):]
        p = (ws / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        if not str(p).startswith(str(ws)) or not p.is_file():
            return f"*（圖片 {alt or p.name} 未附上）*"
        if str(p) in seen:
            return f"![{alt}](files/{seen[str(p)]})"
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        size = p.stat().st_size
        if not mime.startswith("image/") or len(files) >= MAX_FILES or total + size > MAX_BYTES // 2:
            return f"*（圖片 {alt or p.name} 未附上）*"
        total += size
        fid = str(len(files) + 1)
        files.append({"name": p.name, "mime": mime, "data": base64.b64encode(p.read_bytes()).decode()})
        seen[str(p)] = fid
        return f"![{alt}](files/{fid})"
    for msg in messages:
        if msg["role"] == "assistant":
            msg["text"] = IMG_REF.sub(repl, msg["text"])
    return files


def _req(config, method: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{account.base_url(config)}{path}", headers=account.auth_headers(config, "shares:write"),
                             timeout=120, **kw)
    except httpx.HTTPError as e:
        raise ShareError(f"無法連線到 sinobee.ai：{e}") from None


def _err(r: httpx.Response) -> str:
    try:
        j = r.json()
        issues = "; ".join(f"{'.'.join(map(str, i.get('path', [])))}: {i.get('message')}" for i in (j.get("issues") or [])[:5])
        return (j.get("error") or r.text[:300]) + (f"（{issues}）" if issues else "")
    except ValueError:
        return f"HTTP {r.status_code}"


def upload(config, body: dict, *, visibility: str = "LINK", accounts: list[str] | None = None,
           expires_days: int | None = None) -> dict:
    payload = {**body, "visibility": visibility.upper(), "accounts": accounts or [],
               **({"expiresInDays": int(expires_days)} if expires_days else {})}
    r = _req(config, "POST", "/api/v1/shares", json=payload)
    if r.status_code >= 400:
        raise ShareError(f"分享失敗：{_err(r)}")
    return r.json()


def mine(config) -> list[dict]:
    r = _req(config, "GET", "/api/v1/shares")
    if r.status_code >= 400:
        raise ShareError(_err(r))
    return r.json().get("items", [])


def revoke(config, sid: str) -> None:
    r = _req(config, "DELETE", f"/api/v1/shares/{sid}")
    if r.status_code >= 400:
        raise ShareError(_err(r))
