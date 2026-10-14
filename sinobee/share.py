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


HOST_RX = re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
                     r"|169\.254\.\d{1,3}\.\d{1,3}|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:local|lan|internal|intranet|corp|home\.arpa))\b")
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _limit_table_rows(text: str, n: int) -> str:
    """Keep the header + first n data rows of markdown tables in a tool result."""
    out, rows, dropped = [], 0, 0
    for line in text.splitlines():
        if TABLE_ROW.match(line):
            rows += 1
            if rows > n + 2:          # header + separator + n rows
                dropped += 1
                continue
        else:
            if dropped:
                out.append(f"…（另有 {dropped} 列未分享）")
            rows, dropped = 0, 0
        out.append(line)
    if dropped:
        out.append(f"…（另有 {dropped} 列未分享）")
    return "\n".join(out)


def user_turns(messages: list[dict]) -> list[str]:
    """First line of every user message, for the "share up to turn N" picker."""
    return [(_text(m.get("content")).strip().splitlines() or [""])[0][:80] for m in messages
            if m.get("role") == "user" and _text(m.get("content")).strip()]


def build(config, messages: list[dict], *, include_tools: bool = True, title: str | None = None,
          until_turn: int | None = None, query_rows: int = 20, mask_hosts: bool = False,
          extra_mask: list[str] | None = None, keep_paths: bool = False, attach: bool = True) -> dict:
    """messages: session messages as dicts (Message.to_dict()). Returns the redacted share body.

    until_turn   share up to (and including the answer to) the N-th user message (1-based)
    query_rows   database results keep this many table rows (0 = leave query results out)
    mask_hosts   also mask internal IPs and host names (*.local, *.internal, 10.x, 192.168.x …)
    extra_mask   more words to mask (names, customer ids …)
    keep_paths   do not shorten the home directory to ~
    """
    red = Redactor(config)
    if keep_paths:
        red.home = ""
    extra = sorted({w.strip() for w in (extra_mask or []) if len(w.strip()) >= 2}, key=len, reverse=True)

    def clean(t: str) -> str:
        t = red(t)
        for w in extra:
            if w in t:
                red._hit("custom", t.count(w))
                t = t.replace(w, "••••")
        if mask_hosts:
            t, n = HOST_RX.subn("••••", t)
            red._hit("host", n)
        return t

    out: list[dict] = []
    pending: dict[str, dict] = {}
    skills: list[str] = []
    turns = 0
    for m in messages:
        role = m.get("role")
        if role == "user":
            t = _text(m.get("content"))
            if not t.strip():
                continue
            turns += 1
            if until_turn and turns > until_turn:
                break
            out.append({"role": "user", "text": clean(t)})
        elif role == "assistant":
            entry = {"role": "assistant", "text": clean(_text(m.get("content"))), "tools": []}
            for tc in m.get("tool_calls") or []:
                name = str(tc.get("name"))[:80]
                args = tc.get("arguments")
                if name.startswith("skill_") and isinstance(args, dict) and args.get("name"):
                    sk = str(args["name"])[:80]
                    if sk not in skills:
                        skills.append(sk)
                tool = {"name": name}
                if include_tools:
                    a = _scrub(args, red)
                    tool["input"] = clean(_clip(a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)))
                entry["tools"].append(tool)
                pending[str(tc.get("id"))] = tool
            if not entry["tools"]:
                entry.pop("tools")
            if entry["text"].strip() or entry.get("tools"):
                if out and out[-1]["role"] == "assistant":
                    prev = out[-1]
                    prev["text"] = "\n\n".join(x for x in (prev["text"], entry["text"]) if x.strip())
                    if entry.get("tools"):
                        prev.setdefault("tools", []).extend(entry["tools"])
                else:
                    out.append(entry)
        elif role == "tool" and include_tools:
            t = pending.get(str(m.get("tool_call_id")))
            if t is None:
                continue
            content = _text(m.get("content"))
            if t["name"].startswith("db_") and t["name"] != "db_list":
                if query_rows <= 0:
                    t["output"] = "（查詢結果未分享）"
                    continue
                content = _limit_table_rows(content, query_rows)
            t["output"] = clean(_clip(content))
    if not out:
        raise ShareError("這段對話沒有可分享的內容")
    files = attach_files(config, out) if attach else []
    first = next((x["text"].strip() for x in out if x["role"] == "user" and x["text"].strip()), "")
    body = {"title": (title or (first.splitlines()[0] if first else "") or "對話")[:160],
            "messages": out, "files": files, "redactions": red.summary(), "skills": skills,
            "model": config.get("model.default") or None}
    size = len(json.dumps(body, default=str))
    if size > MAX_BYTES:
        raise ShareError(f"對話內容太大（{size // 1024} KiB），請取消附上工具細節或只分享到某一輪")
    return body


LINK_REF = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def attach_files(config, messages: list[dict]) -> list[dict]:
    """Workspace files the answers show or link (images, videos, any attachment) become share files
    (25 MB each, 100 MB in total); refs are rewritten to files/<n>. Files are sent as multipart, not inline."""
    ws = Path(config.workspace).resolve()
    files: list[dict] = []
    seen: dict[str, str] = {}
    total = 0

    def resolve(ref: str) -> Path | None:
        if re.match(r"^(https?:|data:|mailto:|#)", ref):
            return None
        raw = ref.split("?")[0].split("#")[0]
        if raw.startswith("/api/files/"):
            raw = raw[len("/api/files/"):]
        try:
            from urllib.parse import unquote
            raw = unquote(raw)
            p = (ws / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        except (OSError, ValueError):
            return None
        return p if str(p).startswith(str(ws) + os.sep) and p.is_file() else None

    def add(p: Path) -> str | None:
        nonlocal total
        if str(p) in seen:
            return seen[str(p)]
        size = p.stat().st_size
        if len(files) >= 40 or size > MAX_FILE or total + size > MAX_TOTAL:
            return None
        total += size
        fid = str(len(files) + 1)
        files.append({"name": p.name, "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                      "path": str(p), "size": size})
        seen[str(p)] = fid
        return fid

    def img(m):
        alt, ref = m.group(1), m.group(2)
        if re.match(r"^(https?:|data:)", ref):
            return m.group(0)
        p = resolve(ref)
        fid = add(p) if p else None
        return f"![{alt}](files/{fid})" if fid else f"*（{alt or ref} 未附上）*"

    def link(m):
        text, ref = m.group(1), m.group(2)
        p = resolve(ref)
        if not p:
            return m.group(0) if re.match(r"^(https?:|mailto:|#)", ref) else text
        fid = add(p)
        return f"[{text}](files/{fid})" if fid else f"{text}（未附上）"
    for msg in messages:
        if msg["role"] == "assistant":
            msg["text"] = LINK_REF.sub(link, IMG_REF.sub(img, msg["text"]))
    return files


def _req(config, method: str, path: str, **kw) -> httpx.Response:
    try:
        return httpx.request(method, f"{account.base_url(config)}{path}", headers=account.auth_headers(config, "shares:write"),
                             timeout=600, **kw)
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
           expires_days: int | None = 30) -> dict:
    """Multipart upload: `share` (JSON) + `files` (attachments read from disk). expires_days None = never."""
    meta = {k: v for k, v in body.items() if k != "files"}
    meta.update({"visibility": visibility.upper(), "accounts": accounts or [],
                 "expiresInDays": int(expires_days) if expires_days else None, "files": []})
    handles = []
    try:
        parts = []
        for f in body.get("files") or []:
            if "path" in f:
                fh = open(f["path"], "rb")
                handles.append(fh)
                parts.append(("files", (f["name"], fh, f["mime"])))
            elif "data" in f:
                parts.append(("files", (f["name"], base64.b64decode(f["data"]), f["mime"])))
        if parts:
            r = _req(config, "POST", "/api/v1/shares", data={"share": json.dumps(meta, ensure_ascii=False)}, files=parts)
        else:
            r = _req(config, "POST", "/api/v1/shares", json=meta)
    finally:
        for fh in handles:
            fh.close()
    if r.status_code >= 400:
        raise ShareError(f"分享失敗：{_err(r)}")
    res = r.json()
    remember(config, res, body.get("session_id"))
    return res


# ---------- local record of what was shared (the 已分享 marker) ----------
def _records_path(config) -> Path:
    return Path(config.home) / "shares.json"


def records(config) -> list[dict]:
    try:
        return json.loads(_records_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember(config, res: dict, session_id: str | None) -> None:
    items = [r for r in records(config) if r.get("id") != res.get("id")]
    items.insert(0, {"id": res.get("id"), "url": res.get("url"), "title": res.get("title"), "session_id": session_id,
                     "visibility": res.get("visibility"), "created_at": res.get("created_at")})
    _records_path(config).write_text(json.dumps(items[:500], ensure_ascii=False, indent=1), encoding="utf-8")


def forget(config, sid: str) -> None:
    _records_path(config).write_text(json.dumps([r for r in records(config) if r.get("id") != sid], ensure_ascii=False),
                                     encoding="utf-8")


def update(config, sid: str, **changes) -> dict:
    r = _req(config, "PATCH", f"/api/v1/shares/{sid}", json=changes)
    if r.status_code >= 400:
        raise ShareError(_err(r))
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
    forget(config, sid)


# ---------- "continue in my SinoBee": import a share as a new conversation ----------
SHARE_URL = re.compile(r"^https?://[^/]+(?:/[a-z]{2}(?:-[A-Z]{2})?)?/s/([A-Za-z0-9]{6,20})/?$")


def fetch_share(config, url: str) -> tuple[str, dict, str]:
    """Returns (share id, share json incl. doc, hub base). Signed-in users can import LOGIN shares too."""
    m = SHARE_URL.match(url.strip())
    if not m:
        raise ShareError("分享連結格式不正確（https://sinobee.ai/s/<id>）")
    sid = m.group(1)
    base = url.strip().split("/s/")[0]
    base = re.sub(r"/[a-z]{2}(?:-[A-Z]{2})?$", "", base)
    headers = dict(account.UA)
    if account.token(config) and base.rstrip("/") == account.base_url(config):
        headers.update(account.auth_headers(config))
    try:
        r = httpx.get(f"{base}/api/v1/shares/{sid}", headers=headers, timeout=60)
    except httpx.HTTPError as e:
        raise ShareError(f"無法連線到 {base}：{e}") from None
    if r.status_code in (401, 403):
        raise ShareError("這個分享需要登入（或只開放給特定帳號）：請先登入 sinobee.ai")
    if r.status_code == 410:
        raise ShareError("這個分享已撤回或過期")
    if r.status_code >= 400:
        raise ShareError(f"讀取分享失敗：{_err(r)}")
    return sid, r.json(), base


def import_share(config, memory, url: str) -> dict:
    from .types import Message
    sid, data, base = fetch_share(config, url)
    doc = data["doc"]
    headers = dict(account.UA)
    if account.token(config) and base.rstrip("/") == account.base_url(config):
        headers.update(account.auth_headers(config))
    folder = Path(config.workspace) / "imports" / sid
    names: dict[str, str] = {}
    for f in doc.get("files") or []:
        try:
            r = httpx.get(f"{base}/api/shares/{sid}/files/{f['id']}", headers=headers, timeout=120)
            if r.status_code == 200 and len(r.content) <= MAX_FILE:
                folder.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^\w.\-]+", "_", f["name"])[:120] or f"file-{f['id']}"
                (folder / safe).write_bytes(r.content)
                names[f["id"]] = f"imports/{sid}/{safe}"
        except httpx.HTTPError:
            continue

    def fix(t: str) -> str:
        return re.sub(r"\]\(files/(\d+)\)", lambda m: f"]({names.get(m.group(1), '#')})", t)
    msgs = []
    for m in doc.get("messages") or []:
        text = fix(m.get("text") or "")
        if m.get("role") == "assistant" and m.get("tools"):
            text += "\n\n<details><summary>使用的工具：" + "、".join(t["name"] for t in m["tools"]) + "</summary>\n\n" + \
                "\n\n".join(f"**{t['name']}**\n\n```\n{(t.get('output') or t.get('input') or '')[:2000]}\n```"
                             for t in m["tools"] if t.get("output") or t.get("input")) + "\n\n</details>"
        msgs.append(Message(role="user" if m.get("role") == "user" else "assistant", content=text))
    new_sid = f"import-{sid}-{__import__('uuid').uuid4().hex[:6]}"
    memory.ensure_session(new_sid, "web", f"{doc.get('title') or '分享'}（匯入）")
    memory.append_messages(new_sid, msgs)
    return {"session_id": new_sid, "title": doc.get("title"), "messages": len(msgs), "files": len(names)}
