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
    assert c.get("/vendor/katex.min.js").status_code == 200 and c.get("/vendor/katex.min.css").status_code == 200
    font = c.get("/vendor/fonts/KaTeX_Main-Regular.woff2")
    assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
    assert c.get("/vendor/fonts/..%2Fkatex.min.js").status_code == 404
    assert c.get("/vendor/fonts/..%2F..%2Findex.html").status_code == 404


def test_mermaid_labels_the_lexer_rejects_are_quoted_on_retry():
    """quadrantChart axes with （）/ and CJK, and flowchart nodes with parentheses, render after the automatic fix."""
    import shutil, subprocess
    from pathlib import Path

    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    page = (Path(__file__).parents[1] / "sinobee/server/static/index.html").read_text(encoding="utf-8")
    js = page[page.index("const MMD_SHAPES"):page.index("let MERMAID")]
    src = ("quadrantChart\n  title 引擎配置 (示意)\n  x-axis 低封裝緊湊度 (長度大) --> 高 (易橫置/小機艙)\n  quadrant-1 緊湊且平順\n"
           '  直列六缸 (B58): [0.15, 0.95]\n  "V6 120°": [0.7, 0.8]\n')
    out = subprocess.run([node, "-e", js + "process.stdout.write(mmdFix(require('fs').readFileSync(0, 'utf8')))"],
                         input=src, capture_output=True, text=True, check=True).stdout
    assert 'x-axis "低封裝緊湊度 (長度大)" --> "高 (易橫置/小機艙)"' in out
    assert 'quadrant-1 "緊湊且平順"' in out and '"直列六缸 (B58)": [0.15, 0.95]' in out and '"V6 120°": [0.7, 0.8]' in out
    assert "title 引擎配置 (示意)" in out   # titles take the rest of the line as is
    flow = "flowchart LR\n  A[引擎 (V6)] --> B(夾角 (低重心))\n  B -->|好處 (平衡)| C{選 V6/V8?}\n  C --> D[\"ok (x)\"]"
    out = subprocess.run([node, "-e", js + "process.stdout.write(mmdFix(require('fs').readFileSync(0, 'utf8')))"],
                         input=flow, capture_output=True, text=True, check=True).stdout
    assert out.splitlines()[1:] == ['  A["引擎 (V6)"] --> B("夾角 (低重心)")', '  B -->|"好處 (平衡)"| C{"選 V6/V8?"}', '  C --> D["ok (x)"]']
    assert "katex.min.js" in page and "mathInline" in page


def test_view_links_run_skill_pages_sandboxed_and_scoped(home, provider):
    """Pages made by skills (anatomy figures, interactive reports) open in the app through a scoped, sandboxed link."""
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    fig = rt.config.workspace / "figures" / "rig"
    fig.mkdir(parents=True)
    (fig / "rig.html").write_text("<script>document.title = 'ok'</script>")
    (fig / "data.json").write_text("{}")
    (rt.config.workspace / "other.txt").write_text("not in the page's folder")
    v = c.post("/api/view", json={"path": "figures/rig/rig.html"}).json()
    assert v["url"].startswith("/view/") and v["url"].endswith("/rig.html")
    r = c.get(v["url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    cap = v["url"].split("/")[2]
    assert f"connect-src http://testserver/view/{cap}/;" in csp and "frame-ancestors 'self'" in csp
    assert c.get(f"/view/{cap}/data.json").status_code == 200            # files beside the page load
    assert c.get(f"/view/{cap}/../other.txt").status_code == 404          # nothing outside its folder
    assert c.get(f"/view/{cap}/%2e%2e/other.txt").status_code == 404
    assert c.get("/view/nope/rig.html").status_code == 404
    assert c.post("/api/view", json={"path": "other.txt"}).status_code == 400   # only pages open this way
    assert c.post("/api/view", json={"path": str(rt.config.home / "secrets.json")}).status_code in (403, 404)


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


async def test_changing_a_thumbs_up_to_down_takes_back_what_it_taught(config, provider):
    from sinobee.agent import rules
    from sinobee.evals import add_regression, regressions
    from sinobee.learning.reflector import undo_feedback
    rt = Runtime(config, provider=provider)
    rt.memory.ensure_session("s1")
    rt.memory.append_messages("s1", [Message("user", "查一下"), Message("assistant", "答案")])
    tid = rt.memory.record_turn("s1", 0, 1, "done", "test/m", ["web-research"], 3, 0.0, 1.0)
    turn = rt.memory.turn(tid)
    # what the 👍 taught: a success for the skill, a memory, a new skill made from this result
    rt.skills.feedback("web-research", True)
    mem = rt.memory.save("這種做法很好")
    rt.skills.set_status("web-research", "active")
    prev = {"rating": 1, "learn_report": json.dumps({"memories": [{**mem}],
                                                     "skill": {"status": "created", "name": "web-research"}})}
    undone = undo_feedback(rt, turn, prev)
    st = rt.skills.stats("web-research")
    assert st["successes"] == 0 and st["status"] == "candidate" and st["failures"] == 1
    assert undone["memories_archived"] == [mem["id"]] and rt.memory.get(mem["id"]).tier == "archive"
    assert undone["skill_demoted"] == "web-research"
    # what a 👎 taught: a quality rule and a regression eval - withdrawn when it becomes 👍
    r = rules.add(config.home, {"title": "x", "pattern": "TODO"})
    ev = add_regression(config.home, {"prompt": "查一下"})
    undone = undo_feedback(rt, turn, {"rating": -1, "learn_report": json.dumps({"rule": r, "eval": ev["id"]})})
    assert rules.load(config.home)[0]["enabled"] is False and regressions(config.home) == []
    assert undone["rule_disabled"] == r["id"] and undone["eval_removed"] == ev["id"]


def test_mermaid_source_survives_sanitizer():
    # DOMPurify drops attribute values containing "-->" (mXSS guard); mermaid arrows must be URI-encoded
    from pathlib import Path
    html = (Path(__file__).parents[1] / "sinobee/server/static/index.html").read_text()
    assert 'data-src="${encodeURIComponent(text)}"' in html
    assert "decodeURIComponent(src)" in html


def test_session_title_strips_markdown():
    from sinobee.agent.loop import _title
    assert _title("看圖 ![a.png](/tmp/x/a.png)") == "看圖 [a.png]"


def test_favicon_and_app_icons_are_served_without_a_token(home, provider):
    from fastapi.testclient import TestClient
    from sinobee.agent import Runtime
    from sinobee.config import Config
    from sinobee.server import create_app
    c = TestClient(create_app(Runtime(Config.load(), provider=provider)))
    for path, mime in (("/favicon.ico", "image/x-icon"), ("/icons/favicon.svg", "image/svg+xml"),
                       ("/apple-touch-icon.png", "image/png"), ("/icons/icon-512.png", "image/png"),
                       ("/icons/manifest.webmanifest", "application/manifest+json")):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(mime), path
    assert c.get("/icons/../index.html").status_code == 404
    html = c.get("/").text
    assert 'rel="icon" href="/icons/favicon.svg"' in html and 'rel="manifest"' in html


def test_public_serve_always_has_a_token(home, provider, monkeypatch):
    from fastapi.testclient import TestClient
    from sinobee.agent import Runtime
    from sinobee.cli import serve_token
    from sinobee.config import Config
    from sinobee.server import create_app
    monkeypatch.delenv("SINOBEE_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("SINOBEE_IN_DOCKER", raising=False)
    monkeypatch.setattr("os.path.exists", lambda p, _e=__import__("os").path.exists: False if p == "/.dockerenv" else _e(p))
    cfg = Config.load()
    assert serve_token(cfg, "127.0.0.1") == (None, "none")              # local only: no token needed
    tok, src = serve_token(cfg, "0.0.0.0")
    assert src == "generated" and len(tok) >= 24
    assert serve_token(cfg, "0.0.0.0") == (tok, "saved")                  # kept for later runs
    new, src = serve_token(cfg, "0.0.0.0", renew=True)
    assert src == "generated" and new != tok
    assert serve_token(cfg, "0.0.0.0", explicit="my-own-token-123") == ("my-own-token-123", "flag")
    c = TestClient(create_app(Runtime(Config.load(), provider=provider)))
    assert c.get("/api/status").status_code == 401                      # the saved token protects the API
    assert c.get(f"/api/status?token={new}").status_code == 200
    assert c.get("/favicon.ico").status_code == 200                      # static files stay public


def test_pages_printed_by_a_build_script_become_viewable_artifacts(config):
    """`node figures/rig/build.mjs` prints "wrote rig.html": the page beside the script is offered in the viewer."""
    from sinobee.agent import artifacts
    fig = config.workspace / "figures" / "rig"
    fig.mkdir(parents=True)
    (fig / "build.mjs").write_text("// build")
    (fig / "rig.html").write_text("<p>rig</p>")
    found = artifacts.collect(config, "shell", {"command": "node figures/rig/build.mjs --light"}, "wrote rig.html 120 KB", set())
    assert [f["path"] for f in found] == ["figures/rig/rig.html"] and found[0]["kind"] == "doc"


def test_skill_picker_in_the_composer_and_titles_without_slash_prefixes():
    """The composer's skill button (and / in the input) picks skills; the message goes out as /name prefixes."""
    from pathlib import Path
    from sinobee.agent.loop import _title
    page = (Path(__file__).parents[1] / "sinobee/server/static/index.html").read_text(encoding="utf-8")
    assert 'id="skillBtn"' in page and 'id="skillPick"' in page and "SKSEL.map(n => '/' + n)" in page
    assert _title("/youtube-analysis /anatomy https://youtu.be/x 幫我分析") == "https://youtu.be/x 幫我分析"
    assert _title("/youtube-analysis") == "使用技能：youtube-analysis"
    assert _title("/learn https://a") == "/learn https://a"


def test_pages_with_formulas_get_katex_and_lost_backslashes_are_restored(home, provider):
    """HTML reports that write $…$ or formula boxes without a math library get KaTeX in the viewer; LaTeX that lost
    its backslashes in JSON tool arguments is repaired when the file is written."""
    from sinobee.providers.openai_compat import parse_arguments
    from sinobee.tools.files import repair_tex
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    d = rt.config.workspace / "rep"
    d.mkdir()
    (d / "math.html").write_text("<html><head></head><body><p>速度 $v_e$</p><div class='formula'>F = dot{m} cdot v_e</div></body></html>")
    (d / "plain.html").write_text("<html><head></head><body><p>價格 $5 and $10</p></body></html>")
    (d / "own.html").write_text("<html><head><script src='katex.js'></script></head><body>$x$</body></html>")
    for name, injected in (("math.html", True), ("plain.html", False), ("own.html", False)):
        url = c.post("/api/view", json={"path": f"rep/{name}"}).json()["url"]
        r = c.get(url)
        assert ("sb-math.js" in r.text) is injected, name
        assert r.headers["access-control-allow-origin"] == "*" and r.headers["cross-origin-resource-policy"] == "cross-origin"
    cap = url.split("/")[2]
    assert c.get(f"/view/{cap}/__sinobee_math/katex.min.js").status_code == 200
    assert c.get(f"/view/{cap}/__sinobee_math/fonts/KaTeX_Main-Regular.woff2").status_code == 200
    assert c.get(f"/view/{cap}/__sinobee_math/purify.min.js").status_code == 404
    assert c.get(f"/view/{cap}/__sinobee_math/../../app.py").status_code == 404
    # JSON damage: \f → form feed, \b → backspace, \t → tab; and invalid escapes like \g are kept as text
    text, n = repair_tex("r.html", "\x0crac{a}{b} + \x08eta \times 2")
    assert text == "\\frac{a}{b} + \\beta \\times 2" and n == 3
    assert repair_tex("r.py", "\x0c")[1] == 0
    assert parse_arguments('{"content": "$\\gamma$ \\sqrt{2} \\"q\\""}') == {"content": '$\\gamma$ \\sqrt{2} "q"'}
