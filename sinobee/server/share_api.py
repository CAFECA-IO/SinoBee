"""Share a conversation on sinobee.ai: preview (exactly what will be uploaded), upload, manage, import."""
from __future__ import annotations

import asyncio
import base64
import hashlib

from fastapi import FastAPI, HTTPException

from .. import account, share


def _opts(body: dict) -> dict:
    def num(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d
    extra = body.get("extra_mask") or []
    if isinstance(extra, str):
        extra = [w for w in extra.replace(",", "\n").splitlines()]
    return {"include_tools": body.get("include_tools", True) is not False, "title": body.get("title") or None,
            "until_turn": num(body.get("until_turn"), 0) or None, "query_rows": max(0, min(num(body.get("query_rows"), 20), 500)),
            "mask_hosts": bool(body.get("mask_hosts")), "extra_mask": [str(w) for w in extra][:100],
            "keep_paths": bool(body.get("keep_paths"))}


def register(app: FastAPI, rt) -> None:
    def msgs_of(sid: str) -> list[dict]:
        msgs = [m.to_dict() for m in rt().memory.load_messages(sid)]
        if not msgs:
            raise HTTPException(404, "找不到這段對話")
        return msgs

    def timelines_of(sid: str) -> dict[int, dict]:
        """Saved timelines of the conversation's large turns, keyed by the index of the turn's last message."""
        try:
            from ..work.hub import get_hub
            runs = {(r.get("state") or {}).get("turn_id"): r.get("state") or {}
                    for r in get_hub(rt()).store.session_runs(sid)}
            out = {}
            for t in rt().memory.turns(sid):
                st = runs.get(t["id"]) or {}
                if (st.get("timeline") or {}).get("mode") == "full":
                    out[int(t["end_idx"])] = {"timeline": st["timeline"], "deliverables": st.get("deliverables") or []}
            return out
        except Exception:  # noqa: BLE001 - a share without timelines still shares the conversation
            return {}

    def build(body: dict) -> dict:
        try:
            if body.get("doc_path"):
                o = _opts(body)
                b = share.build_doc(rt().config, str(body["doc_path"]), title=o["title"], mask_hosts=o["mask_hosts"],
                                    extra_mask=o["extra_mask"], keep_paths=o["keep_paths"])
                b["doc_path"] = str(body["doc_path"])
                return b
            sid = str(body.get("session_id") or "")
            msgs = msgs_of(sid)
            b = share.build(rt().config, msgs, **_opts(body), timelines=timelines_of(sid))
        except share.ShareError as e:
            raise HTTPException(400, str(e)) from None
        b["session_id"] = body.get("session_id")
        return b

    thumbs: dict[str, bytes] = {}   # previewed link pictures, uploaded exactly as shown

    def thumb_for(body: dict, b: dict) -> tuple[str, bytes | None]:
        title = str(body.get("title") or b["title"])[:160]
        desc = str(body.get("description") if body.get("description") is not None else b.get("description") or "")[:300]
        key = hashlib.sha256(repr((body.get("doc_path"), body.get("session_id"), body.get("until_turn"), title, desc,
                                   [f["name"] for f in b["files"]])).encode()).hexdigest()[:24]
        if key not in thumbs:
            if len(thumbs) > 40:
                thumbs.clear()
            thumbs[key] = share.make_thumbnail(b, title=title, description=desc) or b""
        return key, thumbs[key] or None

    @app.post("/api/share/preview")
    async def preview(body: dict):
        b = await asyncio.to_thread(build, body)
        files = [{"id": str(i + 1), "name": f["name"], "mime": f["mime"], "size": f["size"],
                  **({"shrink": True, "orig_size": f.get("orig_size")} if f.get("shrink") else {})}
                 for i, f in enumerate(b["files"])]
        turns = [] if b.get("kind") == "doc" else share.user_turns(msgs_of(str(body.get("session_id") or "")))
        out = {k: v for k, v in b.items() if not k.startswith("_")}
        return {**out, "files": files, "turns": turns, "signed_in": bool(account.token(rt().config)),
                "hub_url": account.base_url(rt().config)}

    @app.post("/api/share/thumbnail")
    async def thumbnail(body: dict):
        """The link-preview picture (1200×630) for the current title and description."""
        b = await asyncio.to_thread(build, body)
        key, png = await asyncio.to_thread(thumb_for, body, b)
        if not png:
            return {"key": None, "image": None}
        mime = "image/png" if png[:4] == b"\x89PNG" else "image/jpeg"
        return {"key": key, "image": f"data:{mime};base64," + base64.b64encode(png).decode()}

    @app.post("/api/share")
    async def create(body: dict):
        b = await asyncio.to_thread(build, body)
        accounts = [a for a in (body.get("accounts") or []) if str(a).strip()]
        exp = body.get("expires_days", 30)
        desc = str(body["description"])[:300] if body.get("description") is not None else b.get("description")
        png = None
        if body.get("thumbnail") is not False:
            _, png = await asyncio.to_thread(thumb_for, body, b)
        try:
            return await asyncio.to_thread(share.upload, rt().config, b, visibility=str(body.get("visibility") or "LINK"),
                                           accounts=accounts, expires_days=int(exp) if exp else None,
                                           description=desc, thumbnail=png)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/share/mine")
    async def mine():
        try:
            items = await asyncio.to_thread(share.mine, rt().config)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None
        local = {r["id"]: r for r in share.records(rt().config)}
        return {"items": [{**i, "session_id": (local.get(i["id"]) or {}).get("session_id")} for i in items]}

    @app.get("/api/share/session/{sid}")
    async def for_session(sid: str):
        """Shares made from this conversation (for the 已分享 marker)."""
        return {"items": [r for r in share.records(rt().config) if r.get("session_id") == sid]}

    @app.patch("/api/share/{sid}")
    async def update(sid: str, body: dict):
        changes = {k: body[k] for k in ("visibility", "accounts", "expiresInDays", "title") if k in body}
        try:
            return await asyncio.to_thread(share.update, rt().config, sid, **changes)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/share/{sid}")
    async def revoke(sid: str):
        try:
            await asyncio.to_thread(share.revoke, rt().config, sid)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    @app.post("/api/share/import")
    async def import_(body: dict):
        try:
            return await asyncio.to_thread(share.import_share, rt().config, rt().memory, str(body.get("url") or ""))
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None
