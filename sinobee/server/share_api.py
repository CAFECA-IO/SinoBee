"""Share a conversation on sinobee.ai: preview (exactly what will be uploaded), upload, list, revoke."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException

from .. import account, share


def register(app: FastAPI, rt) -> None:
    def build(body: dict) -> dict:
        sid = str(body.get("session_id") or "")
        msgs = [m.to_dict() for m in rt().memory.load_messages(sid)]
        if not msgs:
            raise HTTPException(404, "找不到這段對話")
        try:
            return share.build(rt().config, msgs, include_tools=body.get("include_tools", True) is not False,
                               title=(body.get("title") or None))
        except share.ShareError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/api/share/preview")
    async def preview(body: dict):
        b = await asyncio.to_thread(build, body)
        files = [{"id": str(i + 1), "name": f["name"], "mime": f["mime"], "size": len(f["data"]) * 3 // 4} for i, f in enumerate(b["files"])]
        return {**b, "files": files, "signed_in": bool(account.token(rt().config)), "hub_url": account.base_url(rt().config)}

    @app.post("/api/share")
    async def create(body: dict):
        b = await asyncio.to_thread(build, body)
        accounts = [a for a in (body.get("accounts") or []) if str(a).strip()]
        try:
            return await asyncio.to_thread(share.upload, rt().config, b, visibility=str(body.get("visibility") or "LINK"),
                                           accounts=accounts, expires_days=body.get("expires_days") or None)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/share/mine")
    async def mine():
        try:
            return {"items": await asyncio.to_thread(share.mine, rt().config)}
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/share/{sid}")
    async def revoke(sid: str):
        try:
            await asyncio.to_thread(share.revoke, rt().config, sid)
        except (share.ShareError, account.AccountError) as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}
