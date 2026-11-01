"""Sharing what skills make (reports, interactive pages) and the link preview (description + 1200×630 picture)."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sinobee import account, share, share_doc

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def report(ws: Path) -> Path:
    d = ws / "reports" / "yt"
    (d / "frames").mkdir(parents=True)
    (d / "cover.png").write_bytes(PNG)
    (d / "frames" / "t0915.jpg").write_bytes(b"jpg")
    (d / "transcript.md").write_text("逐字稿", encoding="utf-8")
    (d / "unused.png").write_bytes(PNG)
    (ws / "reports" / "outside.png").write_bytes(PNG)
    (d / "report.md").write_text(
        "# 神經網路是什麼\n\n![封面](cover.png)\n\n> 一句話：神經網路是 $13{,}002$ 個參數的函數，角度 $120^\\circ$。\n\n"
        "![圖](frames/t0915.jpg) [逐字稿](transcript.md) [外面](../outside.png) [網站](https://sinobee.ai)\n\n"
        f"金鑰 sk-ant-{'a' * 30} 在 {Path.home()}/x\n", encoding="utf-8")
    return d / "report.md"


def page(ws: Path) -> Path:
    d = ws / "figures" / "rig"
    (d / "img").mkdir(parents=True)
    (d / "img" / "a.png").write_bytes(PNG)
    (d / "style.css").write_text(".x{background:url('img/a.png')}")
    (d / "data.json").write_text("{}")
    (d / "rig.html").write_text(
        "<html><head><title>Rig 原理</title><link rel=stylesheet href=style.css></head><body><svg><text>標籤</text></svg>"
        "<figcaption><span>圖例 A</span><span>按住開閥：氣體從噴嘴噴出，產生推力。這是一段完整的說明句子，有標點。</span></figcaption>"
        "<script src='https://cdn.example/x.js'></script><script>fetch('data.json')</script></body></html>", encoding="utf-8")
    return d / "rig.html"


def test_a_report_travels_with_the_files_it_uses_and_nothing_else(config):
    entry = report(Path(config.workspace))
    names = [f["name"] for f in share_doc.collect(entry)]
    assert names == ["report.md", "cover.png", "frames/t0915.jpg", "transcript.md"]   # not unused.png, not ../outside.png
    title, desc = share_doc.describe(entry)
    assert title == "神經網路是什麼"
    assert desc.startswith("一句話：神經網路是 13,002 個參數的函數，角度 120°")


def test_a_page_travels_with_its_styles_images_and_data(config):
    entry = page(Path(config.workspace))
    assert [f["name"] for f in share_doc.collect(entry)] == ["rig.html", "style.css", "data.json", "img/a.png"]
    title, desc = share_doc.describe(entry)
    assert title == "Rig 原理" and desc.startswith("按住開閥：氣體從噴嘴噴出")


def test_build_doc_redacts_the_text_files(config):
    entry = report(Path(config.workspace))
    b = share.build_doc(config, str(entry))
    assert b["kind"] == "doc" and b["entry"] == "report.md"
    # older hubs require a message: the document travels as a linked attachment there
    assert [m["role"] for m in b["messages"]] == ["assistant"] and "[開啟文件](files/1)" in b["messages"][0]["text"]
    md = base64.b64decode(next(f["data"] for f in b["files"] if f["name"] == "report.md")).decode()
    assert "sk-ant-" not in md and str(Path.home()) not in md and "••••" in md
    assert "path" in next(f for f in b["files"] if f["name"] == "cover.png")   # binaries are read at upload time
    assert {r["kind"] for r in b["redactions"]} >= {"apikey", "path"}
    with pytest.raises(share.ShareError):
        share.build_doc(config, str(Path(config.workspace) / "reports" / "outside.png"))


def test_a_conversation_share_brings_the_linked_report_along(config):
    report(Path(config.workspace))
    b = share.build(config, [{"role": "user", "content": "分析影片"},
                             {"role": "assistant", "content": "完成：$120^\\circ$ [開啟完整報告](reports/yt/report.md)"}])
    assert [f["name"] for f in b["files"]] == ["doc1/report.md", "doc1/cover.png", "doc1/frames/t0915.jpg", "doc1/transcript.md"]
    assert "[開啟完整報告](files/1)" in b["messages"][1]["text"]
    assert b["description"].startswith("完成：120°")


def test_thumbnails_are_1200_by_630(config):
    pytest.importorskip("playwright")
    png = share_doc.thumbnail(title="比較 V6 與直列六缸", description="兩款均採用 120° 寬夾角", label="對話")
    if png is None:
        pytest.skip("no Chromium / cairosvg here")
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(png)).size == (1200, 630)


def test_upload_sends_description_thumbnail_and_bundle_paths(config, monkeypatch):
    entry = report(Path(config.workspace))
    seen = {}

    def handler(req: httpx.Request):
        import re as _re
        raw = req.read().decode("latin-1")
        m = _re.search(r'name="share"\r\n\r\n(.*?)\r\n--', raw, _re.S)
        seen["body"] = json.loads(m.group(1).encode("latin-1").decode())
        seen["thumb"] = 'name="thumb"; filename="thumb.png"' in raw
        return httpx.Response(201, json={"id": "abc", "url": "http://hub/s/abc", "visibility": "LINK"})
    real = httpx.Client

    def request(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(share.httpx, "request", request)
    account.save(config, {"access_token": "sbh_" + "q" * 30, "scope": "shares:write", "account": "0xabc"})
    b = share.build_doc(config, str(entry))
    share.upload(config, b, description="自訂摘要", thumbnail=PNG)
    body = seen["body"]
    assert body["kind"] == "doc" and body["entry"] == "report.md" and body["description"] == "自訂摘要"
    assert body["fileNames"] == ["report.md", "cover.png", "frames/t0915.jpg", "transcript.md"]
    assert not any(k.startswith("_") for k in body) and seen["thumb"]


def test_share_api_previews_documents(home, provider):
    from sinobee.agent import Runtime
    from sinobee.config import Config
    from sinobee.server import create_app
    rt = Runtime(Config.load(), provider=provider)
    c = TestClient(create_app(rt))
    report(Path(rt.config.workspace))
    p = c.post("/api/share/preview", json={"doc_path": "reports/yt/report.md"}).json()
    assert p["kind"] == "doc" and p["title"] == "神經網路是什麼" and p["description"]
    assert [f["name"] for f in p["files"]][:2] == ["report.md", "cover.png"]
    assert not any(k.startswith("_") for k in p) and "path" not in p["files"][0]
    assert c.post("/api/share/preview", json={"doc_path": "../../etc/passwd"}).status_code in (400, 403, 404)


def test_a_three_js_page_brings_the_modules_of_its_import_map(tmp_path):
    """three.js through an import map: "three" and "three/addons/..." are files next to the page, and a module that
    imports "three" gets it from the map too - the shared page needs all of them."""
    (tmp_path / "vendor/three/build").mkdir(parents=True)
    (tmp_path / "vendor/three/examples/jsm/postprocessing").mkdir(parents=True)
    (tmp_path / "vendor/three/build/three.module.js").write_text("export const A = 1;\n")
    (tmp_path / "vendor/three/examples/jsm/postprocessing/Pass.js").write_text("export const P = 1;\n")
    (tmp_path / "vendor/three/examples/jsm/postprocessing/EffectComposer.js").write_text(
        "import { A } from 'three';\nimport { P } from './Pass.js';\nexport { A, P };\n")
    (tmp_path / "vendor/three/examples/jsm/unused.js").write_text("export {};\n")
    entry = tmp_path / "euv.html"
    entry.write_text('<script type="importmap">{ "imports": { "three": "./vendor/three/build/three.module.js", '
                     '"three/addons/": "./vendor/three/examples/jsm/" } }</script>\n<script type="module">\n'
                     "import * as THREE from 'three';\n"
                     "import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';\n</script>")
    names = [f["name"] for f in share_doc.collect(entry)]
    assert names[0] == "euv.html" and set(names[1:]) == {
        "vendor/three/build/three.module.js", "vendor/three/examples/jsm/postprocessing/EffectComposer.js",
        "vendor/three/examples/jsm/postprocessing/Pass.js"}


def test_a_template_description_the_page_never_filled_in_is_not_the_links_description():
    head = '<title>EUV</title><meta name="description" content="{}">'
    body = "<p>EUV 光刻機用 13.5 nm 的極紫外光在晶圓上曝光，錫滴被雷射打成電漿後發光。</p>"
    title, desc = share_doc.describe_html(head.format("ONE LINE ABOUT THE MACHINE. Built with the Machine Skill skill "
                                                      "for Claude.") + body)
    assert desc.startswith("EUV 光刻機用 13.5 nm")                   # the page's own text instead
    _, desc = share_doc.describe_html(head.format("A cutaway EUV scanner. Built with the Machine Skill skill for Claude."))
    assert desc == "A cutaway EUV scanner."                          # a real line keeps, without the credit


def test_a_page_of_es_modules_is_photographed_and_a_blank_one_is_not(tmp_path):
    """Chromium refuses ES modules from file: - the page is served over http to the browser, and a flat picture
    (a canvas that never drew) leaves the card without one."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Exception:  # noqa: BLE001
        pytest.skip("Chromium for Playwright is not installed")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/paint.js").write_text(
        "export function paint(c){ const g = c.getContext('2d'); g.fillStyle = '#1d6fd1'; g.fillRect(0, 0, 400, 300);"
        " g.fillStyle = '#f5b400'; g.fillRect(400, 0, 500, 560); }\n")
    page = tmp_path / "page.html"
    page.write_text('<script type="importmap">{"imports": {"p": "./vendor/paint.js"}}</script><body style="margin:0;'
                    'background:#fff"><script type="module">import { paint } from "p"; const c = '
                    'document.createElement("canvas"); c.width = 900; c.height = 560; document.body.appendChild(c); '
                    "paint(c);</script>")
    assert share_doc.screenshot(page)
    blank = tmp_path / "blank.html"
    blank.write_text('<body style="margin:0;background:#fff"><canvas width="900" height="560"></canvas></body>')
    assert share_doc.screenshot(blank) is None


def test_code_is_not_redacted_as_a_password_but_a_quoted_one_is(config):
    """three.js: `const pass = this.passes[ i ]` and 'THREE.Pass: .render()' were masked as passwords, which broke
    the shared page's EffectComposer. In code only a quoted value is a password."""
    red = share.Redactor(config)
    js = "const pass = this.passes[ i ];\nconsole.error( 'THREE.Pass: .render() must be implemented' );\n"
    assert red(js, code=True) == js
    assert "hunter22" not in red('const password = "hunter22";', code=True)
    assert "hunter22" not in red("password: hunter22")                 # prose: unquoted still masked


def test_the_pages_picture_is_taken_once_per_version(tmp_path, monkeypatch):
    """Editing the card's title or description must not take the page's picture again (each took ~50 s)."""
    calls = []
    monkeypatch.setattr(share_doc, "_screenshot", lambda p, args=None: calls.append(args) or b"JPEG")
    share_doc._SHOTS.clear()
    page = tmp_path / "p.html"
    page.write_text("<canvas></canvas>")
    assert share_doc.screenshot(page) == share_doc.screenshot(page) == b"JPEG" and len(calls) == 1
    import os
    os.utime(page, ns=(1, 1))                                          # a new version of the page: a new picture
    share_doc.screenshot(page)
    assert len(calls) == 2


def test_the_card_uses_what_the_viewer_shows_now(config, monkeypatch):
    """The viewer's picture of the page (your angle, your state) is the card's picture; the page is not loaded again."""
    monkeypatch.setattr(share_doc, "screenshot", lambda p: pytest.fail("the page must not be photographed again"))
    seen = {}
    monkeypatch.setattr(share_doc, "render_png", lambda markup: seen.setdefault("markup", markup) and b"PNG")
    entry = page(Path(config.workspace))
    b = share.build_doc(config, str(entry))
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 64
    capture = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    assert share.make_thumbnail(b, title="t", description="d", capture=capture) == b"PNG"
    assert base64.b64encode(jpeg).decode() in seen["markup"]
    assert share.capture_image("data:image/jpeg;base64," + base64.b64encode(b"<svg/>").decode()) is None   # not a JPEG
    assert share.capture_image("data:text/html;base64,PGI+") is None


def test_pages_in_the_viewer_can_answer_the_capture_request(config):
    from sinobee.server.files_api import with_capture
    out = with_capture("<html><head><title>x</title></head><body></body></html>", "/view/c/__sinobee_math/")
    assert out.startswith('<html><head><script src="/view/c/__sinobee_math/sb-capture.js"></script><title>')
    assert with_capture("<p>no head</p>", "/b/").startswith('<script src="/b/sb-capture.js"></script>')


class _Writer:
    label = "test/aux"
    provider_name = "openai"
    model = "aux"

    def __init__(self, reply):
        self.reply, self.prompts, self.recorder = reply, [], None

    async def chat(self, messages, tools=None, **kw):
        from sinobee.types import ChatResponse, Message, Usage
        self.prompts.append(messages[0].text())
        return ChatResponse(Message("assistant", self.reply), Usage(10, 5), "stop")

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def test_the_auxiliary_model_writes_the_cards_title_and_description(config):
    from sinobee.agent import Runtime
    from sinobee.server import create_app
    writer = _Writer('{"title": "Rig 怎麼產生推力", "description": "按住閥門，氣體從噴嘴噴出，看推力怎麼來。"}')
    rt = Runtime(config, provider=writer)
    c = TestClient(create_app(rt))
    page(Path(rt.config.workspace))
    j = c.post("/api/share/suggest", json={"doc_path": "figures/rig/rig.html"}).json()
    assert j["title"] == "Rig 怎麼產生推力" and j["description"].startswith("按住閥門")
    assert "PAGE TEXT" in writer.prompts[0] and "氣體從噴嘴噴出" in writer.prompts[0]
    assert "fetch(" not in writer.prompts[0]                                  # scripts are not the page's text
    bad = _Writer("我不知道")
    c2 = TestClient(create_app(Runtime(config, provider=bad)))
    assert c2.post("/api/share/suggest", json={"doc_path": "figures/rig/rig.html"}).status_code == 502
