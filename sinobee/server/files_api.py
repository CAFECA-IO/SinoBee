"""Serve workspace files to the UI (images / videos / attachments referenced in markdown) and accept uploads."""
from __future__ import annotations

import mimetypes
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ..fileaccess import PRIVATE_NAMES, FileAccessError, allowed_roots  # noqa: F401
from ..fileaccess import resolve_file as _resolve

MAX_UPLOAD = 200 * 1024 * 1024
STATIC = Path(__file__).resolve().parent / "static"
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("font/woff2", ".woff2")

# Interactive pages a skill made (anatomy figures, reports with scripts) open in the app through a read-only
# "view" capability: /view/<id>/<file> serves only the folder of the page that was opened, for a limited time, with
# a CSP sandbox. The page runs scripts in an opaque origin: it cannot read the app's token, cookies or storage, cannot
# call /api (connect-src is its own folder), and cannot frame or navigate the app.
VIEW_TTL = 12 * 3600
VIEW_MAX = 300
VIEWABLE = {".html", ".htm", ".svg", ".xhtml"}


def view_csp(base: str) -> str:
    return ("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals allow-downloads; "
            f"default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' {base} https: blob:; "
            f"style-src 'unsafe-inline' {base} https:; img-src {base} data: blob: https:; font-src {base} data: https:; "
            f"media-src {base} data: blob: https:; connect-src {base}; worker-src {base} blob:; frame-src 'none'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


MATH_DIR = "__sinobee_math"
MATH_FILES = {"katex.min.js", "katex.min.css", "sb-math.js"}
_MATH_TEXT = re.compile(r"\$\$[\s\S]+?\$\$|\\\(|\\\[|(?<![\w$])\$(?![\s$\d])[^$\n<]{1,200}?(?<!\s)\$(?!\d)"
                        r"|class=[\"'][^\"']*\b(?:math|formula|equation|latex)\b", re.I)
_HAS_MATH_LIB = re.compile(r"katex|mathjax|mathml|temml|asciimath", re.I)


def wants_math(page: str) -> bool:
    """An HTML page that writes formulas ($…$, \\(…\\), formula boxes) but brings no math renderer of its own."""
    body = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", page, flags=re.I)
    return bool(_MATH_TEXT.search(body)) and not _HAS_MATH_LIB.search(page)


def with_math(page: str, base: str) -> str:
    tags = (f'<link rel="stylesheet" href="{base}katex.min.css"><script src="{base}katex.min.js"></script>'
            f'<script src="{base}sb-math.js"></script>'
            "<script>addEventListener('DOMContentLoaded',function(){sbMath.render(document.body)})</script>")
    i = page.lower().rfind("</head>")
    return page[:i] + tags + page[i:] if i >= 0 else tags + page


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
    views: dict[str, dict] = {}

    @app.post("/api/view")
    async def open_view(body: dict):
        """A short-lived, read-only link to an HTML page (and the files beside it) for the in-app viewer."""
        f = resolve_file(rt_getter().config, str(body.get("path") or ""))
        if f.suffix.lower() not in VIEWABLE:
            raise HTTPException(400, "only HTML and SVG pages open in the page viewer")
        now = time.time()
        for k in [k for k, v in views.items() if v["expires"] < now]:
            views.pop(k, None)
        while len(views) >= VIEW_MAX:
            views.pop(min(views, key=lambda k: views[k]["expires"]))
        cap = secrets.token_urlsafe(18)
        views[cap] = {"root": f.parent, "expires": now + VIEW_TTL}
        return {"url": f"/view/{cap}/{f.name}", "name": f.name, "path": str(f), "expires_in": VIEW_TTL}

    @app.get("/view/{cap}/{rel:path}")
    async def view_file(cap: str, rel: str, request: Request):
        v = views.get(cap)
        if not v or v["expires"] < time.time():
            raise HTTPException(404, "this view link has expired: open the page again from SinoBee")
        root = v["root"]
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            raise HTTPException(404)
        base = f"{request.url.scheme}://{request.url.netloc}/view/{cap}/"
        headers = {"Content-Security-Policy": view_csp(base), "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                   # the page runs with an opaque origin (sandbox): its own images and scripts are cross-origin loads
                   "Cross-Origin-Resource-Policy": "cross-origin",
                   # …and fonts and fetch() of its data files are CORS requests from origin "null"; the link itself
                   # is the capability, so anyone holding it may read these files anyway
                   "Access-Control-Allow-Origin": "*"}
        if rel.startswith(MATH_DIR + "/"):                     # KaTeX for pages that write formulas without it
            name = rel[len(MATH_DIR) + 1:]
            vf = (STATIC / "vendor" / name).resolve()
            if name not in MATH_FILES and not (name.startswith("fonts/") and vf.suffix == ".woff2" and vf.parent.name == "fonts"):
                raise HTTPException(404)
            if not vf.is_file() or (STATIC / "vendor").resolve() not in vf.parents:
                raise HTTPException(404)
            return FileResponse(vf, media_type=mimetypes.guess_type(vf.name)[0] or "font/woff2", headers=headers)
        f = resolve_file(rt_getter().config, str(target))      # still never SinoBee's private files
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if mime == "text/html" and f.stat().st_size < 8 * 1024 * 1024:
            page = f.read_text(encoding="utf-8", errors="replace")
            if wants_math(page):
                return Response(with_math(page, f"/view/{cap}/{MATH_DIR}/"), media_type="text/html; charset=utf-8", headers=headers)
        return FileResponse(f, media_type=mime, headers=headers)

    @app.get("/vendor/{name}")
    async def vendor(name: str):
        f = (STATIC / "vendor" / name).resolve()
        if f.parent != (STATIC / "vendor").resolve() or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/vendor/fonts/{name}")
    async def vendor_font(name: str):   # KaTeX math fonts, referenced as fonts/... by katex.min.css
        fonts = (STATIC / "vendor" / "fonts").resolve()
        f = (fonts / name).resolve()
        if f.parent != fonts or f.suffix != ".woff2" or not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, media_type="font/woff2", headers={"Cache-Control": "public, max-age=604800"})

    icons = (STATIC / "icons").resolve()
    icon_types = {".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png",
                  ".webmanifest": "application/manifest+json"}

    def icon(name: str) -> FileResponse:
        f = (icons / name).resolve()
        if f.parent != icons or not f.is_file() or f.suffix not in icon_types:
            raise HTTPException(404)
        return FileResponse(f, media_type=icon_types[f.suffix], headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/icons/{name}")
    async def icons_file(name: str):
        return icon(name)

    @app.get("/favicon.ico")
    async def favicon():
        return icon("favicon.ico")

    @app.get("/apple-touch-icon.png")
    async def apple_icon():
        return icon("apple-touch-icon.png")

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
