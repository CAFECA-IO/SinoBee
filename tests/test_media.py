"""Videos over the SinoBee.ai attachment limit are re-encoded to fit just before a share is uploaded (media.py)."""
import json
import shutil
import subprocess
import time

import httpx
import pytest

from sinobee import account, media, share

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make_clip(path, seconds=6, rate="6M"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-b:v", rate,
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_the_bitrate_plan_fits_the_size():
    v, a, h = media._plan(32, 25 * 1048576)
    assert (v + a) * 32 / 8 <= 25 * 1048576 * 0.93 and h == 1080
    assert media._plan(600, 25 * 1048576)[2] in (360, 480)          # ten minutes: low resolution
    with pytest.raises(media.MediaError, match="太長"):
        media._plan(3 * 3600, 25 * 1048576)


@needs_ffmpeg
def test_a_big_clip_is_shrunk_once_and_cached(config, tmp_path):
    src = make_clip(tmp_path / "big.mov")
    limit = src.stat().st_size // 3
    t = time.time()
    out = media.shrink_video(config, src, limit)
    assert out.suffix == ".mp4" and 0 < out.stat().st_size <= limit
    assert abs(media.duration(config, out) - 6) < 0.5
    first = time.time() - t
    t = time.time()
    assert media.shrink_video(config, src, limit) == out and time.time() - t < first / 2


def test_without_ffmpeg_or_the_sandbox_image_it_says_what_to_install(config, monkeypatch, tmp_path):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(media.MediaError, match="brew install ffmpeg"):
        media._runner(config)


@needs_ffmpeg
def test_a_shared_video_over_the_limit_is_compressed_before_upload(config, monkeypatch):
    ws = config.workspace
    (ws / "uploads").mkdir(parents=True, exist_ok=True)
    src = make_clip(ws / "uploads" / "Roadster.mov")
    limit = src.stat().st_size // 3
    monkeypatch.setattr(share, "MAX_FILE", limit)
    msgs = [{"role": "user", "content": f"分享到 SinoBee.ai ![Roadster.mov]({src})"},
            {"role": "assistant", "content": "好的，請按上方的分享按鈕。"}]
    b = share.build(config, msgs)
    f = b["files"][0]
    assert f["shrink"] and f["name"] == "Roadster.mp4" and f["mime"] == "video/mp4" and f["orig_size"] > limit
    assert b["messages"][0]["text"].endswith("![Roadster.mov](files/1)")         # your attachment travels with it
    assert b["title"] == "分享到 SinoBee.ai [Roadster.mov]"
    seen = {}

    def handler(req):
        raw = req.read()
        seen["size"] = len(raw)
        seen["names"] = __import__("re").findall(rb'name="files"; filename="([^"]+)"', raw)
        return httpx.Response(201, json={"id": "v1", "url": "http://hub/s/v1", "visibility": "LINK"})
    real = httpx.Client

    def request(method, url, **kw):
        kw.pop("timeout", None)
        with real(transport=httpx.MockTransport(handler)) as c:
            return c.request(method, url, **kw)
    monkeypatch.setattr(share.httpx, "request", request)
    account.save(config, {"access_token": "sbh_" + "q" * 30, "scope": "shares:write", "account": "0xabc"})
    share.upload(config, b)
    assert seen["names"] == [b"Roadster.mp4"] and seen["size"] < limit + 200_000


@pytest.mark.docker
def test_the_sandbox_image_compresses_when_this_computer_has_no_ffmpeg(config, monkeypatch, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg to make the test clip")
    src = make_clip(tmp_path / "clip.mp4", seconds=4)
    real = shutil.which
    monkeypatch.setattr(media.shutil, "which", lambda n: None if n in ("ffmpeg", "ffprobe") else real(n))
    assert media._runner(config)[0] == "docker"
    out = media.shrink_video(config, src, src.stat().st_size // 3)
    assert out.stat().st_size <= src.stat().st_size // 3


@needs_ffmpeg
def test_video_cards_get_a_poster_frame_and_length(config):
    from fastapi.testclient import TestClient

    from sinobee.agent import Runtime
    from sinobee.server import create_app
    (config.workspace / "out").mkdir(parents=True, exist_ok=True)
    clip = make_clip(config.workspace / "out" / "爆炸.mp4", seconds=4, rate="1M")
    jpg, meta = media.thumbnail(config, clip)
    assert jpg.read_bytes()[:2] == b"\xff\xd8" and round(meta["duration"]) == 4 and meta["width"] == 1280
    assert media.thumbnail(config, clip)[0] == jpg                  # cached
    c = TestClient(create_app(Runtime(config)))
    r = c.get("/api/files/thumb", params={"path": str(clip)})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    info = c.get("/api/files/info", params={"path": str(clip)}).json()
    assert round(info["duration"]) == 4 and info["height"] == 720
    (config.workspace / "out" / "a.txt").write_text("x")
    assert c.get("/api/files/thumb", params={"path": str(config.workspace / "out" / "a.txt")}).status_code == 415
