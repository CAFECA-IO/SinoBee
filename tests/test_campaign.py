"""Evaluation campaigns (M3): several thinking + execution combinations on one suite, interleaved, within a budget;
stop and resume; the four answers; the checks before submitting and publishing one report per combination."""
import asyncio
import json

import pytest

from sinobee.agent import Runtime
from sinobee.evals import EvalError, campaign as C, runner
from sinobee.evals import publish as P

from conftest import ScriptedProvider

FAST, SLOW, BAD = "openai/gpt-fast", "anthropic/claude-slow", "ollama/gemma-bad"


def _suite(tmp_path):
    f = tmp_path / "bench-greet.json"
    bench = {"kind": "skill", "skill": "greeter", "threshold": 70, "categories": {"結果": 70, "流程": 30}}
    f.write_text(json.dumps({"title": "技能評鑑：招呼", "bench": bench, "tasks": [
        {"id": f"t{i}", "prompt": f"寫 out.md 跟 Bee{i} 打招呼", "toolsets": ["files"],
         "graders": [{"type": "file_regex", "label": "有打招呼", "path": "out.md", "pattern": "Bee", "category": "結果",
                      "critical": True},
                     {"type": "file_regex", "label": "有驚嘆號", "path": "out.md", "pattern": "!", "category": "結果"},
                     {"type": "status", "is": "done", "label": "完成", "category": "流程"},
                     {"type": "efficiency", "max_steps": 5, "label": "步數", "category": "流程"}]}
        for i in (1, 2)]}), encoding="utf-8")
    return str(f)


def _child(order):
    texts = {FAST: "Hi Bee!", SLOW: "Hello Bee, nice day!", BAD: "hello"}

    def child(rt, cfg):
        ex = cfg.get("model.default")
        order.append((cfg.get("model.thinking"), ex))
        p = ScriptedProvider()
        p.push("", [("write_file", {"path": "out.md", "content": texts[ex]})])
        p.push("寫好了")
        return Runtime(cfg, provider=p)
    return child


@pytest.fixture
def rt(home, config, monkeypatch):
    monkeypatch.setattr(C, "remote_tested", lambda *a, **k: {})
    config.data.setdefault("skills", {})["hub_url"] = "http://127.0.0.1:9"     # no sinobee.ai in tests
    return Runtime(config, provider=ScriptedProvider())


COMBOS = [{"thinking": SLOW, "execution": SLOW, "baseline": True}, {"thinking": SLOW, "execution": FAST},
          {"thinking": FAST, "execution": BAD}]


def test_trials_are_interleaved_and_published_combinations_are_skipped(rt, tmp_path):
    assert C.schedule([{"trials": 2}, {"trials": 3}, {"trials": 2, "skip": True}]) == [[0, 1], [1, 1], [0, 2], [1, 2], [1, 3]]
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS + [COMBOS[1]], k=3, judge="openai/gpt-judge",
              tested={C.ckey(SLOW, FAST): 3, C.ckey(SLOW, SLOW): 5}, session_id="s1")
    assert [x["skip"] for x in c["combos"]] == [False, True, False]          # the baseline always runs; dupes dropped
    assert c["combos"][1]["published_trials"] == 3
    assert c["plan"] == [[0, 1], [2, 1], [0, 2], [2, 2], [0, 3], [2, 3]] and c["progress"]["total"] == 6
    est = c["estimate"]
    assert est["basis"] == "default" and est["tasks"] == 2 and est["trials"] == 6
    assert c["budget_usd"] == est["suggested_budget"] and 1.0 <= c["budget_usd"] <= 20.0
    assert C.listing(rt.config.home, "s1")[0]["id"] == c["id"]
    with pytest.raises(EvalError):
        C.new(rt, suite=_suite(tmp_path), combos=[{"execution": "nomodel"}])


def test_the_estimate_prices_both_roles_and_the_judge(rt, tmp_path):
    rt.config.data["pricing"] = {"claude-slow": {"input": 10, "output": 50}, "gpt-fast": {"input": 1, "output": 2},
                                 "gpt-judge": {"input": 2, "output": 8}}
    est = C.estimate(rt, _suite(tmp_path), [{"thinking": SLOW, "execution": FAST, "trials": 3},
                                            {"thinking": SLOW, "execution": SLOW, "trials": 3}], judge="openai/gpt-judge")
    a, b = est["combos"]
    assert 0 < a["usd_per_trial"] < b["usd_per_trial"]          # the slow model does all the work in b
    assert est["usd"] == pytest.approx(a["usd"] + b["usd"], abs=1e-3) and est["unpriced"] == []
    assert C.estimate(rt, _suite(tmp_path), [{"execution": "x/unknown-model", "trials": 1}])["unpriced"] == ["x/unknown-model"]


async def test_a_campaign_runs_every_combination_and_answers_the_four_questions(rt, tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(runner, "child_runtime", _child(order))
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS, k=3, budget_usd=5)
    events = []
    done = await C.execute(rt, c["id"], emit=lambda k, d: events.append((k, d)))
    assert done["status"] == "done" and done["progress"] == {"done": 9, "total": 9}
    # interleaved: each round runs every combination (2 tasks each)
    assert [x[1] for x in order[:6]] == [SLOW, SLOW, FAST, FAST, BAD, BAD]
    assert order[0] == (None, SLOW) and order[2] == (SLOW, FAST)       # one model alone runs solo
    assert [it["combo"] for it in done["items"]] == [0, 1, 2] * 3
    assert events[-1][0] == "campaign_done" and any("item" in d for k, d in events if k == "campaign_progress")
    res = C.results(rt, c["id"])
    rows = {r["key"]: r for r in res["board"]["rows"]}
    assert rows[C.ckey(SLOW, SLOW)]["trials"] == 3 and rows[C.ckey(SLOW, SLOW)]["qualified"] == 6
    assert rows[C.ckey(FAST, BAD)]["eligible"] is False and rows[C.ckey(FAST, BAD)]["out"] == "critical"
    aw = res["board"]["awards"]
    assert aw["quality"]["winner"] in (C.ckey(SLOW, SLOW), C.ckey(SLOW, FAST))
    assert C.ckey(FAST, BAD) not in aw["quality"]["rank"]
    assert res["baseline"] == C.ckey(SLOW, SLOW)
    ids = {x["id"]: x["status"] for x in res["checks"]}
    assert ids["outputs"] == "ok" and ids["trials"] == "ok" and ids["skill"] == "warn" and ids["inputs"] == "warn"


async def test_the_budget_stops_the_campaign_and_keeps_what_is_done(rt, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    monkeypatch.setattr(C, "_expected", lambda c, ci: 1.0)
    real = runner.run

    async def costly(*a, **k):
        doc = await real(*a, **k)
        doc["totals"]["cost_usd"] = 1.0
        return doc
    monkeypatch.setattr(runner, "run", costly)
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:2], k=3, budget_usd=2.5)
    done = await C.execute(rt, c["id"])
    assert done["status"] == "budget" and len(done["items"]) == 2 and done["spent"]["total"] == pytest.approx(2.0)
    assert "超過預算" in done["error"]
    d = C.get(rt.config.home, c["id"])
    d["budget_usd"] = 10
    C.save(rt.config.home, d)
    again = await C.execute(rt, c["id"])                       # resumes after the budget is raised
    assert again["status"] == "done" and len(again["items"]) == 6


async def test_stop_and_resume_through_the_queue(rt, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    c1 = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:2], k=3, budget_usd=5)
    c2 = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[1:2], k=1, budget_usd=5)
    q = C.Queue()
    stopped = asyncio.Event()

    def emit(kind, data):
        if data["id"] == c1["id"] and data.get("item") and not stopped.is_set():
            stopped.set()
            q.stop(rt.config.home, c1["id"])
    assert q.submit(lambda: rt, c1["id"], emit) == "running"
    assert q.submit(lambda: rt, c2["id"], emit) == "queued"
    assert C.get(rt.config.home, c2["id"])["status"] == "queued"
    for _ in range(400):
        await asyncio.sleep(0.02)
        if C.get(rt.config.home, c2["id"])["status"] == "done" and not q.busy():
            break
    a = C.get(rt.config.home, c1["id"])
    assert a["status"] == "stopped" and 1 <= len(a["items"]) < 6
    assert C.get(rt.config.home, c2["id"])["status"] == "done"   # the next one started when the first stopped
    assert q.submit(lambda: rt, c1["id"]) == "running"
    for _ in range(400):
        await asyncio.sleep(0.02)
        if not q.busy():
            break
    a = C.get(rt.config.home, c1["id"])
    assert a["status"] == "done" and len(a["items"]) == 6 and [i["combo"] for i in a["items"]] == [0, 1] * 3


async def test_publishing_checks_trials_and_carries_the_campaign(rt, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    sent = []
    monkeypatch.setattr(P, "publish", lambda cfg, runs, extra=None: sent.append((runs, extra)) or
                        [{"url": f"https://sinobee.ai/benchmarks/evals/{i}"} for i in range(2)])
    from sinobee.skills import hub
    pubs = []
    monkeypatch.setattr(hub, "publish", lambda mgr, name, cfg: pubs.append(name) or {"ref": name})
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:2], k=1, budget_usd=5)
    await C.execute(rt, c["id"])
    with pytest.raises(EvalError, match="還不支援私人報告"):     # an older sinobee.ai would list it publicly
        C.publish(rt, c["id"], "private")
    monkeypatch.setattr(C, "supports_private", lambda cfg: True)
    with pytest.raises(EvalError, match="沒有組合達到"):
        C.publish(rt, c["id"], "public")
    out = C.publish(rt, c["id"], "private")
    runs, extra = sent[-1]
    assert len(runs) == 2 and extra["visibility"] == "private" and extra["campaign"] == c["id"] and not pubs
    assert out["reports"][0]["url"].startswith("https://") and C.get(rt.config.home, c["id"])["published"]
    c3 = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:2], k=3, budget_usd=5)
    await C.execute(rt, c3["id"])
    with pytest.raises(EvalError, match="不在這台電腦"):        # a skill that is neither here nor on Skill Hub
        C.publish(rt, c3["id"], "public")
    rt.skills.create("greeter", "---\nname: greeter\ndescription: 打招呼\n---\n# 打招呼\n", actor="user")
    rt.skills.refresh()
    out = C.publish(rt, c3["id"], "public")
    assert pubs == ["greeter"] and sent[-1][1]["visibility"] == "public" and len(sent[-1][0]) == 6
    assert C.get(rt.config.home, c3["id"])["status"] == "published"


async def test_the_evaluation_api(rt, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sinobee.evals import authoring as A
    from sinobee.server import create_app
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    import test_eval_authoring as T
    saved = A.save(rt.config.home, T.suite(), T.TEXTS, {"skill": "meeting-minutes", "skill_version": "0.1.0",
                                                         "evaluator": "eval-authoring", "evaluator_version": "1.0.0"})
    with TestClient(create_app(rt)) as c:
        _api_checks(c, rt, saved, tmp_path)


def _api_checks(c, rt, saved, tmp_path):
    import time
    ev = c.get("/api/evals/evaluators", params={"skill": "meeting-minutes"}).json()["evaluators"]
    assert ev[0]["name"] == "eval-authoring" and ev[0]["default"] and ev[0]["security"]["passed"] == 2 and ev[0]["suites"] == 1
    su = c.get("/api/evals/suites", params={"skill": "meeting-minutes", "version": "0.1.0"}).json()["suites"]
    assert su[0]["hash"] == saved["hash"] and su[0]["same_version"] and su[0]["campaigns"] == 0
    d = c.get(f"/api/evals/suites/{saved['hash'][:10]}").json()
    assert len(d["inputs"]) == 3 and d["inputs"][2]["inject"] and all(x["ok"] for x in d["checks"])
    assert {q["type"] for q in d["questions"]} >= {"security", "llm", "command"}
    assert c.get("/api/evals/suites/00000000").status_code == 404
    cand = c.get("/api/evals/candidates").json()
    assert "models" in cand and (cand["baseline"] is None or cand["baseline"].get("current"))   # no task: models in use
    assert c.post("/api/evals/suites/generate", json={"skill": "x"}).status_code == 400
    suite = _suite(tmp_path)
    est = c.post("/api/evals/estimate", json={"suite": suite, "combos": COMBOS[:2], "k": 3}).json()
    assert est["trials"] == 6 and est["tasks"] == 2
    made = c.post("/api/evals/campaigns", json={"suite": suite, "combos": COMBOS[:2], "k": 1, "budget_usd": 3}).json()
    assert made["queue"] in ("running", "queued") and made["progress"]["total"] == 2
    for _ in range(300):
        time.sleep(0.05)
        if c.get(f"/api/evals/campaigns/{made['id']}").json()["status"] == "done":
            break
    assert c.get("/api/evals/campaigns").json()["campaigns"][0]["id"] == made["id"]
    res = c.get(f"/api/evals/campaigns/{made['id']}/results").json()
    assert len(res["board"]["rows"]) == 2 and res["checks"]
    assert c.post(f"/api/evals/campaigns/{made['id']}/publish", json={"visibility": "public"}).status_code == 400
    assert c.post("/api/evals/campaigns", json={"suite": suite, "combos": []}).status_code == 400


def test_a_suite_shared_on_sinobee_ai_is_checked_and_kept(rt, monkeypatch):
    import httpx
    from sinobee.evals import authoring as A
    import test_eval_authoring as T
    s = A._fixed(T.suite(), "meeting-minutes")
    h = A.package_hash(s, T.TEXTS)

    class R:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d
    body = {"skill": "meeting-minutes", "skill_version": "0.1.0", "evaluator": "eval-authoring", "evaluator_version": "1.0.0",
            "suite": s, "inputs": T.TEXTS}
    monkeypatch.setattr(httpx, "get", lambda url, **k: R(200, body) if url.endswith(h) else
                        R(200, {"items": [{"hash": h, "title": "x", "reportCount": 7}]}) if url.endswith("/suites") else R(404, {}))
    assert A.remote_suites(rt.config, "meeting-minutes")[0]["reportCount"] == 7
    saved = A.import_remote(rt.config, h)
    assert saved["hash"] == h
    meta = A.load_ref(rt.config.home, h)[3]
    assert meta["source"] == "sinobee.ai" and meta["evaluator"] == "eval-authoring"
    tampered = {**body, "inputs": {**T.TEXTS, "inputs/orig/transcript.txt": "改過的內容"}}
    monkeypatch.setattr(httpx, "get", lambda url, **k: R(200, tampered))
    with pytest.raises(EvalError, match="不符"):
        A.import_remote(rt.config, h)
    with pytest.raises(EvalError):
        A.import_remote(rt.config, "nothex")


async def test_a_running_campaign_shows_where_it_is_and_an_orphan_is_stopped(rt, tmp_path, monkeypatch):
    """A campaign's trial takes many minutes: its step, input and time are visible while it runs (2026-10-21); one
    that the file says is running but no process runs (SinoBee restarted) shows as stopped and can be resumed."""
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:1], k=1, budget_usd=5)
    seen = []

    def emit(kind, d):
        if d.get("live"):
            seen.append(dict(d["live"]))
            cur = C.current(rt.config.home, c["id"])
            seen[-1]["running"] = cur["status"] == "running" and bool(cur.get("run_started")) and "now" in cur
    C.LIVE.clear()
    await C.execute(rt, c["id"], emit=emit)
    stepped = [x for x in seen if x.get("step")]
    assert stepped and stepped[0]["tool"] == "write_file" and stepped[0]["task_index"] == 1 and stepped[0]["tasks"] == 2
    assert all(x["running"] for x in seen)
    phases = [x.get("phase") for x in seen]
    assert phases[0] == "prepare" and "run" in phases and "grade" in phases     # the panel says run / grade
    graded = [x for x in seen if x.get("phase") == "grade"]
    assert graded[-1]["graded"] == graded[-1]["questions"] >= 1 and graded[0]["title"]
    after = C.current(rt.config.home, c["id"])
    assert after["status"] == "done" and after.get("live") is None and "run_started" not in after
    assert c["id"] not in C.LIVE

    orphan = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:1], k=1, budget_usd=5, session_id="s9")
    orphan["status"] = "running"
    C.save(rt.config.home, orphan)
    assert C.current(rt.config.home, orphan["id"])["status"] == "stopped"
    assert C.listing(rt.config.home, "s9")[0]["status"] == "stopped"
    assert "turn_id" in C.listing(rt.config.home, "s9")[0]           # the answer it evaluates shows it
    assert C.get(rt.config.home, orphan["id"])["status"] == "running"       # the file is left as it was


async def test_a_run_cut_off_half_way_still_counts_what_it_cost(rt, tmp_path, monkeypatch):
    """55 minutes and US$0 (2026-10-21): money was only counted when a whole run (3 inputs) finished, so runs a
    restart or a stop cut off were never counted. The run in progress shows its cost so far, and a cut-off run's
    last known cost is counted as abandoned."""
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    c = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:1], k=1, budget_usd=5, session_id="s7")
    c.update(status="running", live={"combo": 0, "trial": 1, "cost": 0.4, "usd": 0.45, "at": 1})
    C.save(rt.config.home, c)
    shown = C.current(rt.config.home, c["id"])                    # SinoBee restarted: stopped, and the cost shows
    assert shown["status"] == "stopped" and shown["spent"]["total"] == pytest.approx(0.45)
    assert C.get(rt.config.home, c["id"])["spent"]["total"] == 0   # the file is left as it was until resumed
    C.LIVE.clear()
    seen = []
    await C.execute(rt, c["id"], emit=lambda k, d: seen.append(d.get("live")))
    after = C.get(rt.config.home, c["id"])
    assert after["spent"]["abandoned"] == pytest.approx(0.45) and after["spent"]["total"] >= 0.45
    assert after["live"] is None and after["status"] == "done"
    assert any(x and x.get("cost", 0) > 0 and x["usd"] >= x["cost"] for x in seen)   # the run in progress: its cost
    assert C._live_usd(rt.config, {"judge": None}, {"cost": 0.2, "judge_tokens": [0, 0]}) == 0.2


def test_a_skill_evaluation_is_its_own_conversation_from_the_skill_menu(rt, tmp_path, monkeypatch):
    """2026-10-21: the evaluation started under an output read as "grade this output". It starts from the skill
    menu as its own conversation; the suite's material is a finished task picked from the ones that used the skill;
    a campaign can be given up."""
    import time
    from fastapi.testclient import TestClient
    from sinobee.agent import runreport
    from sinobee.server import create_app
    monkeypatch.setattr(runner, "child_runtime", _child([]))
    name = next(iter(rt.skills.skills)) if hasattr(rt.skills, "skills") else None
    d = runreport._dir(rt.config.home)
    (d / "r1.json").write_text(json.dumps({"id": "r1", "status": "done", "session_id": "work1", "turn_id": 4,
        "request": "用動畫介紹風力發電機", "skills": ["greeter"], "models": {"execution": FAST, "thinking": SLOW},
        "outputs": {"n_files": 1}}), encoding="utf-8")
    (d / "r2.json").write_text(json.dumps({"id": "r2", "status": "error", "session_id": "work2", "turn_id": 1,
        "skills": ["greeter"]}), encoding="utf-8")
    with TestClient(create_app(rt)) as c:
        assert name
        s = c.post("/api/evals/sessions", json={"skill": name}).json()
        assert s["id"].startswith("ev-") and s["title"] == f"技能評鑑：{name}"
        assert any(x["id"] == s["id"] for x in c.get("/api/sessions").json())    # a conversation in the sidebar
        assert c.post("/api/evals/sessions", json={"skill": "no-such-skill"}).status_code == 404
        src = c.get("/api/evals/source-tasks", params={"skill": "greeter"}).json()["tasks"]
        assert [(t["session_id"], t["turn_id"]) for t in src] == [("work1", 4)]          # finished tasks only
        cand = c.get("/api/evals/candidates", params={"session_id": "work1", "turn_id": 4}).json()
        assert cand["baseline"]["execution"] == FAST and cand["baseline"]["thinking"] == SLOW
        made = c.post("/api/evals/campaigns", json={"suite": _suite(tmp_path), "combos": COMBOS[:1], "k": 1,
                                                    "budget_usd": 3, "session_id": "ev-x",
                                                    "source": {"session_id": "work1", "turn_id": 4}}).json()
        assert made["session_id"] == "ev-x" and made["source"] == {"session_id": "work1", "turn_id": 4}
        for _ in range(200):
            time.sleep(0.05)
            if c.get(f"/api/evals/campaigns/{made['id']}").json()["status"] == "done":
                break
        assert c.post(f"/api/evals/campaigns/{made['id']}/abandon").status_code == 400     # a finished one stays

    half = C.new(rt, suite=_suite(tmp_path), combos=COMBOS[:1], k=2, budget_usd=5, session_id="ev-y")
    half.update(status="stopped", live={"usd": 0.3})
    C.save(rt.config.home, half)
    out = C.abandon(rt.config.home, half["id"])
    assert out["status"] == "abandoned" and out["spent"]["abandoned"] == pytest.approx(0.3)
    assert C.current(rt.config.home, half["id"])["status"] == "abandoned"
