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
