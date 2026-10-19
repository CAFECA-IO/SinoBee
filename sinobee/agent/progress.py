"""Progress, not a step count, decides when a task is stuck.

After every step (one model reply and the tool calls it made) the tracker records what changed and decides whether
the step moved the task forward:

  strong progress   the acceptance checklist changed, test results changed, a new file was produced, a document
                    changed in structure (headings, or a real amount of text added or removed), a fact was written
                    to the blackboard, work was delegated, the user was asked
  file-only change  a file changed but nothing above did. Allowed for N steps in a row, N by FILE TYPE: code 8
                    (edit, run, read the error is the normal rhythm of debugging), documents 3 (rewording the same
                    paragraphs is how a writer stalls), anything else 5. The type comes from the path and, for the
                    ambiguous ones (HTML, files without an extension), from the content - never from the tool used,
                    since report tasks run shell commands too
  information       a new read, search or command output - progress while no file is being rewritten
  nothing           errors, repeated reads, a file written with the same content, a file changed back

K steps in a row without progress (agent.progress.k, 15) asks the thinking model; a loop asks it at once:
the same step repeated 3 times without progress, two steps alternating A B A B, a file changed back to an earlier
version twice, the same error 3 steps in a row.

Every decision that stops or questions the execution - consult requested or skipped, the thinking model's verdict,
the health check, the safety valve - is a CONTROL ENTRY with the exact trigger, the numbers that crossed the
threshold, the thresholds themselves and the step-by-step evidence. Entries go to the failure report, the run
record, the UI and $SINOBEE_HOME/logs/control.jsonl (see `sinobee control-log`, /api/control-log).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path, PurePath

log = logging.getLogger("sinobee.control")

DEFAULTS = {"k": 15, "n_code": 8, "n_document": 3, "n_other": 5, "health_steps": 40, "safety_max_steps": 300,
            "window": 8, "stall_window": 20, "stall_ratio": 0.5, "wait_limit": 30}

# ------------------------------------------------------------------------------------------------ file types
CODE_EXT = {
    ".py", ".pyi", ".pyx", ".ipynb", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
    ".astro", ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle", ".c", ".h", ".cc", ".cpp", ".cxx",
    ".hpp", ".hh", ".m", ".mm", ".cs", ".fs", ".vb", ".swift", ".rb", ".php", ".pl", ".pm", ".lua", ".r", ".jl",
    ".dart", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj", ".elm", ".zig", ".nim", ".sol", ".sh", ".bash", ".zsh",
    ".fish", ".ps1", ".bat", ".cmd", ".sql", ".css", ".scss", ".sass", ".less", ".styl", ".proto", ".graphql", ".gql",
    ".tf", ".hcl", ".nix", ".cmake", ".mk", ".asm", ".s", ".wasm", ".wat", ".j2", ".jinja", ".jinja2", ".ejs", ".hbs",
    ".mustache", ".liquid", ".twig", ".erb", ".pug", ".jsonnet", ".dockerfile", ".prisma", ".graphqls", ".bzl",
    ".cue", ".rego", ".wgsl", ".glsl", ".hlsl", ".metal", ".cu", ".ino",
}
DOC_EXT = {".md", ".markdown", ".mdx", ".txt", ".text", ".rst", ".adoc", ".asciidoc", ".org", ".tex", ".bib", ".rtf",
           ".docx", ".doc", ".odt", ".pdf", ".pptx", ".ppt", ".odp", ".epub", ".srt", ".vtt"}
DATA_EXT = {".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".csv", ".tsv",
            ".xml", ".xlsx", ".xls", ".ods", ".parquet", ".lock", ".properties", ".plist", ".webmanifest",
            ".tsbuildinfo", ".map", ".geojson", ".sqlite", ".db"}
AMBIGUOUS_EXT = {".html", ".htm", ".xhtml", ".svg"}
SUFFIX_WRAPPERS = {".example", ".sample", ".template", ".tmpl", ".dist", ".bak", ".orig", ".old", ".new", ".in"}
CODE_NAMES = {"makefile", "gnumakefile", "dockerfile", "containerfile", "jenkinsfile", "rakefile", "gemfile",
              "procfile", "vagrantfile", "justfile", "brewfile", "podfile", "cmakelists.txt", "build", "workspace",
              "pipfile", "requirements.txt", "package.json", "tsconfig.json", "pyproject.toml", "setup.cfg",
              "cargo.toml", "go.mod", "go.sum", ".gitignore", ".dockerignore", ".editorconfig", ".bashrc", ".zshrc"}
DATA_NAMES = {".env", ".npmrc", ".yarnrc", ".nvmrc", ".python-version", ".tool-versions", ".prettierrc", ".eslintrc",
              ".babelrc", ".env.local", ".env.production", ".env.development"}
DOC_NAMES = {"readme", "changelog", "license", "licence", "authors", "contributing", "notes", "todo", "copying"}
CODE_DIRS = {"src", "lib", "app", "apps", "components", "pages", "routes", "views", "templates", "static", "public",
             "scripts", "test", "tests", "spec", "pkg", "cmd", "internal", "server", "client", "frontend", "backend",
             "web", "ui", "widgets", "layouts", "partials", "includes", "assets", "node_modules", "site-packages"}
DOC_DIRS = {"docs", "doc", "documentation", "report", "reports", "notes", "drafts", "articles", "posts", "content",
            "blog", "writing", "papers", "manuscript", "chapters", "outputs", "output", "deliverables"}
POLL = re.compile(r'"status"\s*:\s*"(?:working|running|pending|queued|processing|in_progress|started|waiting)"|'
                  r"\b(?:still (?:running|working|processing)|in progress|queued|pending|processing\.\.\.|waiting for)\b|"
                  r"處理中|進行中|排隊中|生成中|渲染中|等待中", re.I)
SHEBANG = re.compile(r"^#!\s*/\S+")
TEMPLATE_MARK = re.compile(r"\{\{|\{%|<%|\bv-(?:if|for|bind|model)\b|\bng-[a-z]+=|\bx-data\b|@click|:class=")


def _html_parts(text: str) -> dict:
    """Shares (%) of the page that are script, style, inline SVG drawing and readable text."""
    def size(rx):
        return sum(len(re.sub(r"\s+", "", m)) for m in re.findall(rx, text, re.I))
    script = size(r"<script\b[\s\S]*?</script>")
    style = size(r"<style\b[\s\S]*?</style>")
    svg = size(r"<svg\b[\s\S]*?</svg>")
    body = re.sub(r"<script\b[\s\S]*?</script>|<style\b[\s\S]*?</style>|<svg\b[\s\S]*?</svg>", " ", text, flags=re.I)
    visible = len(re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", body)))
    total = max(1, len(re.sub(r"\s+", "", text)))
    return {"script": script * 100 // total, "style": style * 100 // total, "svg": svg * 100 // total,
            "visible": visible * 100 // total, "canvas": bool(re.search(r"<canvas\b", text, re.I))}


def _html_strong(text: str) -> tuple[str, str] | None:
    """Content that decides by itself, whatever folder the page is in."""
    if TEMPLATE_MARK.search(text):
        return "code", "HTML 含模板或框架語法"
    h = _html_parts(text)
    graphic = h["script"] + h["svg"] + h["style"]
    if graphic >= 80 or (h["canvas"] and h["script"] >= 30):
        return "code", (f"HTML 以程式與圖形為主（script {h['script']}%、SVG {h['svg']}%、style {h['style']}%，"
                        f"可讀文字 {h['visible']}%），修改方式接近改程式")
    if h["visible"] >= 50:
        return "document", f"HTML 以可讀文字為主（可讀文字佔 {h['visible']}%）"
    return None


def _html_kind(text: str) -> tuple[str, str]:
    """A page is a document when it is mostly readable text (a report), code when it is mostly script, drawing or
    template markup (an app, an animation, generated diagrams, a template)."""
    strong = _html_strong(text)
    if strong:
        return strong
    h = _html_parts(text)
    graphic = h["script"] + h["svg"] + h["style"]
    if graphic >= 50:
        return "code", f"HTML 以程式與圖形為主（script/SVG/style 共 {graphic}%）"
    if h["visible"] >= 20:
        return "document", f"HTML 以文字內容為主（可讀文字佔 {h['visible']}%）"
    return "document", f"HTML 預設視為文件（可讀文字 {h['visible']}%，程式與圖形 {graphic}%）"


def _binary(text: str) -> bool:
    sample = text[:4000]
    if "\x00" in sample:
        return True
    odd = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\n\r\t\f") + sample.count("�")
    return odd / max(1, len(sample)) > 0.05


def classify(path: str, content: str | None = None) -> tuple[str, str]:
    """("code" | "document" | "data" | "other", why). The extension or file name decides when it is unambiguous;
    HTML is decided by decisive content first (mostly drawing/script, template syntax, or mostly text), then by the
    folder, then by the remaining content; files without an extension by shebang, name and content."""
    p = PurePath(str(path).replace("\\", "/"))
    name = p.name.lower()
    ext = p.suffix.lower()
    if content and _binary(content):
        return "other", "二進位檔"
    if ext in SUFFIX_WRAPPERS and (len(p.suffixes) >= 2 or name.startswith(".")):
        inner = name[: -len(ext)]                              # config.yaml.example -> config.yaml, .env.example -> .env
        kind, why = classify(str(p.with_name(inner)), content)
        return kind, f"{why}（{ext} 範本或備份）"
    if name in CODE_NAMES or name.startswith(("dockerfile", "makefile")):
        return "code", f"檔名 {p.name} 是建置或設定程式"
    if name in DATA_NAMES:
        return "data", f"檔名 {p.name} 是設定資料"
    if ext in CODE_EXT:
        return "code", f"副檔名 {ext}"
    if ext in DOC_EXT:
        return "document", f"副檔名 {ext}"
    if ext in DATA_EXT:
        return "data", f"副檔名 {ext}"
    dirs = {d.lower() for d in p.parts[:-1]}
    if ext in AMBIGUOUS_EXT:
        if ext == ".svg":
            return "other", "SVG 圖檔"
        if content:
            strong = _html_strong(content)
            if strong:
                return strong
        if dirs & CODE_DIRS and not dirs & DOC_DIRS:
            return "code", f"HTML 位於程式資料夾（{', '.join(sorted(dirs & CODE_DIRS))}）"
        if dirs & DOC_DIRS and not dirs & CODE_DIRS:
            return "document", f"HTML 位於文件資料夾（{', '.join(sorted(dirs & DOC_DIRS))}）"
        if content:
            return _html_kind(content)
        return "document", "HTML 預設視為文件"
    if not ext or ext == name:
        stem = name.split(".")[0]
        if content and SHEBANG.match(content):
            return "code", "檔案開頭有 shebang（可執行腳本）"
        if stem in DOC_NAMES:
            return "document", f"檔名 {p.name}"
        if content and _looks_like_prose(content):
            return "document", "無副檔名，內容是一般文字"
        return "other", "無副檔名，無法判定"
    if content and _looks_like_prose(content):
        return "document", f"副檔名 {ext} 不認得，內容是一般文字"
    return "other", f"未知的副檔名 {ext}"


def _looks_like_prose(text: str) -> bool:
    sample = text[:4000]
    if not sample.strip():
        return False
    symbols = len(re.findall(r"[{}();=<>\[\]]", sample))
    return symbols / max(1, len(sample)) < 0.02


# ------------------------------------------------------------------------------------------------ change size
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S|^\s*<h[1-6]\b", re.M | re.I)
TAGS = re.compile(r"<script\b[\s\S]*?</script>|<style\b[\s\S]*?</style>|<[^>]+>", re.I)


def _norm(text: str) -> str:
    """Whitespace-insensitive form: re-indenting, re-wrapping or trailing spaces are not a change."""
    return re.sub(r"\s+", "", text)


def _digest(text: str) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8", "replace")).hexdigest()[:16]


def doc_shape(text: str) -> dict:
    plain = TAGS.sub(" ", text) if "<" in text else text
    return {"headings": len(HEADING.findall(text)), "chars": len(_norm(plain))}


def doc_change(before: dict | None, after: dict) -> tuple[bool, str]:
    """A structural change: headings added or removed, or a real amount of text added or removed (at least 200
    characters and 5%). Rewording within the same size is not."""
    if before is None:
        return True, "新文件"
    if after["headings"] != before["headings"]:
        return True, f"標題數 {before['headings']}→{after['headings']}"
    delta = after["chars"] - before["chars"]
    need = max(200, int(before["chars"] * 0.05))
    if abs(delta) >= need:
        return True, f"內容 {'+' if delta > 0 else ''}{delta} 字（門檻 {need}）"
    return False, f"內容變動 {'+' if delta > 0 else ''}{delta} 字，未達結構變化門檻 {need}"


# ------------------------------------------------------------------------------------------------ test results
TEST_PATTERNS = [
    ("pytest", re.compile(r"=+ (?:(\d+) failed)?[, ]*(?:(\d+) passed)?[^=\n]*? in [\d.]+s", re.I)),
    ("jest", re.compile(r"Tests:\s+(?:(\d+) failed, )?(?:\d+ skipped, )?(?:(\d+) passed, )?\d+ total", re.I)),
    ("cargo", re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed", re.I)),
    ("mocha", re.compile(r"(\d+) passing[\s\S]{0,80}?(?:(\d+) failing)?", re.I)),
    ("unittest", re.compile(r"Ran (\d+) tests?[\s\S]{0,200}?(OK|FAILED \((?:failures|errors)=(\d+))", re.I)),
    ("go", re.compile(r"^(ok|FAIL)\s+\S+", re.M)),
]


def test_signature(output: str) -> str | None:
    """A short summary of test results found in a command's output ("pytest 3F/12P"), or None."""
    tail = output[-6000:]
    for name, rx in TEST_PATTERNS:
        if name == "go":
            res = rx.findall(tail)
            if res:
                return f"go {res.count('FAIL')}F/{res.count('ok')}P"
            continue
        ms = list(rx.finditer(tail))
        if not ms:
            continue
        m = ms[-1]
        g = m.groups()
        if name == "pytest":
            if not (g[0] or g[1]):
                continue
            return f"pytest {int(g[0] or 0)}F/{int(g[1] or 0)}P"
        if name == "jest":
            return f"jest {int(g[0] or 0)}F/{int(g[1] or 0)}P"
        if name == "cargo":
            return f"cargo {int(g[1])}F/{int(g[0])}P"
        if name == "mocha":
            return f"mocha {int(g[1] or 0)}F/{int(g[0])}P"
        if name == "unittest":
            failed = int(g[2] or 0) if g[1].upper() != "OK" else 0
            return f"unittest {failed}F/{int(g[0]) - failed}P"
    return None


def error_class(text: str) -> str:
    """The same kind of error with different numbers or paths counts as the same error."""
    first = next((ln for ln in str(text).splitlines() if ln.strip() and not ln.startswith("[exit")), str(text))
    first = re.sub(r"(/[\w.\-]+)+", "<path>", first)
    first = re.sub(r"\d+", "#", first)
    return first.strip()[:120]


STATEFUL_TOOLS = {"browser", "screenshot", "screen_info"}
READ_TOOLS = {"read_file", "list_dir", "search_files", "web_search", "web_fetch", "skill_view", "skill_search",
              "board_read", "memory_search", "session_search", "session_read", "db_list", "db_schema", "db_query",
              "screenshot", "screen_info", "usage_stats", "memory_stats", "job_list", "browser", "working_memory"}
WRITE_TOOLS = {"write_file", "edit_file", "skill_write_file"}
RUN_TOOLS = {"shell", "skill_run", "db_execute"}
STRONG_TOOLS = {"board_write": "寫入共享事實", "delegate": "委派子代理", "run_workflow": "啟動工作流程",
                "job_create": "建立排程工作", "ask_user": "詢問使用者", "memory_save": "保存記憶",
                "skill_create": "建立技能", "skill_patch": "修改技能"}


def _sig(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


class Tracker:
    """Per-turn progress state. `observe` is called once per step with the step's tool calls and results."""

    def __init__(self, config, read_file=None):
        cfg = {**DEFAULTS, **(config.get("agent.progress", {}) or {})} if config is not None else dict(DEFAULTS)
        self.k = int(cfg["k"])
        self.n = {"code": int(cfg["n_code"]), "document": int(cfg["n_document"]), "data": int(cfg["n_other"]),
                  "other": int(cfg["n_other"])}
        self.health_steps = int(cfg["health_steps"])
        self.safety_max = int(cfg["safety_max_steps"])
        self.window = int(cfg["window"])
        self.stall_window = int(cfg.get("stall_window", 20))
        self.stall_ratio = float(cfg.get("stall_ratio", 0.5))
        self.advised_at = 0                      # the step the thinking model last advised at
        self.wait_limit = int(cfg.get("wait_limit", 30))
        self.waits = 0                           # polls in a row that say the job is still running
        self.read_file = read_file or (lambda p: Path(p).read_text(encoding="utf-8", errors="replace")[:2_000_000])
        self.files: dict[str, dict] = {}        # path -> {kind, why, digest, history[digests], shape}
        self.seen_reads: set[str] = set()
        self.seen_runs: set[str] = set()
        self.page_state = ""                     # digest of the last browser answer (scroll, URL, elements)
        self.test_sig: str | None = None
        self.checklist_sig: str | None = None
        self.file_only = 0                       # steps in a row with only file changes
        self.streak_kinds: list[set] = []        # file kinds touched in each step of that streak
        self.no_progress = 0                     # steps in a row without progress
        self.last_progress_step = 0
        self.trace: list[dict] = []              # one entry per step
        self.step_sigs: list[str] = []
        self.reverts: list[int] = []             # steps where a file went back to an earlier version
        self.errors: list[str | None] = []       # error class per step (None = no error)

    # ---------------------------------------------------------------------------------------------------
    def thresholds(self) -> dict:
        return {"K": self.k, "N_code": self.n["code"], "N_document": self.n["document"], "N_other": self.n["other"],
                "health_steps": self.health_steps, "safety_max_steps": self.safety_max}

    def evidence(self, last: int = 6) -> list[str]:
        return [f"#{t['step']} {'✓' if t['progress'] else '·'} {t['why']}" for t in self.trace[-last:]]

    def set_checklist(self, items: list[dict]) -> None:
        self.checklist_sig = _sig([(i.get("id"), i.get("status")) for i in items or []])

    def _file(self, path: str, resolved: str | None) -> dict:
        real = resolved or path
        try:
            text = self.read_file(real)
        except Exception:  # noqa: BLE001 - a binary or vanished file: classify by name only
            text = None
        rec = self.files.get(real)
        kind, why = classify(path, text)
        out = {"kind": kind, "why": why, "path": path, "new": rec is None}
        if text is None:
            out.update(change="unknown")
            self.files[real] = {**(rec or {}), "kind": kind, "why": why, "history": (rec or {}).get("history", [])}
            return out
        dg = _digest(text)
        hist = (rec or {}).get("history", [])
        if rec is not None and hist and dg == hist[-1]:
            out["change"] = "same"
        elif dg in hist:
            out["change"] = "revert"
        elif kind == "document":
            structural, detail = doc_change((rec or {}).get("shape") if rec else None, doc_shape(text))
            out["change"] = "doc_struct" if structural else "doc_minor"
            out["detail"] = detail
        else:
            out["change"] = "changed"
        self.files[real] = {"kind": kind, "why": why, "history": (hist + [dg])[-20:],
                            "shape": doc_shape(text) if kind == "document" else None}
        return out

    def observe(self, step: int, calls: list[tuple], checklist: list[dict] | None = None,
                resolve=None) -> dict:
        """calls: [(name, arguments, result_text, is_error)]. Returns the step's verdict (also kept in trace)."""
        strong, weak_files, info, nothing, waiting = [], [], [], [], []
        kinds = []
        err = None
        for name, args, text, is_error in calls:
            args = args if isinstance(args, dict) else {}
            if is_error:
                err = error_class(text)
                nothing.append(f"{name} 失敗：{err[:60]}")
                continue
            if name == "task_checklist":
                continue
            if name in WRITE_TOOLS and args.get("path"):
                try:
                    resolved = str(resolve(args["path"])) if resolve else None
                except Exception:  # noqa: BLE001
                    resolved = None
                f = self._file(str(args["path"]), resolved)
                kinds.append(f["kind"])
                label = f"{PurePath(str(args['path'])).name}（{f['kind']}：{f['why']}）"
                if f["new"] and f.get("change") != "same":
                    strong.append(f"新檔案 {label}")
                elif f["change"] == "doc_struct":
                    strong.append(f"文件結構變化 {label}：{f['detail']}")
                elif f["change"] in ("changed", "doc_minor", "unknown"):
                    weak_files.append(f"修改 {label}" + (f"：{f['detail']}" if f.get("detail") else ""))
                elif f["change"] == "revert":
                    self.reverts.append(step)
                    nothing.append(f"{label} 改回先前的版本")
                else:
                    nothing.append(f"{label} 內容沒有變")
                continue
            if name in RUN_TOOLS:
                ts = test_signature(text or "")
                if ts and ts != self.test_sig:
                    strong.append(f"測試結果 {self.test_sig or '（首次）'} → {ts}")
                    self.test_sig = ts
                    continue
                if ts:
                    nothing.append(f"測試結果沒變（{ts}）")
                    continue
                key = _sig([name, args, _digest(text or "")])
                if key in self.seen_runs and POLL.search((text or "")[-3000:]):
                    waiting.append(f"{name} 等待中（輸出顯示工作仍在進行）")
                elif key in self.seen_runs:
                    nothing.append(f"{name} 重複執行，輸出相同")
                else:
                    self.seen_runs.add(key)
                    info.append(f"{name} 新的輸出")
                continue
            if name in STRONG_TOOLS:
                strong.append(STRONG_TOOLS[name])
                continue
            if name in STATEFUL_TOOLS:
                # the browser's answer depends on the page's state (scroll position, URL, what was clicked): the
                # same call is new information when the page changed; a screenshot is as new as the page state
                # before it (its text says only "Browser screenshot of <url>")
                state = _digest(text or "")
                if "screenshot" in json.dumps(args, ensure_ascii=False) or name == "screenshot":
                    state = _sig([state, self.page_state])
                else:
                    self.page_state = state
                key = _sig([name, args, state])
            else:
                key = _sig([name, args])
            if key in self.seen_reads:
                nothing.append(f"{name} 重複讀取")
            else:
                self.seen_reads.add(key)
                info.append(f"{name} 新的資訊")
        if checklist is not None:
            sig = _sig([(i.get("id"), i.get("status")) for i in checklist])
            if self.checklist_sig is not None and sig != self.checklist_sig:
                done = sum(1 for i in checklist if i.get("status") == "done")
                strong.insert(0, f"驗收清單更新（完成 {done}/{len(checklist)}）")
            self.checklist_sig = sig
        self.errors.append(err)

        kind = "code" if "code" in kinds else ("document" if "document" in kinds else (kinds[-1] if kinds else None))
        if strong:
            progress, why = True, "；".join(strong)
            self.file_only = 0
            self.streak_kinds = []
        elif weak_files:
            self.file_only += 1
            self.streak_kinds.append(set(kinds) or {"other"})
            # the streak's N comes from the main output being edited: the most tolerant kind touched in the last
            # n_code steps. A task that edits code and writes its report (in one step or alternating) keeps the code
            # N=8; a stray code edit long ago does not keep relaxing a document that is only being reworded.
            recent = set().union(*self.streak_kinds[-max(self.n.values()):])
            kind = max(recent, key=lambda k: self.n.get(k, self.n["other"]))
            n = self.n.get(kind, self.n["other"])
            mixed = f"，本段涉及 {'+'.join(sorted(recent))}，取最寬的 {kind}" if len(recent) > 1 else ""
            progress = self.file_only <= n
            why = (f"只有檔案修改 {self.file_only}/{n} 步（{kind}{mixed}）：" + "；".join(weak_files)) if progress else \
                (f"只有檔案修改已連續 {self.file_only} 步，超過 {kind} 的容忍 N={n}{mixed}：" + "；".join(weak_files))
        elif info and not err:
            progress, why = True, "；".join(info)
        else:
            progress, why = False, "；".join(nothing) or "沒有任何變化"
        # polling a running job (a render, a build, a download) is waiting, not looping: it neither counts as
        # progress nor toward K; a wait of wait_limit polls in a row asks the thinking model (long_wait)
        wait = not progress and waiting and not nothing and not err
        if wait:
            why = "；".join(waiting)
            self.waits += 1
        else:
            self.waits = 0
        if progress:
            self.no_progress = 0
            self.last_progress_step = step
        elif not wait:
            self.no_progress += 1
        step_sig = _sig(sorted([(n, a) for n, a, _, _ in calls], key=lambda x: x[0] + json.dumps(x[1], sort_keys=True, default=str)))
        self.step_sigs.append(step_sig)
        verdict = {"step": step, "progress": progress, "strong": bool(strong), "why": why[:400], "kind": kind,
                   "waiting": wait,
                   "file_only": self.file_only, "no_progress": self.no_progress}
        self.trace.append(verdict)
        del self.trace[:-400]
        verdict["loop"] = self._loop(step)
        return verdict

    def _loop(self, step: int) -> dict | None:
        """A loop needs no K steps to be noticed."""
        recent = self.trace[-self.window:]
        sigs = self.step_sigs[-self.window:]
        cur = self.trace[-1]
        if cur.get("waiting"):
            if self.waits >= self.wait_limit:
                return {"trigger": "long_wait",
                        "detail": f"已連續 {self.waits} 次查詢工作狀態，輸出都顯示仍在進行（門檻 {self.wait_limit} 次）"}
            return None
        if not cur["progress"] and sigs.count(sigs[-1]) >= 3:
            same = [t["step"] for t, s in zip(recent, sigs) if s == sigs[-1]]
            between = [t for t in recent if same[-3] < t["step"] < same[-1]]
            # only a loop when nothing moved in between: re-reading a file after each edit that changed it is
            # checking, not looping (Roadster turn 4, steps 2-6)
            if not any(t["progress"] for t in between):
                return {"trigger": "loop_repeat",
                        "detail": f"同一組操作在最近 {len(sigs)} 步內出現 {len(same)} 次（第 {', '.join(map(str, same))} 步），"
                                  "期間沒有任何進度"}
        if len(sigs) >= 4 and sigs[-1] == sigs[-3] and sigs[-2] == sigs[-4] and sigs[-1] != sigs[-2] \
                and not any(t["progress"] for t in recent[-4:]):
            steps = [t["step"] for t in recent[-4:]]
            return {"trigger": "loop_alternating",
                    "detail": f"兩組操作交替重複 A→B→A→B（第 {', '.join(map(str, steps))} 步），每一步都沒有進度"}
        rv = [s for s in self.reverts if step - s < self.window]
        if len(rv) >= 2 and rv[-1] == step:
            return {"trigger": "file_oscillation",
                    "detail": f"檔案內容在最近 {self.window} 步內改回先前版本 {len(rv)} 次（第 {', '.join(map(str, rv))} 步）"}
        errs = self.errors[-3:]
        if len(errs) == 3 and errs[0] and errs.count(errs[0]) == 3:
            return {"trigger": "repeated_error", "detail": f"連續 3 步出現相同的錯誤：{errs[0][:100]}"}
        # exploring without producing: in the last stall_window steps (since the last advice) no file was changed and
        # nothing strong happened, and most steps re-read what was already read. New reads in between keep
        # resetting the K count, so this needs its own rule (the stalled Roadster turn: 58 steps, no file written,
        # skill_view anatomy / ls / the same report read over and over)
        w = self.stall_window
        span = [t for t in self.trace[-w:] if t["step"] > self.advised_at]
        if len(span) >= w and not cur["progress"] and not any(t["strong"] or t["file_only"] for t in span):
            idle = sum(1 for t in span if not t["progress"])
            if idle >= self.stall_ratio * w:
                return {"trigger": "exploration_stall",
                        "detail": f"最近 {w} 步（第 {span[0]['step']}–{span[-1]['step']} 步）沒有寫任何檔案，"
                                  f"其中 {idle} 步是重複的讀取或指令（門檻 {int(self.stall_ratio * 100)}%）"}
        return None

    def produced_within(self, n: int) -> bool:
        """Whether any of the last n steps changed a file or made strong progress."""
        return any(t["strong"] or t["file_only"] for t in self.trace[-n:])

    def new_milestone(self, step: int) -> None:
        """A milestone starts with a fresh context: the streaks, the stall window and the waits start over."""
        self.no_progress = 0
        self.file_only = 0
        self.streak_kinds = []
        self.waits = 0
        self.advised_at = step
        self.last_progress_step = step

    def advised(self, step: int) -> None:
        """The thinking model just advised: the advice gets K fresh steps and a fresh stall window."""
        self.no_progress = 0
        self.advised_at = step
        self.waits = 0


# ------------------------------------------------------------------------------------------------ control log
TRIGGERS = {
    "no_progress": "連續多步沒有進度",
    "loop_repeat": "同一操作重複且沒有進度",
    "loop_alternating": "兩組操作交替重複",
    "file_oscillation": "檔案反覆改回先前版本",
    "repeated_error": "相同錯誤連續出現",
    "exploration_stall": "反覆探索、沒有產出",
    "long_wait": "等待工作完成過久",
    "milestone_started": "里程碑計畫開始",
    "milestone_confirmed": "使用者確認里程碑計畫",
    "milestone_corrected": "使用者修正里程碑計畫",
    "milestone_delegated": "里程碑交給子代理",
    "milestone_done": "里程碑完成",
    "milestone_dropped": "里程碑略過",
    "milestone_taken_over": "子代理未完成，主代理接手",
    "milestone_interrupted": "立即中斷並重劃",
    "milestone_replanned": "里程碑重新規劃",
    "fact_conflict": "共享事實衝突",
    "consecutive_errors": "工具連續失敗",
    "repeated_failed_call": "重複執行已失敗的操作",
    "health_steps": "里程碑健康水位：步數",
    "health_compaction": "里程碑健康水位：上下文壓縮",
    "safety_valve": "安全閥：單一 Agent 步數上限",
    "thinking_stop": "思維模型判斷無法完成",
    "advice_exhausted": "諮詢次數用完仍卡住",
}


def control_path(home) -> Path:
    p = Path(home) / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "control.jsonl"


def append_log(home, entry: dict, max_bytes: int = 5_000_000) -> None:
    try:
        path = control_path(home)
        if path.exists() and path.stat().st_size > max_bytes:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        log.debug("could not write the control log", exc_info=True)


def read_log(home, *, session: str | None = None, trigger: str | None = None, event: str | None = None,
             limit: int = 200) -> list[dict]:
    out = []
    path = control_path(home)
    files = [path.with_suffix(".jsonl.1"), path]
    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if session and e.get("session_id") != session:
                continue
            if trigger and e.get("trigger") != trigger:
                continue
            if event and e.get("event") != event:
                continue
            out.append(e)
    return out[-limit:]


def entry(event: str, trigger: str, step: int, detail: str, tracker: Tracker | None, **extra) -> dict:
    return {"t": round(time.time(), 3), "event": event, "trigger": trigger, "label": TRIGGERS.get(trigger, trigger),
            "step": step, "detail": detail, "thresholds": tracker.thresholds() if tracker else {},
            "evidence": tracker.evidence() if tracker else [], **extra}


def describe(e: dict) -> str:
    """One line for people: what happened, why, with the numbers."""
    what = {"consult_requested": "請教思維模型", "consult": "思維模型回覆", "consult_skipped": "未請教思維模型",
            "stop": "停止任務", "health_check": "健康檢查", "milestone": "里程碑"}.get(e.get("event"), e.get("event"))
    tail = f"→ {e['decision']}" if e.get("decision") else ""
    return f"[第 {e.get('step')} 步] {what}：{e.get('label')} — {e.get('detail')} {tail}".strip()
