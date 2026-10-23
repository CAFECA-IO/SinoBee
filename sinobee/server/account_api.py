"""sinobee.ai sign-in from the Web UI: start a device-code login, poll it in the background, sign out."""
from __future__ import annotations

import asyncio
import time

from fastapi import FastAPI, HTTPException

from .. import account, billing, profiles


def register(app: FastAPI, rt, busy=None, restart=None) -> None:
    """busy() -> bool: a task is running (switching accounts restarts SinoBee); restart(): start again in place."""
    state: dict = {"login": None}   # {user_code, verification_uri, ..., status, task}; device_code never leaves here
    busy = busy or (lambda: False)
    restart = restart or profiles.restart_process

    async def switch_when_idle(lg: dict) -> None:
        """The sign-in belongs to another account's data: move there and restart - after running tasks end."""
        lg["status"] = "switching"
        for _ in range(360):
            if not busy():
                break
            await asyncio.sleep(5)
        res = await asyncio.to_thread(profiles.switch_after_login, rt().config, account.account(rt().config))
        lg["profile"] = res
        if res["switched"]:
            restart()
        else:
            lg["status"] = "ok"

    def public_login() -> dict | None:
        lg = state["login"]
        if not lg:
            return None
        out = {k: lg.get(k) for k in ("user_code", "verification_uri", "verification_uri_complete", "expires_at",
                                      "status", "error", "profile")}
        if lg.get("status") == "pending":
            out["qr_svg"] = lg.get("qr_svg")
        return out

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
                if res["status"] == "ok":
                    await switch_when_idle(lg)
        if lg["status"] == "pending":
            lg["status"] = "expired"

    @app.get("/api/account")
    async def status(remote: bool = False):
        cfg = rt().config
        me = await asyncio.to_thread(account.whoami, cfg, remote) if remote else account.account(cfg)
        if me:
            profiles.claim_if_unowned(cfg, me)       # the data that was here is the signed-in person's
        return {"hub_url": account.base_url(cfg), "signed_in": bool(me), "account": me, "login": public_login(),
                "profile": profiles.current(cfg)}

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
        lg = {**s, "status": "pending", "error": None, "expires_at": time.time() + int(s.get("expires_in", 900)),
              "qr_svg": account.qr_svg(s.get("verification_uri_complete") or s["verification_uri"])}
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
        cfg = rt().config
        separated = profiles.load().get("base_owner") is not None or account.account(cfg) is not None
        if separated and busy():
            raise HTTPException(409, "有任務正在執行：登出會切換到本機資料並重新啟動 SinoBee，請等任務結束或先停止它")
        account.logout(cfg)
        state["login"] = None
        state.pop("quota", None)
        res = await asyncio.to_thread(profiles.switch_after_logout, cfg)
        if res["switched"]:
            restart()
        return {"ok": True, "restarting": res["switched"]}

    @app.get("/api/llm/quota")
    async def llm_quota(refresh: bool = False):
        """SinoBee.ai subscription rings (6 h / 7 days), plan and expiry; cached 20 s (the GUI polls it)."""
        cfg = rt().config
        hit = state.get("quota")
        if hit and not refresh and time.time() - hit[0] < 20 and hit[2] == account.token(cfg):
            return hit[1]
        q = await asyncio.to_thread(account.quota, cfg)
        q = {**q, "hub_url": account.base_url(cfg), "subscribe_url": f"{account.base_url(cfg)}/subscribe",
             "in_use": any(str(s or "").startswith("sinobee/") for s in
                           [cfg.get("model.default"), *(cfg.get("model.fallbacks") or [])]),
             "fetched_at": time.time()}
        billing.note_quota(cfg, q)            # remember the points; allowance back = straight back to the subscription
        q["billing"] = billing_public()
        state["quota"] = (time.time(), q, account.token(cfg))
        if q.get("status") == "ok":
            w6 = next((w for w in q.get("windows") or [] if w.get("window") == "6h"), None)
            if w6 and w6.get("usedPct") is not None:
                try:
                    rt().memory.mark_quota(w6["usedPct"])
                except Exception:  # noqa: BLE001
                    pass
        return q

    def billing_public() -> dict:
        cfg = rt().config
        st = billing.active(cfg)
        return {"source": (st or {}).get("source") or billing.SUBSCRIPTION, "until": (st or {}).get("until"),
                "window": (st or {}).get("window"), "points": billing.points(cfg),
                "auto_points": bool(cfg.get("billing.auto_points")),
                "on_exhausted": cfg.get("billing.on_exhausted") or "ask"}

    def pool_and_offer(failed: str | None = None) -> tuple[list[str], dict | None]:
        """Your own keys / local models in the chain that can be called now, and (none) one to add."""
        from ..providers import usable_now
        r = rt()
        prov = r.provider
        pool = []
        for p in (prov.pool() if hasattr(prov, "pool") else []):
            try:
                if usable_now(p.provider_name, r.config):
                    pool.append(p.label)
            except Exception:  # noqa: BLE001
                pass
        offer = None
        if not pool:
            from ..providers import fallback
            o = fallback.suggest(r.config, failed or prov.label)
            if o and not o["spec"].startswith("sinobee/"):
                offer = o
        return pool, offer

    @app.get("/api/billing")
    async def billing_get():
        """For the allowance-used-up dialog: who pays now, points, your own keys / local models that can take over."""
        pool, offer = pool_and_offer()
        return {**billing_public(), "pool": pool, "offer": offer}

    @app.post("/api/billing/choose")
    async def billing_choose(body: dict):
        """The user's click in the dialog: points | own | pause. points / own resume the stopped turn at once;
        pause leaves it to the recovery check (resumed when the allowance is back). remember = do this next time."""
        r = rt()
        cfg = r.config
        choice = body.get("choice")
        if choice not in (billing.POINTS, billing.OWN, "pause"):
            raise HTTPException(400, "choice must be points, own or pause")
        if choice == billing.OWN:
            pool, offer = pool_and_offer()
            if not pool:
                if not (offer and body.get("add") == offer["spec"]):
                    raise HTTPException(409, "沒有可用的自備金鑰或本地模型")
                cfg.save_gui_settings({"model": {"fallbacks": [*(cfg.get("model.fallbacks") or []), offer["spec"]]}})
                r.reload()
        if body.get("remember"):
            billing.remember(cfg, choice)
        if choice == "pause":
            billing.clear(cfg, "paused")
            return {"ok": True, "resumed": False}
        billing.choose(cfg, choice, window=body.get("window"), retry_at=body.get("retry_at"))
        sid = body.get("session_id")
        resumed = False
        if sid:
            from ..work.hub import get_hub
            from ..work.store import ACTIVE
            hub = get_hub(r)
            runs = [x for x in hub.store.runs(limit=300) if x["kind"] == "chat" and x.get("session_id") == sid]
            if runs and runs[0]["status"] not in ACTIVE:
                why = "改用平台點數繼續" if choice == billing.POINTS else "改用你的 API 金鑰與本地模型繼續"
                try:
                    hub.resume(runs[0]["id"], brief={"resume": True, "reason": why, "done": [], "remaining": [],
                                                     "next": f"訂閱額度用完，{why}：先確認做到哪裡，從中斷處接著做"})
                    resumed = True
                except Exception:  # noqa: BLE001
                    resumed = False
        return {"ok": True, "resumed": resumed, **billing_public()}

