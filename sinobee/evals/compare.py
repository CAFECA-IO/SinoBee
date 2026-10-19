"""Creativity: a blind, pairwise comparison of what different model combinations produced for the same suite.

    sinobee eval compare <run id> <run id> ... --judge <model>

"Is this creative?" has no absolute answer a judge gives reliably, but "which of these two offers more original,
useful insight?" it answers far more steadily. So for every pair of combinations, the qualified trials of each task
are paired up (trial 1 with trial 1, …) and the judge is asked twice with the two results swapped, so that a
preference for whichever comes first cancels out. Results that did not qualify (score below the pass mark or a
critical question failed) take no part: something wrong cannot win on novelty.

Each run gets `creativity` = {compare, judge, wins, total}; sinobee.ai shows wins / total as the creativity rate.
"""
from __future__ import annotations

import itertools
import json
import logging
import time
import uuid
from pathlib import Path

from . import EvalError, evals_dir

log = logging.getLogger("sinobee.evals")
MAX_PAIRS = 3          # trial pairs per task and pair of combinations

PROMPT = """Two results of the same task, made by two different AI setups (you are not told which). Both passed the
quality checks. Which one offers more original and useful insight - angles, explanations, structure or extras a
reader would value beyond what was asked - while staying accurate? Do not prefer the longer one for its length.
TASK: {prompt}
RESULT A:
{a}
RESULT B:
{b}
Reply with JSON only: {{"winner": "A" | "B", "reason": "<one sentence>"}}"""


def key(doc: dict) -> str:
    return f"{doc.get('thinking') or doc.get('model')}+{doc.get('execution') or doc.get('model')}"


def _text(home, doc: dict, task_id: str, trial: dict, limit: int = 7000) -> str:
    import re
    base = evals_dir(home) / "results" / doc["id"] / re.sub(r"[^\w.-]+", "-", task_id) / f"t{trial.get('trial')}"
    parts = []
    for o in trial.get("outputs") or []:
        if o.get("kind") != "text":
            continue
        try:
            parts.append(f"--- {o['path']}\n" + (base / o["path"]).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    text = "\n".join(parts) or str(trial.get("answer") or "")
    return text[:limit]


async def _ask(judge, prompt: str) -> str | None:
    from ..learning.reflector import parse_json
    from ..types import Message
    resp = await judge.chat([Message("user", prompt)], None, temperature=0, max_tokens=300)
    w = str(parse_json(resp.message.text()).get("winner") or "").strip().upper()[:1]
    return w if w in ("A", "B") else None


async def compare(rt, runs: list[dict], judge_spec: str | None = None, emit=None) -> dict:
    from .runner import judge_provider
    if len({r.get("suite") for r in runs}) > 1:
        raise EvalError("runs come from different suites")
    groups: dict[str, list[dict]] = {}
    for r in runs:
        if r.get("status") != "done":
            raise EvalError(f"run {r['id']} is not finished")
        groups.setdefault(key(r), []).append(r)
    if len(groups) < 2:
        raise EvalError("give runs of at least two model combinations")
    home = rt.config.home
    usage: dict = {}
    judge = judge_provider(rt, judge_spec, usage)

    def qualified(docs: list[dict]) -> dict[str, list[tuple[dict, dict]]]:
        out: dict[str, list[tuple[dict, dict]]] = {}
        for d in docs:
            for t in d.get("tasks") or []:
                out.setdefault(t["id"], []).extend((d, x) for x in t["trials"] if x.get("qualified"))
        return out
    q = {k: qualified(v) for k, v in groups.items()}
    prompts = {t["id"]: t.get("prompt", "") for d in runs for t in d.get("tasks") or []}
    score = {k: {"wins": 0, "total": 0} for k in groups}
    pairs = []
    for ka, kb in itertools.combinations(groups, 2):
        for tid in prompts:
            for (da, ta), (db, tb) in list(zip(q[ka].get(tid, []), q[kb].get(tid, [])))[:MAX_PAIRS]:
                a, b = _text(home, da, tid, ta), _text(home, db, tid, tb)
                for first, second, fk, sk in ((a, b, ka, kb), (b, a, kb, ka)):
                    try:
                        w = await _ask(judge, PROMPT.format(prompt=prompts[tid][:2000], a=first, b=second))
                    except Exception as e:  # noqa: BLE001 - one failed call skips that comparison
                        log.warning("creativity comparison failed: %s", e)
                        w = None
                    if w is None:
                        continue
                    win = fk if w == "A" else sk
                    score[fk]["total"] += 1
                    score[sk]["total"] += 1
                    score[win]["wins"] += 1
                    pairs.append({"task": tid, "a": fk, "b": sk, "winner": win})
                if emit:
                    emit("compare_progress", {"pairs": len(pairs)})
    cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    jlabel = judge_spec or getattr(judge, "label", "") or None
    res = {"id": cid, "suite": runs[0].get("suite"), "judge": jlabel, "created": time.time(), "scores": score,
           "pairs": pairs, "runs": [r["id"] for r in runs], "judge_usage": usage}
    folder = evals_dir(home) / "results"
    (folder / f"compare-{cid}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, docs in groups.items():
        for d in docs:
            d["creativity"] = {"compare": cid, "judge": jlabel, **score[k], "combos": len(groups)}
            Path(folder / f"{d['id']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return res
