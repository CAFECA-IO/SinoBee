"""The sandbox in the GUI (sandbox plan D1, D4, C2): live view, taking over the desktop, page previews, network.

    GET  /api/sandbox/{sid}                    is there a sandbox, is it yours right now, what got blocked
    POST /api/sandbox/{sid}/takeover {on}      you take over the desktop (the agent's desktop/browser tools wait)
    WS   /api/sandbox/{sid}/vnc?token=          the desktop: VNC over a WebSocket (noVNC in the page)
    ANY  /api/sandbox/{sid}/preview/{port}/... a web server running in the sandbox, in your own browser
    GET  /api/sandbox/{sid}/folders            the folders of this computer given to the conversation (📁 資料夾)
    POST /api/sandbox/{sid}/folders            {path, mode: write|ro}   give one (write = review or direct, as Settings say)
    DELETE /api/sandbox/{sid}/folders?path=&force=   take one away (its review copy too; force when changes wait)
    GET  /api/sandbox-browse?path=&hidden=     the folders inside a folder of your home (or /Volumes), to pick one
    GET  /api/sandbox/{sid}/changes            changes waiting for review in the copies of your folders (staging.py)
    POST /api/sandbox/{sid}/changes/apply      {folder, paths?, force?}  bring them to your folder
    POST /api/sandbox/{sid}/changes/discard    {folder, paths?}          drop them (the copy gets the original back)
    POST /api/sandbox/{sid}/changes/undo       {id}                      put back what an apply replaced
    GET  /api/sandbox-pending                  {conversation: changes waiting}, for the sidebar
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

    def _folder(sid: str, name: str) -> dict:
        from ..sandbox.folders import folders_for
        f = next((x for x in folders_for(rt().config, sid) if x["name"] == name and x["mode"] == "review"), None)
        if f is None:
            raise HTTPException(404, f"no review folder {name}")
        return f

    async def _after(sid: str) -> dict:
        from ..sandbox import staging
        items = await asyncio.to_thread(staging.pending, rt().config, sid, False)
        staging.remember_pending(rt().config, sid, items)
        if broadcast:
            broadcast("host_changes", {"session_id": sid, "count": len(items)})
        return {"pending": len(items)}

    def _shown(p: str) -> str:
        from pathlib import Path
        home = str(Path.home())
        return "~" + p[len(home):] if p.startswith(home + "/") else p

    @app.get("/api/sandbox-browse")
    async def browse(path: str = "", hidden: bool = False):
        """Folder picker for 📁 資料夾: names of the subfolders only (no files), within your home or /Volumes; each
        says whether it may be given to a sandbox (folders.refuse)."""
        import os
        from pathlib import Path

        from ..sandbox import folders as fd
        cfg = rt().config
        home = Path.home().resolve()
        roots = [home] + ([Path("/Volumes")] if Path("/Volumes").is_dir() else [])
        try:
            p = Path(path).expanduser().resolve() if path else home
        except (OSError, RuntimeError):
            raise HTTPException(400, "路徑無效") from None
        if not any(p == r or r in p.parents for r in roots):
            raise HTTPException(403, "只能瀏覽家目錄（與外接磁碟）裡的資料夾")
        if not p.is_dir():
            raise HTTPException(404, "找不到這個資料夾")

        def listing():
            out = []
            try:
                with os.scandir(p) as it:
                    for e in it:
                        if len(out) >= 500:
                            break
                        if e.name.startswith(".") and not hidden:
                            continue
                        try:
                            if not e.is_dir(follow_symlinks=True):
                                continue
                        except OSError:
                            continue
                        q = str(Path(e.path))
                        why = fd.refuse(cfg, q)
                        out.append({"name": e.name, "path": q, "shown": _shown(q), "can_pick": why is None, "why": why})
            except PermissionError:
                raise HTTPException(403, "沒有權限讀取這個資料夾（macOS 可能要在「系統設定 → 隱私權」允許）") from None
            return sorted(out, key=lambda x: x["name"].lower())
        dirs = await asyncio.to_thread(listing)
        why = fd.refuse(cfg, str(p))
        parent = p.parent if p != p.parent and any(p.parent == r or r in p.parent.parents for r in roots) else None
        picks = [("桌面", "Desktop"), ("文件", "Documents"), ("下載", "Downloads"), ("專案", "Projects"),
                 ("GitHub", "Documents/GitHub"), ("GitHub", "GitHub"), ("iCloud 雲碟", "Library/Mobile Documents/com~apple~CloudDocs")]
        shortcuts, seen = [], set()
        for label, rel in picks:
            q = home / rel
            if q.is_dir() and label not in seen:
                seen.add(label)
                shortcuts.append({"label": label, "path": str(q)})
        root = next(r for r in roots if p == r or r in p.parents)
        crumbs = [{"label": "~" if root == home else root.name or str(root), "path": str(root)}]
        for part in p.relative_to(root).parts:
            crumbs.append({"label": part, "path": str(Path(crumbs[-1]["path"]) / part)})
        return {"path": str(p), "shown": _shown(str(p)), "parent": str(parent) if parent else None, "crumbs": crumbs,
                "can_pick": why is None, "why": why, "dirs": dirs, "shortcuts": shortcuts}

    @app.get("/api/sandbox/{sid}/folders")
    async def conv_folders(sid: str):
        from pathlib import Path

        from ..sandbox import executor_kind, staging
        from ..sandbox.folders import folders_for, review_writes
        cfg = rt().config
        out = []
        for f in folders_for(cfg, sid):
            n = len(await asyncio.to_thread(staging.changes, cfg, sid, f, False)) if f["mode"] == "review" else 0
            out.append({**f, "shown": _shown(f["path"]), "inner": f"/host/{f['name']}",
                        "exists": Path(f["path"]).is_dir(), "pending": n})
        return {"folders": out, "review_writes": review_writes(cfg), "sandbox": executor_kind(cfg) == "sandbox"}

    @app.post("/api/sandbox/{sid}/folders")
    async def conv_folder_add(sid: str, body: dict):
        from pathlib import Path

        from ..sandbox import folders as fd
        from ..sandbox import make_executor
        from ..tools.sandbox_tools import grant
        cfg = rt().config
        ex = make_executor(cfg, sid)
        if ex.kind != "sandbox":
            raise HTTPException(409, "工具不在沙盒執行時不需要授權資料夾（設定 → 權限與安全 → 沙盒）")
        path = str(body.get("path") or "").strip()
        why = fd.refuse(cfg, path)
        if why:
            raise HTTPException(400, why)
        mode = fd.write_mode(cfg, "ro" if body.get("mode") == "ro" else "write")
        target = Path(path).expanduser().resolve()
        if mode == "review" and await asyncio.to_thread(fd.count_files, target, fd.review_max(cfg)) > fd.review_max(cfg):
            raise HTTPException(400, f"這個資料夾超過 {fd.review_max(cfg):,} 個檔案，太大無法做審核用的副本：請選唯讀，"
                                     "或在設定關閉「寫入前審核」後直接寫入")
        msg = await grant(cfg, ex, target, mode)
        if broadcast:
            broadcast("folders", {"session_id": sid})
        return {"ok": True, "message": msg, **(await conv_folders(sid))}

    @app.delete("/api/sandbox/{sid}/folders")
    async def conv_folder_remove(sid: str, path: str, force: bool = False):
        from pathlib import Path

        from ..sandbox import staging
        from ..sandbox.folders import folders_for, remove_grant
        cfg = rt().config
        target = str(Path(path).expanduser().resolve())
        f = next((x for x in folders_for(cfg, sid) if x["path"] == target), None)
        if f is None:
            raise HTTPException(404, "這個對話沒有這個資料夾")
        if f["mode"] == "review" and not force:
            n = len(await asyncio.to_thread(staging.changes, cfg, sid, f, False))
            if n:
                raise HTTPException(409, f"{n} 個變更還沒審核，移除會一併捨棄")
        await asyncio.to_thread(remove_grant, cfg, sid, target)
        staging.remember_pending(cfg, sid, await asyncio.to_thread(staging.pending, cfg, sid, False))
        if f["mode"] in ("ro", "rw"):
            from ..sandbox.manager import manager_for
            m = manager_for(cfg)
            if sid in m.boxes:
                await m.refresh(sid)
        if broadcast:
            broadcast("folders", {"session_id": sid})
        return {"ok": True, **(await conv_folders(sid))}

    @app.get("/api/sandbox-pending")
    async def sandbox_pending():
        from ..sandbox import staging
        return await asyncio.to_thread(staging.pending_counts, rt().config)

    @app.get("/api/sandbox/{sid}/changes")
    async def host_changes(sid: str):
        from ..sandbox import staging
        cfg = rt().config
        folders = []
        for f in staging.review_folders(cfg, sid):
            items = await asyncio.to_thread(staging.changes, cfg, sid, f, True)
            folders.append({"name": f["name"], "origin": f["path"], "changes": items})
        n = sum(len(f["changes"]) for f in folders)
        staging.remember_pending(cfg, sid, [c for f in folders for c in f["changes"]])
        return {"folders": folders, "count": n,
                "applied": [{k: b.get(k) for k in ("id", "folder", "t", "undone")} | {"files": len(b.get("items") or [])}
                            for b in staging.applied_batches(cfg, sid)[:10]]}

    @app.post("/api/sandbox/{sid}/changes/apply")
    async def host_apply(sid: str, body: dict):
        from ..sandbox import staging
        f = _folder(sid, str(body.get("folder") or ""))
        paths = body.get("paths")
        res = await asyncio.to_thread(staging.apply, rt().config, sid, f,
                                      [str(p) for p in paths] if isinstance(paths, list) else None,
                                      [str(p) for p in body.get("force") or []])
        return {**res, **await _after(sid)}

    @app.post("/api/sandbox/{sid}/changes/discard")
    async def host_discard(sid: str, body: dict):
        from ..sandbox import staging
        f = _folder(sid, str(body.get("folder") or ""))
        paths = body.get("paths")
        done = await asyncio.to_thread(staging.discard, rt().config, sid, f,
                                       [str(p) for p in paths] if isinstance(paths, list) else None)
        return {"discarded": done, **await _after(sid)}

    @app.post("/api/sandbox/{sid}/changes/undo")
    async def host_undo(sid: str, body: dict):
        from ..sandbox import staging
        try:
            done = await asyncio.to_thread(staging.undo, rt().config, sid, str(body.get("id") or ""))
        except staging.StagingError as e:
            raise HTTPException(404, str(e)) from None
        return {"restored": done, **await _after(sid)}

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
