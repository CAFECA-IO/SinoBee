"""Step 3 of the quality pipeline: quality rules from reported bugs, skill-declared checks, evaluations, file
snapshots with undo, and long-task progress."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sinobee import evals as E
from sinobee.agent import Agent, Runtime, rules, snapshots
from sinobee.config import Config
from sinobee.evals import runner
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider


def ws(cfg) -> Path:
    return cfg.workspace


# ---------------------------------------------------------------- rules
def test_rule_validation_and_check(home):
    cfg = Config.load()
    for bad in ({"title": "x", "pattern": "a*"}, {"title": "", "pattern": "TODO"}, {"title": "x", "pattern": "("},
                {"title": "x", "pattern": "ab", "kind": "maybe"}):
        try:
            rules.validate(bad)
            raise AssertionError(f"accepted {bad}")
        except rules.RuleError:
            pass
    r = rules.add(cfg.home, {"title": "不能留 TODO", "pattern": r"\bTODO\b", "files": ["*.md"]})
    req = rules.add(cfg.home, {"title": "要附來源", "kind": "require", "pattern": "參考來源", "files": ["report*.md"]})
    try:
        rules.add(cfg.home, {"title": "dup", "pattern": r"\bTODO\b", "files": ["*.md"]})
        raise AssertionError("duplicate accepted")
    except rules.RuleError:
        pass
    d = ws(cfg)
    (d / "report.md").write_text("# 報告\n\nTODO: 補資料\n", encoding="utf-8")
    (d / "notes.txt").write_text("TODO", encoding="utf-8")
    issues = rules.check(cfg.home, [d / "report.md", d / "notes.txt"])
    assert {i["rule"] for i in issues} == {r["id"], req["id"]} and all(i["name"] == "report.md" for i in issues)
    assert "第 3 行" in next(i for i in issues if i["rule"] == r["id"])["detail"]
    assert {x["id"]: x["hits"] for x in rules.load(cfg.home)} == {r["id"]: 1, req["id"]: 1}
    rules.update(cfg.home, r["id"], enabled=False)
    assert [i["rule"] for i in rules.check(cfg.home, [d / "report.md"])] == [req["id"]]


async def test_quality_gate_applies_the_users_rules(home, config, provider):
    rules.add(config.home, {"title": "不能留 TODO", "pattern": r"\bTODO\b", "files": ["*.md"], "message": "還有 TODO"})
    provider.push("", [("write_file", {"path": "a.md", "content": "# A\n\nTODO\n"})])
    provider.push("寫好了")
    provider.push("", [("write_file", {"path": "a.md", "content": "# A\n\n完成\n"})])
    provider.push("修好了")
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    out = await agent.run("寫 a.md")
    assert out == "修好了" and agent.last_status["status"] == "done"
    fix = [m for m in agent.history if m.role == "user" and "自動成果檢查" in m.text()]
    assert fix and "違反品質規則「不能留 TODO」" in fix[0].text()


async def test_a_thumbs_down_becomes_a_rule_that_catches_the_bug_and_a_regression_task(home, config):
    from sinobee.agent.rules import propose_from_feedback
    d = ws(config)
    (d / "page.md").write_text("# 公式\n\n$$\x0crac{a}{b}$$\n", encoding="utf-8")
    msgs = [Message("user", "寫 page.md 說明公式"),
            Message("assistant", "", [ToolCall("w1", "write_file", {"path": "page.md", "content": "…"})]),
            Message("tool", "wrote page.md", tool_call_id="w1"), Message("assistant", "好了")]
    aux = ScriptedProvider()
    aux.push(json.dumps({"rule": {"title": "公式的反斜線不能被吃掉", "files": ["*.md"], "kind": "forbid",
                                  "pattern": "[\\x08\\x0c]", "message": "\\f 變成控制字元"},
                         "eval": {"prompt": "寫一頁用 LaTeX 解釋分數 \\frac{a}{b} 的 Markdown", "rubric": "公式正確顯示"}}))
    rt = Runtime(config, provider=aux)
    rep = await propose_from_feedback(rt, {"id": 7, "session_id": "s1"}, msgs, -1, "公式壞掉了", ["format"])
    assert rep["rule"]["title"] == "公式的反斜線不能被吃掉" and rep["rule"]["source"]["ref"] == 7
    assert rules.load(config.home)[0]["id"] == rep["rule"]["id"]
    reg = E.regressions(config.home)
    assert len(reg) == 1 and reg[0]["kind"] == "regression"
    assert {g["type"] for g in reg[0]["graders"]} == {"status", "rule", "llm"}
    # a proposal that would not have caught the bug is not kept
    aux.push(json.dumps({"rule": {"title": "不能有 lorem", "files": ["*.md"], "kind": "forbid", "pattern": "lorem"},
                         "eval": None}))
    rep2 = await propose_from_feedback(rt, {"id": 8, "session_id": "s1"}, msgs, -1, "", [])
    assert "rule" not in rep2 and "does not flag" in rep2["rule_rejected"] and len(rules.load(config.home)) == 1


async def test_quality_rule_tool(home, config, provider):
    provider.push("", [("quality_rule", {"action": "add", "title": "報告要有來源", "kind": "require",
                                         "pattern": "參考來源", "files": ["*.md"]})])
    provider.push("", [("quality_rule", {"action": "list"})])
    provider.push("加好了")
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    await agent.run("以後報告都要附參考來源")
    rs = rules.load(config.home)
    assert len(rs) == 1 and rs[0]["kind"] == "require" and rs[0]["source"]["type"] == "agent"
    listed = [m.text() for m in agent.history if m.role == "tool"][1]
    assert rs[0]["id"] in listed and "報告要有來源" in listed


# ---------------------------------------------------------------- skill-declared checks
def _check_skill(cfg):
    d = cfg.skills_dir / "report-maker"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("""---
name: report-maker
description: Makes a report file. Use when asked for a report-maker report.
metadata:
  version: "1.0.0"
  sinobee-checks: "scripts/check.py {file}"
  sinobee-checks-on: "out/*.md"
---
# Procedure
Write out/<name>.md with a line OK.
""", encoding="utf-8")
    (d / "scripts" / "check.py").write_text(
        "import json,sys\ntext=open(sys.argv[1]).read()\n"
        "print(json.dumps({'status':'ok' if 'OK' in text else 'fix','problems':[] if 'OK' in text else ['missing OK line']}))\n",
        encoding="utf-8")


async def test_skill_declared_checks_run_before_the_task_may_finish(home, config, provider):
    _check_skill(config)
    provider.push("", [("write_file", {"path": "out/r.md", "content": "# R\n"})])
    provider.push("寫好了")
    provider.push("", [("write_file", {"path": "out/r.md", "content": "# R\n\nOK\n"})])
    provider.push("修好了")
    rt = Runtime(config, provider=provider)
    assert "report-maker" in rt.skills.skills
    agent = Agent(rt)
    out = await agent.run("/report-maker 做一份報告")
    assert out == "修好了"
    fix = [m.text() for m in agent.history if m.role == "user" and "自動成果檢查" in m.text()]
    assert fix and "技能 report-maker 的檢查未通過：missing OK line" in fix[0]
    st = rt.skills.stats("report-maker")["checks"]
    assert st["runs"] == 2 and st["passed"] == 1


def test_check_output_parsing():
    from sinobee.skills.checks import parse
    assert parse(0, "[exit 0] cwd=/x\n{\"status\": \"ok\", \"problems\": []}") == []
    assert parse(0, "[exit 0] cwd=/x\n{\"status\": \"fix\", \"problems\": [\"a\", \"b\"]}") == ["a", "b"]
    assert parse(0, "[exit 0] cwd=/x\n{\"ok\": false, \"error\": \"bad\"}") == ["bad"]
    assert parse(0, "[exit 0] cwd=/x\nall good") == []
    assert parse(3, "[exit 3] cwd=/x\nboom")[0].startswith("check exited with code 3")


# ---------------------------------------------------------------- snapshots
async def test_snapshot_and_undo_of_a_turn(home, config, provider):
    d = ws(config)
    (d / "keep.txt").write_text("original", encoding="utf-8")
    provider.push("", [("write_file", {"path": "keep.txt", "content": "changed once"})])
    provider.push("", [("edit_file", {"path": "keep.txt", "old_string": "changed once", "new_string": "changed twice"}),
                       ("write_file", {"path": "new.txt", "content": "brand new"})])
    provider.push("改好了")
    events = []
    rt = Runtime(config, provider=provider)
    agent = Agent(rt, emit=lambda k, v: events.append((k, v)))
    await agent.run("改檔案")
    end = next(v for k, v in events if k == "turn_end")
    snap = end["snapshot"]
    assert snap and snap["files"] == 2
    assert (d / "new.txt").exists() and (d / "keep.txt").read_text() != "original"
    rep = snapshots.restore(config.home, snap["session"], snap["key"])
    assert (d / "keep.txt").read_text() == "original" and not (d / "new.txt").exists()
    assert len(rep["restored"]) == 1 and len(rep["removed"]) == 1 and not rep["failed"]
    try:
        snapshots.info(config.home, "../x", snap["key"])
        raise AssertionError("path traversal accepted")
    except snapshots.SnapshotError:
        pass


def test_snapshot_api(home, config, provider):
    from sinobee.server import create_app
    d = ws(config)
    (d / "f.txt").write_text("v1", encoding="utf-8")
    snapshots.take(config.home, "sess1", "t1", d / "f.txt")
    (d / "f.txt").write_text("v2", encoding="utf-8")
    rt = Runtime(config, provider=provider)
    with TestClient(create_app(rt)) as c:
        assert c.get("/api/sessions/sess1/snapshots/t1").json()["files"][0]["existed"] is True
        r = c.post("/api/sessions/sess1/snapshots/t1/restore").json()
        assert r["restored"] and (d / "f.txt").read_text() == "v1"
        assert c.get("/api/sessions/sess1/snapshots/nope").status_code == 404
        # rules CRUD
        rid = c.post("/api/rules", json={"title": "no TODO", "pattern": "TODO"}).json()["id"]
        assert c.patch(f"/api/rules/{rid}", json={"enabled": False}).json()["enabled"] is False
        assert c.post("/api/rules", json={"title": "bad", "pattern": "("}).status_code == 400
        assert c.delete(f"/api/rules/{rid}").json()["ok"] and c.get("/api/rules").json()["rules"] == []
        ov = c.get("/api/evals").json()
        assert any(s["name"] == "core" and s["tasks"] >= 5 for s in ov["suites"]) and ov["running"] is False


# ---------------------------------------------------------------- long tasks
async def test_long_checklist_focus_hint_and_progress_note(home, config, provider):
    items = [{"text": f"第 {i} 項"} for i in range(1, 10)]
    provider.push("", [("task_checklist", {"action": "set", "items": items})])
    provider.push("", [("list_dir", {"path": "."})])
    rt = Runtime(config, provider=provider)
    agent = Agent(rt, max_iterations=2)
    await agent.run("做九件事")
    assert agent.last_status["status"] == "incomplete"
    tail = provider.requests[1][0][-1].text()
    assert "<focus>" in tail and "ONE item at a time" in tail
    from sinobee.work.hub import get_hub
    notes = get_hub(rt).store.board_read(agent.ctx.state["board_scope"], kinds=["note"], key="progress", limit=1)
    assert notes and "尚未完成" in notes[0]["content"] and "#1 第 1 項" in notes[0]["content"]


# ---------------------------------------------------------------- evals
def _suite(tmp_path) -> Path:
    f = tmp_path / "suite.json"
    f.write_text(json.dumps({"tasks": [{
        "id": "write-hello", "prompt": "根據 in.txt 寫 out/hello.md", "toolsets": ["files"],
        "setup": {"files": {"in.txt": "name: Bee"}},
        "graders": [{"type": "file_exists", "path": "out/hello.md"},
                    {"type": "file_regex", "path": "out/*.md", "pattern": "Hello,\\s*Bee"},
                    {"type": "file_not_regex", "path": "out/hello.md", "pattern": "TODO"},
                    {"type": "answer_regex", "pattern": "完成"},
                    {"type": "tool_used", "name": "write_file"},
                    {"type": "command", "cmd": "test -s out/hello.md && grep -q Bee in.txt"},
                    {"type": "qa_clean"},
                    {"type": "status", "is": "done"},
                    {"type": "llm", "rubric": "greets Bee"}]}]}), encoding="utf-8")
    return f


async def test_eval_run_pass_at_k_and_regressions(home, config, tmp_path, monkeypatch):
    def script(good: bool):
        p = ScriptedProvider()
        p.push("", [("read_file", {"path": "in.txt"})])
        p.push("", [("write_file", {"path": "out/hello.md", "content": "# Hi\n\nHello, Bee\n" if good else "# Hi\n\nTODO\n"})])
        p.push("完成了")
        return p
    plan = [True, True, False, False]
    monkeypatch.setattr(runner, "child_runtime", lambda rt, cfg: Runtime(cfg, provider=script(plan.pop(0))))
    judge = ScriptedProvider()
    for _ in range(4):
        judge.push(json.dumps({"pass": True, "reason": "ok"}))
    rt = Runtime(config, provider=judge)
    suite = _suite(tmp_path)
    doc = await runner.run(rt, str(suite), k=2)
    t = doc["tasks"][0]
    assert doc["status"] == "done" and t["pass_at_k"] and t["pass_hat_k"], runner.report(doc)
    assert doc["totals"]["pass_hat_k"] == 1.0 and doc["totals"]["trials"] == 2
    tr = t["trials"][0]
    assert tr["files"] == ["out/hello.md"] and tr["steps"] == 3 and tr["tool_calls"] == 2
    assert not any(Path(p).exists() for p in [tr.get("workdir") or "/nonexistent"])
    # the eval's model calls are added to the user's usage
    n = rt.memory.db.execute("SELECT COUNT(*) FROM usage WHERE purpose='eval'").fetchone()[0]
    assert n == 6                               # 3 model calls per trial, recorded for the user
    doc2 = await runner.run(rt, str(suite), k=2)
    assert not doc2["tasks"][0]["pass_at_k"] and doc2["regressions"] == ["write-hello"]
    bad = [g for g in doc2["tasks"][0]["trials"][0]["grades"] if not g["ok"]]
    assert {g["type"] for g in bad} >= {"file_regex", "file_not_regex"}
    assert [r["id"] for r in E.results(config.home)][:2] == [doc2["id"], doc["id"]]
    assert "REGRESSIONS" in runner.report(doc2)
    # nothing leaked into the user's real workspace or memory
    assert not (ws(config) / "out").exists()


def test_skill_evals_are_loaded_in_the_anatomy_format(home, config):
    d = config.skills_dir / "greeter"
    (d / "evals" / "files").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: greeter\ndescription: Greets people. Use to greet.\n---\n# Procedure\nGreet.\n",
                                encoding="utf-8")
    (d / "evals" / "files" / "names.txt").write_text("Bee", encoding="utf-8")
    (d / "evals" / "evals.json").write_text(json.dumps({"skill_name": "greeter", "evals": [
        {"id": 1, "prompt": "greet everyone in names.txt", "expected_output": "a greeting for Bee",
         "files": ["evals/files/names.txt"]}]}), encoding="utf-8")
    rt = Runtime(config, provider=ScriptedProvider())
    name, tasks = E.load("skill:greeter", home=config.home, skills=rt.skills)
    assert name == "skill:greeter" and tasks[0]["prompt"].startswith("/greeter ")
    assert "names.txt" in tasks[0]["setup"]["files"]
    assert any(g["type"] == "llm" and "Bee" in g["rubric"] for g in tasks[0]["graders"])
    assert any(s["name"] == "skill:greeter" for s in E.suites(config.home, rt.skills))


def test_core_suite_is_valid():
    name, tasks = E.load("core", home=Path("/tmp/sinobee-test-evals"))
    assert name == "core" and len(tasks) >= 5
    assert all(t["graders"] for t in tasks)
