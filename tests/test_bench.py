"""Model and skill evaluations for sinobee.ai reports: the model under test runs alone (every role), a named judge
grades the rubrics, each trial records time and tokens, and finished runs are published as one report."""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

from sinobee import account
from sinobee import evals as E
from sinobee.agent import Runtime
from sinobee.evals import publish as P
from sinobee.evals import runner
from sinobee.evals.graders import Trial, grade

from conftest import ScriptedProvider


def _suite(tmp_path, kind="model"):
    f = tmp_path / "bench-x.json"
    bench = {"kind": kind, "domain": "寫作", "skill": "greeter" if kind == "skill" else None, "threshold": 70,
             "categories": {"結果": 60, "語氣": 40}, "outputs": ["*.md"],
             "anchors": [{"rubric": "有打招呼", "content": "你好", "expect": True},
                         {"rubric": "有打招呼", "content": "……", "expect": False}]}
    f.write_text(json.dumps({"title": "模型評鑑：寫招呼", "bench": bench, "tasks": [{
        "id": "hello", "title": "寫招呼", "prompt": "寫 out.md 跟 Bee 打招呼", "toolsets": ["files"],
        "graders": [{"type": "file_regex", "label": "有打招呼", "path": "out.md", "pattern": "Bee", "category": "結果",
                     "critical": True},
                    {"type": "efficiency", "label": "步數不超過 5", "max_steps": 5, "category": "結果"},
                    {"type": "llm", "label": "語氣（評鑑模型）", "rubric": "friendly", "category": "語氣",
                     "owner": "thinking"}]}]}), encoding="utf-8")
    return f


def _child(seen, text="Hello, Bee"):
    def child(rt, cfg):
        seen.append({k: cfg.get(f"model.{k}") for k in ("default", "fallbacks", "thinking", "reviewer", "auxiliary")})
        p = ScriptedProvider()
        p.push("", [("write_file", {"path": "out.md", "content": text})])
        p.push("寫好了")
        return Runtime(cfg, provider=p)
    return child


async def test_the_model_under_test_runs_alone_and_the_judge_is_named(home, config, tmp_path, monkeypatch):
    config.data["model"].update(thinking="anthropic/claude-opus-5-5", reviewer="openai/gpt-x", auxiliary="ollama/small")
    seen = []
    monkeypatch.setattr(runner, "child_runtime", _child(seen))
    judge = ScriptedProvider([])
    judge.push(json.dumps({"pass": True, "reason": "anchor"}))
    judge.push(json.dumps({"pass": False, "reason": "anchor"}))
    for _ in range(3):        # an llm rubric in an evaluation suite is voted on three times
        judge.push(json.dumps({"pass": False, "reason": "too terse", "evidence": "Hello, Bee"}))
    import sinobee.providers as providers
    monkeypatch.setattr(providers, "build_provider", lambda spec, cfg: judge)
    rt = Runtime(config, provider=ScriptedProvider())
    doc = await runner.run(rt, str(_suite(tmp_path)), model="gemini/gemini-flash-latest", judge="gemini/gemini-pro-latest")
    assert seen == [{"default": "gemini/gemini-flash-latest", "fallbacks": [], "thinking": None, "reviewer": None,
                     "auxiliary": "gemini/gemini-flash-latest"}]
    assert doc["model"] == "gemini/gemini-flash-latest" and doc["solo"] and doc["judge"] == "gemini/gemini-pro-latest"
    assert doc["thinking"] == doc["execution"] == "gemini/gemini-flash-latest"
    assert doc["title"] == "模型評鑑：寫招呼" and doc["bench"]["domain"] == "寫作" and doc["sinobee_version"]
    assert doc["judge_usage"] == {"calls": 5, "input_tokens": 5000, "output_tokens": 2500}
    assert doc["judge_check"]["total"] == 2 and doc["judge_check"]["correct"] == 2
    assert len(doc["suite_hash"]) == 64 and len(doc["input_hash"]) == 64
    tr = doc["tasks"][0]["trials"][0]
    assert (tr["input_tokens"], tr["output_tokens"]) == (2000, 1000) and tr["started"] and tr["duration_s"] >= 0
    assert [(g["label"], g["ok"]) for g in tr["grades"]] == [("有打招呼", True), ("步數不超過 5", True),
                                                            ("語氣（評鑑模型）", False)]
    assert tr["grades"][2]["votes"] == [0, 3] and tr["grades"][2]["detail"].startswith("0/3 票")
    # SFS: 結果 2/2 × 60 + 語氣 0/1 × 40 → 60, below the pass mark of 70
    assert tr["q"] == 60.0 and not tr["crit_fail"] and not tr["qualified"]
    assert tr["outputs"] == [{"path": "out.md", "bytes": 10, "kind": "text"}]
    assert (E.evals_dir(home / "home") / "results" / doc["id"] / "hello" / "t1" / "out.md").read_text() == "Hello, Bee"
    assert tr["roles"]["execution"]["input_tokens"] == 2000 and tr["roles"]["thinking"]["input_tokens"] == 0
    q = doc["tasks"][0]["questions"]
    assert q[0] == {"label": "有打招呼", "category": "結果", "kind": "det", "critical": True, "owner": "execution",
                    "stage": "output"} and q[2]["kind"] == "judge" and q[2]["owner"] == "thinking"
    t = doc["totals"]
    assert t["score"] == pytest.approx(66.7) and t["input_tokens"] == 2000 and t["avg_duration_s"] >= 0
    assert t["sfs"] == {"n": 1, "mean": 60.0, "se": 0.0, "fair": 60.0} and t["qualified"] == 0


async def test_a_skill_evaluation_runs_a_thinking_and_execution_combination(home, config, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(runner, "child_runtime", _child(seen, text="Hi"))
    judge = ScriptedProvider([])
    judge.push(json.dumps({"pass": True}))
    judge.push(json.dumps({"pass": False}))
    for _ in range(3):
        judge.push(json.dumps({"pass": True, "reason": "warm"}))       # no evidence quoted: counted as a fail
    import sinobee.providers as providers
    monkeypatch.setattr(providers, "build_provider", lambda spec, cfg: judge)
    rt = Runtime(config, provider=ScriptedProvider())
    doc = await runner.run(rt, str(_suite(tmp_path, "skill")), model="gemini/gemini-flash-latest",
                           thinking="gemini/gemini-pro-latest", judge="gemini/gemini-pro-latest")
    assert seen[0]["default"] == "gemini/gemini-flash-latest" and seen[0]["thinking"] == "gemini/gemini-pro-latest"
    assert seen[0]["reviewer"] == "gemini/gemini-pro-latest" and seen[0]["auxiliary"] == "gemini/gemini-flash-latest"
    assert not doc["solo"] and doc["thinking"] == "gemini/gemini-pro-latest" and doc["execution"] == "gemini/gemini-flash-latest"
    tr = doc["tasks"][0]["trials"][0]
    assert not tr["grades"][0]["ok"] and "evidence" in tr["grades"][2]["detail"]
    assert tr["crit_fail"] and tr["q"] <= 40 and not tr["qualified"]       # the critical question caps the score
    with pytest.raises(E.EvalError, match="execution model"):
        await runner.run(rt, str(_suite(tmp_path, "skill")), thinking="gemini/gemini-pro-latest")


def test_sfs_scores_categories_caps_critical_failures_and_takes_off_the_standard_error():
    from sinobee.evals import sfs
    qs = [sfs.question({"type": "command", "category": "A", "critical": True}),
          sfs.question({"type": "command", "category": "A"}),
          sfs.question({"type": "llm", "category": "B", "owner": "nobody", "stage": "x"})]
    assert qs[2] == {"label": "llm", "category": "B", "kind": "judge", "critical": False, "owner": "execution",
                     "stage": "output"}
    w = sfs.weights({"categories": {"A": 30, "B": 10}}, qs)
    assert w == {"A": 30.0, "B": 10.0} and sfs.weights({}, qs) == {"A": 1.0, "B": 1.0}
    assert sfs.trial_score([True, False, True], qs, w) == {"q": 62.5, "crit_fail": False, "qualified": False}
    assert sfs.trial_score([True, True, True], qs, w)["qualified"]
    assert sfs.trial_score([False, True, True], qs, w) == {"q": 40.0, "crit_fail": True, "qualified": False}
    f = sfs.fair([81.75, 88.75, 85.75])
    assert f["mean"] == 85.42 and f["se"] == 2.03 and f["fair"] == 83.39
    assert sfs.family("gemini/gemini-pro-latest") == "gemini" and sfs.family("anthropic/claude-opus-5-5") == "claude"
    assert sfs.family("ollama/gemma4:e4b") == "gemma"


async def test_the_efficiency_grader_reads_the_trial_cost(tmp_path):
    t = Trial(prompt="p", workspace=tmp_path, tokens=1200, steps=7,
              trace=[{"retries": 2}, {"retries": 1, "failed": True}])
    ok, why = await grade({"type": "efficiency", "max_tokens": 2000, "max_steps": 10}, t)
    assert ok and why == "tokens 1200, steps 7"
    ok, why = await grade({"type": "efficiency", "max_retries": 2, "max_failed": 0}, t)
    assert not ok and why == "retries 3 > 2; failed steps 1 > 0"


async def test_a_second_judge_is_asked_once_and_its_agreement_recorded(tmp_path):
    (tmp_path / "a.md").write_text("Hello")
    j1, j2 = ScriptedProvider([]), ScriptedProvider([])
    for v in (True, True, False):
        j1.push(json.dumps({"pass": v, "reason": "r", "evidence": "Hello"}))
    j2.push(json.dumps({"pass": False, "reason": "no", "evidence": "x"}))
    from sinobee.evals.graders import grade_full
    t = Trial(prompt="p", workspace=tmp_path, files=[tmp_path / "a.md"], judge=j1, judge2=j2)
    r = await grade_full({"type": "llm", "rubric": "greets", "votes": 3, "require_evidence": True}, t)
    assert r == {"ok": True, "detail": "2/3 票：r", "evidence": "Hello", "votes": [2, 3], "judge2_ok": False}
    assert len(j2.requests) == 1 and j1.requests[0][0][0].content == j2.requests[0][0][0].content


def test_the_bench_suites_are_valid_and_their_graders_tell_right_from_wrong(home):
    for name in ("bench-coding", "bench-youtube"):
        suite, tasks = E.load(name, home=home)
        meta = E.bench_meta(name)
        assert meta["bench"]["kind"] in ("model", "skill") and all(g.get("label") for t in tasks for g in t["graders"])
    _, (task,) = E.load("bench-coding", home=home)

    async def grades(fix: bool):
        ws = Path(tempfile.mkdtemp())
        runner._setup(ws, task["setup"]["files"])
        if fix:
            m = ws / "ledger/money.py"
            m.write_text(m.read_text().replace('.replace(",", ".")', '.replace(",", "")'))
            r = ws / "ledger/report.py"
            r.write_text(r.read_text().replace("return date[:6]", "return date[:7]").replace(
                'e["category"])', 'e["category"].lower())') + '''

def budget_alerts(entries, budgets, threshold=0.8):
    caps = {k.lower(): parse_amount(v) for k, v in budgets.items()}
    out = []
    for (month, cat), spent in sorted(monthly_totals(entries).items()):
        if cat in caps and (spent > caps[cat] or spent >= caps[cat] * Decimal(str(threshold))):
            out.append({"month": month, "category": cat, "spent": spent, "budget": caps[cat],
                        "level": "over" if spent > caps[cat] else "warn"})
    return out
''')
            (ws / "tests/test_budget.py").write_text(
                "import unittest\nfrom ledger.report import budget_alerts\n\n\nclass T(unittest.TestCase):\n"
                "    def test_none(self):\n        self.assertEqual(budget_alerts([], {}), [])\n")
        t = Trial(prompt=task["prompt"], workspace=ws, status="done")
        out = {g["label"]: (await grade(g, t))[0] for g in task["graders"] if g["type"] != "llm"}
        shutil.rmtree(ws, ignore_errors=True)
        return out
    assert all(asyncio.run(grades(True)).values())
    wrong = asyncio.run(grades(False))
    assert not wrong["全部單元測試通過"] and not wrong["新功能符合規格（隱藏測試）"] and wrong["沒有修改既有測試"]


def _doc(rid, thinking, execution, qs=(80.0,), started=1792500000, judge="gemini/gemini-pro-latest", h="h1"):
    return {"id": rid, "suite": "bench-youtube", "status": "done", "model": execution, "solo": thinking == execution,
            "thinking": thinking, "execution": execution, "judge": judge, "judge2": None, "suite_hash": h,
            "input_hash": "i1", "started": started, "finished": started + 600, "sinobee_version": "0.9",
            "title": "技能評鑑：youtube", "bench": {"kind": "skill", "skill": "youtube-analysis", "version": "1.1",
                                                 "threshold": 70, "summary": "s"},
            "judge_check": {"total": 4, "correct": 4, "items": []}, "totals": {"judge2_agree": [3, 4]},
            "tasks": [{"id": "yt", "title": "t", "prompt": "p", "questions": [{"label": "a", "category": "正確性",
                       "kind": "det", "critical": True, "owner": "execution", "stage": "output"}],
                       "weights": {"正確性": 40.0}, "trials": [
                           {"trial": i + 1, "passed": q >= 70, "q": q, "qualified": q >= 70, "crit_fail": False,
                            "status": "done", "duration_s": 200.0, "cost_usd": 0.7, "input_tokens": 9, "output_tokens": 1,
                            "roles": {"thinking": {"input_tokens": 1}}, "steps_trace": [{"step": 1, "tokens": 10}],
                            "grades": [{"type": "file_regex", "label": "a", "ok": True, "detail": "d", "evidence": "e",
                                        "votes": [3, 3]}], "workdir": "/tmp/x", "answer": "ok",
                            "outputs": [{"path": "r.md", "bytes": 5, "kind": "text"}]} for i, q in enumerate(qs)]}]}


def test_publish_merges_the_runs_of_a_combination_and_sends_one_report_each(home, config, monkeypatch):
    a1 = _doc("a1", "gemini/pro", "gemini/flash", (81.75,))
    a2 = _doc("a2", "gemini/pro", "gemini/flash", (88.75, 85.75), started=1792501000)
    b1 = _doc("b1", "gemini/pro", "gemini/pro", (90.0,))
    with pytest.raises(P.PublishError, match="different judges"):
        P.payloads([a1, _doc("x", "a", "b", judge="other")])
    with pytest.raises(P.PublishError, match="different suite versions"):
        P.payloads([a1, _doc("x", "a", "b", h="h2")])
    with pytest.raises(P.PublishError, match="bench block"):
        P.payloads([{**a1, "bench": None}])
    out = P.payloads([a2, b1, a1])
    assert len(out) == 2
    r = next(x for x in out if x["execution"] == "gemini/flash")
    assert r["kind"] == "skill" and r["subject"] == "youtube-analysis" and r["subject_version"] == "1.1"
    assert r["thinking"] == "gemini/pro" and r["runs"] == ["a1", "a2"] and r["tested_at"] == 1792500000
    assert r["judge_check"] == {"total": 8, "correct": 8} and r["judge2_agree"] == [6, 8] and r["creativity"] is None
    trials = r["tasks"][0]["trials"]
    assert [t["trial"] for t in trials] == [1, 2, 3] and [t["q"] for t in trials] == [81.75, 88.75, 85.75]
    assert trials[0]["grades"] == [{"ok": True, "detail": "d", "evidence": "e", "votes": [3, 3]}]
    assert "workdir" not in trials[0] and trials[0]["steps_trace"] == [{"step": 1, "tokens": 10}]
    assert r["tasks"][0]["questions"][0]["critical"] and r["tasks"][0]["weights"] == {"正確性": 40.0}

    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.url.path, json.loads(req.content)))
        return httpx.Response(201, json={"id": len(sent), "url": f"https://sinobee.ai/benchmarks/evals/{len(sent)}"})
    real = httpx.Client

    def call(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(account.httpx, "request", call)
    with pytest.raises(P.PublishError, match="sign in"):
        P.publish(config, [a1])
    account.save(config, {"access_token": "sbh_" + "a" * 40, "expires_in": 900, "scope": "llm"})
    with pytest.raises(P.PublishError, match="sign in again"):
        P.publish(config, [a1])
    account.save(config, {"access_token": "sbh_" + "a" * 40, "expires_in": 900, "scope": "llm evals:write"})
    res = P.publish(config, [a1, b1])
    assert [x["url"][-1] for x in res] == ["1", "2"] and all(p == "/api/v1/evals" for p, _ in sent)


async def test_creativity_is_compared_blind_in_both_orders_among_qualified_results(home, config):
    from sinobee.evals import compare as C
    rt = Runtime(config, provider=ScriptedProvider())
    res_dir = E.evals_dir(config.home) / "results"
    docs = [_doc("c1", "g/pro", "g/flash", (80.0, 90.0)), _doc("c2", "g/pro", "g/pro", (85.0, 60.0))]
    for d in docs:
        for t in d["tasks"][0]["trials"]:
            f = res_dir / d["id"] / "yt" / f"t{t['trial']}" / "r.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"report of {d['id']} trial {t['trial']}")
        (res_dir / f"{d['id']}.json").write_text(json.dumps(d))
    judge = ScriptedProvider([])
    judge.push(json.dumps({"winner": "A"}))       # c1 first: c1 wins
    judge.push(json.dumps({"winner": "B"}))       # swapped, c1 second: c1 wins again
    rt.judge = lambda: judge
    res = await C.compare(rt, docs)
    # c2's second trial did not qualify, so one pair of trials, asked twice
    assert res["scores"] == {"g/pro+g/flash": {"wins": 2, "total": 2}, "g/pro+g/pro": {"wins": 0, "total": 2}}
    asked = [m[0][0].content for m in judge.requests]
    assert "report of c1 trial 1" in asked[0].split("RESULT B")[0] and "report of c1 trial 1" in asked[1].split("RESULT B")[1]
    assert "c1" not in asked[0].split("RESULT A")[0]                 # blind: no run or model names
    saved = E.result(config.home, "c1")
    assert saved["creativity"] == {"compare": res["id"], "judge": "test/scripted", "wins": 2, "total": 2, "combos": 2}
    assert P.payloads([saved, E.result(config.home, "c2")])[0]["creativity"]["wins"] in (0, 2)
    with pytest.raises(E.EvalError, match="two model combinations"):
        await C.compare(rt, docs[:1])
