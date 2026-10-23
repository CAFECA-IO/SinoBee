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


def collect(config, tool: str, args: dict, text: str, seen: set[str], since: float | None = None,
            conversation: str | None = None) -> list[dict]:
    """Existing, viewable files mentioned by a tool call that were not reported before (newest first)."""
    from ..sandbox import for_config
    pmap = for_config(config, conversation)
    found = []
    # folders named in the arguments (e.g. `render videos/duel`) are bases for paths the script prints relative
    bases = []
    for v in (args or {}).values():
        for tok in str(v).split() if isinstance(v, str) else []:
            tok = tok.strip("\"'")
            if not tok or len(tok) > 1024:
                continue
            try:
                d = pmap.to_host(tok, config.workspace)      # a sandbox path means a file on this computer
                is_file, is_dir = d.is_file(), d.is_dir()
            except (OSError, ValueError, RuntimeError):   # a sentence is not a path; "~250" is no user's home
                continue
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


# ------------------------------------------------------------------ the files a turn hands over
JUNK_DIRS = {"test", "tests", "tmp", "temp", "scratch", ".cache", "node_modules", "__pycache__", ".git"}
JUNK_NAME = re.compile(r"(\.bak|\.orig|\.old|\.tmp|~|\.swp)$|\.(before|broken|backup)[-.\w]*$|^(tmp|temp|scratch)[-_.]",
                       re.I)
MAX_KEY = 6


def is_junk(path: str) -> bool:
    """A backup, scratch or test file: listed under 「其他檔案」, never as a deliverable."""
    p = Path(str(path))
    return any(part.lower() in JUNK_DIRS for part in p.parts[:-1]) or bool(JUNK_NAME.search(p.name))


def key_files(artifacts: list[dict], final_text: str = "", milestones: list | None = None,
              checklist: list | None = None, written: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(deliverables, other files) among the files a turn reported.

    A deliverable is named by the work itself: a milestone's declared or reported outputs, a file given as evidence in
    the acceptance checklist, or a file the final answer mentions. Without any of these, files the turn wrote with the
    file tools that are not backups, tests or scratch. At most MAX_KEY; the rest stay available as other files."""
    seen, files = set(), []
    for a in artifacts or []:
        if a.get("path") and a["path"] not in seen:
            seen.add(a["path"])
            files.append(a)
    named: list[str] = []
    for m in milestones or []:
        named += [str(x) for x in (m.get("result_outputs") or m.get("outputs") or [])]
    for it in checklist or []:
        named.append(str(it.get("evidence") or ""))
    named.append(final_text or "")
    blob = "\n".join(named)

    def mentioned(a: dict) -> bool:
        return a["path"] in blob or (a.get("name") and re.search(r"(?<![\w.-])" + re.escape(a["name"]) + r"(?![\w-])",
                                                                     blob) is not None)
    key = [a for a in files if not is_junk(a["path"]) and mentioned(a)]
    if not key:
        key = [a for a in files if not is_junk(a["path"]) and (written is None or any(
            w == a["path"] or w.endswith("/" + a["path"]) for w in written))]
    key = key[:MAX_KEY]
    ids = {a["path"] for a in key}
    return key, [a for a in files if a["path"] not in ids]
