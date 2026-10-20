"""Package a finished task into a skill: the way it was done, reusable with other inputs.

    draft = await draft_from_session(rt, session_id)        # what the thinking model distils from the task
    res = save_draft(rt, draft["id"], edits)                 # the user's edits -> a skill (experimental)

A draft is made from the task's run report (agent/runreport.py: request, answer, acceptance checklist, every tool
step, files produced) and the conversation. The thinking model writes the method (SKILL.md body), the description
that decides when the skill is used, and the parameters: this task's concrete values (file names, dates, people,
formats) become {placeholders} the next user fills in. Scripts and templates the task wrote are offered as files of
the skill; input data is never copied in.

Privacy (sinobee/privacy.py): people, e-mails, paths and secrets found in the draft are replaced by neutral
placeholders; a file still containing personal data or a secret is switched off and cannot be saved with the skill.
The skill is created as the user's own (trust local) with status candidate ("experimental") until it has been used
successfully. No evaluation is set up here - a skill is evaluated after it has been used (see sinobee/evals).

Drafts live in $SINOBEE_HOME/skill_drafts/<id>.json for a day.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from pathlib import Path

from .. import privacy
from . import spec
from .manager import SkillError

log = logging.getLogger("sinobee.skills")

DRAFT_TTL = 24 * 3600
MAX_FILE = 200_000
MAX_FILES = 12
MAX_PARAMS = 12
SCRIPT_EXT = {".py", ".sh", ".js", ".mjs", ".ts", ".sql", ".r"}
TEMPLATE_EXT = {".md", ".html", ".htm", ".css", ".json", ".csv", ".yaml", ".yml", ".toml", ".txt", ".tex", ".svg"}
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PackageError(SkillError):
    pass


# ---------------------------------------------------------------- what the task did

def _drafts(home) -> Path:
    d = Path(home) / "skill_drafts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report(home, sid: str, turn_id=None) -> dict | None:
    """The newest run report of the session's finished top-level task (or of that turn)."""
    from ..agent import runreport
    best = None
    for f in sorted(runreport._dir(home).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("session_id") != sid or (turn_id is not None and r.get("turn_id") != turn_id):
            continue
        if r.get("status") == "done":
            return r
        best = best or r
    return best


def _written(report: dict) -> list[str]:
    """Files the task produced: the run report's outputs plus every path a write tool was given."""
    out = list((report.get("outputs") or {}).get("files") or [])
    for w in report.get("work_log") or []:
        if w.get("tool") in ("write_file", "edit_file", "append_file", "create_file") and w.get("ok") is not False:
            try:
                p = json.loads(w.get("args") or "{}").get("path")
            except ValueError:
                p = None
            if p and p not in out:
                out.append(p)
    return out


def _candidate(ws: Path, rel: str) -> dict | None:
    p = Path(rel)
    p = p if p.is_absolute() else ws / p
    try:
        p = p.resolve()
        p.relative_to(ws.resolve())
    except (OSError, ValueError):
        return None
    if not p.is_file():
        return None
    ext, size = p.suffix.lower(), p.stat().st_size
    role = "script" if ext in SCRIPT_EXT or "scripts" in p.parts else "template" if ext in TEMPLATE_EXT else None
    if role is None or size > MAX_FILE:
        return None
    return {"source": str(p.relative_to(ws.resolve())), "role": role, "bytes": size}


def collect(rt, sid: str, turn_id=None) -> dict:
    rep = _report(rt.config.home, sid, turn_id)
    if not rep:
        msgs = rt.memory.load_messages(sid)
        if not msgs:
            raise PackageError("找不到這段對話")
        req = next((m.text() for m in msgs if m.role == "user"), "")
        ans = next((m.text() for m in reversed(msgs) if m.role == "assistant" and m.text()), "")
        rep = {"request": req, "final_answer": ans, "checklist": [], "work_log": [], "outputs": {}, "steps": 0}
    ws = Path(str(rt.config.workspace))
    cands = []
    for rel in _written(rep):
        c = _candidate(ws, rel)
        if c and all(c["source"] != x["source"] for x in cands):
            cands.append(c)
    steps = [f"{'✗' if w.get('ok') is False else '·'} {w['tool']} {w.get('args', '')[:160]}" if w.get("tool")
             else f"  ({w.get('note', '')[:200]})" for w in (rep.get("work_log") or [])[-80:]]
    return {"request": rep.get("request") or "", "answer": rep.get("final_answer") or "",
            "checklist": [i.get("text") for i in rep.get("checklist") or [] if isinstance(i, dict) and i.get("text")],
            "steps": steps, "n_steps": rep.get("steps") or 0, "files": cands, "turn_id": rep.get("turn_id"),
            "models": rep.get("models") or {}, "skills": rep.get("skills") or []}


# ---------------------------------------------------------------- the draft

PROMPT = """You turn a task an AI agent just finished into a reusable Agent Skill: the METHOD, so the next time a
user gives similar inputs the agent does the same job the same way. Write in the user's language (Traditional
Chinese if the request is).

THE TASK
Request: {request}
Acceptance checklist: {checklist}
Steps taken (tool calls, oldest first):
{steps}
Final answer: {answer}
Files the task wrote (candidates to ship with the skill): {files}

RULES
- Generalize. This task's concrete values (file names, dates, people, companies, amounts, URLs) become parameters;
  write them in the method as {{param_name}}. The skill must never contain this task's data.
- Parameters: snake_case names, a one-line description, required true/false, and an example that is generic or
  invented (never this task's real value).
- The method: numbered steps a capable agent can follow, naming the scripts / templates it ships (scripts/x.py,
  templates/y.md), the decisions that mattered in this task (e.g. how a mistake was avoided), and a short
  "完成前檢查" list of checks before reporting done.
- Files: only scripts and templates that the method uses; never input data or the task's results.
- description: one or two sentences on what it does and when to use it (it decides when the skill is picked).
- names: every person, company or other identifying name that appears in the task (so it can be removed).

Reply with JSON only:
{{"name": "lowercase-hyphen-name", "title": "short human title", "description": "...",
  "params": [{{"name": "...", "description": "...", "required": true, "example": "..."}}],
  "body": "markdown: ## 做法 ... ## 完成前檢查 ...",
  "files": [{{"source": "<one of the candidate paths>", "target": "scripts/... or templates/...", "reason": "..."}}],
  "triggers": ["keywords that hint this skill"], "names": ["..."]}}"""


def _unique_name(mgr, name: str) -> str:
    base = spec.suggest_name(name or "packaged-skill")[:56] or "packaged-skill"
    out, n = base, 2
    while out in mgr.skills:
        out, n = f"{base}-{n}", n + 1
    return out


def _clean_params(raw) -> list[dict]:
    out, seen = [], set()
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        n = re.sub(r"[^a-z0-9_]", "_", str(p.get("name") or "").strip().lower()).strip("_")
        if not n or not n[0].isalpha():
            n = "p_" + n if n else ""
        if not PARAM_RE.match(n or "") or n in seen:
            continue
        seen.add(n)
        out.append({"name": n, "description": str(p.get("description") or "")[:200],
                    "required": bool(p.get("required", True)), "example": str(p.get("example") or "")[:120]})
        if len(out) >= MAX_PARAMS:
            break
    return out


def _file_check(ws: Path, source: str, names: list[str]) -> tuple[str | None, list[dict], str]:
    """(text after generalizing workspace paths, findings left, warning)."""
    p = (ws / source).resolve()
    try:
        p.relative_to(ws.resolve())
        text = p.read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return None, [], "讀不到這個檔案"
    # paths into the task's workspace become relative (the skill runs in another workspace)
    for root in {str(ws), str(ws.resolve())}:
        text = text.replace(root.rstrip("/") + "/", "")
    found = [f.as_dict() for f in privacy.scan(text, names)]
    warn = ""
    if found:
        warn = "含" + "、".join(sorted({f["label"] for f in found})) + "，不收進技能"
    return text, found, warn


def _mask(v: str) -> str:
    """Enough of a value to recognize it in the panel, not enough to copy it around."""
    if len(v) <= 4:
        return v[:1] + "○" * (len(v) - 1)
    return v[:2] + "…" + v[-2:]


def _free_target(target: str, taken: set) -> str:
    if target not in taken:
        return target
    p = Path(target)
    n = 2
    while (out := str(p.with_name(f"{p.stem}-{n}{p.suffix}"))) in taken:
        n += 1
    return out


async def draft_from_session(rt, sid: str, turn_id=None) -> dict:
    from ..learning.reflector import parse_json
    from ..types import Message
    c = collect(rt, sid, turn_id)
    if not c["request"]:
        raise PackageError("這段對話沒有可以打包的任務")
    prompt = PROMPT.format(request=c["request"][:3000], checklist="; ".join(c["checklist"])[:1500] or "(none)",
                           steps="\n".join(c["steps"])[:9000] or "(none)", answer=c["answer"][:2000] or "(none)",
                           files=", ".join(f["source"] for f in c["files"]) or "(none)")
    resp = await rt.thinker().chat([Message("user", prompt)], None, temperature=0.2, max_tokens=4000)
    data = parse_json(resp.message.text())
    if not data.get("body") and not data.get("description"):
        raise PackageError("思維模型沒有產生技能草稿，請再試一次")
    mgr = rt.skills
    names = [str(n) for n in data.get("names") or [] if str(n).strip()]
    mapping: dict[str, str] = {}
    findings: list[dict] = []

    def clean(text: str, where: str) -> str:
        nonlocal mapping
        for f in privacy.scan(text or "", names):
            findings.append({**f.as_dict(), "value": _mask(f.value), "where": where, "action": "replaced"})
        out, mapping = privacy.deidentify(text or "", mapping, names)
        return out
    description = clean(str(data.get("description") or "")[:1000], "說明")
    body = clean(str(data.get("body") or ""), "做法")
    params = _clean_params(data.get("params"))
    for p in params:
        p["description"] = clean(p["description"], f"參數 {p['name']}")
        p["example"] = clean(p["example"], f"參數 {p['name']}")
    ws = Path(str(rt.config.workspace))
    by_source = {f["source"]: f for f in c["files"]}
    files = []
    for f in data.get("files") or []:
        src = str((f or {}).get("source") or "")
        if src not in by_source or any(x["source"] == src for x in files):
            continue
        role = by_source[src]["role"]
        target = str(f.get("target") or "")
        tp = Path(target)
        if not target or tp.is_absolute() or ".." in tp.parts or tp.parts[0] not in ("scripts", "templates", "references", "assets"):
            target = f"{'scripts' if role == 'script' else 'templates'}/{Path(src).name}"
        target = _free_target(target, {x["target"] for x in files})
        _text, found, warn = _file_check(ws, src, names)
        for x in found:
            findings.append({**x, "value": _mask(x["value"]), "where": target, "action": "excluded"})
        files.append({"source": src, "target": target, "role": role, "bytes": by_source[src]["bytes"],
                      "reason": str(f.get("reason") or "")[:200], "include": not found and _text is not None,
                      "locked": bool(found) or _text is None, "warning": warn})
    # files the task wrote that the model did not pick (input data, results): shown switched off
    for src, cf in by_source.items():
        if not any(x["source"] == src for x in files) and len(files) < MAX_FILES + 4:
            target = _free_target(f"{'scripts' if cf['role'] == 'script' else 'templates'}/{Path(src).name}",
                                  {x["target"] for x in files})
            files.append({"source": src, "target": target,
                          "role": cf["role"], "bytes": cf["bytes"], "reason": "本次任務的檔案（可能是輸入或成果）",
                          "include": False, "locked": False, "warning": ""})
    draft = {
        "id": time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(3), "created": time.time(),
        "session_id": sid, "turn_id": c["turn_id"], "name": _unique_name(mgr, str(data.get("name") or "")),
        "title": clean(str(data.get("title") or "")[:80], "標題"), "description": description, "params": params,
        "body": body, "files": files[:MAX_FILES + 4], "triggers": [clean(str(t)[:40], "觸發詞")
                                                                 for t in (data.get("triggers") or [])][:10],
        "names": names, "privacy": findings, "n_steps": c["n_steps"], "request": clean(c["request"][:300], "需求"),
        "models": c["models"],
    }
    _cleanup(rt.config.home)
    (_drafts(rt.config.home) / f"{draft['id']}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")
    return _public(draft)


def _public(draft: dict) -> dict:
    """What the UI gets: the names list stays on the server (it is the personal data being removed)."""
    return {k: v for k, v in draft.items() if k != "names"}


def _cleanup(home) -> None:
    now = time.time()
    for f in _drafts(home).glob("*.json"):
        try:
            if now - f.stat().st_mtime > DRAFT_TTL:
                f.unlink()
        except OSError:
            pass


def load_draft(home, draft_id: str) -> dict:
    if not re.fullmatch(r"[\w-]{6,40}", draft_id or ""):
        raise PackageError("草稿編號不正確")
    try:
        return json.loads((_drafts(home) / f"{draft_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PackageError("草稿不存在或已過期（草稿保留 24 小時），請重新打包") from None


# ---------------------------------------------------------------- the skill

def render(draft: dict) -> str:
    """SKILL.md text: frontmatter (name, description, metadata) and the body with its parameter list."""
    params = draft.get("params") or []
    meta = {"version": "0.1.0", "sinobee-source": "packaged",
            "sinobee-params": ", ".join(p["name"] + ("*" if p.get("required") else "") for p in params)}
    trig = [t for t in draft.get("triggers") or [] if t]
    if trig:
        meta["sinobee-triggers"] = ", ".join(trig)
    fm = {"name": draft["name"], "description": draft["description"].replace("\n", " ").strip(), "metadata": meta}
    lines = [f"# {draft.get('title') or draft['name']}", "", draft["description"].strip(), ""]
    if params:
        lines += ["## 參數", ""]
        for p in params:
            ex = f"例：{p['example']}" if p.get("example") else ""
            lines.append(f"- `{p['name']}`（{'必填' if p.get('required') else '選填'}）：{p['description']}{'。' + ex if ex else ''}")
        lines.append("")
    lines.append(draft["body"].strip())
    return spec.render(fm, "\n".join(lines).strip() + "\n")


def apply_edits(draft: dict, edits: dict) -> dict:
    d = dict(draft)
    if edits.get("name"):
        d["name"] = str(edits["name"]).strip()
    for k in ("title", "description", "body"):
        if isinstance(edits.get(k), str):
            d[k] = edits[k]
    if isinstance(edits.get("params"), list):
        d["params"] = _clean_params(edits["params"])
    on = edits.get("files")
    if isinstance(on, dict):
        d["files"] = [{**f, "include": bool(on.get(f["source"], f["include"])) and not f.get("locked")} for f in d["files"]]
    return d


def save_draft(rt, draft_id: str, edits: dict | None = None) -> dict:
    """Create the skill from the (edited) draft. The texts are scanned again: what the user typed may add personal
    data, and a file is re-read from the workspace (it may have changed since the draft)."""
    edits = edits or {}
    draft = apply_edits(load_draft(rt.config.home, draft_id), edits)
    mgr = rt.skills
    names = draft.get("names") or []
    leaks = [f.as_dict() for t in (draft["description"], draft["body"], json.dumps(draft["params"], ensure_ascii=False))
             for f in privacy.scan(t, names) if f.kind in ("secret", "email", "phone", "national_id", "card", "name")]
    if leaks:
        raise PackageError("技能內容含有個資或金鑰（" + "、".join(sorted({x["label"] for x in leaks}))
                           + "），請先移除再儲存")
    content = render(draft)
    ws = Path(str(rt.config.workspace))
    files = []
    for f in draft["files"]:
        if not f.get("include"):
            continue
        text, found, warn = _file_check(ws, f["source"], names)
        if text is None or found:
            raise PackageError(f"{f['target']}：{warn or '讀不到這個檔案'}")
        files.append((f["target"], text))
    scope = edits.get("scope") if edits.get("scope") in ("global", "project") else "global"
    res = mgr.create(draft["name"], content, scope=scope, actor="user", source="package")
    written = []
    for target, text in files:
        try:
            written.append(mgr.write_file(draft["name"], target, text, actor="user")["path"])
        except SkillError as e:
            log.warning("packaged file %s refused: %s", target, e)
            raise PackageError(f"{target}：{e}") from None
    mgr.set_status(draft["name"], "candidate")       # experimental until it has been used successfully
    out = {**res, "files": written, "params": [p["name"] for p in draft["params"]], "published": None}
    if edits.get("publish"):
        from . import hub
        try:
            out["published"] = hub.publish(mgr, draft["name"], rt.config)
        except SkillError as e:
            out["publish_error"] = str(e)
    try:
        (_drafts(rt.config.home) / f"{draft_id}.json").unlink()
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- when to offer it

def worth_packaging(*, depth: int, status: str, steps: int, checklist: int, outputs: int, skills_used: list,
                    query: str, mgr) -> bool:
    """Offer packaging for a substantial, finished task that no skill covers yet (shown after the user's 👍)."""
    if depth != 0 or status != "done" or steps < 10 or skills_used:
        return False
    if not checklist and not outputs:
        return False
    try:
        return mgr is None or mgr.suggest(query) is None
    except Exception:  # noqa: BLE001
        return False
