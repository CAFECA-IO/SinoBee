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
                d = pmap.to_host(tok, pmap.workspace or config.workspace)   # a sandbox path means a file here
                is_file, is_dir = d.is_file(), d.is_dir()
            except (OSError, ValueError, RuntimeError):   # a sentence is not a path; "~250" is no user's home
                continue
            if d.name in ("project.json", "plan.json"):
                d = d.parent
            if d.is_file() and d.suffix in (".mjs", ".js", ".py", ".sh", ".ts"):
                d = d.parent          # `node figures/rig/build.mjs` prints "wrote rig.html" next to the script
            if d.is_dir() and d.resolve() not in (config.workspace.resolve(), Path(pmap.workspace or config.workspace).resolve()):
                bases.append(str(d))
    ws = Path(pmap.workspace or config.workspace)
    if ws.resolve() != config.workspace.resolve():
        bases.append(str(ws))                 # a name without a folder is in the conversation's own /workspace
    raws = []
    for raw in candidates(tool, args, text):
        raws.append(raw)
        if not raw.startswith(("/", "~")):
            raws += [f"{b}/{raw}" for b in bases]
    for raw in raws:
        if any(part in SKIP_PARTS for part in Path(raw).parts):
            continue
        try:
            p = resolve_file(config, raw, conversation)
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


RAW_DIRS = {"raw", "download", "downloads", "source", "sources", "cache", "vendor", "fetched"}
OUT_DIRS = {"report", "reports", "output", "outputs", "deliverable", "deliverables", "dist", "final"}
WORK_DIRS = {"notes", "work", "working", "drafts", "draft"}
CODE_EXT = {".py", ".js", ".mjs", ".ts", ".sh", ".r", ".ipynb"}


SOURCE_EXT = {".html", ".htm", ".pdf", ".xml", ".xhtml"}


def _same_file(w: str, path: str) -> bool:
    """A written file (this computer's real path) and a reported one (often the sandbox's /workspace/...): the same
    when the path is equal or the last two parts (folder and name) match."""
    if w == str(path):
        return True
    a, b = Path(str(w)).parts, Path(str(path)).parts
    return len(a) >= 2 and len(b) >= 2 and a[-2:] == b[-2:]


def fetched_source(path: str, written: set[str] | None = None) -> bool:
    """A file the task downloaded as material (a saved web page or PDF under data/, raw/ ...) rather than made: not
    written with the file tools, and either in a raw-data folder or a page/PDF outside the output folders."""
    p = Path(str(path))
    if written and any(_same_file(w, path) for w in written):
        return False
    parts = [x.lower() for x in p.parts[:-1]]
    if any(x in RAW_DIRS for x in parts):
        return True
    return p.suffix.lower() in SOURCE_EXT and not any(x in OUT_DIRS for x in parts)


def key_files(artifacts: list[dict], final_text: str = "", milestones: list | None = None,
              checklist: list | None = None, written: set[str] | None = None,
              exists=None) -> tuple[list[dict], list[dict]]:
    """(deliverables, other files) among the files a turn reported.

    A deliverable is named by the work itself: a milestone's declared or reported outputs, a file given as evidence in
    the acceptance checklist, or a file the final answer mentions. Without any of these, files the turn wrote with the
    file tools that are not backups, tests or scratch. At most MAX_KEY, the most deliverable first; the rest stay
    available as other files.

    Ranked, not taken in the order the files appeared: the 00935 task (2026-10-30) showed two downloaded PDFs, three
    raw web pages it had already removed (「找不到檔案」) and a note as 關鍵產出, while the report and its charts - named
    in the answer, the milestones and the checklist - were cut off below them. `exists(path)` drops files that are gone."""
    seen, files = set(), []
    for a in artifacts or []:
        if a.get("path") and a["path"] not in seen:
            seen.add(a["path"])
            files.append(a)
    outputs = "\n".join(str(x) for m in milestones or [] for x in (m.get("result_outputs") or m.get("outputs") or []))
    evidence = "\n".join(str(it.get("evidence") or "") for it in checklist or [])
    answer = final_text or ""

    def hits(a: dict, blob: str) -> int:
        n = blob.count(a["path"]) if a["path"] else 0
        if not n and a.get("name"):
            n = len(re.findall(r"(?<![\w.-])" + re.escape(a["name"]) + r"(?![\w-])", blob))
        return n

    def was_written(a: dict) -> bool:
        return written is not None and any(_same_file(w, a["path"]) for w in written)

    def gone(a: dict) -> bool:
        if exists is None:
            return False
        try:
            return not exists(a["path"])
        except Exception:  # noqa: BLE001 - when unsure, keep it
            return False

    def score(a: dict) -> float:
        parts = [x.lower() for x in Path(str(a["path"])).parts[:-1]]
        sc = 4.0 * bool(hits(a, answer)) + 2.0 * bool(hits(a, outputs)) + min(3, hits(a, evidence))
        sc += 2.0 * was_written(a)
        sc += 2.0 * (any(x in OUT_DIRS for x in parts) or "report" in Path(str(a["path"])).name.lower())
        sc -= 3.0 * any(x in RAW_DIRS for x in parts)
        sc -= 4.0 * fetched_source(a["path"], written)     # material it downloaded, even when it names it
        sc -= 1.5 * any(x in WORK_DIRS for x in parts)
        # the script that made a chart is how, not what: below the report and its pictures unless code was the task
        sc -= 2.5 * (Path(str(a["path"])).suffix.lower() in CODE_EXT and not code_task)
        return sc

    live = [a for a in files if not is_junk(a["path"]) and not gone(a)]
    code_task = bool(live) and all(Path(str(a["path"])).suffix.lower() in CODE_EXT for a in live)
    key = [a for a in live if hits(a, "\n".join((outputs, evidence, answer)))]
    if not key:
        key = [a for a in live if was_written(a)]
    order = {id(a): i for i, a in enumerate(key)}
    key = sorted(key, key=lambda a: (-score(a), order[id(a)]))[:MAX_KEY]
    ids = {a["path"] for a in key}
    return key, [a for a in files if a["path"] not in ids]


# ------------------------------------------------------------------ where a turn's files are on this computer
def places(config, conversation: str | None, items: list[dict], limit: int = 4) -> list[dict]:
    """The folders on this computer that hold a turn's files: [{"path", "shown", "kind", "files"}], most files first.
    kind: workspace (the conversation's /workspace), folder (a folder you gave, written directly or read-only),
    review (a folder you gave for review: the files are in its copy until you apply them - path is your folder)."""
    from ..sandbox import executor_kind
    from ..sandbox.workspaces import shown
    roots: list[tuple[Path, Path, str]] = []          # (where the files are, what to show, kind)
    ws = Path(config.workspace)
    if conversation and executor_kind(config) == "sandbox":
        from ..sandbox.folders import folders_for
        from ..sandbox.staging import copy_dir
        from ..sandbox.workspaces import dir_of
        ws = dir_of(config, conversation)
        for f in folders_for(config, conversation):
            if f["mode"] == "review":
                roots.append((copy_dir(config, conversation, f["name"]), Path(f["path"]), "review"))
            else:
                roots.append((Path(f["path"]), Path(f["path"]), "folder"))
    roots.append((ws, ws, "workspace"))
    if Path(config.workspace) != ws:
        roots.append((Path(config.workspace), Path(config.workspace), "workspace"))
    roots = [(a.resolve(), b, k) for a, b, k in roots]
    found: dict[str, dict] = {}
    for it in items or []:
        raw = str(it.get("path") or "")
        p = Path(raw).expanduser()
        p = (p if p.is_absolute() else Path(config.workspace) / p).resolve()
        hit = max(((a, b, k) for a, b, k in roots if p == a or a in p.parents),
                  key=lambda r: len(r[0].parts), default=None)
        if hit is None:
            a, b, k = p.parent, p.parent, "folder"
            rel_parts: tuple = ()
        else:
            a, b, k = hit
            rel_parts = p.relative_to(a).parts
        top = b / rel_parts[0] if len(rel_parts) > 1 else b       # the project folder, not every subfolder
        key = str(top)
        if key not in found:
            found[key] = {"path": key, "shown": shown(top), "kind": k, "files": 0}
        found[key]["files"] += 1
    return sorted(found.values(), key=lambda x: -x["files"])[:limit]
