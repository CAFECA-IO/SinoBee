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


def _suite(tmp_path):
    f = tmp_path / "bench-x.json"
    f.write_text(json.dumps({"title": "模型評鑑：寫招呼", "bench": {"kind": "model", "domain": "寫作"}, "tasks": [{
        "id": "hello", "title": "寫招呼", "prompt": "寫 out.md 跟 Bee 打招呼", "toolsets": ["files"],
        "graders": [{"type": "file_regex", "label": "有打招呼", "path": "out.md", "pattern": "Bee"},
                    {"type": "llm", "label": "語氣（評鑑模型）", "rubric": "friendly"}]}]}), encoding="utf-8")
    return f


async def test_the_model_under_test_runs_alone_and_the_judge_is_named(home, config, tmp_path, monkeypatch):
    config.data["model"].update(thinking="anthropic/claude-opus-5-5", reviewer="openai/gpt-x", auxiliary="ollama/small")
    seen = []

    def child(rt, cfg):
        seen.append({k: cfg.get(f"model.{k}") for k in ("default", "fallbacks", "thinking", "reviewer", "auxiliary")})
        p = ScriptedProvider()
        p.push("", [("write_file", {"path": "out.md", "content": "Hello, Bee"})])
        p.push("寫好了")
        return Runtime(cfg, provider=p)
    monkeypatch.setattr(runner, "child_runtime", child)
    judge = ScriptedProvider([])
    judge.push(json.dumps({"pass": False, "reason": "too terse"}))
    import sinobee.providers as providers
    monkeypatch.setattr(providers, "build_provider", lambda spec, cfg: judge)
    rt = Runtime(config, provider=ScriptedProvider())
    doc = await runner.run(rt, str(_suite(tmp_path)), model="gemini/gemini-flash-latest", judge="gemini/gemini-pro-latest")
    assert seen == [{"default": "gemini/gemini-flash-latest", "fallbacks": [], "thinking": None, "reviewer": None,
                     "auxiliary": "gemini/gemini-flash-latest"}]
    assert doc["model"] == "gemini/gemini-flash-latest" and doc["solo"] and doc["judge"] == "gemini/gemini-pro-latest"
    assert doc["title"] == "模型評鑑：寫招呼" and doc["bench"]["domain"] == "寫作" and doc["sinobee_version"]
    assert doc["judge_usage"] == {"calls": 1, "input_tokens": 1000, "output_tokens": 500}
    tr = doc["tasks"][0]["trials"][0]
    assert (tr["input_tokens"], tr["output_tokens"]) == (2000, 1000) and tr["started"] and tr["duration_s"] >= 0
    assert [(g["label"], g["ok"]) for g in tr["grades"]] == [("有打招呼", True), ("語氣（評鑑模型）", False)]
    assert doc["tasks"][0]["graders"] == ["有打招呼", "語氣（評鑑模型）"] and doc["tasks"][0]["title"] == "寫招呼"
    t = doc["totals"]
    assert t["score"] == 50.0 and t["input_tokens"] == 2000 and t["avg_duration_s"] >= 0


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


def _run(model, judge="gemini/gemini-pro-latest", suite="bench-coding"):
    return {"id": f"r-{model}", "suite": suite, "status": "done", "model": model, "solo": True, "judge": judge,
            "started": 1792500000, "finished": 1792503600, "sinobee_version": "0.9", "title": "模型評鑑：程式",
            "bench": {"kind": "model", "domain": "程式修改與除錯"}, "totals": {"score": 80, "input_tokens": 10},
            "tasks": [{"id": "coding-ledger", "title": "t", "prompt": "p", "graders": ["a"], "pass_at_k": True,
                       "pass_hat_k": True, "trials": [{"trial": 1, "passed": True, "grades": [
                           {"type": "command", "label": "a", "ok": True, "detail": "exit 0"}], "duration_s": 61.2,
                           "input_tokens": 9, "output_tokens": 1, "workdir": "/tmp/x", "answer": "ok"}]}]}


def test_publish_checks_the_runs_and_sends_one_report(home, config, monkeypatch):
    with pytest.raises(P.PublishError, match="different judges"):
        P.payload([_run("a"), _run("b", judge="other")])
    with pytest.raises(P.PublishError, match="one run per model"):
        P.payload([_run("a"), _run("a")])
    with pytest.raises(P.PublishError, match="different suites"):
        P.payload([_run("a"), _run("b", suite="x")])
    body = P.payload([_run("gemini/a"), _run("ollama/b")], body="# 報告")
    assert body["kind"] == "model" and body["judge"] == "gemini/gemini-pro-latest" and body["domain"] == "程式修改與除錯"
    assert [r["model"] for r in body["runs"]] == ["gemini/a", "ollama/b"] and body["tested_at"] == 1792500000
    tr = body["runs"][0]["tasks"][0]["trials"][0]
    assert tr["duration_s"] == 61.2 and tr["input_tokens"] == 9 and "workdir" not in tr

    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.url.path, json.loads(req.content)))
        return httpx.Response(201, json={"id": "st1", "url": "https://sinobee.ai/benchmarks/studies/st1"})
    real = httpx.Client

    def call(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(account.httpx, "request", call)
    with pytest.raises(P.PublishError, match="sign in"):
        P.publish(config, [_run("gemini/a")])
    account.save(config, {"access_token": "sbh_" + "a" * 40, "expires_in": 900, "scope": "llm"})
    with pytest.raises(P.PublishError, match="sign in again"):
        P.publish(config, [_run("gemini/a")])
    account.save(config, {"access_token": "sbh_" + "a" * 40, "expires_in": 900, "scope": "llm evals:write"})
    res = P.publish(config, [_run("gemini/a"), _run("ollama/b")], body="# 報告")
    assert res["url"].endswith("/st1") and sent[0][0] == "/api/v1/evaluations" and sent[0][1]["body"] == "# 報告"
