#!/usr/bin/env python3
"""PDF files: read text and tables, inspect, merge, split, rotate, stamp, fill forms, make PDFs from HTML,
render pages, check.

    run.py info <file.pdf>                          pages, page sizes, metadata, encryption, form fields,
                                                    fonts and whether they are embedded, scanned pages
    run.py text <file.pdf> [--pages 1-3,7]          text per page (layout-aware with pdfplumber when present)
    run.py tables <file.pdf> [--pages …] [--csv DIR] tables as Markdown (pdfplumber), optionally CSV files
    run.py render <file.pdf> <outdir> [--pages …] [--dpi 110]   PNG per page, to look at
    run.py merge <out.pdf> <a.pdf> <b.pdf> …        pages of all inputs in order (bookmarks per input)
    run.py split <file.pdf> <outdir> [--ranges 1-3,4-6,7-]      one PDF per range (default: per page)
    run.py rotate <file.pdf> <out.pdf> <90|180|270> [--pages …]
    run.py stamp <file.pdf> <stamp.pdf> <out.pdf> [--pages …]   overlay page 1 of stamp.pdf (watermark,
                                                    letterhead, signature block) on the pages
    run.py fields <file.pdf>                        fillable form fields: name, type, current value, options
    run.py fill <file.pdf> <values.json> <out.pdf>  fill form fields by name
    run.py from-html <page.html> <out.pdf> [--size A4|Letter] [--landscape]   print a page with Chromium
                                                    (correct CJK, CSS @page, background colours)
    run.py check <file.pdf>                         problems a reader would hit (JSON status/problems)

Every command prints JSON. Exit 0 = ok, 1 = check found problems, 2 = usage / input error.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

logging.getLogger("pdfminer").setLevel(logging.ERROR)


def out(data: dict, code: int = 0):
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str))
    sys.exit(code)


def need(mod="pypdf", pip="pypdf"):
    try:
        __import__(mod)
    except ImportError:
        out({"status": "error", "error": f"{pip} is not installed in this Python",
             "fix": f"{sys.executable} -m pip install {pip}"}, 2)


def pages_arg(args: list[str], n: int, flag="--pages") -> list[int]:
    """'1-3,7,9-' -> zero-based page indexes."""
    if flag not in args:
        return list(range(n))
    spec = args[args.index(flag) + 1]
    idx = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo = int(a) if a else 1
        hi = (int(b) if b else n) if "-" in part else lo
        idx += [i - 1 for i in range(lo, min(hi, n) + 1) if i >= 1]
    return idx


def reader(path: Path):
    need()
    from pypdf import PdfReader
    try:
        r = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001
        out({"status": "error", "error": f"cannot open {path.name}: {type(e).__name__}: {e}"}, 2)
    if r.is_encrypted:
        try:
            if not r.decrypt(""):
                out({"status": "error", "error": "the PDF is password-protected; ask the user for the password "
                                                 "(never guess) and decrypt with pypdf"}, 2)
        except Exception as e:  # noqa: BLE001
            out({"status": "error", "error": f"encrypted PDF: {e}"}, 2)
    return r


# ------------------------------------------------------------------ reading

def _fonts(r) -> tuple[set, set]:
    names, unembedded = set(), set()
    for page in r.pages:
        res = page.get("/Resources") or {}
        fonts = res.get("/Font") if hasattr(res, "get") else None
        if not fonts:
            continue
        for f in fonts.values():
            f = f.get_object()
            name = str(f.get("/BaseFont", "?")).lstrip("/")
            names.add(name)
            desc = f.get("/FontDescriptor")
            if f.get("/Subtype") == "/Type0":
                kids = f.get("/DescendantFonts")
                if kids:
                    desc = kids[0].get_object().get("/FontDescriptor")
            if f.get("/Subtype") == "/Type3":
                continue
            d = desc.get_object() if desc is not None else None
            if d is None or not any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                unembedded.add(name)
    return names, unembedded


def info(path: Path):
    r = reader(path)
    sizes = {}
    scanned = []
    for i, p in enumerate(r.pages, 1):
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        key = f"{w / 72 * 25.4:.0f}×{h / 72 * 25.4:.0f} mm"
        sizes[key] = sizes.get(key, 0) + 1
        try:
            t = p.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if len(t.strip()) < 15 and "/XObject" in (p.get("/Resources") or {}):
            scanned.append(i)
    fields = r.get_fields() or {}
    names, unembedded = _fonts(r)
    meta = {k.lstrip("/"): str(v) for k, v in (r.metadata or {}).items()}
    out({"status": "ok", "pages": len(r.pages), "page_sizes": sizes, "metadata": meta,
         "form_fields": len(fields), "fonts": sorted(names)[:40], "fonts_not_embedded": sorted(unembedded),
         "image_only_pages": scanned[:50],
         "hint": "image-only pages have no text layer: render them and read the images, or OCR"
         if scanned else ""})


def text(path: Path, args):
    r = reader(path)
    idx = pages_arg(args, len(r.pages))
    pages = []
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for i in idx:
                pages.append({"page": i + 1, "text": pdf.pages[i].extract_text(layout=False) or ""})
    except ImportError:
        for i in idx:
            pages.append({"page": i + 1, "text": r.pages[i].extract_text() or ""})
    empty = [p["page"] for p in pages if len(p["text"].strip()) < 15]
    out({"status": "ok", "pages": pages, "empty_pages": empty,
         "hint": "pages without text are probably scanned: render them and look" if empty else ""})


def tables(path: Path, args):
    need("pdfplumber", "pdfplumber")
    import csv
    import pdfplumber
    found = []
    csv_dir = Path(args[args.index("--csv") + 1]) if "--csv" in args else None
    with pdfplumber.open(str(path)) as pdf:
        for i in pages_arg(args, len(pdf.pages)):
            for k, t in enumerate(pdf.pages[i].extract_tables(), 1):
                rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in t if row]
                if not rows:
                    continue
                md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
                md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                item = {"page": i + 1, "table": k, "rows": len(rows), "markdown": "\n".join(md)}
                if csv_dir:
                    csv_dir.mkdir(parents=True, exist_ok=True)
                    f = csv_dir / f"p{i + 1}_t{k}.csv"
                    with f.open("w", newline="", encoding="utf-8-sig") as fh:
                        csv.writer(fh).writerows(rows)
                    item["csv"] = str(f)
                found.append(item)
    out({"status": "ok", "tables": found,
         "hint": "" if found else "no ruled tables found; try text, or render the page and read it"})


def render(path: Path, outdir: Path, args):
    outdir.mkdir(parents=True, exist_ok=True)
    dpi = int(args[args.index("--dpi") + 1]) if "--dpi" in args else 110
    n = len(reader(path).pages)
    idx = pages_arg(args, n)[:60]
    made = []
    if shutil.which("pdftoppm"):
        for i in idx:
            prefix = outdir / f"page-{i + 1}"
            subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(i + 1), "-l", str(i + 1), "-singlefile",
                            str(path), str(prefix)], capture_output=True, timeout=120)
            if prefix.with_suffix(".png").is_file():
                made.append(str(prefix.with_suffix(".png")))
    else:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            out({"status": "error", "error": "no renderer: install poppler (pdftoppm) or pypdfium2",
                 "fix": f"{sys.executable} -m pip install pypdfium2"}, 2)
        doc = pdfium.PdfDocument(str(path))
        for i in idx:
            f = outdir / f"page-{i + 1}.png"
            doc[i].render(scale=dpi / 72).to_pil().save(f)
            made.append(str(f))
    out({"status": "ok", "pages": made, "next": "read_file the page images to look at them"})


# ------------------------------------------------------------------ changing

def merge(dst: Path, srcs: list[Path]):
    need()
    from pypdf import PdfWriter
    w = PdfWriter()
    for s in srcs:
        start = len(w.pages)
        w.append(str(s))
        w.add_outline_item(s.stem, start)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as fh:
        w.write(fh)
    out({"status": "ok", "file": str(dst), "pages": len(w.pages)})


def split(path: Path, outdir: Path, args):
    from pypdf import PdfWriter
    r = reader(path)
    n = len(r.pages)
    ranges = args[args.index("--ranges") + 1].split(",") if "--ranges" in args else [str(i) for i in range(1, n + 1)]
    outdir.mkdir(parents=True, exist_ok=True)
    made = []
    for rg in ranges:
        idx = pages_arg(["--pages", rg], n)
        if not idx:
            continue
        w = PdfWriter()
        for i in idx:
            w.add_page(r.pages[i])
        f = outdir / (f"{path.stem}_p{idx[0] + 1}" + (f"-{idx[-1] + 1}" if len(idx) > 1 else "") + ".pdf")
        with f.open("wb") as fh:
            w.write(fh)
        made.append(str(f))
    out({"status": "ok", "files": made})


def rotate(path: Path, dst: Path, deg: int, args):
    from pypdf import PdfWriter
    r = reader(path)
    w = PdfWriter()
    sel = set(pages_arg(args, len(r.pages)))
    for i, p in enumerate(r.pages):
        if i in sel:
            p.rotate(deg)
        w.add_page(p)
    with dst.open("wb") as fh:
        w.write(fh)
    out({"status": "ok", "file": str(dst), "rotated": len(sel)})


def stamp(path: Path, stamp_pdf: Path, dst: Path, args):
    from pypdf import PdfWriter
    r = reader(path)
    st = reader(stamp_pdf).pages[0]
    w = PdfWriter()
    sel = set(pages_arg(args, len(r.pages)))
    for i, p in enumerate(r.pages):
        if i in sel:
            p.merge_page(st)
        w.add_page(p)
    with dst.open("wb") as fh:
        w.write(fh)
    out({"status": "ok", "file": str(dst), "stamped": len(sel)})


def fields(path: Path):
    r = reader(path)
    fs = r.get_fields() or {}
    items = []
    for name, f in fs.items():
        kind = {"/Tx": "text", "/Btn": "checkbox/radio", "/Ch": "choice", "/Sig": "signature"}.get(f.get("/FT"), f.get("/FT"))
        opts = f.get("/_States_") or f.get("/Opt") or []
        items.append({"name": name, "type": kind, "value": str(f.get("/V", "")),
                      "options": [str(o) for o in opts][:20]})
    out({"status": "ok", "fields": items,
         "hint": "" if items else "no fillable fields: write text onto the page with `stamp` (make the stamp "
                                  "PDF from HTML positioned with CSS) instead"})


def fill(path: Path, values_path: Path, dst: Path):
    from pypdf import PdfWriter
    r = reader(path)
    values = json.loads(values_path.read_text(encoding="utf-8"))
    names = set((r.get_fields() or {}).keys())
    unknown = [k for k in values if k not in names]
    if unknown:
        out({"status": "error", "error": f"no such fields: {unknown[:10]}", "fields": sorted(names)[:60]}, 2)
    w = PdfWriter(clone_from=r)
    for page in w.pages:
        w.update_page_form_field_values(page, {k: v for k, v in values.items()}, auto_regenerate=False)
    w.set_need_appearances_writer(True)
    with dst.open("wb") as fh:
        w.write(fh)
    out({"status": "ok", "file": str(dst), "filled": len(values),
         "next": "render the filled pages and look: every value in its box"})


def from_html(src: Path, dst: Path, args):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        out({"status": "error", "error": "playwright is not installed: cannot print HTML to PDF",
             "fix": f"{sys.executable} -m pip install playwright && {sys.executable} -m playwright install chromium"}, 2)
    size = args[args.index("--size") + 1] if "--size" in args else "A4"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        try:
            pg = b.new_page()
            pg.goto(src.resolve().as_uri(), wait_until="networkidle")
            pg.emulate_media(media="print")
            pg.pdf(path=str(dst), format=size, landscape="--landscape" in args, print_background=True,
                   prefer_css_page_size=True, margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
        finally:
            b.close()
    out({"status": "ok", "file": str(dst), "next": f"run.py check {dst} and render it"})


def check(path: Path):
    problems, notes = [], []
    r = reader(path)
    n = len(r.pages)
    if n == 0:
        problems.append("the PDF has no pages")
    names, unembedded = _fonts(r)
    if unembedded:
        problems.append("fonts not embedded (text may show in another font or as boxes on other computers): "
                        + ", ".join(sorted(unembedded)[:6]))
    empty, sizes = [], set()
    for i, p in enumerate(r.pages, 1):
        sizes.add((round(float(p.mediabox.width)), round(float(p.mediabox.height))))
        try:
            t = p.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        res = p.get("/Resources") or {}
        if len(t.strip()) < 3 and "/XObject" not in res:
            empty.append(i)
        if re.search(r"lorem ipsum|\bTODO\b|替換成|請填入", t, re.I):
            problems.append(f"page {i}: placeholder text left")
    if empty:
        problems.append(f"blank page(s): {empty[:10]}")
    if len(sizes) > 1:
        notes.append(f"mixed page sizes: {sorted(sizes)[:4]} (fine for landscape inserts, odd otherwise)")
    try:
        if "�" in "".join((p.extract_text() or "") for p in r.pages[:5]):
            notes.append("some text extracts as � - copy/search may fail for those glyphs")
    except Exception:  # noqa: BLE001
        pass
    out({"status": "fix" if problems else "ok", "problems": problems, "notes": notes, "pages": n},
        1 if problems else 0)


def main(argv):
    if not argv:
        out({"status": "error", "error": __doc__}, 2)
    cmd, a = argv[0], argv[1:]
    try:
        if cmd == "info" and a:
            info(Path(a[0]))
        elif cmd == "text" and a:
            text(Path(a[0]), a)
        elif cmd == "tables" and a:
            tables(Path(a[0]), a)
        elif cmd == "render" and len(a) >= 2:
            render(Path(a[0]), Path(a[1]), a)
        elif cmd == "merge" and len(a) >= 3:
            merge(Path(a[0]), [Path(x) for x in a[1:]])
        elif cmd == "split" and len(a) >= 2:
            split(Path(a[0]), Path(a[1]), a)
        elif cmd == "rotate" and len(a) >= 3:
            rotate(Path(a[0]), Path(a[1]), int(a[2]), a)
        elif cmd == "stamp" and len(a) >= 3:
            stamp(Path(a[0]), Path(a[1]), Path(a[2]), a)
        elif cmd == "fields" and a:
            fields(Path(a[0]))
        elif cmd == "fill" and len(a) == 3:
            fill(Path(a[0]), Path(a[1]), Path(a[2]))
        elif cmd == "from-html" and len(a) >= 2:
            from_html(Path(a[0]), Path(a[1]), a)
        elif cmd == "check" and len(a) == 1:
            check(Path(a[0]))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        out({"status": "error", "error": f"{type(e).__name__}: {e}"}, 2)
    out({"status": "error", "error": "usage:\n" + __doc__}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
