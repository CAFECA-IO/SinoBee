"""The sandbox in the GUI (sandbox plan D1, D4, C2): live view, taking over the desktop, page previews, network.

    GET  /api/sandbox/{sid}                    is there a sandbox, is it yours right now, what got blocked
    POST /api/sandbox/{sid}/takeover {on}      you take over the desktop (the agent's desktop/browser tools wait)
    WS   /api/sandbox/{sid}/vnc?token=          the desktop: VNC over a WebSocket (noVNC in the page)
    ANY  /api/sandbox/{sid}/preview/{port}/... a web server running in the sandbox, in your own browser
    GET  /api/evidence/{sid}                   the conversation's evidence list (agent/evidence.py), ?turn=
    GET  /api/evidence/{sid}/{file}            one captured picture, frame or clip
    GET  /api/evidence/{sid}.zip?turn=         all of it (or one turn's) with the manifest, to keep or hand on

Nothing is published from the container: every byte goes through `docker exec` (manager.open_stream / bridge),
and only this server - behind its token - reaches it.
"""
from __future__ import annotations

import asyncio
import secrets

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "content-encoding"}


def register(app: FastAPI, rt, broadcast=None) -> None:
    def manager():
        from ..sandbox import executor_kind
        from ..sandbox.manager import manager_for
        cfg = rt().config
        if executor_kind(cfg) != "sandbox":
            raise HTTPException(409, "the tools do not run in the sandbox (Settings → 權限與安全 → 沙盒)")
        return manager_for(cfg)

    @app.get("/api/evidence/{sid}.zip")
    async def evidence_zip(sid: str, turn: str | None = None):
        import io
        import json
        import zipfile
        from ..agent.evidence import Store
        st = Store(rt().config, sid)
        entries = st.for_turn(turn) if turn else st.entries()
        if not entries:
            raise HTTPException(404, "no evidence")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(entries, ensure_ascii=False, indent=1))
            for e in entries:
                for name in e.get("files") or []:
                    f = st.file(name)
                    if f:
                        z.write(f, name)
        name = f"evidence-{st.conversation}{'-' + turn if turn else ''}.zip"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/evidence/{sid}")
    async def evidence_list(sid: str, turn: str | None = None):
        from ..agent.evidence import Store
        st = Store(rt().config, sid)
        return st.for_turn(turn) if turn else st.entries()

    @app.get("/api/evidence/{sid}/{name}")
    async def evidence_file(sid: str, name: str, download: bool = False):
        from ..agent.evidence import Store
        f = Store(rt().config, sid).file(name)
        if not f:
            raise HTTPException(404, "no such evidence file")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4"}.get(f.suffix, "application/octet-stream")
        return FileResponse(f, media_type=mime, filename=f.name if download else None,
                            headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/sandbox/{sid}")
    async def sandbox_status(sid: str):
        from ..sandbox import executor_kind
        from ..sandbox.manager import manager_for
        cfg = rt().config
        if executor_kind(cfg) != "sandbox":
            return {"sandbox": False}
        m = manager_for(cfg)
        await m.connection_log()
        blocked = [c for c in m.connections if c.get("conversation") == sid and not c.get("allowed")][-20:]
        return {"sandbox": True, "running": sid in m.boxes, "takeover": sid in m.takeover,
                "name": m.name_of(sid), "blocked": blocked, "grants": sorted(m.grants.get(sid, set())),
                "open": sid in m.open_all}

    @app.post("/api/sandbox/{sid}/takeover")
    async def takeover(sid: str, body: dict):
        m = manager()
        on = bool(body.get("on"))
        m.set_takeover(sid, on)
        if broadcast:
            broadcast("sandbox", {"session_id": sid, "takeover": on})
        return {"takeover": on}

    @app.websocket("/api/sandbox/{sid}/vnc")
    async def vnc(websocket: WebSocket, sid: str):
        expected = rt().config.server_token()
        token = websocket.query_params.get("token")
        if expected and not secrets.compare_digest(str(token or ""), str(expected)):
            await websocket.close(code=4401)
            return
        try:
            m = manager()
            # view-only unless you have taken over (x11vnc on 5900 ignores input; 5901 takes it)
            reader, writer, proc = await m.open_stream(sid, 5901 if sid in m.takeover else 5900)
        except Exception as e:  # noqa: BLE001
            await websocket.close(code=4500, reason=str(e)[:120])
            return
        sub = "binary" if "binary" in (websocket.headers.get("sec-websocket-protocol") or "") else None
        await websocket.accept(subprotocol=sub)

        async def down():
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:  # noqa: BLE001
                pass

        async def up():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    data = msg.get("bytes") or (msg.get("text") or "").encode()
                    if data:
                        writer.write(data)
                        await writer.drain()
            except (WebSocketDisconnect, ConnectionError, RuntimeError):
                pass
        tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()
        if proc.returncode is None:
            proc.kill()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass

    @app.api_route("/api/sandbox/{sid}/preview/{port}/{path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def preview(sid: str, port: int, path: str, request: Request):
        import httpx
        m = manager()
        if not 1 <= port <= 65535:
            raise HTTPException(400, "bad port")
        local = await m.bridge(sid, port)
        url = f"http://127.0.0.1:{local}/{path}" + (f"?{request.url.query}" if request.url.query else "")
        query_token = request.query_params.get("token")
        if query_token:            # the page's own links do not carry ?token: keep it in a cookie for this preview
            url = url.replace(f"token={query_token}", "").replace("?&", "?").rstrip("?&")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP and k.lower() != "cookie"}
        headers["host"] = f"localhost:{port}"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=60) as c:
                r = await c.request(request.method, url, headers=headers, content=await request.body())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"nothing answers on port {port} in the sandbox ({type(e).__name__})")
        out = Response(r.content, status_code=r.status_code,
                       headers={k: v for k, v in r.headers.items() if k.lower() not in HOP})
        if query_token:
            out.set_cookie("sb_token", query_token, path=f"/api/sandbox/{sid}/preview/{port}", httponly=True,
                           samesite="strict")
        return out
