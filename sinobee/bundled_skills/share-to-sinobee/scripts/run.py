#!/usr/bin/env python3
"""share-to-sinobee helpers - standard library only, never changes the original files.

    run.py clean <file> <outdir>     a copy without metadata: JPEG / PNG / WebP without EXIF, GPS, XMP and text
                                     chunks; .docx / .xlsx / .pptx without author, company, custom properties and
                                     comment authors; PDF without document info (when pypdf is installed)
    run.py inventory <folder>        the folder's files with size, type and a suggestion: share or leave out

JSON out; exit 0 ok, 1 could not do it, 2 usage error.
"""
from __future__ import annotations

import json
import re
import struct
import sys
import zipfile
from pathlib import Path


def out(d: dict, code: int = 0):
    print(json.dumps(d, ensure_ascii=False, indent=1))
    sys.exit(code)


# ---------------------------------------------------------------- images
def clean_jpeg(b: bytes) -> tuple[bytes, list[str]]:
    if b[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i, outb, removed = 2, bytearray(b[:2]), []
    while i < len(b):
        if b[i] != 0xFF:
            raise ValueError("broken JPEG segment")
        marker = b[i + 1]
        if marker == 0xDA:                                    # start of scan: the rest is image data
            outb += b[i:]
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            outb += b[i:i + 2]
            i += 2
            continue
        ln = struct.unpack(">H", b[i + 2:i + 4])[0]
        seg = b[i:i + 2 + ln]
        payload = seg[4:]
        keep = True
        if 0xE1 <= marker <= 0xEF and not (marker == 0xE2 and payload.startswith(b"ICC_PROFILE")):
            keep = False                                      # EXIF (GPS, camera), XMP, Photoshop IPTC ...
            removed.append({0xE1: "EXIF/XMP", 0xED: "IPTC"}.get(marker, f"APP{marker - 0xE0}"))
        elif marker == 0xFE:
            keep = False
            removed.append("comment")
        if keep:
            outb += seg
        i += 2 + ln
    return bytes(outb), sorted(set(removed))


PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs",
            b"bKGD", b"acTL", b"fcTL", b"fdAT"}


def clean_png(b: bytes) -> tuple[bytes, list[str]]:
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    i, outb, removed = 8, bytearray(b[:8]), []
    while i < len(b):
        ln = struct.unpack(">I", b[i:i + 4])[0]
        typ = b[i + 4:i + 8]
        chunk = b[i:i + 12 + ln]
        if typ in PNG_KEEP:
            outb += chunk
        else:
            removed.append(typ.decode("latin-1"))
        i += 12 + ln
        if typ == b"IEND":
            break
    return bytes(outb), sorted(set(removed))


def clean_webp(b: bytes) -> tuple[bytes, list[str]]:
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        raise ValueError("not a WebP")
    i, chunks, removed = 12, [], []
    while i + 8 <= len(b):
        typ = b[i:i + 4]
        ln = struct.unpack("<I", b[i + 4:i + 8])[0]
        data = b[i + 8:i + 8 + ln]
        i += 8 + ln + (ln & 1)
        if typ in (b"EXIF", b"XMP "):
            removed.append(typ.decode().strip())
            continue
        if typ == b"VP8X":
            data = bytes([data[0] & ~0x0C]) + data[1:]       # no EXIF / XMP flags any more
        chunks.append(typ + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b""))
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body, removed


# ---------------------------------------------------------------- Office
CORE = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>')


def clean_ooxml(src: Path, dst: Path) -> list[str]:
    removed = []
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            raise ValueError("啟用巨集的檔案：請在 Office 另存成不含巨集的 .docx / .xlsx / .pptx 再分享")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                n = info.filename
                data = zin.read(info)
                low = n.lower()
                if low == "docprops/core.xml":
                    data = CORE.encode()
                    removed.append("作者／修改者／標題等屬性")
                elif low == "docprops/app.xml":
                    t = data.decode("utf-8", "replace")
                    t2 = re.sub(r"<(Company|Manager|HyperlinkBase)>[^<]*</\1>", "", t)
                    if t2 != t:
                        removed.append("公司／主管")
                    data = t2.encode()
                elif low == "docprops/custom.xml":
                    data = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.'
                            'openxmlformats.org/officeDocument/2006/custom-properties"/>').encode()
                    removed.append("自訂屬性")
                elif re.search(r"(comments\d*|commentsExtended|people|persons/person)\.xml$", low) or "threadedcomments" in low:
                    t = data.decode("utf-8", "replace")
                    t2 = re.sub(r'\b(w:author|w:initials|author|displayName|userId|w15:userId)="[^"]*"', r'\1=""', t)
                    t2 = re.sub(r"<author>[^<]*</author>", "<author></author>", t2)
                    if t2 != t:
                        removed.append("註解作者")
                    data = t2.encode()
                zout.writestr(info, data)
    return sorted(set(removed))


def clean_pdf(src: Path, dst: Path) -> list[str]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        raise ValueError("沙盒沒有 pypdf，無法清除 PDF 屬性；請從原始檔重新輸出 PDF") from None
    r = PdfReader(str(src))
    w = PdfWriter()
    for page in r.pages:
        w.add_page(page)
    w.add_metadata({"/Producer": ""})
    try:
        w._root_object.pop("/Metadata", None)               # XMP
    except Exception:  # noqa: BLE001
        pass
    with open(dst, "wb") as fh:
        w.write(fh)
    return ["文件資訊（作者、建立程式）", "XMP"]


def clean(src: Path, outdir: Path) -> dict:
    if not src.is_file():
        out({"error": f"找不到 {src}"}, 1)
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / src.name
    if dst.resolve() == src.resolve():
        out({"error": "輸出資料夾不能是原檔所在的資料夾（不覆寫原檔）"}, 2)
    ext = src.suffix.lower()
    b = src.read_bytes()
    try:
        if ext in (".jpg", ".jpeg"):
            data, removed = clean_jpeg(b)
            dst.write_bytes(data)
        elif ext == ".png":
            data, removed = clean_png(b)
            dst.write_bytes(data)
        elif ext == ".webp":
            data, removed = clean_webp(b)
            dst.write_bytes(data)
        elif ext in (".docx", ".xlsx", ".pptx"):
            removed = clean_ooxml(src, dst)
        elif ext == ".pdf":
            removed = clean_pdf(src, dst)
        else:
            out({"error": f"{ext or '這種檔案'} 沒有可清除的中繼資料；文字檔的金鑰會在上傳時自動遮蔽"}, 1)
    except (ValueError, struct.error, zipfile.BadZipFile) as e:
        if dst.exists():
            dst.unlink()
        out({"error": f"{src.name}：{e}"}, 1)
    return {"copy": str(dst), "removed": removed or [], "bytes": dst.stat().st_size,
            "next": "share_check 這個副本，沒有 ⛔ 再 share_files"}


# ---------------------------------------------------------------- inventory
LEAVE = [
    (re.compile(r"(^|/)(raw|downloads?|sources?|cache|tmp|temp|scratch|tests?|node_modules|__pycache__)(/|$)", re.I),
     "原始下載、暫存或測試檔"),
    (re.compile(r"(^|/)\.[^/]+"), "隱藏檔"),
    (re.compile(r"(^|/)(\.env.*|id_[a-z0-9]+|.*\.(pem|key|p12|pfx|kdbx|ovpn))$", re.I), "憑證或金鑰"),
    (re.compile(r"\.(py|sh|js|mjs|ipynb)$", re.I), "產生成果的程式（除非要分享程式本身）"),
    (re.compile(r"(^|/)notes?(/|$)|\.(log|bak|tmp|orig)$", re.I), "筆記或過程檔"),
    (re.compile(r"\.(exe|dll|bat|cmd|ps1|vbs|msi|dmg|apk|jar|docm|xlsm|pptm)$", re.I), "程式或巨集，不能分享"),
]


def inventory(folder: Path) -> dict:
    if not folder.is_dir():
        out({"error": f"找不到資料夾 {folder}"}, 1)
    items = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or len(items) >= 500:
            continue
        rel = p.relative_to(folder).as_posix()
        why = next((w for rx, w in LEAVE if rx.search(rel)), None)
        items.append({"file": rel, "bytes": p.stat().st_size, "suggest": "leave out" if why else "share",
                      **({"why": why} if why else {})})
    share = [i["file"] for i in items if i["suggest"] == "share"]
    return {"folder": str(folder), "files": items, "share": share,
            "next": "把要分享的檔案交給 share_check；一份報告或頁面只要給那一個檔案"}


def main(argv: list[str]):
    if len(argv) >= 3 and argv[0] == "clean":
        out(clean(Path(argv[1]), Path(argv[2])))
    if len(argv) >= 2 and argv[0] == "inventory":
        out(inventory(Path(argv[1])))
    out({"error": "usage: run.py clean <file> <outdir> | run.py inventory <folder>"}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
