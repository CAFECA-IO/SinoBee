"""Quality rules: problems the user reported once, turned into deterministic checks that run on every task's files.

A rule says which files it applies to and a regular expression that must not (forbid) or must (require) appear:

    {"id": "r3f2a", "title": "公式的反斜線不能被吃掉", "files": ["*.html", "*.md"], "kind": "forbid",
     "pattern": "[\\x0c\\x08]", "message": "LaTeX 的 \\f / \\b 變成控制字元", "enabled": true,
     "source": {"type": "feedback", "ref": 42}, "created": 1760000000, "hits": 3}

Rules come from three places: the user asks for one ("以後報告都要附來源"), the agent adds one with the
quality_rule tool, or a 👎 on a turn makes the learning module propose one. A proposed rule is only kept when it
actually flags the files of the turn the user disliked - proof that it catches that bug - and a regression task for
`sinobee eval` is saved with it, so the same mistake is caught both live and in evaluations.
Rules live in $SINOBEE_HOME/qa_rules.json; the file checks (agent/qa.py) apply them to every text file a task makes.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import re
import secrets
import time
from pathlib import Path

log = logging.getLogger("sinobee.rules")
MAX_RULES = 200
TEXT_EXT = {".md", ".markdown", ".html", ".htm", ".svg", ".txt", ".csv", ".json", ".yaml", ".yml", ".srt", ".tex",
            ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".sql", ".sh"}


class RuleError(ValueError):
    pass


def _path(home) -> Path:
    return Path(home) / "qa_rules.json"


def load(home) -> list[dict]:
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(home, rules: list[dict]) -> None:
    _path(home).write_text(json.dumps(rules[-MAX_RULES:], ensure_ascii=False, indent=1), encoding="utf-8")


def validate(rule: dict) -> dict:
    kind = rule.get("kind") or "forbid"
    if kind not in ("forbid", "require"):
        raise RuleError("kind must be forbid or require")
    pattern = str(rule.get("pattern") or "")
    if not 2 <= len(pattern) <= 400:
        raise RuleError("pattern must be 2-400 characters")
    flags = "".join(c for c in str(rule.get("flags") or "") if c in "ims")
    try:
        rx = re.compile(pattern, sum({"i": re.I, "m": re.M, "s": re.S}[c] for c in flags))
    except re.error as e:
        raise RuleError(f"pattern is not a valid regular expression: {e}") from None
    if kind == "forbid" and rx.search("") is not None:
        raise RuleError("a forbid pattern must not match empty text (it would flag every file)")
    files = [str(f).strip() for f in (rule.get("files") or ["*.md", "*.html"]) if str(f).strip()][:10]
    title = str(rule.get("title") or "").strip()[:120]
    if not title:
        raise RuleError("give the rule a short title")
    return {"id": rule.get("id") or "r" + secrets.token_hex(3), "title": title, "files": files, "kind": kind,
            "pattern": pattern, "flags": flags, "message": str(rule.get("message") or title).strip()[:300],
            "enabled": rule.get("enabled", True) is not False, "source": rule.get("source") or {"type": "user"},
            "created": rule.get("created") or time.time(), "hits": int(rule.get("hits") or 0)}


def add(home, rule: dict) -> dict:
    r = validate(rule)
    rules = [x for x in load(home) if x.get("id") != r["id"]]
    if any(x["pattern"] == r["pattern"] and x["kind"] == r["kind"] and x["files"] == r["files"] for x in rules):
        raise RuleError("the same rule already exists")
    rules.append(r)
    save(home, rules)
    return r


def update(home, rid: str, **changes) -> dict:
    rules = load(home)
    for i, r in enumerate(rules):
        if r.get("id") == rid:
            rules[i] = validate({**r, **{k: v for k, v in changes.items() if v is not None}, "id": rid})
            save(home, rules)
            return rules[i]
    raise RuleError(f"no rule {rid}")


def remove(home, rid: str) -> None:
    rules = load(home)
    if not any(r.get("id") == rid for r in rules):
        raise RuleError(f"no rule {rid}")
    save(home, [r for r in rules if r.get("id") != rid])


def _applies(rule: dict, path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, g) or fnmatch.fnmatch(str(path), g) for g in rule.get("files") or [])


def _rx(rule: dict):
    return re.compile(rule["pattern"], sum({"i": re.I, "m": re.M, "s": re.S}[c] for c in rule.get("flags") or ""))


def violations(rule: dict, path: Path, text: str) -> str | None:
    """What the rule finds in this file, or None."""
    try:
        m = _rx(rule).search(text)
    except re.error:
        return None
    if rule["kind"] == "forbid" and m:
        line = text.count("\n", 0, m.start()) + 1
        return f"第 {line} 行：{text[max(0, m.start() - 30):m.end() + 30]!r}"
    if rule["kind"] == "require" and not m:
        return "找不到必要的內容"
    return None


def check(home, paths: list[Path], rules: list[dict] | None = None) -> list[dict]:
    """Issues (same shape as agent/qa.py) for every enabled rule that a file breaks; counts hits."""
    rules = [r for r in (rules if rules is not None else load(home)) if r.get("enabled", True)]
    if not rules:
        return []
    issues, hit = [], set()
    for p in paths:
        if p.suffix.lower() not in TEXT_EXT or not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for r in rules:
            if _applies(r, p):
                found = violations(r, p, text)
                if found:
                    hit.add(r["id"])
                    issues.append({"path": str(p), "name": p.name, "problem": f"違反品質規則「{r['title']}」：{r['message']}",
                                   "detail": found[:300], "rule": r["id"]})
    if hit:
        try:
            all_rules = load(home)
            for r in all_rules:
                if r.get("id") in hit:
                    r["hits"] = int(r.get("hits") or 0) + 1
            save(home, all_rules)
        except OSError:
            pass
    return issues


# ---------------------------------------------------------------- from a 👎 to a rule + a regression task
PROPOSE = """The user was NOT satisfied with the result of this task. Decide whether the problem they point at can be
caught MECHANICALLY in the files the task produced, with one regular expression - e.g. a forbidden leftover
("TODO", "lorem ipsum", a control character, a raw "$$" left unrendered), or something every such file must contain
("參考來源", a cover image line). If the complaint is about judgement, tone or facts that a regex cannot see, return
null for the rule.
USER FEEDBACK: {feedback}
REQUEST: {request}
FILES THE TASK PRODUCED (excerpts):
{files}
Reply with JSON only:
{{"rule": {{"title": "<short, user's language>", "files": ["*.md"], "kind": "forbid" | "require",
           "pattern": "<Python regex>", "flags": "<i/m/s or empty>", "message": "<what is wrong, user's language>"}} | null,
  "eval": {{"prompt": "<a request that would reproduce the task>", "rubric": "<what a good result must do, from the
           feedback>"}} | null}}"""


async def propose_from_feedback(rt, turn: dict, messages: list, rating: int, comment: str, tags: list[str]) -> dict:
    """A 👎 becomes, when it can, a rule that flags this turn's files, plus a regression task. Returns a report."""
    from .review import excerpts
    from ..fileaccess import resolve_file
    from ..learning.reflector import parse_json
    if rating > 0 or not rt.config.get("learning.quality_rules", True):
        return {}
    paths = []
    for m in messages:
        for c in getattr(m, "tool_calls", None) or []:
            p = (c.arguments or {}).get("path") if c.name in ("write_file", "edit_file") else None
            if p:
                try:
                    paths.append(resolve_file(rt.config, str(p)))
                except Exception:  # noqa: BLE001
                    continue
    paths = [p for p in dict.fromkeys(paths) if p.is_file()][:8]
    if not paths:
        return {}
    request = next((m.text() for m in messages if m.role == "user"), "")[:2000]
    feedback = (", ".join(tags) + "; " if tags else "") + (comment or "(no comment)")
    from ..types import Message
    prompt = PROPOSE.format(feedback=feedback[:1000], request=request, files=excerpts(paths)[:12000])
    try:
        resp = await rt.aux.chat([Message("user", prompt)], None, temperature=0, max_tokens=600)
        data = parse_json(resp.message.text())
    except Exception as e:  # noqa: BLE001
        log.info("rule proposal failed: %s", e)
        return {}
    report: dict = {}
    raw = data.get("rule") if isinstance(data, dict) else None
    if isinstance(raw, dict):
        try:
            rule = validate({**raw, "source": {"type": "feedback", "ref": turn.get("id"), "session": turn.get("session_id")}})
            texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in paths}
            caught = [p for p, t in texts.items() if _applies(rule, p) and violations(rule, p, t)]
            if caught:                       # it really catches the reported bug: keep it
                report["rule"] = add(rt.config.home, rule)
            else:
                report["rule_rejected"] = "the proposed rule does not flag the disliked files"
        except RuleError as e:
            report["rule_rejected"] = str(e)
    ev = data.get("eval") if isinstance(data, dict) else None
    if isinstance(ev, dict) and str(ev.get("prompt") or "").strip():
        from ..evals import add_regression
        graders = [{"type": "status", "is": "done"}]
        if report.get("rule"):
            graders.append({"type": "rule", "rule_id": report["rule"]["id"]})
        if ev.get("rubric"):
            graders.append({"type": "llm", "rubric": str(ev["rubric"])[:1000]})
        report["eval"] = add_regression(rt.config.home, {"prompt": str(ev["prompt"])[:3000], "graders": graders,
                                                          "source": {"type": "feedback", "ref": turn.get("id")}})
    return report
