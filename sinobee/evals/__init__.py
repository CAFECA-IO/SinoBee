"""Evaluations: run the agent on fixed tasks and grade the result, so a change of model, prompt, skill or setting
can be measured instead of guessed - and a bug the user reported once stays fixed.

A task:

    {"id": "latex-html", "prompt": "…", "kind": "capability" | "regression", "trials": 1, "timeout": 900,
     "toolsets": ["files", "shell"],                       # optional: narrow the tools
     "setup": {"files": {"notes.md": "text …", "img/a.png": {"base64": "…"}, "data.csv": {"from": "<skill file>"}}},
     "graders": [{"type": "file_exists", "path": "physics.html"}, {"type": "qa_clean"}, {"type": "llm", "rubric": "…"}]}

Suites:
  core          built in (suites/core.json): the kinds of mistakes SinoBee has made before - mermaid syntax,
                LaTeX in pages, broken image links, skipping the checklist, making up an answer
  regressions   $SINOBEE_HOME/evals/regressions.json: tasks saved from 👎 feedback (see agent/rules.py)
  skill:<name>  a skill's evals/evals.json (the anatomy format: {skill_name, evals: [{id, prompt, expected_output,
                files}]}); graded by the expected output
  <path>.json   any file with {"tasks": [...]} or a list of tasks
Results go to $SINOBEE_HOME/evals/results/<run id>.json; see runner.py for pass@k / pass^k and regressions.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

SUITES_DIR = Path(__file__).resolve().parent / "suites"
GRADER_TYPES = ("file_exists", "file_regex", "file_not_regex", "answer_regex", "answer_not_regex", "qa_clean",
                "status", "command", "tool_used", "rule", "llm")
MAX_REGRESSIONS = 200


class EvalError(ValueError):
    pass


def evals_dir(home) -> Path:
    p = Path(home) / "evals"
    (p / "results").mkdir(parents=True, exist_ok=True)
    return p


def _reg_path(home) -> Path:
    return evals_dir(home) / "regressions.json"


def regressions(home) -> list[dict]:
    try:
        data = json.loads(_reg_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_regressions(home, tasks: list[dict]) -> None:
    _reg_path(home).write_text(json.dumps(tasks[-MAX_REGRESSIONS:], ensure_ascii=False, indent=1), encoding="utf-8")


def normalize(task: dict, default_id: str = "") -> dict:
    """Check a task and fill the defaults; raises EvalError."""
    if not isinstance(task, dict):
        raise EvalError("a task must be an object")
    prompt = str(task.get("prompt") or "").strip()
    if not prompt:
        raise EvalError(f"task {task.get('id') or default_id!r} has no prompt")
    graders = task.get("graders") or [{"type": "status", "is": "done"}]
    for g in graders:
        if not isinstance(g, dict) or g.get("type") not in GRADER_TYPES:
            raise EvalError(f"task {task.get('id') or default_id!r}: unknown grader {g!r}")
    out = {**task, "id": str(task.get("id") or default_id or "t" + secrets.token_hex(3)), "prompt": prompt,
           "graders": graders, "kind": task.get("kind") or "capability",
           "trials": max(1, min(int(task.get("trials") or 1), 10)),
           "timeout": max(30, min(int(task.get("timeout") or 900), 3600))}
    return out


def add_regression(home, task: dict) -> dict:
    """Save a regression task (from a 👎, or added by hand); the same prompt is stored once."""
    t = normalize({**task, "kind": "regression", "id": task.get("id") or "reg-" + secrets.token_hex(3)})
    t.setdefault("created", time.time())
    tasks = [x for x in regressions(home) if x.get("prompt") != t["prompt"] and x.get("id") != t["id"]]
    tasks.append(t)
    _save_regressions(home, tasks)
    return t


def remove_regression(home, tid: str) -> None:
    tasks = regressions(home)
    if not any(t.get("id") == tid for t in tasks):
        raise EvalError(f"no regression task {tid}")
    _save_regressions(home, [t for t in tasks if t.get("id") != tid])


def _from_skill(skill) -> list[dict]:
    f = skill.path / "evals" / "evals.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EvalError(f"skill {skill.name} has no readable evals/evals.json ({e})") from None
    tasks = []
    for i, ev in enumerate(data.get("evals") or []):
        if not isinstance(ev, dict) or not ev.get("prompt"):
            continue
        files = {}
        for rel in ev.get("files") or []:
            src = (skill.path / rel).resolve()
            if str(src).startswith(str(skill.path.resolve()) + "/") and src.is_file():
                files[Path(rel).name] = {"from": str(src)}
        graders = [{"type": "status", "is": "done"}, {"type": "qa_clean"}]
        if ev.get("expected_output"):
            graders.append({"type": "llm", "rubric": str(ev["expected_output"])})
        graders += [g for g in ev.get("graders") or [] if isinstance(g, dict)]
        tasks.append(normalize({"id": f"{skill.name}:{ev.get('id', i + 1)}", "prompt": f"/{skill.name} {ev['prompt']}",
                                "setup": {"files": files}, "graders": graders, "trials": ev.get("trials", 1),
                                "timeout": ev.get("timeout", 1800)}))
    if not tasks:
        raise EvalError(f"skill {skill.name}: evals/evals.json has no evals")
    return tasks


def load(target: str, *, home, skills=None) -> tuple[str, list[dict]]:
    """(suite name, tasks) for core | regressions | skill:<name> | a JSON file path."""
    target = (target or "core").strip()
    if target == "regressions":
        tasks = [normalize(t) for t in regressions(home)]
        if not tasks:
            raise EvalError("no regression tasks yet (they are saved from 👎 feedback, or with add_regression)")
        return "regressions", tasks
    if target.startswith("skill:"):
        name = target.split(":", 1)[1]
        if skills is None or name not in skills.skills:
            raise EvalError(f"no skill {name}")
        return target, _from_skill(skills.skills[name])
    f = SUITES_DIR / f"{target}.json" if "/" not in target and not target.endswith(".json") else Path(target).expanduser()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EvalError(f"cannot read suite {target}: {e}") from None
    raw = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        raise EvalError(f"suite {target} has no tasks")
    return (f.stem if f.parent == SUITES_DIR else str(f)), [normalize(t, f"t{i + 1}") for i, t in enumerate(raw)]


def suites(home, skills=None) -> list[dict]:
    out = []
    for f in sorted(SUITES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem, "title": data.get("title") or f.stem, "tasks": len(data.get("tasks") or [])})
        except (OSError, ValueError):
            continue
    reg = regressions(home)
    out.append({"name": "regressions", "title": "回歸測試（從 👎 回饋產生）", "tasks": len(reg)})
    for name, s in sorted((skills.skills if skills else {}).items()):
        f = s.path / "evals" / "evals.json"
        if f.is_file():
            try:
                n = len(json.loads(f.read_text(encoding="utf-8")).get("evals") or [])
            except (OSError, ValueError):
                n = 0
            out.append({"name": f"skill:{name}", "title": f"技能 {name}", "tasks": n})
    return out


def results(home, limit: int = 20) -> list[dict]:
    """Recent runs (summaries, newest first)."""
    out = []
    for f in sorted(evals_dir(home).joinpath("results").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: data.get(k) for k in ("id", "suite", "status", "started", "finished", "model", "totals",
                                             "regressions", "k", "progress")})
        if len(out) >= limit:
            break
    return out


def result(home, rid: str) -> dict:
    f = evals_dir(home) / "results" / f"{Path(rid).name}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvalError(f"no eval run {rid}") from None
