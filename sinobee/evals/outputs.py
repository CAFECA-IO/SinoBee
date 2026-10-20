"""What a trial produced, as a report shows it (design draft 2026-10-21, R1/R2): the files with their hashes, where
the judge's evidence sits in them, and an HTML output made into one self-contained page that runs in a sandboxed
srcdoc frame - the trial's own images, scripts and styles inlined, nothing fetched from the network.

The same page is built by sinobee.ai (src/lib/eval-outputs.ts) from the uploaded files; both keep the files as
graded (the hash in the report is the hash of the file the judge read)."""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from pathlib import Path

TEXT_EXT = {".md", ".txt", ".html", ".htm", ".json", ".csv", ".py", ".js", ".ts", ".css", ".svg", ".yaml", ".yml"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
INLINE_MAX = 3_000_000          # an asset bigger than this is left out of the page (and said so)

# no network, no storage: the page runs, but cannot reach anything (the trial ran without a network too)
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
       "font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'")

# the report asks the page to mark the judge's quote: {"sinobeeFind": "<text>"} -> wrap the first match in <mark>
FINDER = """<script>(function(){var last=null;function clear(){if(last&&last.parentNode){var t=document.createTextNode(last.textContent);last.parentNode.replaceChild(t,last);t.parentNode.normalize();}last=null;}
function norm(s){return String(s||'').replace(/\\s+/g,' ').trim();}
window.addEventListener('message',function(e){var d=e.data||{};if(typeof d.sinobeeFind!=='string')return;clear();var q=norm(d.sinobeeFind);if(!q||!document.body)return;
var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,null),n,found=false;while((n=w.nextNode())){var i=n.nodeValue.indexOf(q.slice(0,40));if(i<0)continue;
var r=document.createRange();r.setStart(n,i);r.setEnd(n,Math.min(n.nodeValue.length,i+q.length));var m=document.createElement('mark');m.style.cssText='background:#ffe58a;color:#000;outline:2px solid #f5a623';
try{r.surroundContents(m);last=m;m.scrollIntoView({block:'center',behavior:'smooth'});found=true;}catch(x){}break;}
parent.postMessage({sinobeeFound:found},'*');});})();</script>"""


def kind_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in (".html", ".htm"):
        return "html"
    if ext in IMG_EXT:
        return "image"
    if ext == ".svg":
        return "svg"
    if ext == ".md":
        return "markdown"
    if ext == ".csv":
        return "csv"
    return "text" if ext in TEXT_EXT else "other"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def locate(evidence: str, texts: dict[str, str]) -> dict | None:
    """Where the judge's quote is in the trial's text files: {file, start, end} (characters), else None. Spaces and
    line breaks may differ; a quote cut with "..." is matched by its longest piece."""
    ev = _norm(evidence).strip("「」\"'“”")
    if len(ev) < 4:
        return None
    pieces = sorted((p.strip() for p in re.split(r"\.{3}|…", ev) if len(p.strip()) >= 4), key=len, reverse=True) or [ev]
    for piece in pieces:
        words = [re.escape(w) for w in piece.split(" ")]
        rx = re.compile(r"\s*".join(words) if len(words) == 1 else r"\s+".join(words))
        rx_loose = re.compile(r"\s*".join(re.escape(c) for c in piece.replace(" ", "")))
        for name, text in texts.items():
            m = rx.search(text) or rx_loose.search(text)
            if m:
                return {"file": name, "start": m.start(), "end": m.end(), "piece": piece[:200]}
    return None


def _data_uri(p: Path) -> str | None:
    try:
        if p.stat().st_size > INLINE_MAX:
            return None
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"
    except OSError:
        return None


def _inside(base: Path, ref: str, page: Path) -> Path | None:
    if not ref or re.match(r"^[a-z][a-z0-9+.-]*:|^//|^#", ref, re.I):
        return None                                      # absolute, protocol-relative, data: or an anchor
    ref = ref.split("#", 1)[0].split("?", 1)[0]
    p = (page.parent / ref).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def page(base: Path, rel: str) -> str:
    """The HTML output `rel` of the trial folder `base` as one page for a sandboxed srcdoc frame: relative images,
    scripts and stylesheets of the same trial inlined, the CSP above and the evidence finder added."""
    base = base.resolve()
    f = (base / rel).resolve()
    f.relative_to(base)                                  # ValueError outside the trial
    html = f.read_text(encoding="utf-8", errors="replace")

    def script(m):
        p = _inside(base, m.group(2), f)
        if not p:
            return m.group(0)
        body = p.read_text(encoding="utf-8", errors="replace").replace("</script", "<" + "\\/script")
        return "<script" + m.group(1) + m.group(3) + ">" + body + "</script>"

    def style(m):
        p = _inside(base, m.group(1), f)
        return f"<style>{p.read_text(encoding='utf-8', errors='replace')}</style>" if p else m.group(0)

    def src(m):
        p = _inside(base, m.group(3), f)
        uri = _data_uri(p) if p else None
        return f"{m.group(1)}={m.group(2)}{uri}{m.group(2)}" if uri else m.group(0)

    html = re.sub(r"<script\b([^>]*?)\s+src=[\"']([^\"']+)[\"']([^>]*)>\s*</script>", script, html, flags=re.I)
    html = re.sub(r"<link\b[^>]*?rel=[\"']?stylesheet[\"']?[^>]*?href=[\"']([^\"']+)[\"'][^>]*>", style, html, flags=re.I)
    html = re.sub(r"<link\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*?rel=[\"']?stylesheet[\"']?[^>]*>", style, html, flags=re.I)
    html = re.sub(r"\b(src|poster)=([\"'])([^\"']+)\2", src, html, flags=re.I)
    head = f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
    if re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + head, html, count=1, flags=re.I)
    else:
        html = head + html
    return html + FINDER


def files(base: Path, listed: list[dict]) -> list[dict]:
    """The trial's saved outputs with kind, size and hash (the hash of the file as graded)."""
    out = []
    base = base.resolve()
    for o in listed or []:
        p = (base / o["path"]).resolve()
        try:
            p.relative_to(base)
            data = p.read_bytes()
        except (ValueError, OSError):
            out.append({**o, "kind": kind_of(o["path"]), "missing": True})
            continue
        out.append({"path": o["path"], "kind": kind_of(o["path"]), "bytes": len(data), "sha256": sha256(data)})
    return out
