import json

from fastapi.testclient import TestClient

from sinobee.agent import Agent, Runtime
from sinobee.config import Config
from sinobee.learning import learn_from_feedback
from sinobee.server import create_app
from sinobee.types import ImagePart, Message

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
                    "0001e221bc330000000049454e44ae426082")


def test_files_api_and_upload(home, provider):
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    ws = rt.config.workspace
    (ws / "chart.png").write_bytes(PNG)
    r = c.get("/api/files", params={"path": "chart.png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get("/api/files", params={"path": str(ws / "chart.png"), "download": 1}).headers[
        "content-disposition"].startswith("attachment")
    (ws / "page.html").write_text("<script>alert(1)</script>")
    assert c.get("/api/files", params={"path": "page.html"}).headers["content-disposition"].startswith("attachment")
    assert c.get("/api/files", params={"path": "../secrets.json"}).status_code in (403, 404)
    rt.config.secrets.set("openai", "sk-secret-123456")
    assert c.get("/api/files", params={"path": str(rt.config.home / "secrets.json")}).status_code == 403
    assert c.get("/api/files", params={"path": "/etc/passwd"}).status_code == 403
    info = c.get("/api/files/info", params={"path": "chart.png"}).json()
    assert info["size"] == len(PNG)
    up = c.post("/api/upload", files=[("files", ("我的 圖.png", PNG, "image/png")), ("files", ("a.pdf", b"%PDF", "application/pdf"))]).json()
    assert len(up) == 2 and up[0]["path"].startswith(str(ws / "uploads")) and up[0]["mime"] == "image/png"
    assert c.get("/api/files", params={"path": up[1]["path"]}).status_code == 200
    assert c.get("/vendor/marked.min.js").status_code == 200 and c.get("/vendor/mermaid.min.js").status_code == 200
    assert c.get("/vendor/..%2Fapp.py").status_code == 404


async def test_attached_images_are_sent_to_the_model(config, provider):
    (config.workspace / "uploads").mkdir(parents=True, exist_ok=True)
    img = config.workspace / "uploads" / "shot.png"
    img.write_bytes(PNG)
    provider.push("看到了")
    rt = Runtime(config, provider=provider)
    await Agent(rt).run(f"這張圖是什麼？\n\n![shot.png]({img})")
    user = provider.requests[0][0][1]
    assert any(isinstance(p, ImagePart) for p in user.content)


async def test_feedback_records_turn_and_learns(config, provider):
    rt = Runtime(config, provider=provider)
    provider.push("", [("skill_view", {"name": "web-research"})])
    provider.push("答案")
    ends = []
    agent = Agent(rt, emit=lambda k, d: ends.append(d) if k == "turn_end" else None)
    await agent.run("查一下")
    await rt.drain()
    turn_id = ends[0]["turn_id"]
    t = rt.memory.turn(turn_id)
    assert t["skills"] == ["web-research"] and t["status"] == "done"
    assert [m.role for m in rt.memory.turn_messages(t)][0] == "user"

    def reflect_reply(messages):
        assert "NEGATIVE" in messages[1].text() and "要表格" in messages[1].text()
        return Message("assistant", json.dumps({"memories": [{"content": "使用者偏好以表格呈現比較結果",
                                                              "kind": "preference", "importance": 0.9}]}))
    provider.script.append(reflect_reply)
    fid = rt.memory.add_feedback(turn_id, agent.session_id, -1, ["format"], "我要表格")
    rep = await learn_from_feedback(rt, fid, t, -1, ["format"], "我要表格")
    assert rep["skills_rated"] == ["web-research"] and rt.skills.stats("web-research")["failures"] == 1
    assert any("表格" in m.content for m in rt.memory.list())
    st = rt.memory.feedback_stats()
    assert st["totals"]["down"] == 1 and st["by_skill"][0]["key"] == "web-research"
    assert st["negative_tags"][0][0] == "format"
    data = rt.memory.feedback_dataset()
    assert data[0]["rating"] == -1 and data[0]["messages"][0]["role"] == "user"


def test_feedback_api_upsert(home, provider):
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    rt.memory.ensure_session("s1")
    rt.memory.append_messages("s1", [Message("user", "hi"), Message("assistant", "hello")])
    tid = rt.memory.record_turn("s1", 0, 1, "done", "test/m", [], 0, 0.0, 1.0)
    assert c.get("/api/sessions/s1/turns").json()[0]["id"] == tid
    assert c.post("/api/feedback", json={"turn_id": tid, "rating": 1}).json()["status"] == "recorded"
    assert c.post("/api/feedback", json={"turn_id": tid, "rating": 1}).json()["status"] == "unchanged"
    c.post("/api/feedback", json={"turn_id": tid, "rating": 1, "comment": "很好，以後都這樣"})
    assert c.get("/api/feedback/stats").json()["totals"]["n"] == 1
    assert c.get("/api/sessions/s1/turns").json()[0]["rating"] == 1
    r = c.get("/api/feedback/export")
    assert r.status_code == 200 and json.loads(r.text.splitlines()[0])["comment"] == "很好，以後都這樣"


def test_mermaid_source_survives_sanitizer():
    # DOMPurify drops attribute values containing "-->" (mXSS guard); mermaid arrows must be URI-encoded
    from pathlib import Path
    html = (Path(__file__).parents[1] / "sinobee/server/static/index.html").read_text()
    assert 'data-src="${encodeURIComponent(text)}"' in html
    assert "decodeURIComponent(src)" in html


def test_session_title_strips_markdown():
    from sinobee.agent.loop import _title
    assert _title("看圖 ![a.png](/tmp/x/a.png)") == "看圖 [a.png]"
