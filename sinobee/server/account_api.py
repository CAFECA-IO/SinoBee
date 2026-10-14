"""sinobee.ai sign-in from the Web UI: start a device-code login, poll it in the background, sign out."""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException

from .. import account


def register(app: FastAPI, rt) -> None:
    state: dict = {"login": None}   # {user_code, verification_uri, ..., status, task}; device_code never leaves here

    def public_login() -> dict | None:
        lg = state["login"]
        if not lg:
            return None
        return {k: lg.get(k) for k in ("user_code", "verification_uri", "verification_uri_complete", "expires_at",
                                       "status", "error")}

    async def poller(lg: dict) -> None:
        cfg = rt().config
        interval = int(lg.get("interval", 5))
        while time.time() < lg["expires_at"] and lg["status"] == "pending":
            await asyncio.sleep(interval)
            try:
                res = await asyncio.to_thread(account.poll_once, cfg, lg["device_code"])
            except account.AccountError as e:
                lg["error"] = str(e)
                continue
            if res["status"] == "slow_down":
                interval += 5
            elif res["status"] != "pending":
                lg["status"] = res["status"]
                lg["error"] = res.get("error")
        if lg["status"] == "pending":
            lg["status"] = "expired"

    @app.get("/api/account")
    async def status(remote: bool = False):
        cfg = rt().config
        me = await asyncio.to_thread(account.whoami, cfg, remote) if remote else account.account(cfg)
        return {"hub_url": account.base_url(cfg), "signed_in": bool(me), "account": me, "login": public_login()}

    @app.post("/api/account/login")
    async def login():
        old = state["login"]
        if old and old.get("task") and not old["task"].done():
            old["status"] = "cancelled"
            old["task"].cancel()
        try:
            s = await asyncio.to_thread(account.start, rt().config)
        except account.AccountError as e:
            raise HTTPException(502, str(e)) from None
        lg = {**s, "status": "pending", "error": None, "expires_at": time.time() + int(s.get("expires_in", 900))}
        lg["task"] = asyncio.create_task(poller(lg))
        state["login"] = lg
        return public_login()

    @app.delete("/api/account/login")
    async def cancel():
        lg = state["login"]
        if lg and lg.get("task"):
            lg["task"].cancel()
        state["login"] = None
        return {"ok": True}

    @app.post("/api/account/logout")
    async def logout():
        account.logout(rt().config)
        state["login"] = None
        return {"ok": True}
