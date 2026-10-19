"""SFS v1 - the scoring of published evaluations (sinobee.ai /benchmarks/evals).

Every grader is a yes/no question in a category. A suite's `bench` block sets the category weights, the pass mark
and which questions are critical:

    "bench": {"kind": "skill", "skill": "youtube-analysis", "threshold": 70,
              "categories": {"正確性": 40, "完整性": 25, "可讀性": 15, "流程健康": 20},
              "anchors": [{"rubric": "…", "content": "…", "expect": true}]}
    grader:  {"type": "llm", "label": "…", "category": "正確性", "critical": true,
              "owner": "execution", "stage": "output", "votes": 3}

  trial score Q = Σ weight × (questions passed ÷ questions) over the categories it has, out of 100
  a critical question that fails caps Q at 40; a trial *qualifies* when Q >= threshold and no critical one failed
  owner   who loses the point when it fails: skill | thinking | execution (the report shows where points went)
  stage   where it is checked: plan | exec | review | output

sinobee.ai recomputes everything from the trials (mean, standard error, fair score F = mean - SE, the four awards);
this module gives SinoBee the same numbers for its own summary.
"""
from __future__ import annotations

import hashlib
import json
import math

DEFAULT_CATEGORY = "結果"
CRIT_CAP = 40.0
OWNERS = ("skill", "thinking", "execution")
STAGES = ("plan", "exec", "review", "output")
FLOW_CATEGORY = "流程健康"


def question(g: dict) -> dict:
    """The published description of one grader (no rubric text that would leak hidden tests)."""
    owner = g.get("owner") if g.get("owner") in OWNERS else "execution"
    stage = g.get("stage") if g.get("stage") in STAGES else "output"
    return {"label": g.get("label") or g["type"], "category": str(g.get("category") or DEFAULT_CATEGORY),
            "kind": "judge" if g.get("type") == "llm" else "det", "critical": bool(g.get("critical")),
            "owner": owner, "stage": stage}


def weights(bench: dict, questions: list[dict]) -> dict[str, float]:
    cats = []
    for q in questions:
        if q["category"] not in cats:
            cats.append(q["category"])
    given = {str(k): float(v) for k, v in ((bench or {}).get("categories") or {}).items() if float(v) > 0}
    return {c: given.get(c, 0.0) for c in cats} if given else {c: 1.0 for c in cats}


def trial_score(oks: list[bool], questions: list[dict], w: dict[str, float], threshold: float = 70) -> dict:
    """Q, whether a critical question failed and whether the trial qualifies."""
    total = sum(v for v in w.values() if v > 0) or 1.0
    q = 0.0
    for cat, wt in w.items():
        idx = [i for i, x in enumerate(questions) if x["category"] == cat]
        if idx and wt > 0:
            q += wt * sum(1 for i in idx if i < len(oks) and oks[i]) / len(idx)
    q = 100 * q / total
    crit = any(questions[i]["critical"] and not (i < len(oks) and oks[i]) for i in range(len(questions)))
    if crit:
        q = min(q, CRIT_CAP)
    q = round(q, 2)
    return {"q": q, "crit_fail": crit, "qualified": (q >= threshold) and not crit}


def fair(qs: list[float]) -> dict:
    """mean, standard error and the fair score F = mean - SE (SE = 0 for a single trial)."""
    n = len(qs)
    if not n:
        return {"n": 0, "mean": 0.0, "se": 0.0, "fair": 0.0}
    m = sum(qs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in qs) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n)
    return {"n": n, "mean": round(m, 2), "se": round(se, 2), "fair": round(m - se, 2)}


def suite_hash(tasks: list[dict], bench: dict) -> str:
    """Fixes what was asked and how it is graded: the tasks (prompts, setup files, graders) and the scoring."""
    keep = [{k: t.get(k) for k in ("id", "prompt", "setup", "graders", "toolsets", "timeout")} for t in tasks]
    body = json.dumps({"tasks": keep, "bench": {k: v for k, v in (bench or {}).items() if k != "summary"}},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def input_hash(tasks: list[dict]) -> str:
    """The inputs every trial starts from: the prompts and the setup files."""
    keep = [{"prompt": t.get("prompt"), "files": (t.get("setup") or {}).get("files")} for t in tasks]
    return hashlib.sha256(json.dumps(keep, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def family(model: str | None) -> str:
    """claude | gemini | gpt | … from a model spec ("gemini/gemini-pro-latest", "anthropic/claude-opus-5-5")."""
    m = (model or "").lower().split("/")[-1]
    for f in ("claude", "gemini", "gpt", "gemma", "llama", "qwen", "mistral", "deepseek", "grok"):
        if f in m:
            return f
    return m.split("-")[0].split(":")[0]
