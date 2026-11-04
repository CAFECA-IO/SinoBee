"""scroll-world (MIT, by cyw / @oso95) ships as a SinoBee edition: spec-valid, credited, every reference the page
names exists, the author's references are unchanged, nothing that only Claude Code reads is shipped, and the
Skill Hub scan has nothing to flag."""
import py_compile
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sinobee.skills import spec

SK = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "scroll-world"
REFS = ["prompts.md", "pipeline.md", "scrub-engine.js", "index-template.html", "knockout.py", "upstream-SKILL.md"]


def test_valid_and_credited(config):
    from sinobee.agent import Runtime
    p = spec.parse((SK / "SKILL.md").read_text(encoding="utf-8"))
    assert not p.diagnostics and not spec.validate(p.frontmatter, "scroll-world")
    assert not spec.quality_warnings(p.frontmatter, p.body)
    fm, meta = p.frontmatter, p.frontmatter["metadata"]
    assert fm["license"] == "MIT" and "@oso95" in meta["author"]
    assert meta["sinobee-source"] == "https://github.com/oso95/scroll-world"
    assert meta["sinobee-upstream-commit"].startswith("71cc36d") and "71cc36d" in meta["version"]
    assert "allowed-tools" not in fm                       # upstream lists Claude Code tool names
    assert "Copyright (c) 2026 cyw" in (SK / "LICENSE").read_text()
    notice = (SK / "NOTICE.md").read_text(encoding="utf-8")
    assert "oso95/scroll-world" in notice and "71cc36d3bb150248ae36a2c552f9cbf88802a79c" in notice
    assert "Source and credits" in p.body and "出處與作者" in p.body
    sk = Runtime(config).skills.skills["scroll-world"]
    assert sk.trust == "builtin"


def test_the_page_names_only_existing_references_and_keeps_the_upstream_text():
    body = (SK / "SKILL.md").read_text(encoding="utf-8")
    for ref in set(re.findall(r"references/[\w.-]+", body)):
        assert (SK / ref).is_file(), ref
    for r in REFS:
        assert (SK / "references" / r).is_file(), r
    up = (SK / "references" / "upstream-SKILL.md").read_text(encoding="utf-8")
    assert up.startswith("<!-- Maintainer copy") and "\nname: scroll-world\n" in up and "## Gotchas (hard-won)" in up


def test_claude_code_only_parts_are_left_out():
    names = {p.name for p in SK.rglob("*")}
    assert not {".claude-plugin", "plugin.json", "marketplace.json"} & names
    body = (SK / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert "ask_user" in body and "request_network" in body and "request_secret" in body


def test_hub_scan_has_nothing_to_flag():
    """Same rules as SinoBee.ai src/lib/skills/scan.ts."""
    rules = [re.compile(r"ignore (all )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous) "
                        r"(prompt|instructions)", re.I),
             re.compile(r"(send|upload|post|exfiltrate)[^\n]{0,60}(api[_ -]?key|secrets?\.json|\.ssh|id_rsa|credentials)", re.I),
             re.compile(r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b", re.I),
             re.compile(r"[​-‏‪-‮⁠-⁤﻿](?!$)"),
             re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
             re.compile(r"(eval|exec)\s*\(\s*(base64|atob|bytes\.fromhex|codecs\.decode)", re.I)]
    text_ext = re.compile(r"\.(md|txt|py|sh|js|json|html|css|ya?ml)$")
    for f in SK.rglob("*"):
        if f.is_file() and (text_ext.search(f.name) or f.name == "SKILL.md"):
            t = f.read_text(encoding="utf-8", errors="ignore")
            for r in rules:
                assert not r.search(t), f"{f.relative_to(SK)}: {r.pattern[:40]}"


def test_the_scripts_parse(tmp_path):
    py_compile.compile(str(SK / "references" / "knockout.py"), cfile=str(tmp_path / "k.pyc"), doraise=True)
    if not shutil.which("node"):
        pytest.skip("no node")
    r = subprocess.run(["node", "--check", str(SK / "references" / "scrub-engine.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
