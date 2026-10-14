"""Share a conversation on sinobee.ai: preview (exactly what will be uploaded), upload, manage, import."""
from __future__ import annotations

import asyncio

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

    def build(body: dict) -> dict:
        msgs = msgs_of(str(body.get("session_id") or ""))
        try:
            b = share.build(rt().config, msgs, **_opts(body))
        except share.ShareError as e:
            raise HTTPException(400, str(e)) from None
        b["session_id"] = body.get("session_id")
        return b

    @app.post("/api/share/preview")
    async def preview(body: dict):
        b = await asyncio.to_thread(build, body)
        files = [{"id": str(i + 1), "name": f["name"], "mime": f["mime"], "size": f["size"]} for i, f in enumerate(b["files"])]
        turns = share.user_turns(msgs_of(str(body.get("session_id") or "")))
        return {**b, "files": files, "turns": turns, "signed_in": bool(account.token(rt().config)),
                "hub_url": account.base_url(rt().config)}

    @app.post("/api/share")
    async def create(body: dict):
        b = await asyncio.to_thread(build, body)
        accounts = [a for a in (body.get("accounts") or []) if str(a).strip()]
        exp = body.get("expires_days", 30)
        try:
            return await asyncio.to_thread(share.upload, rt().config, b, visibility=str(body.get("visibility") or "LINK"),
                                           accounts=accounts, expires_days=int(exp) if exp else None)
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
