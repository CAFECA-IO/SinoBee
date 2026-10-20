"""Packaging a finished task into a skill (skills/package.py) and the privacy scan it relies on (privacy.py).

A skill describes HOW a task was done; this task's people, contacts, paths and secrets never go into it."""
import json

import pytest

from sinobee import privacy
from sinobee.agent import Agent, Runtime
from sinobee.skills import package
from sinobee.skills.package import PackageError
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


# ---------------------------------------------------------------- privacy

def test_privacy_finds_personal_data_and_secrets():
    text = ("聯絡王小明 wang@example.com 或 0912-345-678，身分證 A123456789，卡號 4111 1111 1111 1111，"
            "金鑰 sk-abcdefghijklmnopqrstuv，檔案在 /Users/wang/Desktop/報表.xlsx，主機 10.2.3.4。")
    kinds = privacy.summary(privacy.scan(text, ["王小明"]))
    assert kinds == {"name": 1, "email": 1, "phone": 1, "national_id": 1, "card": 1, "secret": 1, "path": 1, "ip": 1}
    path = next(f for f in privacy.scan(text) if f.kind == "path")
    assert path.value == "/Users/wang/Desktop/報表.xlsx"          # stops at the full-width punctuation


def test_privacy_ignores_numbers_that_are_not_cards_or_phones():
    assert privacy.scan("訂單 1234 5678 9012 3456 共 3 件，版本 1.2.3") == []      # fails the Luhn check


def test_deidentify_is_stable_across_texts():
    t1, m = privacy.deidentify("王小明寄信給 a@b.co，副本 陳大華", None, ["王小明", "陳大華"])
    t2, m = privacy.deidentify("再提醒 陳大華 與 王小明，回覆 a@b.co", m, ["王小明", "陳大華"])
    assert t1 == "人員 A寄信給 <email-1>，副本 人員 B"
    assert t2 == "再提醒 人員 B 與 人員 A，回覆 <email-1>"
    t3, m = privacy.deidentify("新來的 林小美", m, ["林小美"])
    assert t3 == "新來的 人員 C"


# ---------------------------------------------------------------- packaging a task

DRAFT = {
    "name": "Weekly Sales Report",
    "title": "每週業績報表",
    "description": "把一週的銷售 CSV 整理成摘要報表，寄給 wang@example.com 的那種週報。",
    "params": [{"name": "Sales CSV", "description": "銷售明細檔", "required": True, "example": "sales.csv"},
               {"name": "week", "description": "週次", "required": False, "example": "2026-W42"},
               {"name": "9bad", "description": "x"}],
    "body": "## 做法\n1. 執行 scripts/summarize.py {sales_csv}\n2. 套用 templates/report.md，署名王小明\n\n## 完成前檢查\n- 數字加總正確",
    "files": [{"source": "scripts/summarize.py", "target": "scripts/summarize.py", "reason": "彙總"},
              {"source": "templates/report.md", "target": "templates/report.md", "reason": "版型"},
              {"source": "../outside.py", "target": "scripts/x.py"}],
    "triggers": ["週報", "業績"],
    "names": ["王小明"],
}


def thinker_for(draft):
    def answer(messages):
        text = messages[-1].text()
        return Message("assistant", json.dumps(draft, ensure_ascii=False) if "reusable Agent Skill" in text else "{}")
    return ScriptedProvider([answer] * 20)


async def finished_task(config, draft=DRAFT, sid="pk"):
    config.data["agent"].update(failure_diagnosis=False)
    main = ScriptedProvider([
        msg("", [("task_checklist", {"action": "add", "items": ["產出週報"]})]),
        msg("", [("write_file", {"path": "scripts/summarize.py", "content": "import csv, sys\nprint(sum(1 for _ in csv.reader(open(sys.argv[1]))))\n"})]),
        msg("", [("write_file", {"path": "templates/report.md", "content": "# 週報\n聯絡人：0912-345-678\n"})]),
        msg("", [("write_file", {"path": "data/sales.csv", "content": "a,b\n1,2\n"})]),
        msg("", [("task_checklist", {"action": "update", "id": 1, "status": "done", "evidence": "templates/report.md"})]),
        msg("週報完成")])
    rt = Runtime(config, provider=main, thinking_provider=thinker_for(draft), aux_provider=ScriptedProvider())
    events = []
    agent = Agent(rt, session_id=sid, platform="web", emit=lambda k, d: events.append((k, d)))
    await agent.run("把這週給王小明的業績整理成週報")
    end = next(d for k, d in events if k == "turn_end")
    assert end["status"] == "done", (end["error"], end["text"])
    return rt, end


async def test_a_draft_generalizes_the_task_and_removes_personal_data(home, config):
    rt, end = await finished_task(config)
    assert end["package_hint"] is False           # a 6-step task is not worth offering (needs ≥ 10 steps)
    d = await package.draft_from_session(rt, "pk")
    assert d["name"] == "weekly-sales-report" and "names" not in d
    assert [p["name"] for p in d["params"]] == ["sales_csv", "week", "p_9bad"]   # cleaned to snake_case
    assert "王小明" not in d["body"] and "人員 A" in d["body"]
    assert "wang@example.com" not in d["description"] and "<email-1>" in d["description"]
    assert "王小明" not in d["request"]
    where = {(f["where"], f["kind"]) for f in d["privacy"]}
    assert ("做法", "name") in where and ("說明", "email") in where
    assert all("wang@example.com" != f["value"] for f in d["privacy"])        # values are masked for the UI
    files = {f["source"]: f for f in d["files"]}
    assert files["scripts/summarize.py"]["include"] and not files["scripts/summarize.py"]["locked"]
    assert files["templates/report.md"]["locked"] and not files["templates/report.md"]["include"]
    assert "電話" in files["templates/report.md"]["warning"]
    assert files["data/sales.csv"]["include"] is False                        # input data: offered switched off
    assert "../outside.py" not in files


async def test_saving_a_draft_creates_an_experimental_skill_with_its_files(home, config):
    rt, _ = await finished_task(config)
    d = await package.draft_from_session(rt, "pk")
    res = package.save_draft(rt, d["id"], {"title": "週報產生器", "files": {"templates/report.md": True}})
    assert res["files"] == ["scripts/summarize.py"]            # the locked file stays out even when switched on
    assert res["params"] == ["sales_csv", "week", "p_9bad"] and res["published"] is None
    rt.skills.refresh()
    s = rt.skills.get("weekly-sales-report")
    assert s.trust == "local" and rt.skills.stats(s.name)["status"] == "candidate"
    text = s.location.read_text(encoding="utf-8")
    assert "sinobee-params: sales_csv*, week" in text and "sinobee-source: packaged" in text
    assert "# 週報產生器" in text and "## 參數" in text and "`sales_csv`（必填）" in text
    assert "王小明" not in text and "wang@example.com" not in text
    assert (s.path / "scripts" / "summarize.py").is_file()
    with pytest.raises(PackageError):                           # a draft is used once
        package.save_draft(rt, d["id"], {})


async def test_saving_refuses_personal_data_typed_into_the_draft(home, config):
    rt, _ = await finished_task(config)
    d = await package.draft_from_session(rt, "pk")
    with pytest.raises(PackageError, match="Email"):
        package.save_draft(rt, d["id"], {"body": d["body"] + "\n有問題寄 boss@corp.com"})
    with pytest.raises(PackageError, match="人名"):                 # the draft's names are still checked
        package.save_draft(rt, d["id"], {"body": d["body"] + "\n交給王小明"})
    assert package.save_draft(rt, d["id"], {})["name"] == "weekly-sales-report"


async def test_publish_is_requested_only_when_asked(home, config, monkeypatch):
    rt, _ = await finished_task(config)
    d = await package.draft_from_session(rt, "pk")
    calls = []
    from sinobee.skills import hub
    monkeypatch.setattr(hub, "publish", lambda mgr, name, cfg: calls.append(name) or {"ref": f"hub:{name}"})
    res = package.save_draft(rt, d["id"], {"publish": True})
    assert calls == ["weekly-sales-report"] and res["published"] == {"ref": "hub:weekly-sales-report"}


async def test_a_second_package_gets_a_new_name(home, config):
    rt, _ = await finished_task(config)
    package.save_draft(rt, (await package.draft_from_session(rt, "pk"))["id"], {})
    d2 = await package.draft_from_session(rt, "pk")
    assert d2["name"] == "weekly-sales-report-2"


async def test_an_empty_draft_is_an_error(home, config):
    rt, _ = await finished_task(config, draft={"name": "x"})
    with pytest.raises(PackageError):
        await package.draft_from_session(rt, "pk")
    with pytest.raises(PackageError):
        package.load_draft(rt.config.home, "../../etc")


def test_worth_packaging():
    class Mgr:
        def __init__(self, hit):
            self.hit = hit

        def suggest(self, q):
            return self.hit
    base = dict(depth=0, status="done", steps=12, checklist=3, outputs=1, skills_used=[], query="做週報", mgr=Mgr(None))
    assert package.worth_packaging(**base)
    assert not package.worth_packaging(**{**base, "steps": 6})
    assert not package.worth_packaging(**{**base, "status": "incomplete"})
    assert not package.worth_packaging(**{**base, "depth": 1})
    assert not package.worth_packaging(**{**base, "skills_used": ["weekly"]})         # a skill already did it
    assert not package.worth_packaging(**{**base, "checklist": 0, "outputs": 0})
    assert not package.worth_packaging(**{**base, "mgr": Mgr(object())})              # a skill already covers it


async def test_the_skill_lists_its_parameters(home, config):
    rt, _ = await finished_task(config)
    package.save_draft(rt, (await package.draft_from_session(rt, "pk"))["id"], {})
    rt.skills.refresh()
    info = rt.skills.get("weekly-sales-report").info()
    assert info["packaged"] is True
    assert info["params"] == [{"name": "sales_csv", "required": True}, {"name": "week", "required": False},
                              {"name": "p_9bad", "required": True}]


async def test_the_api_drafts_and_saves(home, config):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    rt, _ = await finished_task(config)
    c = TestClient(create_app(rt))
    d = c.post("/api/sessions/pk/package", json={}).json()
    assert d["name"] == "weekly-sales-report" and d["params"]
    assert c.post("/api/sessions/nope/package", json={}).status_code == 400
    bad = c.post("/api/skills/package", json={"draft_id": d["id"], "edits": {"body": "寄給 x@y.com"}})
    assert bad.status_code == 400 and "Email" in bad.json()["detail"]
    ok = c.post("/api/skills/package", json={"draft_id": d["id"], "edits": {"name": "my-weekly"}, "scope": "global"})
    assert ok.status_code == 200 and ok.json()["name"] == "my-weekly"
    row = next(s for s in c.get("/api/skills").json()["skills"] if s["name"] == "my-weekly")
    assert row["packaged"] and row["stats"]["status"] == "candidate"


def test_targets_stay_unique_and_values_are_masked():
    assert package._free_target("templates/a.md", {"templates/a.md", "templates/a-2.md"}) == "templates/a-3.md"
    assert package._mask("王小明") == "王○○" and package._mask("wang@example.com") == "wa…om"
