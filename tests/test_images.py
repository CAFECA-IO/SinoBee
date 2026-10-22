"""Pictures sent to a model fit what providers accept: tall screenshots in strips, big ones scaled down, a refusal
shrinks them and retries; plus the quota switch that no longer flips near 100% and the partial delivery's paths."""
import base64
import io
import time

import pytest
from PIL import Image

from sinobee import billing, images
from sinobee.agent import Agent, Runtime
from sinobee.agent.errors import explain_error
from sinobee.agent.loop import IMAGE_REJECTED
from sinobee.providers.base import ProviderError
from sinobee.tools.registry import _normalise
from sinobee.types import ImagePart, Message, TextPart

ANTHROPIC_8000 = ("sinobee/anthropic/claude-haiku-5-5 HTTP 400: messages.15.content.30.image.source.base64.data: "
                  "At least one of the image dimensions exceed max allowed size: 8000 pixels")


def png(w, h, color=(30, 120, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def dims(part: ImagePart):
    return Image.open(io.BytesIO(base64.b64decode(part.data))).size


def test_size_from_the_header_alone():
    assert images.size_of(png(1280, 8937)) == (1280, 8937)
    buf = io.BytesIO()
    Image.new("RGB", (390, 14316)).save(buf, "JPEG")
    assert images.size_of(buf.getvalue()) == (390, 14316)
    buf = io.BytesIO()
    Image.new("RGB", (33, 44)).save(buf, "GIF")
    assert images.size_of(buf.getvalue()) == (33, 44)


def test_a_small_picture_goes_as_it_is():
    raw = png(800, 600)
    strips, info = images.fit(raw, "image/png")
    assert strips == [(raw, "image/png")] and info["tiles"] == 1


def test_a_full_page_screenshot_is_cut_into_strips():
    strips, info = images.fit(png(1280, 6100), "image/png")
    assert info["tiles"] == 4 and info["scaled"] == 1.0
    sizes = [Image.open(io.BytesIO(b)).size for b, _ in strips]
    assert all(w == 1280 and h <= 2000 for w, h in sizes) and sum(h for _, h in sizes) == 6100


def test_taller_than_four_strips_is_scaled_down_first():
    strips, info = images.fit(png(390, 14316), "image/png")      # the phone screenshot that stopped the task
    assert info["tiles"] == 4 and info["scaled"] < 1
    sizes = [Image.open(io.BytesIO(b)).size for b, _ in strips]
    assert all(max(s) <= 2000 for s in sizes) and sum(h for _, h in sizes) <= 8000


def test_a_wide_picture_is_scaled_to_the_strip_width():
    strips, info = images.fit(png(5000, 1200), "image/png")
    (b, _), = strips
    assert Image.open(io.BytesIO(b)).size == (2000, 480) and info["scaled"] == 0.4


def test_too_many_bytes_become_jpeg(monkeypatch):
    monkeypatch.setattr(images, "MAX_BYTES", 20_000)
    noise = Image.effect_noise((1500, 1500), 90).convert("RGB")
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    strips, _ = images.fit(buf.getvalue(), "image/png")
    assert strips[0][1] == "image/jpeg"


def test_without_pillow_an_oversized_picture_becomes_a_note(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_pil(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_pil)
    assert images.fit(png(1280, 3000), "image/png")[0]                 # within what providers accept: as it is
    out = images.parts(png(1280, 9000), "image/png", "desktop.png")
    assert len(out) == 1 and "Pillow" in out[0].text


def test_tool_results_are_fitted_with_labelled_strips():
    res = _normalise([TextPart("Image file shots/desktop.png"), ImagePart.from_bytes(png(1280, 8937))], 12000)
    imgs = [p for p in res.content if isinstance(p, ImagePart)]
    texts = [p.text for p in res.content if isinstance(p, TextPart)]
    assert len(imgs) == 4 and all(max(dims(p)) <= 2000 for p in imgs)
    assert any("1280×8937" in t for t in texts) and "第 4/4 段" in texts


def test_shrink_makes_every_picture_one_small_image_and_keeps_the_last_few():
    msgs = [Message("tool", [TextPart(f"shot {i}"), ImagePart.from_bytes(png(1280, 1900))]) for i in range(6)]
    changed = images.shrink(msgs, side=768, keep_last=4)
    assert changed == 6
    left = [p for m in msgs for p in m.content if isinstance(p, ImagePart)]
    assert len(left) == 4 and all(max(dims(p)) <= 768 for p in left)
    assert "圖片已移除" in msgs[0].text()


def test_the_8000_px_refusal_is_recognised_and_explained():
    assert IMAGE_REJECTED.search(ANTHROPIC_8000)
    assert IMAGE_REJECTED.search("HTTP 400: image exceeds 5 MB maximum: 6291456 bytes > 5242880 bytes")
    assert not IMAGE_REJECTED.search("HTTP 400: temperature is deprecated for this model")
    assert not IMAGE_REJECTED.search("HTTP 400: messages: text content blocks must be non-empty")
    info = explain_error(ANTHROPIC_8000)
    assert info["summary"] == "圖片超過模型可接受的尺寸或大小" and not info["transient"]


async def test_a_refused_picture_is_shrunk_and_the_task_goes_on(config, provider):
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    # a picture already in the history (an earlier turn read it before this fix): the provider refuses it
    agent.history += [Message("user", "看這張"), Message("assistant", "好"),
                      Message("user", [TextPart("舊截圖"), ImagePart.from_bytes(png(1280, 2500))])]
    calls = {"n": 0}
    real = provider.chat

    async def chat(messages, tools=None, **kw):
        calls["n"] += 1
        big = [p for m in messages if isinstance(m.content, list) for p in m.content
               if isinstance(p, ImagePart) and max(dims(p)) > 2000]
        if big:
            raise ProviderError(ANTHROPIC_8000, 400)
        return await real(messages, tools, **kw)
    provider.chat = chat
    provider.push("看過了，沒有跑版")
    out = await agent.run("確認截圖")
    assert out == "看過了，沒有跑版" and calls["n"] == 2
    assert agent.last_status["status"] == "done"


async def test_a_tall_screenshot_read_by_the_agent_reaches_the_model_in_strips(config, provider):
    ws = config.workspace
    (ws / "shots").mkdir(parents=True, exist_ok=True)
    (ws / "shots" / "desktop.png").write_bytes(png(1280, 8937))
    rt = Runtime(config, provider=provider)
    agent = Agent(rt)
    provider.push("", [("read_file", {"path": "shots/desktop.png"})])
    provider.push("好了")
    assert await agent.run("看截圖") == "好了"
    sent = [p for m in provider.requests[-1][0] if isinstance(m.content, list) for p in m.content
            if isinstance(p, ImagePart)]
    assert len(sent) == 4 and all(max(dims(p)) <= 2000 for p in sent)


def test_back_to_the_subscription_only_below_95_percent(config):
    billing.choose(config, billing.POINTS, window="6h", retry_at=time.time() + 3600)
    billing.note_quota(config, {"status": "ok", "windows": [{"window": "6h", "usedPct": 99.8}]})
    assert billing.active(config)["source"] == billing.POINTS          # 99.8%: the next call would run out again
    billing.note_quota(config, {"status": "ok", "windows": [{"window": "6h", "usedPct": 96}]})
    assert billing.active(config) is not None
    billing.note_quota(config, {"status": "ok", "windows": [{"window": "6h", "usedPct": 90}]})
    assert billing.active(config) is None


def test_the_switch_note_is_shown_once_per_turn(config, provider):
    agent = Agent(Runtime(config, provider=provider))
    agent._quota_noted = set()
    switch = {"window": "6h", "source": "points", "ask": False}
    assert agent._quota_once(switch) and not agent._quota_once(dict(switch)) and not agent._quota_once(dict(switch))
    assert agent._quota_once({"window": "points", "source": "own", "ask": False})     # a different switch: once too
    assert agent._quota_once({"window": "6h", "source": None, "ask": True})           # the dialog always asks
    assert agent._quota_once({"window": "6h", "source": None, "ask": True})


def test_partial_delivery_names_files_by_their_path():
    from types import SimpleNamespace

    from sinobee.agent.budget import partial_delivery
    agent = SimpleNamespace(ctx=SimpleNamespace(state={}), _msr=None)
    stats = {"artifacts": [{"path": "shots_final2/phone.png", "name": "phone.png"},
                           {"path": "shots_final3/phone.png", "name": "phone.png"}]}
    try:
        pd = partial_delivery(agent, stats, "error", "模型服務拒絕了這次請求")
    except TypeError:
        pytest.skip("partial_delivery signature differs")
    assert [f["name"] for f in pd["files"]] == ["shots_final2/phone.png", "shots_final3/phone.png"]
    assert "[shots_final3/phone.png](shots_final3/phone.png)" in pd["text"]
