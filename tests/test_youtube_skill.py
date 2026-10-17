"""The bundled youtube-analysis skill: caption choice and parsing, transcript layout, cover text, report check."""
import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "youtube-analysis" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import categories  # noqa: E402
import check  # noqa: E402
import cover  # noqa: E402
import fetch  # noqa: E402


def test_sixteen_categories_with_a_check_level():
    assert len(categories.CATEGORIES) == 16
    assert {lvl for *_, lvl in categories.CATEGORIES.values()} == {"full", "claims", "none"}
    doc = (SCRIPTS.parent / "references" / "categories.md").read_text()
    for key, (zh, _, _) in categories.CATEGORIES.items():
        assert f"| {key} | {zh} |" in doc, key


def test_video_id_from_any_link():
    for url in ("https://www.youtube.com/watch?v=aircAruvnKk&t=10s", "https://youtu.be/aircAruvnKk", "https://www.youtube.com/shorts/aircAruvnKk", "aircAruvnKk"):
        assert fetch.video_id(url) == "aircAruvnKk"
    with pytest.raises(ValueError):
        fetch.video_id("https://example.com/x")


def test_caption_track_preference():
    info = {"language": "en-US", "subtitles": {"zh-TW": [], "en": []}, "automatic_captions": {"en-orig": [], "zh-TW": []}}
    assert fetch.pick_track(info, "zh-TW") == ("en", "manual")            # original language first
    info["subtitles"] = {"zh-TW": []}
    assert fetch.pick_track(info, "zh-TW") == ("zh-TW", "manual")         # then the reader's language, still human
    info["subtitles"] = {}
    assert fetch.pick_track(info, "zh-TW") == ("en-orig", "auto")         # then the original speech recognised
    assert fetch.pick_track({"automatic_captions": {"zh-TW": []}}, "zh-TW") == ("zh-TW", "auto-translated")
    assert fetch.pick_track({}, "zh-TW") is None


def test_json3_and_paragraphs_break_at_chapters():
    raw = json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "This is a 3."}]},
        {"tStartMs": 2000, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 3000, "dDurationMs": 2000, "segs": [{"utf8": "It is "}, {"utf8": "sloppy."}]},
        {"tStartMs": 70000, "dDurationMs": 2000, "segs": [{"utf8": "New chapter."}]},
    ]})
    segs = fetch.parse_json3(raw)
    assert [s["text"] for s in segs] == ["This is a 3.", "It is sloppy.", "New chapter."]
    paras = fetch.paragraphs(segs, [{"start": 0, "title": "a"}, {"start": 67, "title": "b"}])
    assert [p["start"] for p in paras] == [0, 70]
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c>hello</c>\n\n00:01:05.500 --> 00:01:06.000\nworld\n"
    assert [(s["start"], s["text"]) for s in fetch.parse_vtt(vtt)] == [(1.0, "hello"), (65.5, "world")]


def test_cover_wrap_keeps_punctuation_off_line_starts():
    lines = cover.wrap("神經網路到底是什麼？從手寫數字 3 看懂深度學習的第一課", 76, 700, 3)
    assert lines and not any(ln[0] in "，。？！、" for ln in lines)
    assert cover.wrap("x" * 400, 30, 300, 2)[-1].endswith("…")


def test_cover_svg_has_brand_category_and_title(tmp_path):
    (tmp_path / "source.json").write_text(json.dumps({"id": "aircAruvnKk", "channel": "3Blue1Brown", "duration": 1120, "date": "2017-10-05",
                                                      "view_count": 24842173, "chapters": [{"start": 67, "title": "x"}], "webpage_url": "u"}))
    (tmp_path / "cover.json").write_text(json.dumps({"title": "神經網路是什麼", "tagline": "t", "category": "science", "points": ["a"],
                                                     "verdict": {"label": "大致正確", "tone": "ok"}}))
    svg = cover.svg(tmp_path)
    assert "SinoBee.ai" in svg and "知識科普" in svg and "神經網路是什麼" in svg and "查核：大致正確" in svg and "2,484 萬" in svg
    (tmp_path / "cover.json").write_text(json.dumps({"title": "x", "category": "nope"}))
    with pytest.raises(ValueError):
        cover.svg(tmp_path)


GOOD = """# 標題

![封面](cover.png)

> 一句話摘要。

## 影片資訊
| 項目 | 內容 |
|---|---|
| 分類 | 知識科普 |

## 這部影片在說什麼
內容。{pad}
![圖](frames/t0001.jpg)

## 重點時間軸
- [00:01](https://www.youtube.com/watch?v=aircAruvnKk&t=1s) a
- [00:02](https://www.youtube.com/watch?v=aircAruvnKk&t=2s) b
- [00:03](https://www.youtube.com/watch?v=aircAruvnKk&t=3s) c

## 分類分析：知識科普
分析。

## 論述查核
| # | 時間 | 影片中的說法 | 判定 | 查核說明 | 來源 |
|---|---|---|---|---|---|
| 1 | a | b | 正確 | c | [x](https://a.example) |
| 2 | a | b | 錯誤 | c | [x](https://b.example) |
| 3 | a | b | 觀點 | c | — |

## 結論
好。

## 資料來源與方法
- 方法。
"""


def test_check_accepts_a_complete_report_and_lists_what_is_missing(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "t0001.jpg").write_bytes(b"x")
    (tmp_path / "cover.png").write_bytes(b"x")
    (tmp_path / "cover.json").write_text(json.dumps({"category": "science"}))
    (tmp_path / "report.md").write_text(GOOD.format(pad="字" * 1500))
    assert check.check(tmp_path)["status"] == "ok", check.check(tmp_path)["problems"]
    bad = GOOD.format(pad="字" * 1500).replace("| 2 | a | b | 錯誤 | c | [x](https://b.example) |", "| 2 | a | b | 錯誤 | c | 書上 |").replace("## 結論", "## 心得")
    (tmp_path / "report.md").write_text(bad)
    problems = " / ".join(check.check(tmp_path)["problems"])
    assert "## 結論" in problems and "no source link" in problems
    (tmp_path / "cover.json").write_text(json.dumps({"category": "music"}))   # 不查核的分類：標題要對
    assert "分類分析：音樂表演" in " ".join(check.check(tmp_path)["problems"])


def test_skill_is_bundled(config):
    from sinobee.agent import Runtime
    from tests.conftest import ScriptedProvider
    rt = Runtime(config, provider=ScriptedProvider())
    sk = rt.skills.skills["youtube-analysis"]
    assert sk.trust == "builtin" and sk.entry == "scripts/run.py"
    assert not [d for d in sk.diagnostics if "error" in str(d).lower()]
