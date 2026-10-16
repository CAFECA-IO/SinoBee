"""Serve workspace files to the UI (images / videos / attachments referenced in markdown) and accept uploads."""
from __future__ import annotations

import mimetypes
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..fileaccess import PRIVATE_NAMES, FileAccessError, allowed_roots  # noqa: F401
from ..fileaccess import resolve_file as _resolve

MAX_UPLOAD = 200 * 1024 * 1024
STATIC = Path(__file__).resolve().parent / "static"
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("text/markdown", ".md")


def resolve_file(config, path: str) -> Path:
    """Only files inside the workspace / project / allowed paths, never SinoBee's own private files."""
    try:
        return _resolve(config, path)
    except FileAccessError as e:
        raise HTTPException(e.status, str(e)) from None


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
