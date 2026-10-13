"""Serve workspace files to the UI (images / videos / attachments referenced in markdown) and accept uploads."""
from __future__ import annotations

import mimetypes
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

MAX_UPLOAD = 200 * 1024 * 1024
STATIC = Path(__file__).resolve().parent / "static"
PRIVATE_NAMES = {"secrets.json", "settings.json", "config.yaml", "sinobee.db", "sinobee.db-wal", "sinobee.db-shm",
                 "trusted_projects.json", ".vncpass"}
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("text/markdown", ".md")


def allowed_roots(config) -> list[Path]:
    roots = [config.workspace, config.project_dir] + [Path(p) for p in (config.get("permissions.allowed_paths") or [])]
    out = []
    for r in roots:
        try:
            out.append(Path(r).expanduser().resolve())
        except OSError:
            pass
    return out


def resolve_file(config, path: str) -> Path:
    """Only files inside the workspace / project / allowed paths, never SinoBee's own private files."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = config.workspace / p
    p = p.resolve()
    home = config.home.resolve()
    if p.name in PRIVATE_NAMES or (home in p.parents and config.workspace.resolve() not in [p, *p.parents]):
        raise HTTPException(403, "this file is private to SinoBee")
    if not any(p == r or r in p.parents for r in allowed_roots(config)):
        raise HTTPException(403, "file is outside the workspace / allowed paths")
    if not p.is_file():
        raise HTTPException(404, "file not found")
    return p


def safe_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\-㐀-鿿]+", "_", name).strip("._") or "file"
    return name[:120]


def register(app: FastAPI, rt_getter) -> None:
    @app.get("/vendor/{name}")
    async def vendor(name: str):
        f = (STATIC / "vendor" / name).resolve()
        if f.parent != (STATIC / "vendor").resolve() or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/api/files")
    async def get_file(path: str, download: bool = False):
        f = resolve_file(rt_getter().config, path)
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if mime in ("text/html", "image/svg+xml", "application/xhtml+xml"):
            download = True   # never render active content from the workspace inside the app's origin
        return FileResponse(f, media_type=mime, filename=f.name if download else None,
                            content_disposition_type="attachment" if download else "inline",
                            headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/files/info")
    async def file_info(path: str):
        f = resolve_file(rt_getter().config, path)
        st = f.stat()
        return {"name": f.name, "path": str(f), "size": st.st_size, "modified": st.st_mtime,
                "mime": mimetypes.guess_type(f.name)[0] or "application/octet-stream"}

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        cfg = rt_getter().config
        day = time.strftime("%Y%m%d")
        dest_dir = cfg.workspace / "uploads" / day
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for uf in files:
            name = safe_name(uf.filename or "file")
            dest = dest_dir / name
            n = 1
            while dest.exists():
                dest = dest_dir / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            size = 0
            with dest.open("wb") as fh:
                while chunk := await uf.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise HTTPException(413, f"{name} is larger than {MAX_UPLOAD // 1024 // 1024} MB")
                    fh.write(chunk)
            out.append({"name": dest.name, "path": str(dest), "size": size,
                        "mime": uf.content_type or mimetypes.guess_type(dest.name)[0] or "application/octet-stream"})
        return out
