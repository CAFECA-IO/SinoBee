"""Intermediate products: files a tool call created or reported, so the UI can link them while the task runs."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..fileaccess import FileAccessError, resolve_file

KINDS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "svg"},
    "video": {"mp4", "webm", "mov", "m4v", "mkv"},
    "audio": {"mp3", "wav", "m4a", "ogg", "flac", "aac", "aiff"},
    "doc": {"pdf", "md", "txt", "csv", "tsv", "xlsx", "xls", "docx", "doc", "pptx", "ppt", "html", "htm", "srt",
            "json", "yaml", "yml", "zip", "sql", "log", "ipynb"},
}
EXT = {e: k for k, exts in KINDS.items() for e in exts}
# a path-ish token ending in a known extension (quotes, brackets and markdown punctuation stripped)
PATH_RE = re.compile(r"""(?:(?<=[\s"'`(\[<=:])|^)((?:~|/|\.{1,2}/|[\w㐀-鿿-])[^\s"'`<>()\[\]{}|,;]*?"""
                     r"""\.(?:%s))(?=$|[\s"'`<>()\[\]{}|,;:.!?])""" % "|".join(sorted(EXT, key=len, reverse=True)),
                     re.I | re.M)
ARG_KEYS = ("path", "file", "file_path", "output", "out", "dest", "destination", "filename")
WRITE_TOOLS = {"write_file", "edit_file"}
SKIP_PARTS = {"node_modules", ".git", "__pycache__", ".venv", "site-packages"}
MAX_PER_CALL = 12


def kind_of(path: str) -> str | None:
    return EXT.get(Path(path).suffix.lower().lstrip("."))


def candidates(tool: str, args: dict, text: str) -> list[str]:
    out: list[str] = []
    for k in ARG_KEYS:
        v = (args or {}).get(k)
        if isinstance(v, str) and kind_of(v):
            out.append(v)
    if tool in ("read_file", "list_dir", "search_files", "skill_view"):
        return []                                        # reading is not producing
    for m in PATH_RE.finditer(text or ""):
        out.append(m.group(1).rstrip(".,"))
    # JSON results (scripts) often carry relative paths with a base folder, e.g. {"folder": "...", "clip": "..."}
    data = None
    start = (text or "").find("{")
    if start != -1:      # shell / skill_run results start with an "[exit 0] cwd=…" header
        try:
            data = json.loads(text[start:])
        except (json.JSONDecodeError, ValueError):
            data = None
    if isinstance(data, dict) and isinstance(data.get("folder"), str):
        base = data["folder"]
        out += [f"{base}/{p}" for p in list(out) if not p.startswith(("/", "~", base))]
    return out


def collect(config, tool: str, args: dict, text: str, seen: set[str], since: float | None = None) -> list[dict]:
    """Existing, viewable files mentioned by a tool call that were not reported before (newest first)."""
    found = []
    # folders named in the arguments (e.g. `render videos/duel`) are bases for paths the script prints relative
    bases = []
    for v in (args or {}).values():
        for tok in str(v).split() if isinstance(v, str) else []:
            tok = tok.strip("\"'")
            if not tok or len(tok) > 1024:
                continue
            try:
                d = Path(tok).expanduser()
                d = d if d.is_absolute() else config.workspace / d
                is_file, is_dir = d.is_file(), d.is_dir()
            except (OSError, ValueError, RuntimeError):   # a sentence is not a path; "~250" is no user's home
                continue
            d = Path(tok).expanduser()
            d = d if d.is_absolute() else config.workspace / d
            if d.name in ("project.json", "plan.json"):
                d = d.parent
            if d.is_file() and d.suffix in (".mjs", ".js", ".py", ".sh", ".ts"):
                d = d.parent          # `node figures/rig/build.mjs` prints "wrote rig.html" next to the script
            if d.is_dir() and d.resolve() != config.workspace.resolve():
                bases.append(str(d))
    raws = []
    for raw in candidates(tool, args, text):
        raws.append(raw)
        if not raw.startswith(("/", "~")):
            raws += [f"{b}/{raw}" for b in bases]
    for raw in raws:
        if any(part in SKIP_PARTS for part in Path(raw).parts):
            continue
        try:
            p = resolve_file(config, raw)
        except (FileAccessError, OSError, ValueError, RuntimeError):
            continue
        key = str(p)
        if key in seen:
            continue
        st = p.stat()
        if since is not None and st.st_mtime < since - 1 and tool not in WRITE_TOOLS:
            continue          # an old file merely mentioned in output is not a product of this task
        seen.add(key)
        try:
            rel = str(p.relative_to(config.workspace.resolve()))
        except ValueError:
            rel = key
        found.append({"path": rel, "name": p.name, "kind": kind_of(p.name), "size": st.st_size,
                      "modified": st.st_mtime})
        if len(found) >= MAX_PER_CALL:
            break
    return found
