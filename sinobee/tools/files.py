"""File tools: read, write, exact-string edit, list, search."""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from ..types import ImagePart, TextPart
from .registry import ToolContext, tool

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build"}


def resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ctx.config.workspace / p
    p = p.resolve()
    allowed = [Path(a).expanduser().resolve() for a in (ctx.config.get("permissions.allowed_paths") or [])]
    if allowed and not any(p == a or a in p.parents for a in allowed + [ctx.config.workspace.resolve()]):
        raise PermissionError(f"{p} is outside permissions.allowed_paths")
    return p


@tool("read_file", "files", "Read a text file with line numbers (or view an image file).",
      {"properties": {"path": {"type": "string"},
                      "offset": {"type": "integer", "description": "1-based start line"},
                      "limit": {"type": "integer", "description": "max lines (default 400)"}},
       "required": ["path"]})
def read_file(ctx: ToolContext, path: str, offset: int = 1, limit: int = 400):
    p = resolve(ctx, path)
    if p.suffix.lower() in IMAGE_EXT:
        return [TextPart(f"Image file {p}"), ImagePart.from_bytes(p.read_bytes(), IMAGE_EXT[p.suffix.lower()])]
    from ..docread import EXT as DOC_BINARY, extract
    if p.suffix.lower() in DOC_BINARY:      # Office / PDF: their text, not the bytes of the zip or PDF
        lines = extract(p).splitlines()
    else:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(offset or 1))
    chunk = lines[start - 1:start - 1 + int(limit or 400)]
    body = "\n".join(f"{i:>6}\t{l}" for i, l in enumerate(chunk, start))
    more = len(lines) - (start - 1 + len(chunk))
    return f"{p} ({len(lines)} lines)\n{body}" + (f"\n... {more} more lines" if more > 0 else "")


DOC_EXT = {".html", ".htm", ".xhtml", ".svg", ".md", ".markdown", ".tex", ".mdx"}
# LaTeX written inside JSON tool arguments loses its backslashes: "\frac" arrives as a form feed + "rac",
# "\beta" as a backspace + "eta", "\times" as a tab + "imes". Those control characters never belong in a page.
_TEX_CTRL = [("\x0c", "\\f"), ("\x08", "\\b"), ("\x0b", "\\v"), ("\x07", "\\a")]
_TEX_TAB = re.compile(r"\t(?=(?:imes|heta|ext|extbf|extit|au|ilde|riangle|op|o)(?![a-z]))")
_TEX_CR = re.compile(r"\r(?=(?:ho|ightarrow|ight|angle|ceil|floor)(?![a-z]))")


def repair_tex(path, text: str) -> tuple[str, int]:
    """Undo JSON-escape damage to LaTeX in documents (pages and reports); returns (text, repairs)."""
    if Path(str(path)).suffix.lower() not in DOC_EXT or not isinstance(text, str):
        return text, 0
    n = 0
    for bad, good in _TEX_CTRL:
        if bad in text:
            n += text.count(bad)
            text = text.replace(bad, good)
    text, k = _TEX_TAB.subn(r"\\t", text)
    text, j = _TEX_CR.subn(r"\\r", text)
    return text, n + k + j


@tool("write_file", "files", "Create or overwrite a file with the given content (creates parent folders).",
      {"properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
      writes=True)
def write_file(ctx: ToolContext, path: str, content: str):
    p = resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content, fixed = repair_tex(p, content)
    p.write_text(content, encoding="utf-8")
    note = (f" (restored {fixed} LaTeX backslash(es) lost in the JSON arguments: write \\\\frac in JSON for \\frac)"
            if fixed else "")
    return f"wrote {len(content)} chars to {p}{note}"


@tool("edit_file", "files",
      "Replace an exact string in a file. old_string must match exactly once unless replace_all is true. "
      "Prefer this over rewriting whole files.",
      {"properties": {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
                      "replace_all": {"type": "boolean"}}, "required": ["path", "old_string", "new_string"]},
      writes=True)
def edit_file(ctx: ToolContext, path: str, old_string: str, new_string: str, replace_all: bool = False):
    p = resolve(ctx, path)
    text = p.read_text(encoding="utf-8")
    n = text.count(old_string)
    if n == 0:
        return "ERROR: old_string not found (read the file first and copy text exactly)"
    if n > 1 and not replace_all:
        return f"ERROR: old_string occurs {n} times; add context or set replace_all"
    new_string, _ = repair_tex(p, new_string)
    p.write_text(text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1),
                 encoding="utf-8")
    return f"edited {p} ({n if replace_all else 1} replacement(s))"


@tool("list_dir", "files", "List a directory (optionally recursive, depth-limited).",
      {"properties": {"path": {"type": "string"}, "depth": {"type": "integer", "description": "default 1, max 4"}}})
def list_dir(ctx: ToolContext, path: str = ".", depth: int = 1):
    root = resolve(ctx, path)
    depth = max(1, min(int(depth or 1), 4))
    out = []
    for cur, dirs, files in os.walk(root):
        rel = Path(cur).relative_to(root)
        level = 0 if str(rel) == "." else len(rel.parts)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if level >= depth:
            dirs[:] = []
        indent = "  " * level
        for d in dirs:
            out.append(f"{indent}{d}/")
        for f in sorted(files):
            try:
                size = (Path(cur) / f).stat().st_size
            except OSError:
                size = 0
            out.append(f"{indent}{f}  ({size} B)")
        if len(out) > 800:
            out.append("... (truncated)")
            break
    return f"{root}\n" + "\n".join(out)


@tool("search_files", "files", "Search file contents with a regex (like grep -rn). Returns matching lines.",
      {"properties": {"pattern": {"type": "string"}, "path": {"type": "string"},
                      "glob": {"type": "string", "description": "filename filter, e.g. *.py"},
                      "max_results": {"type": "integer"}}, "required": ["pattern"]})
def search_files(ctx: ToolContext, pattern: str, path: str = ".", glob: str | None = None, max_results: int = 100):
    root = resolve(ctx, path)
    rx = re.compile(pattern)
    hits = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if glob and not fnmatch.fnmatch(f, glob):
                continue
            fp = Path(cur) / f
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{fp.relative_to(root)}:{i}: {line.strip()[:200]}")
                        if len(hits) >= max_results:
                            return "\n".join(hits) + "\n... (max results reached)"
            except OSError:
                continue
    return "\n".join(hits) or "no matches"
