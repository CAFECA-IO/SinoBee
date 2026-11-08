"""taiwan-honest-human: simplified -> traditional by phrase where one decides, mainland terms replaced only when
unambiguous, Taiwan text left alone, code and URLs untouched, style and review items reported, not changed."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SK = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "taiwan-honest-human"


def run(*args):
    r = subprocess.run([sys.executable, "-I", str(SK / "scripts" / "run.py"), *map(str, args)],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, json.loads(r.stdout)


def test_skill_is_valid_and_loads_as_builtin(config):
    from sinobee.agent import Runtime
    from sinobee.skills import spec
    p = spec.parse((SK / "SKILL.md").read_text(encoding="utf-8"))
    assert not p.diagnostics and not spec.validate(p.frontmatter, "taiwan-honest-human")
    assert not spec.quality_warnings(p.frontmatter, p.body)
    sk = Runtime(config).skills.skills["taiwan-honest-human"]
    assert sk.trust == "builtin" and sk.entry == "scripts/run.py"
    assert (SK / "scripts/data/OPENCC-LICENSE").read_text().startswith("Apache License")


def test_fix_converts_mainland_text(tmp_path):
    src = tmp_path / "cn.md"
    src.write_text('他的头发很长，走了三公里，后来在台风里迷路了。我们的软件运行在服务器上,'
                   '视频会议需要人工智能.\n\n```py\nprint("软件")\n```\n见 https://example.com/软件\n', encoding="utf-8")
    code, res = run("fix", src, tmp_path / "tw.md")
    text = (tmp_path / "tw.md").read_text(encoding="utf-8")
    assert "頭髮" in text and "三公里" in text and "後來" in text and "颱風" in text
    assert "軟體" in text and "伺服器" in text and "視訊會議" in text and "人工智慧" in text
    assert "伺服器上，" in text and "人工智慧。" in text
    assert 'print("软件")' in text and "https://example.com/软件" in text      # code and URLs untouched
    assert res["fixed"]["mainland_source"] is True
    assert {g["char"] for g in res["fixed"]["multi_form_guesses"]} >= {"里"}       # 颱風裡: a guess, flagged
    assert res["still_to_review"]["counts"]["simplified"] == 0
    assert any(r["term"] == "運行" for r in res["still_to_review"]["review"])       # V: reported, not changed
    assert "運行" in text


def test_taiwan_text_is_left_alone(tmp_path):
    src = tmp_path / "tw.md"
    src.write_text("我們走了三公里，台灣的里長說這裡有一隻狗。比特幣價格每天變動。\n", encoding="utf-8")
    code, res = run("check", src)
    assert res["mainland_source"] is False and res["needs_review"] is False
    c = res["counts"]
    assert c["simplified"] == c["multi"] == c["replace"] == c["punctuation"] == 0
    code, res = run("fix", src, tmp_path / "out.md")
    assert (tmp_path / "out.md").read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_style_and_terms_lookup(tmp_path):
    src = tmp_path / "s.md"
    src.write_text("值得注意的是，隨著科技的快速發展，我們致力於打造一站式平台。\n", encoding="utf-8")
    code, res = run("check", src)
    assert code == 0 and res["needs_review"] and {s["text"] for s in res["style"]} >= {"值得注意的是", "打造", "一站式"}
    code, res = run("terms", "软件")
    assert code == 0 and res["matches"][0]["to"] == "軟體"


def test_fix_refuses_to_overwrite(tmp_path):
    src = tmp_path / "a.md"
    src.write_text("软件", encoding="utf-8")
    code, res = run("fix", src, src)
    assert code == 2 and src.read_text(encoding="utf-8") == "软件"
