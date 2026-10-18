"""Text out of Office and PDF files, with no third-party packages for Office.

read_file used to decode a .docx / .xlsx / .pptx / .pdf as UTF-8 and hand the model a page of binary noise. Office
files are zip archives of XML, so their text is read with the standard library: paragraphs (with heading levels and
list markers), tables as Markdown, comments and tracked changes for Word; cells with their formulas for Excel;
slide titles, text, tables and speaker notes for PowerPoint. PDFs use pypdf when installed, else poppler's
pdftotext, else say how to get either.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

OFFICE = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx"}
EXT = OFFICE | {".pdf"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_CELLS = 20000


def extract(path: Path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_text(path)
    try:
        with zipfile.ZipFile(path) as z:
            if ext.startswith(".doc"):
                return docx_text(z)
            if ext.startswith(".xl"):
                return xlsx_text(z)
            return pptx_text(z)
    except zipfile.BadZipFile:
        return f"[{path.name} is not a valid Office file (not a zip archive); it may be an old binary .doc/.xls/.ppt " \
               f"- convert it with LibreOffice: soffice --headless --convert-to {ext.lstrip('.')} <file>]"


def _xml(z: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None


def _rels(z: zipfile.ZipFile, part: str) -> dict:
    d, f = part.rsplit("/", 1)
    root = _xml(z, f"{d}/_rels/{f}.rels")
    if root is None:
        return {}
    out = {}
    for r in root.iter(f"{PR}Relationship"):
        tgt = r.get("Target", "")
        out[r.get("Id")] = tgt if tgt.startswith("/") else f"{d}/{tgt}"
    return {k: re.sub(r"[^/]+/\.\./", "", v).lstrip("/") for k, v in out.items()}


# ---------------------------------------------------------------- Word

def _wtext(el) -> str:
    out = []
    for n in el.iter():
        if n.tag == f"{W}t":
            out.append(n.text or "")
        elif n.tag == f"{W}delText":
            out.append(f"[-{n.text or ''}-]")
        elif n.tag in (f"{W}tab",):
            out.append("\t")
        elif n.tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
    return "".join(out)


def _para(p) -> str:
    # tracked insertions show as {+…+}, deletions as [-…-]
    parts = []
    for child in p:
        if child.tag == f"{W}ins":
            parts.append("{+" + _wtext(child) + "+}")
        elif child.tag in (f"{W}r", f"{W}hyperlink", f"{W}del", f"{W}smartTag", f"{W}sdt"):
            parts.append(_wtext(child))
    text = "".join(parts)
    ppr = p.find(f"{W}pPr")
    style = ppr.find(f"{W}pStyle").get(f"{W}val") if ppr is not None and ppr.find(f"{W}pStyle") is not None else ""
    m = re.match(r"(?i)heading\s*(\d)|title$", style or "")
    if m and text.strip():
        return "#" * int(m.group(1) or 1) + " " + text
    if text.strip() and ((ppr is not None and ppr.find(f"{W}numPr") is not None) or re.match(r"(?i)list", style or "")):
        lvl = ppr.find(f"{W}numPr/{W}ilvl")
        return "  " * int(lvl.get(f"{W}val", 0) if lvl is not None else 0) + "- " + text
    return text


def _table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [[c.replace("|", "\\|").replace("\n", " ") for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def docx_text(z: zipfile.ZipFile) -> str:
    root = _xml(z, "word/document.xml")
    if root is None:
        return "[no word/document.xml in this file]"
    body = root.find(f"{W}body")
    out = []
    for el in body if body is not None else []:
        if el.tag == f"{W}p":
            out.append(_para(el))
        elif el.tag == f"{W}tbl":
            rows = [["\n".join(_para(p) for p in tc.iter(f"{W}p")).strip() for tc in tr.iter(f"{W}tc")]
                    for tr in el.iter(f"{W}tr")]
            out.append(_table(rows))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    com = _xml(z, "word/comments.xml")
    if com is not None:
        notes = [f"- {c.get(f'{W}author', '?')}: {_wtext(c).strip()}" for c in com.iter(f"{W}comment")]
        if notes:
            text += "\n\n## Comments\n" + "\n".join(notes)
    if "{+" in text or "[-" in text:
        text = "(tracked changes: {+inserted+} [-deleted-])\n\n" + text
    return text


# ---------------------------------------------------------------- Excel

def xlsx_text(z: zipfile.ZipFile) -> str:
    shared = []
    sst = _xml(z, "xl/sharedStrings.xml")
    if sst is not None:
        shared = ["".join(t.text or "" for t in si.iter(f"{S}t")) for si in sst.iter(f"{S}si")]
    wb = _xml(z, "xl/workbook.xml")
    rels = _rels(z, "xl/workbook.xml")
    out, cells = [], 0
    for sh in (wb.iter(f"{S}sheet") if wb is not None else []):
        part = rels.get(sh.get(f"{R}id"), "")
        root = _xml(z, part)
        if root is None:
            continue
        dim = root.find(f"{S}dimension")
        out.append(f"## Sheet: {sh.get('name')}" + (f" ({dim.get('ref')})" if dim is not None else ""))
        for row in root.iter(f"{S}row"):
            vals = []
            for c in row.iter(f"{S}c"):
                v, f = c.find(f"{S}v"), c.find(f"{S}f")
                t = c.get("t")
                if t == "s" and v is not None:
                    val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{S}t"))
                else:
                    val = v.text if v is not None and v.text is not None else ""
                if f is not None and f.text:
                    val = f"={f.text}" + (f" → {val}" if val else " (not calculated)")
                if val != "":
                    vals.append(f"{c.get('r')}: {val}")
                    cells += 1
            if vals:
                out.append("  ".join(vals))
            if cells > MAX_CELLS:
                out.append(f"… stopped after {MAX_CELLS} cells")
                return "\n".join(out)
    return "\n".join(out)


# ---------------------------------------------------------------- PowerPoint

def _shape_text(sp) -> list[str]:
    out = []
    for p in sp.iter(f"{A}p"):
        t = "".join(x.text or "" for x in p.iter(f"{A}t"))
        if t.strip():
            lvl = p.find(f"{A}pPr")
            out.append("  " * int(lvl.get("lvl", 0) if lvl is not None else 0) + t)
    return out


def pptx_text(z: zipfile.ZipFile) -> str:
    pres = _xml(z, "ppt/presentation.xml")
    rels = _rels(z, "ppt/presentation.xml")
    out = []
    ids = [rels.get(s.get(f"{R}id"), "") for s in pres.iter(f"{P}sldId")] if pres is not None else []
    for n, part in enumerate(ids, 1):
        root = _xml(z, part)
        if root is None:
            continue
        title, body = "", []
        for sp in root.iter(f"{P}sp"):
            ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            lines = _shape_text(sp)
            if ph is not None and ph.get("type") in ("title", "ctrTitle") and not title:
                title = " ".join(lines)
            else:
                body += lines
        for tbl in root.iter(f"{A}tbl"):
            body.append(_table([["".join(t.text or "" for t in tc.iter(f"{A}t")) for tc in tr.iter(f"{A}tc")]
                                for tr in tbl.iter(f"{A}tr")]))
        out.append(f"## Slide {n}: {title}".rstrip(": ") + ("\n" + "\n".join(body) if body else ""))
        notes = next((v for k, v in _rels(z, part).items() if "notesSlide" in v), None)
        nroot = _xml(z, notes) if notes else None
        if nroot is not None:
            nt = [t for sp in nroot.iter(f"{P}sp") if (sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph") is not None and
                  sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph").get("type") == "body") for t in _shape_text(sp)]
            if nt:
                out.append("Notes: " + " ".join(nt))
    return "\n\n".join(out)


# ---------------------------------------------------------------- PDF

def pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [f"## Page {i}\n{(pg.extract_text() or '').strip()}" for i, pg in enumerate(reader.pages, 1)]
        text = "\n\n".join(pages)
    except ImportError:
        exe = shutil.which("pdftotext")
        if not exe:
            return ("[cannot read PDF text here: install pypdf into SinoBee's Python (pip install \"sinobee[office]\") "
                    "or poppler's pdftotext]")
        res = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, text=True, timeout=120)
        text = "\n\n".join(f"## Page {i}\n{t.strip()}" for i, t in enumerate(res.stdout.split("\f"), 1) if t.strip())
    except Exception as e:  # noqa: BLE001 - encrypted / broken PDF
        return f"[cannot read this PDF: {type(e).__name__}: {e}]"
    if len(re.sub(r"## Page \d+|\s", "", text)) < 20:
        text += ("\n\n[almost no text: this PDF is probably scanned images. Look at its pages instead "
                 "(pdf-documents skill: render) or OCR them.]")
    return text


# ---------------------------------------------------------------- pictures of pages

SOFFICE_MAC = "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice") or (SOFFICE_MAC if Path(SOFFICE_MAC).exists() else None)


def render_pages(path: Path, outdir: Path, pages: int = 4, dpi: int = 70) -> list[Path]:
    """PNG pictures of the first pages of a PDF or Office file (LibreOffice converts Office to PDF first; poppler's
    pdftoppm or pypdfium2 draws the pages). Returns [] when the tools are not installed."""
    path, outdir = Path(path), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pdf = path
    if path.suffix.lower() in OFFICE:
        exe = soffice()
        if not exe:
            return []
        res = subprocess.run([exe, "--headless", f"-env:UserInstallation=file://{outdir.resolve()}/.lo",
                              "--convert-to", "pdf", "--outdir", str(outdir), str(path)],
                             capture_output=True, text=True, timeout=180)
        pdf = outdir / (path.stem + ".pdf")
        if res.returncode != 0 or not pdf.is_file():
            return []
    elif path.suffix.lower() != ".pdf":
        return []
    exe = shutil.which("pdftoppm")
    if exe:
        subprocess.run([exe, "-png", "-r", str(dpi), "-f", "1", "-l", str(pages), str(pdf), str(outdir / "page")],
                       capture_output=True, timeout=180)
        return sorted(outdir.glob("page*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    doc, out = pdfium.PdfDocument(str(pdf)), []
    for i in range(min(pages, len(doc))):
        f = outdir / f"page-{i + 1}.png"
        doc[i].render(scale=dpi / 72).to_pil().save(f)
        out.append(f)
    return out
