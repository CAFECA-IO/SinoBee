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

from . import account, media
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


TL_TEXT = ("title", "q", "a", "source", "result", "status", "text", "summary", "kind", "phase", "id")
TL_MAX_ITEMS = 300


def _timeline(tl: dict, deliverables: list, clean) -> dict:
    """A turn's timeline (agent/timeline.py) as the share page shows it: the same questions, work and checks,
    redacted like the rest; tool rows keep their name and a short brief."""
    items = []
    for it in (tl.get("items") or [])[-TL_MAX_ITEMS:]:
        x = {k: clean(_clip(str(it[k]), 600)) for k in TL_TEXT if it.get(k) not in (None, "")}
        if it.get("ok") is not None:
            x["ok"] = bool(it["ok"])
        if it.get("notes"):
            x["notes"] = [clean(_clip(str(n), 300)) for n in it["notes"][:20]]
        if it.get("tools"):
            x["tools"] = [{"name": str(t.get("name"))[:80], "brief": clean(_clip(str(t.get("brief") or ""), 200))}
                          for t in it["tools"][:40]]
        if it.get("files"):
            x["files"] = [clean(str(f.get("name") or f.get("path") or ""))[:200] for f in it["files"][:20]]
        items.append(x)
    return {"mode": "full", "items": items,
            "key": [clean(str(d.get("name") or d.get("path") or ""))[:200] for d in (deliverables or [])][:6]}


def build(config, messages: list[dict], *, include_tools: bool = True, title: str | None = None,
          until_turn: int | None = None, query_rows: int = 20, mask_hosts: bool = False,
          extra_mask: list[str] | None = None, keep_paths: bool = False, attach: bool = True,
          timelines: dict[int, dict] | None = None) -> dict:
    """messages: session messages as dicts (Message.to_dict()). Returns the redacted share body.

    until_turn   share up to (and including the answer to) the N-th user message (1-based)
    query_rows   database results keep this many table rows (0 = leave query results out)
    mask_hosts   also mask internal IPs and host names (*.local, *.internal, 10.x, 192.168.x …)
    extra_mask   more words to mask (names, customer ids …)
    keep_paths   do not shorten the home directory to ~
    timelines    {index of a turn's last message: {"timeline", "deliverables"}} for large turns: the share shows the
                 turn as SinoBee did - its answer, key files and the folded process - instead of every interim note
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
    final_parts: list[str] = []          # this turn's closing words (assistant messages after its last tool call)
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant":
            if m.get("tool_calls"):
                final_parts = []
            elif _text(m.get("content")).strip():
                final_parts.append(_text(m.get("content")))
        if role == "user":
            t = _text(m.get("content"))
            final_parts = []
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
        tl = (timelines or {}).get(i)
        if tl and out and out[-1]["role"] == "assistant" and (tl.get("timeline") or {}).get("mode") == "full":
            out[-1]["timeline"] = _timeline(tl["timeline"], tl.get("deliverables") or [], clean)
            if final_parts:
                out[-1]["text"] = clean("\n\n".join(final_parts))
    if not out:
        raise ShareError("這段對話沒有可分享的內容")
    files = attach_files(config, out, clean) if attach else []
    first = next((x["text"].strip() for x in out if x["role"] == "user" and x["text"].strip()), "")
    from .share_doc import describe_chat
    first = re.sub(r"\[([^\]]+)\]\((?:files/\d+|[^)]*)\)", r"\1", re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[\1]", first))
    body = {"title": (title or (first.splitlines()[0] if first else "") or "對話")[:160],
            "description": describe_chat(out), "messages": out, "files": files, "redactions": red.summary(), "skills": skills,
            "model": config.get("model.default") or None}
    size = len(json.dumps(body, default=str))
    if size > MAX_BYTES:
        raise ShareError(f"對話內容太大（{size // 1024} KiB），請取消附上工具細節或只分享到某一輪")
    return body


LINK_REF = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024


def attach_files(config, messages: list[dict], clean=None) -> list[dict]:
    """Workspace files the answers show or link (images, videos, any attachment) become share files
    (25 MB each, 100 MB in total); refs are rewritten to files/<n>. Files are sent as multipart, not inline.
    A linked report or page (.md / .html) travels with the files it uses, under doc<k>/ with its relative paths,
    so sinobee.ai can show it like SinoBee's viewer does; its text is redacted like the conversation."""
    from . import share_doc
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
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        shrink = size > MAX_FILE and media.is_video(p, mime)       # re-encoded to fit just before upload
        counted = MAX_FILE if shrink else size
        if len(files) >= 40 or (size > MAX_FILE and not shrink) or total + counted > MAX_TOTAL:
            return None
        total += counted
        fid = str(len(files) + 1)
        entry = {"name": p.name, "mime": mime, "path": str(p), "size": counted}
        if shrink:
            entry.update(name=p.stem + ".mp4", mime="video/mp4", shrink=True, orig_size=size)
        files.append(entry)
        seen[str(p)] = fid
        return fid

    docs: dict[str, str] = {}

    def add_doc(p: Path) -> str | None:
        nonlocal total
        if str(p) in docs:
            return docs[str(p)]
        bundle = share_doc.collect(p)
        size = sum(f["size"] for f in bundle)
        if len(files) + len(bundle) > 40 or total + size > MAX_TOTAL:
            return add(p)
        prefix = f"doc{len(docs) + 1}/"
        entry = None
        for f in bundle:
            total += f["size"]
            fid = str(len(files) + 1)
            files.append(_doc_file(f, prefix, clean))
            entry = entry or fid
        docs[str(p)] = entry
        return entry

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
        fid = add_doc(p) if p.suffix.lower() in (".md", ".markdown", ".html", ".htm") else add(p)
        return f"[{text}](files/{fid})" if fid else f"{text}（未附上）"
    for msg in messages:
        if msg["role"] == "assistant":
            msg["text"] = LINK_REF.sub(link, IMG_REF.sub(img, msg["text"]))
        elif msg["role"] == "user":                 # what you attached (a picture, a video) travels with the share
            msg["text"] = IMG_REF.sub(img, msg["text"])
    return files


def _doc_file(f: dict, prefix: str, clean) -> dict:
    """A document bundle file; text files are redacted and sent inline (the redacted text, not the disk file)."""
    from . import share_doc
    out = {"name": prefix + f["name"], "mime": f["mime"], "size": f["size"]}
    if clean and Path(f["name"]).suffix.lower() in share_doc.TEXT_EXT:
        data = clean(Path(f["path"]).read_text(encoding="utf-8", errors="replace")).encode()
        out.update(data=base64.b64encode(data).decode(), size=len(data))
        if out["mime"].startswith("text/") or out["mime"] in ("application/javascript", "image/svg+xml"):
            out["mime"] = out["mime"].split(";")[0]
    else:
        out["path"] = f["path"]
    return out


def build_doc(config, path: str, *, title: str | None = None, mask_hosts: bool = False,
              extra_mask: list[str] | None = None, keep_paths: bool = False) -> dict:
    """A report or page a skill made, shared on its own: the entry, the files it uses, a title and a description."""
    from . import share_doc
    from .fileaccess import FileAccessError, resolve_file
    try:
        entry = resolve_file(config, path)
    except FileAccessError as e:
        raise ShareError(str(e)) from None
    if entry.suffix.lower() not in share_doc.DOC_EXT:
        raise ShareError("只能分享 Markdown 報告或 HTML／SVG 頁面")
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
    bundle = share_doc.collect(entry)
    files = [_doc_file(f, "", clean) for f in bundle]
    auto_title, desc = share_doc.describe(entry)
    title = clean(title or auto_title)[:160]
    # one message that links the document: hubs that predate document shares show it as a conversation with the
    # file attached (instead of refusing the share); newer hubs render the document itself and ignore it
    note = {"role": "assistant", "text": f"**{title}**\n\n{clean(desc)}\n\n[開啟文件](files/1)".replace("\n\n\n\n", "\n\n")}
    return {"kind": "doc", "entry": bundle[0]["name"], "title": title, "description": clean(desc),
            "messages": [note], "files": files, "redactions": red.summary(), "skills": [],
            "model": None, "_entry_path": str(entry), "_bundle": bundle}


def make_thumbnail(body: dict, *, title: str, description: str) -> bytes | None:
    """The 1200×630 picture social platforms show for the link."""
    from . import share_doc
    if body.get("kind") == "doc":
        entry = Path(body["_entry_path"])
        label = "報告" if entry.suffix.lower() in (".md", ".markdown") else "互動頁面"
        return share_doc.thumbnail(title=title, description=description, label=label, entry=entry, files=body["_bundle"])
    image = None
    for f in body.get("files") or []:
        if f["mime"] in ("image/png", "image/jpeg", "image/webp") and "path" in f and f["size"] <= 8 * 1024 * 1024:
            image = (Path(f["path"]).read_bytes(), f["mime"])
            break
    return share_doc.thumbnail(title=title, description=description, label="對話", image=image)


def _req(config, method: str, path: str, **kw) -> httpx.Response:
    try:
        return account.send(config, method, f"{account.base_url(config)}{path}", scope="shares:write", timeout=600, **kw)
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
           expires_days: int | None = 30, description: str | None = None, thumbnail: bytes | None = None) -> dict:
    """Multipart upload: `share` (JSON) + `files` (attachments read from disk) + `thumb` (the link-preview picture).
    expires_days None = never."""
    meta = {k: v for k, v in body.items() if k != "files" and not k.startswith("_")}
    meta.update({"visibility": visibility.upper(), "accounts": accounts or [],
                 "expiresInDays": int(expires_days) if expires_days else None, "files": []})
    if description is not None:
        meta["description"] = description[:300]
    # multipart file names lose their folders on some servers: the bundle paths travel in the JSON too
    meta["fileNames"] = [f["name"] for f in body.get("files") or []]
    handles = []
    try:
        parts = []
        for f in body.get("files") or []:
            if f.get("shrink"):
                try:
                    f["path"] = str(media.shrink_video(config, Path(f["path"]), MAX_FILE))
                except media.MediaError as e:
                    raise ShareError(f"{f['name']}：{e}") from None
                f.pop("shrink", None)
                f["size"] = Path(f["path"]).stat().st_size
            if "path" in f:
                fh = open(f["path"], "rb")
                handles.append(fh)
                parts.append(("files", (f["name"], fh, f["mime"])))
            elif "data" in f:
                parts.append(("files", (f["name"], base64.b64decode(f["data"]), f["mime"])))
        if thumbnail:
            parts.append(("thumb", ("thumb.png" if thumbnail[:4] == b"\x89PNG" else "thumb.jpg", thumbnail,
                                    "image/png" if thumbnail[:4] == b"\x89PNG" else "image/jpeg")))
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
    remember(config, res, body.get("session_id"), body.get("doc_path"))
    return res


# ---------- local record of what was shared (the 已分享 marker) ----------
def _records_path(config) -> Path:
    return Path(config.home) / "shares.json"


def records(config) -> list[dict]:
    try:
        return json.loads(_records_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def remember(config, res: dict, session_id: str | None, doc_path: str | None = None) -> None:
    items = [r for r in records(config) if r.get("id") != res.get("id")]
    items.insert(0, {"id": res.get("id"), "url": res.get("url"), "title": res.get("title"), "session_id": session_id, "doc_path": doc_path,
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
    mine = bool(account.token(config)) and base.rstrip("/") == account.base_url(config)
    try:
        r = account.send(config, "GET", f"{base}/api/v1/shares/{sid}", timeout=60) if mine else \
            httpx.get(f"{base}/api/v1/shares/{sid}", headers=account.UA, timeout=60)
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
    mine = bool(account.token(config)) and base.rstrip("/") == account.base_url(config)
    folder = Path(config.workspace) / "imports" / sid
    names: dict[str, str] = {}
    for f in doc.get("files") or []:
        try:
            fu = f"{base}/api/shares/{sid}/files/{f['id']}"
            r = account.send(config, "GET", fu, timeout=120) if mine else httpx.get(fu, headers=account.UA, timeout=120)
            if r.status_code == 200 and len(r.content) <= MAX_FILE:
                folder.mkdir(parents=True, exist_ok=True)
                # bundle files keep their folders (doc1/frames/t0915.jpg), each part made safe, never ../
                parts = [re.sub(r"[^\w.\-]+", "_", x)[:120].lstrip(".") for x in str(f["name"]).split("/") if x not in ("", ".", "..")]
                safe = "/".join(x for x in parts if x) or f"file-{f['id']}"
                (folder / safe).parent.mkdir(parents=True, exist_ok=True)
                (folder / safe).write_bytes(r.content)
                names[f["id"]] = f"imports/{sid}/{safe}"
        except httpx.HTTPError:
            continue

    def fix(t: str) -> str:
        return re.sub(r"\]\(files/(\d+)\)", lambda m: f"]({names.get(m.group(1), '#')})", t)
    msgs = []
    if doc.get("kind") == "doc":
        entry = next((names[f["id"]] for f in doc.get("files") or [] if f["name"] == doc.get("entry") and f["id"] in names), None)
        msgs.append(Message(role="assistant", content=f"**{doc.get('title') or '分享的文件'}**\n\n{doc.get('description') or ''}\n\n"
                            + (f"[開啟文件]({entry})" if entry else "（文件無法下載）")))
    for m in [] if doc.get("kind") == "doc" else doc.get("messages") or []:
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
