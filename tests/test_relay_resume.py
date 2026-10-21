"""Long answers through the SinoBee.ai relay survive a cut connection (plan "長回覆斷線", 2026-10-21): the call carries
a request id, a cut stream is resumed after the last whole event (twice), then polled; after a cut, new calls start
as jobs; tools stream their arguments eagerly; a stream that cannot be saved is sent once more and then the agent
asks for the work in parts (U4)."""
import asyncio
import json

import httpx
import pytest

from sinobee.providers import base
from sinobee.providers.anthropic import AnthropicProvider
from sinobee.types import Message, ToolSpec

ROOT = "https://sb.test/api/v1/llm"


def ev(n: int | None, kind: str, **data) -> bytes:
    head = f"id: {n}\n" if n is not None else ""
    return f"{head}event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode()


def events() -> list[tuple[str, dict]]:
    """An answer with text and a tool call whose argument arrives in pieces."""
    page = "<html>" + "x" * 50 + "</html>"
    arg = json.dumps({"path": "index.html", "content": page})
    out = [("message_start", {"message": {"id": "m", "model": "claude-x", "usage": {"input_tokens": 10}}}),
           ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})]
    out += [("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": w}}) for w in ("寫", "好", "了")]
    out += [("content_block_stop", {"index": 0}),
            ("content_block_start", {"index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "write_file"}})]
    out += [("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": arg[i:i + 9]}})
            for i in range(0, len(arg), 9)]
    out += [("content_block_stop", {"index": 1}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 99}}),
            ("message_stop", {})]
    return out


class Cut(httpx.AsyncByteStream):
    """Sends these bytes, then the connection drops (or ends cleanly)."""

    def __init__(self, chunks: list[bytes], cut: bool):
        self.chunks, self.cut = chunks, cut

    async def __aiter__(self):
        for c in self.chunks:
            yield c
        if self.cut:
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body "
                                            "(incomplete chunked read)")


class FakeRelay:
    def __init__(self, script):
        self.evs = events()
        self.script = list(script)   # what each request does, in order
        self.seen: list[tuple[str, str, dict]] = []

    def sse(self, start: int, stop: int, cut: bool) -> httpx.Response:
        body = [ev(i + 1, k, **d) for i, (k, d) in enumerate(self.evs) if start <= i < stop]
        if not cut:
            body.append(b": x-sinobee-quota {}\n\n")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Cut(body, cut))

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.seen.append((req.method, req.url.path + (f"?{req.url.query.decode()}" if req.url.query else ""),
                          dict(req.headers)))
        step = self.script.pop(0)
        return step(self, req)


@pytest.fixture
def relay(monkeypatch):
    holder = {}
    real = httpx.AsyncClient

    def client(**kw):
        return real(transport=httpx.MockTransport(holder["r"].handler), **kw)
    monkeypatch.setattr(base.httpx, "AsyncClient", client)
    monkeypatch.setattr(base, "_poll_until", 0.0)

    def make(script):
        holder["r"] = FakeRelay(script)
        return holder["r"]
    return make


def provider():
    return AnthropicProvider("claude-x", f"{ROOT}/anthropic", None, provider_name="sinobee",
                             auth=lambda m, u, b: {"authorization": "Bearer t", "x-sb-signature": f"{m} {u}"})


def tools():
    return [ToolSpec("write_file", "Create a file.", {"type": "object", "properties": {"path": {"type": "string"},
                                                                                        "content": {"type": "string"}}})]


def check(resp):
    assert resp.message.text() == "寫好了"
    [call] = resp.message.tool_calls
    assert call.name == "write_file" and call.arguments["content"].startswith("<html>xxx") and \
        call.arguments["content"].endswith("</html>")
    assert resp.usage.output_tokens == 99


def test_a_cut_stream_is_resumed_after_the_last_whole_event_then_polled(relay):
    n = len(events())

    def first(r, req):
        body = json.loads(req.content)
        assert all(t["eager_input_streaming"] is True for t in body["tools"]), "big arguments stream as written"
        assert req.headers["x-sinobee-request-id"].startswith("sb_")
        # 4 whole events, then half of the 5th, then the connection drops
        resp = r.sse(0, 4, cut=True)
        resp.stream.chunks.append(b"id: 5\nevent: content_block_delta\ndata: {\"type\":")
        return resp

    def resume1(r, req):
        assert req.headers["last-event-id"] == "4" and req.headers["x-sb-signature"].startswith("GET ")
        return r.sse(4, 8, cut=True)

    def resume2(r, req):
        assert req.headers["last-event-id"] == "8"
        raise httpx.ConnectError("connection reset")

    def poll(r, req):
        assert req.url.params["after"] == "8"
        blocks = [f"event: {k}\ndata: {json.dumps({'type': k, **d})}\n\n" for k, d in r.evs[8:]]
        return httpx.Response(200, json={"id": "x", "status": "done", "events": blocks, "next": n, "error": None})

    r = relay([first, resume1, resume2, poll])
    resp = asyncio.run(provider().chat([Message("user", "寫頁面")], tools()))
    check(resp)
    rid = r.seen[0][2]["x-sinobee-request-id"]
    assert [s[1] for s in r.seen] == ["/api/v1/llm/anthropic/v1/messages", f"/api/v1/llm/streams/{rid}",
                                      f"/api/v1/llm/streams/{rid}", f"/api/v1/llm/jobs/{rid}?after=8&wait=20"]
    assert base.relay_polling(), "after a cut, new calls start as jobs for a while"

    # the next call starts as a job and is polled; the model is called once per request id
    def job(r, req):
        assert req.url.path == "/api/v1/llm/jobs" and req.url.params["format"] == "anthropic"
        assert req.headers["x-sb-signature"] == f"POST {ROOT}/jobs?format=anthropic", "signed for the jobs URL"
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(202, json={"id": req.headers["x-sinobee-request-id"], "status": "running"})

    def half(r, req):
        blocks = [f"event: {k}\ndata: {json.dumps({'type': k, **d})}\n\n" for k, d in r.evs[:6]]
        return httpx.Response(200, json={"status": "running", "events": blocks, "next": 6})

    def rest(r, req):
        assert req.url.params["after"] == "6"
        blocks = [f"event: {k}\ndata: {json.dumps({'type': k, **d})}\n\n" for k, d in r.evs[6:]]
        return httpx.Response(200, json={"status": "done", "events": blocks, "next": n})

    relay([job, half, rest])
    check(asyncio.run(provider().chat([Message("user", "再一頁")], tools())))


def test_a_stream_that_cannot_be_resumed_is_sent_once_more_then_given_up(relay, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(base.asyncio, "sleep", lambda s: real_sleep(0))

    def cut(r, req):
        return r.sse(0, 3, cut=True)

    def unknown(r, req):
        return httpx.Response(404, json={"error": {"message": "no such stream"}})

    def job(r, req):
        return httpx.Response(202, json={"id": req.headers["x-sinobee-request-id"], "status": "running"})

    def down(r, req):
        raise httpx.ConnectError("connection reset")

    r = relay([cut, unknown, job] + [down] * base.RelayFeed.ATTEMPTS)
    with pytest.raises(base.ProviderError) as e:
        asyncio.run(provider().chat([Message("user", "寫頁面")], tools()))
    assert base.is_stream_cut(e.value)
    posts = [s for s in r.seen if s[0] == "POST"]
    assert [p[1] for p in posts] == ["/api/v1/llm/anthropic/v1/messages", "/api/v1/llm/jobs?format=anthropic"], \
        "sent once more - as a polled job, since the stream was just cut - and not four times"
    assert posts[0][2]["x-sinobee-request-id"] != posts[1][2]["x-sinobee-request-id"]
    assert not r.script


def test_a_whole_stream_is_read_as_before(relay):
    r = relay([lambda r, req: r.sse(0, len(r.evs), cut=False)])
    check(asyncio.run(provider().chat([Message("user", "x")], tools())))
    assert len(r.seen) == 1 and not base.relay_polling()


def test_complex_work_is_marked_for_the_quality_floor():
    from sinobee import routing
    assert routing.is_complex("做一個 3D 網頁遊戲") and routing.is_complex("修這個 bug")
    assert routing.is_complex("整理資料：" + "細節" * 400)
    assert not routing.is_complex("今天天氣好嗎")


def test_a_stream_cut_that_could_not_be_saved_turns_into_writing_in_parts(config):
    from conftest import ScriptedProvider

    from sinobee.agent import Agent, Runtime
    from sinobee.providers.base import ProviderError

    class Cutting(ScriptedProvider):
        def __init__(self):
            super().__init__()
            self.cuts = 1

        async def chat(self, messages, tools=None, **kw):
            if self.cuts:
                self.cuts -= 1
                raise ProviderError("sinobee/anthropic/claude-x network error: stream cut, resuming failed 6 times: "
                                    "RemoteProtocolError('incomplete chunked read')", None, True)
            return await super().chat(messages, tools, **kw)

    p = Cutting()
    p.push("好了")
    events_seen = []
    agent = Agent(Runtime(config, provider=p), session_id="cut", emit=lambda k, d: events_seen.append((k, d)))
    out = asyncio.run(agent.run("寫一頁"))
    assert "好了" in out
    assert any("串流中途斷線" in str(d) for k, d in events_seen if k == "status")


def test_an_older_relay_without_jobs_is_streamed_as_before(relay, monkeypatch):
    monkeypatch.setattr(base, "_poll_until", 9e12)
    r = relay([lambda r, req: httpx.Response(404, text="<html>Not Found</html>"),
               lambda r, req: r.sse(0, len(r.evs), cut=False)])
    check(asyncio.run(provider().chat([Message("user", "x")], tools())))
    assert [s[1] for s in r.seen] == ["/api/v1/llm/jobs?format=anthropic", "/api/v1/llm/anthropic/v1/messages"]
    assert not base.relay_polling()
