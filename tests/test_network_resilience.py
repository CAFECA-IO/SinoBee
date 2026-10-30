"""A task survives a sleeping laptop or a dropped network (00935 task, 2026-10-29: 3 steps in 2 h 35 min, every model
call ended in ReadTimeout('') a few seconds of awake time after it started, and SinoBee called it "too long").

- a timeout long before the configured read timeout, a ConnectTimeout, or a call during which the computer slept is
  a dropped connection: the provider waits for the network and sends the call again (not "write it in parts");
- the error explanation and the failure diagnosis say so;
- a resumed turn that is resumed again does not nest the previous resume text;
- a sub-agent stopped by a temporary error gets its milestone again once, told where the first one got to;
- a new conversation's own /workspace is kept from its second turn on;
- caffeinate keeps a Mac awake only while a task runs."""
import time

import httpx
import pytest

from sinobee.agent import errors, failures
from sinobee.agent.milestone_runner import _transient_stop
from sinobee.providers import base
from sinobee.providers.base import Provider, ProviderError, dropped_connection
from sinobee.types import ChatResponse, Message, Usage
from sinobee.work.hub import original_request


def test_a_quick_read_timeout_is_a_dropped_connection_not_a_slow_model():
    assert dropped_connection(httpx.ReadTimeout(""), 9.0, 9.0, 180)
    assert dropped_connection(httpx.ConnectTimeout(""), 30.0, 30.0, 180) == "could not connect"
    assert "asleep" in dropped_connection(httpx.ReadTimeout(""), 9.0, 2400.0, 180)
    assert dropped_connection(httpx.ReadTimeout(""), 181.0, 181.0, 180) == ""      # the model really was slow
    assert dropped_connection(httpx.ReadTimeout(""), 0.0, 0.0, 180) == ""          # not a socket giving up
    tok = base.TOO_LONG.set(True)
    try:
        assert dropped_connection(httpx.ReadTimeout(""), 9.0, 9.0, 180) == ""      # right after a cut-off reply
    finally:
        base.TOO_LONG.reset(tok)


class _Flaky(Provider):
    name = "flaky"

    def __init__(self, failures_before_ok: list):
        super().__init__("m", "https://api.example.invalid", "k", timeout=180)
        self.queue = list(failures_before_ok)
        self.calls = 0

    async def _chat(self, messages, tools, *, temperature, max_tokens):
        self.calls += 1
        if self.queue:
            raise self.queue.pop(0)
        return ChatResponse(Message("assistant", "ok"), Usage(1, 1), "end_turn", "m")


class _Asleep:
    """base.time while the computer sleeps through every call: the wall clock runs 10 minutes ahead each time"""
    def __init__(self):
        self.t = time.time()

    def monotonic(self):
        return time.monotonic()

    def time(self):
        self.t += 600
        return self.t


async def test_a_dropped_connection_waits_for_the_network_and_is_sent_again(monkeypatch):
    waits = []

    async def wait_online(self, budget):
        waits.append(budget)
        return 120.0                        # the network came back after two minutes

    monkeypatch.setenv("SINOBEE_NETWORK_WAIT_S", "900")
    monkeypatch.setattr(_Flaky, "_wait_online", wait_online)
    monkeypatch.setattr(base.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(base, "time", _Asleep())
    p = _Flaky([httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ConnectError("down"), httpx.ConnectTimeout(""),
                httpx.ReadTimeout("")])
    r = await p.chat([Message("user", "hi")], retries=4)
    assert r.message.text() == "ok"
    assert p.calls == 6 and len(waits) == 5         # a dropped call is not limited to one retry like a real timeout


async def test_a_real_timeout_is_still_retried_only_once(monkeypatch):
    monkeypatch.setattr(base.asyncio, "sleep", _no_sleep)
    p = _Flaky([httpx.ReadTimeout(""), httpx.ReadTimeout(""), httpx.ReadTimeout("")])
    with pytest.raises(ProviderError) as e:
        await p.chat([Message("user", "hi")], retries=4)
    assert "timed out" in str(e.value) and p.calls == 2


async def test_the_network_wait_has_a_limit(monkeypatch):
    async def wait_online(self, budget):
        return budget                          # never came back

    monkeypatch.setenv("SINOBEE_NETWORK_WAIT_S", "900")
    monkeypatch.setattr(_Flaky, "_wait_online", wait_online)
    monkeypatch.setattr(base.asyncio, "sleep", _no_sleep)
    p = _Flaky([httpx.ConnectError("down")] * 20)
    with pytest.raises(ProviderError) as e:
        await p.chat([Message("user", "hi")], retries=4)
    assert "network error" in str(e.value) and p.calls <= 6


async def _no_sleep(_s):
    return None


def test_the_explanation_and_the_diagnosis_name_the_network_not_the_length():
    detail = "anthropic/claude-opus-5-5 network error: connection dropped after 9s (timeout 180s) (ReadTimeout(''))"
    e = errors.explain_error(detail)
    assert "連線中斷" in e["summary"] and "沒有回完" not in e["summary"] and e["transient"]
    asleep = errors.explain_error("anthropic/x network error: the computer was asleep for 2400s during the call (ReadTimeout(''))")
    assert "睡眠" in asleep["summary"]
    d = failures.rule_diagnosis({"reason": {"summary": e["summary"], "detail": detail}, "models": {}})
    assert d["category"] == "environment"
    # a genuine timeout keeps its old explanation
    assert "沒有回完" in errors.explain_error("anthropic/x timed out: ReadTimeout('')")["summary"]


def test_a_resumed_turn_resumed_again_carries_the_request_once():
    once = ("[自動續跑] 上一次的任務在中途被中斷了。已完成的步驟都在這段對話裡：先確認做到哪裡，從中斷處接著完成，不要重做已完成的部分。"
            "原本的要求：分析 00935\n進度整理（輔助模型依工作紀錄整理，請先核對）：\n先做：x")
    twice = once.replace("分析 00935", once)
    assert original_request(once) == "分析 00935"
    assert original_request(twice) == "分析 00935"
    assert original_request("分析 00935") == "分析 00935"


def test_a_sub_agent_stopped_by_the_network_is_a_temporary_stop():
    assert _transient_stop({"status": "error", "result": "⚠︎ 任務中斷：連不到模型服務（網路或供應商暫時故障）"})
    assert not _transient_stop({"status": "error", "result": "找不到資料"})
    assert not _transient_stop({"status": "done", "result": "連不到模型服務"})


async def test_wait_online_returns_at_once_when_the_service_answers():
    import asyncio
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    p = _Flaky([])
    p.base_url = f"http://127.0.0.1:{port}"
    assert await p._wait_online(30) < 1.0
    server.close()
    p.base_url = "http://127.0.0.1:1"                  # nothing listens: gives up after its budget
    assert 0.9 <= await p._wait_online(1.0) < 5


def test_a_conversation_that_already_has_its_own_folder_keeps_it(config):
    from sinobee.sandbox import convconf, workspaces
    d = workspaces.own_dir(config, "c-new")
    (d / "reports").mkdir()
    assert workspaces.settle(config, "c-new", legacy=True) == "own"
    assert convconf.load(config, "c-new")["workspace"] == "own"
    assert workspaces.settle(config, "c-old", legacy=True) == "shared"        # an old conversation: no own files


def test_keep_awake_only_on_a_mac_and_only_while_held(monkeypatch):
    from sinobee import keepawake
    started = []
    monkeypatch.setattr(keepawake, "supported", lambda: True)
    monkeypatch.setattr(keepawake, "_start", lambda: started.append(1))
    stopped = []
    monkeypatch.setattr(keepawake, "_stop", lambda: stopped.append(1))
    with keepawake.hold():
        with keepawake.hold():
            pass
        assert started == [1] and stopped == []
    assert stopped == [1]

    class Off:
        def get(self, k, d=None):
            return False if k == "system.keep_awake" else d
    assert keepawake.acquire(Off()) is False


def test_claude_thinking_models_get_room_for_short_side_calls():
    from sinobee.agent.loop import thinking_room
    assert thinking_room("anthropic/claude-opus-5-5", 1400) >= 8192          # consult came back as thinking only
    assert thinking_room("anthropic/claude-sonnet-4-6", 400) >= 8192
    assert thinking_room("anthropic/claude-opus-4-1", 1400) == 1400


def test_a_cut_off_visual_judgment_keeps_its_whole_verdicts():
    from sinobee.agent.visual import parse
    items = parse('{"items":[{"shot":1,"ok":true},{"shot":2,"ok":false,"severity":"blocking","problem":"缺字"} '
                  '{"shot":3,"ok":tr')
    assert [i["shot"] for i in items] == [1, 2]
    with pytest.raises(ValueError):
        parse("沒有 JSON")


def test_a_clickable_link_in_a_report_is_not_an_on_screen_item():
    from sinobee.tools.checklist_tools import is_visual
    assert not is_visual("恰好十件事件 每列有日期、影響說明與可點擊的來源連結")
    assert is_visual("點擊開始按鈕後遊戲開始")


def test_the_report_and_its_charts_are_the_key_outputs_not_downloaded_sources():
    """00935 task: 關鍵產出 showed two downloaded PDFs, three removed web pages and a note; the report was cut off."""
    from sinobee.agent.artifacts import fetched_source, key_files
    names = ["data/tip/n1340.pdf", "data/tip/list2026.html", "data/tip/n1219.pdf", "data/isin2.html",
             "data/isin4.html", "notes/structure.md", "notes/events.md", "data/holdings.csv",
             "reports/00935_2026_analysis.md", "reports/fig_1_structure.png", "reports/fig_2_top10.png",
             "reports/fig_3_ytd_events.png", "reports/fig_4_compare.png", "reports/make_charts.py"]
    arts = [{"path": "/workspace/" + n, "name": n.split("/")[-1]} for n in names]
    answer = ("isin4.html、list2026.html、isin2.html 已移出。n1340.pdf 與 n1219.pdf 是來源。"
              "報告在 reports/00935_2026_analysis.md")
    ms = [{"outputs": ["reports/00935_2026_analysis.md", "圖表 PNG"],
           "result_outputs": ["/workspace/reports/" + n for n in ("00935_2026_analysis.md", "fig_1_structure.png",
                              "fig_2_top10.png", "fig_3_ytd_events.png", "fig_4_compare.png", "make_charts.py")]}]
    written = {"/Users/x/.sinobee/workspace/conversations/c/reports/00935_2026_analysis.md",
               "/Users/x/.sinobee/workspace/conversations/c/notes/structure.md"}
    gone = {"isin2.html", "isin4.html", "list2026.html"}
    key, other = key_files(arts, answer, ms, [{"evidence": "data/tip/ 的 n1340.pdf"}], written,
                           exists=lambda p: p.split("/")[-1] not in gone)
    got = [a["name"] for a in key]
    assert got[0] == "00935_2026_analysis.md"
    assert {"fig_1_structure.png", "fig_2_top10.png", "fig_3_ytd_events.png", "fig_4_compare.png"} <= set(got)
    assert not gone & set(got) and "n1340.pdf" not in got
    assert fetched_source("/workspace/data/isin4.html", written)
    assert not fetched_source("/workspace/reports/00935_2026_report.html", written)
    assert not fetched_source("/workspace/notes/structure.md", written)


def test_a_check_that_sends_work_back_asks_for_an_answer_to_the_user():
    from sinobee.agent.loop import ANSWER_FOR_USER
    assert "取代你上一個回答" in ANSWER_FOR_USER and "主要結論" in ANSWER_FOR_USER
