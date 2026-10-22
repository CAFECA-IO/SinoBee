"""Proposal R: a large task is shown as one bubble - questions and answers, work and results - and reopens as it was
shown live; small tasks keep the simple presentation."""
import json

from sinobee.agent import Agent, Runtime
from sinobee.agent.artifacts import is_junk, key_files
from sinobee.agent.timeline import Timeline
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider


def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(json.dumps(a, ensure_ascii=False))}{len(text)}", n, a)
                                       for i, (n, a) in enumerate(calls or [])])


def test_events_become_questions_answers_work_and_checks():
    seen = []
    tl = Timeline(lambda k, d: seen.append(d))
    tl.feed("plan", {"understanding": "做一個 3D 遊戲", "steps": ["寫頁面", "實測"]})
    tl.feed("assistant", {"text": "問題：SyntaxError 出在哪個區塊？\n先把腳本抽出來檢查。", "final": False})
    tl.feed("tool_call", {"id": "a", "name": "shell", "arguments": {"command": "node --check game.js"}})
    tl.feed("tool_result", {"id": "a", "preview": "[exit 1] cwd=/w\n/tmp/game.js:306 } SyntaxError", "error": False})
    tl.feed("assistant", {"text": "答：地面段的 else if 分支多了一段。\n重寫那一段。", "final": False})
    tl.feed("tool_call", {"id": "b", "name": "edit_file", "arguments": {"path": "index.html"}})
    tl.feed("tool_result", {"id": "b", "preview": "edited index.html"})
    tl.feed("control", {"event": "consult_requested", "trigger": "production_gap", "label": "x", "detail": "d"})
    tl.feed("control", {"event": "consult", "trigger": "production_gap", "detail": "思維模型 m：先寫完整驗證腳本"})
    tl.feed("qa", {"issues": []})
    tl.feed("steer_applied", {"text": "改成直式"})
    tl.feed("assistant", {"text": "完成了遊戲。", "final": True})
    tl.finish()
    kinds = [(i["kind"], i.get("source")) for i in tl.items]
    assert kinds == [("think", "思維模型"), ("think", "執行模型"), ("work", None), ("think", "思維模型"),
                     ("check", "系統檢查"), ("steer", "你")]
    q = tl.items[1]
    assert q["q"] == "SyntaxError 出在哪個區塊？" and q["a"] == "地面段的 else if 分支多了一段。"
    w = tl.items[2]
    assert w["title"] == "先把腳本抽出來檢查。" and w["notes"] == ["重寫那一段。"], "one piece of work, its notes as steps"
    assert [t["brief"] for t in w["tools"]] == ["node --check game.js", "index.html"] and w["status"] == "ok"
    assert w["result"] == "edited index.html"
    assert tl.items[3]["q"].startswith("驗收清單還沒完成") and tl.items[3]["a"] == "先寫完整驗證腳本"
    assert tl.mode == "full" and seen and all("mode" in s for s in seen)


def test_long_work_is_split_and_results_are_readable():
    tl = Timeline(workspace="/ws")
    for i in range(7):
        tl.feed("assistant", {"text": f"第 {i} 步。", "final": False})
        tl.feed("tool_call", {"id": str(i), "name": "shell", "arguments": {"command": "node run.js"}})
        tl.feed("tool_result", {"id": str(i), "preview": '[exit 0] cwd=/ws\n{\n "hits": 0,\n "finished": true\n}'})
    works = [i for i in tl.items if i["kind"] == "work"]
    assert len(works) == 2 and len(works[0]["tools"]) == 5, "a piece of work without a question is split after 5 tools"
    assert works[0]["result"] == '{"hits": 0, "finished": true}'
    tl.feed("tool_call", {"id": "w", "name": "write_file", "arguments": {"path": "a.md"}})
    tl.feed("tool_result", {"id": "w", "preview": "wrote 3 chars to /ws/game/a.md"})
    assert works[1]["result"] == "wrote 3 chars to game/a.md"


def test_a_consult_answer_is_its_diagnosis_and_next_step():
    from sinobee.agent.timeline import advice_gist
    adv = "(1) 目前實際的問題\n停在定點外的反例沒有測到墜落分支。\n(2) 接下來\n1. 先寫 run_full.js 一次驗證\n(3) 停止\n不要再 grep"
    assert advice_gist(adv) == "停在定點外的反例沒有測到墜落分支；下一步：先寫 run_full.js 一次驗證"


def test_work_without_a_note_is_titled_by_its_tools_and_small_turns_stay_compact():
    tl = Timeline()
    for i in range(3):
        tl.feed("tool_call", {"id": str(i), "name": "shell", "arguments": {"command": "ls"}})
    assert tl.items[0]["title"] == "執行 3 個指令" and tl.mode == "compact"
    for i in range(3, 6):
        tl.feed("tool_call", {"id": str(i), "name": "read_file", "arguments": {"path": "a"}})
    assert tl.mode == "full"


def test_the_user_answer_to_a_question_is_a_decision():
    tl = Timeline()
    tl.feed("tool_call", {"id": "q", "name": "ask_user", "arguments": {"question": "要用哪個框架？"}})
    tl.feed("tool_result", {"id": "q", "preview": "The user chose: three.js. Their note: 要輕量"})
    assert tl.items[0] == {**tl.items[0], "kind": "think", "q": "要用哪個框架？", "a": "three.js（要輕量）", "source": "你"}


def test_deliverables_leave_out_backups_tests_and_scratch():
    arts = [{"path": p, "name": p.rsplit("/", 1)[-1]} for p in
            ["roadster-rocket/index.html", "roadster-rocket/index.html.bak", "roadster-rocket/index.html.before-collision",
             "roadster-rocket/test/final.png", "roadster-rocket/test/run_full.js", "roadster-rocket/notes.md"]]
    key, other = key_files(arts, "遊戲在 roadster-rocket/index.html，可以直接打開。")
    assert [a["path"] for a in key] == ["roadster-rocket/index.html"] and len(other) == 5
    assert is_junk("a/test/x.png") and is_junk("index.html.broken-syntax") and not is_junk("report.md")
    key, _ = key_files(arts, "完成", written={"/ws/roadster-rocket/notes.md"})
    assert [a["path"] for a in key] == ["roadster-rocket/notes.md"], "nothing named: what the file tools wrote"


async def test_a_large_turn_is_saved_with_its_timeline_and_reopens(home, config):
    steps = [msg(f"問題：第 {i} 部分要寫什麼？\n寫第 {i} 部分。", [("write_file", {"path": f"r/p{i}.txt", "content": f"# {i}"})])
             for i in range(6)]
    steps += [msg("", [("write_file", {"path": "r/p0.txt.bak", "content": "old"})]),
              msg("答：六個部分都寫好了。\n報告在 r/p0.txt 等六個檔案。")]
    ends = []
    rt = Runtime(config, provider=ScriptedProvider(steps))
    agent = Agent(rt, session_id="tl", platform="web", emit=lambda k, d: k == "turn_end" and ends.append(d))
    await agent.run("寫六個部分")
    end = ends[-1]
    assert end["timeline"]["mode"] == "full" and end["timeline"]["counts"]["think"] >= 6
    assert [a["path"] for a in end["deliverables"]] == ["r/p0.txt"], "only the file the answer names"
    assert "r/p1.txt" in [a["path"] for a in end["other_files"]]
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    with TestClient(create_app(Runtime(config, provider=ScriptedProvider()))) as c:     # a restarted server
        [t] = c.get("/api/sessions/tl/timelines").json()
    assert t["turn_id"] == end["turn_id"] and t["mode"] == "full"
    qs = [i for i in t["items"] if i["kind"] == "think"]
    assert qs[0]["q"] == "第 0 部分要寫什麼？" and qs[0]["a"] == "", "no 答 line: shown with its work's result"
    assert qs[-1]["q"] == "第 5 部分要寫什麼？" and qs[-1]["a"] == "六個部分都寫好了。"


async def test_a_small_turn_keeps_the_simple_presentation(home, config):
    ends = []
    rt = Runtime(config, provider=ScriptedProvider([msg("", [("list_dir", {"path": "."})]), msg("好了")]))
    await Agent(rt, session_id="sm", platform="web", emit=lambda k, d: k == "turn_end" and ends.append(d)).run("看看")
    assert ends[-1]["timeline"]["mode"] == "compact"
