"""code-modernization (Apache-2.0, by Anthropic) ships as a SinoBee edition: spec-valid, credited, every reference
and script the router names exists, nothing that needs Claude Code alone (Workflow scripts, hooks, telemetry) is
shipped, the Skill Hub scan has nothing to flag, and the upstream scripts' own unit tests pass."""
import re
import subprocess
import sys
from pathlib import Path

from sinobee.skills import spec

SK = Path(__file__).resolve().parent.parent / "sinobee" / "bundled_skills" / "code-modernization"
STEPS = ["start", "preflight", "assess", "map", "extract-rules", "review", "brief", "uplift", "transform",
         "reimagine", "verify", "harden", "status"]
AGENTS = ["legacy-analyst", "business-rules-extractor", "architecture-critic", "security-auditor",
          "version-delta-analyst", "test-engineer", "scaffolder", "uplift-migrator"]


def test_valid_and_credited(config):
    from sinobee.agent import Runtime
    p = spec.parse((SK / "SKILL.md").read_text(encoding="utf-8"))
    assert not p.diagnostics and not spec.validate(p.frontmatter, "code-modernization")
    assert not spec.quality_warnings(p.frontmatter, p.body)
    meta = p.frontmatter["metadata"]
    assert p.frontmatter["license"] == "Apache-2.0" and meta["author"] == "Anthropic"
    assert meta["sinobee-upstream-commit"].startswith("a2e251d")
    assert "Apache License" in (SK / "LICENSE").read_text()
    notice = " ".join((SK / "NOTICE.md").read_text().split())
    assert "modified" in notice and "not endorsed by Anthropic" in notice
    assert "Source and credits" in p.body
    sk = Runtime(config).skills.skills["code-modernization"]
    assert sk.trust == "builtin"


def test_router_names_only_existing_files():
    body = (SK / "SKILL.md").read_text(encoding="utf-8")
    for s in STEPS:
        assert (SK / "references" / f"{s}.md").is_file(), s
    for a in AGENTS:
        assert (SK / "references" / f"agent-{a}.md").is_file(), a
    for ref in set(re.findall(r"references/[\w.-]+\.md", body)):
        assert (SK / ref).is_file(), ref
    used = set()
    for f in (SK / "references").glob("*.md"):
        used |= set(re.findall(r"<skill>/(scripts/[\w.-]+\.py|assets/[\w./-]+)", f.read_text(encoding="utf-8")))
    assert used, "references call the scripts"
    for rel in used:
        assert (SK / rel.rstrip(".")).exists(), rel


def test_claude_code_only_parts_are_left_out():
    names = {p.name for p in SK.rglob("*")}
    assert not {"hooks", "workflows", ".claude-plugin", "telemetry.py", "telemetry.sh", "media"} & names
    for f in (SK / "references").glob("*.md"):
        t = f.read_text(encoding="utf-8")
        assert "CLAUDE_PLUGIN_ROOT" not in t and "/code-modernization:modernize" not in t and "AskUserQuestion" not in t


def test_hub_scan_has_nothing_to_flag():
    """Same patterns as SinoBee.ai src/lib/skills/scan.ts (prompt-injection, invisible characters, keys)."""
    rules = [re.compile(r"ignore (all )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous) "
                        r"(prompt|instructions)", re.IGNORECASE),
             re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"),
             re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----")]
    text_ext = re.compile(r"\.(md|txt|py|sh|js|json|html|css|ya?ml)$")
    for f in SK.rglob("*"):
        if f.is_file() and text_ext.search(f.name):
            t = f.read_text(encoding="utf-8", errors="ignore")
            for r in rules:
                assert not r.search(t), f"{f.relative_to(SK)}: {r.pattern[:40]}"


def test_report_names_the_sinobee_step_command():
    t = (SK / "scripts" / "build_report.py").read_text(encoding="utf-8")
    assert '"/code-modernization %s %s"' in t


def test_upstream_script_tests_pass():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], capture_output=True, text=True, timeout=600, check=False,
                       cwd=SK / "scripts")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]


def test_skill_run_runs_a_named_script_from_the_workspace(config):
    """The references call scripts as skill_run(script="scripts/X.py"); the report lands in the workspace."""
    import asyncio

    from sinobee.agent import Runtime
    from sinobee.tools import REGISTRY, ToolContext
    from sinobee.types import ToolCall
    rt = Runtime(config)
    ws = config.workspace
    (ws / "analysis" / "demo").mkdir(parents=True, exist_ok=True)
    (ws / "INTENT.md").write_text("# Intent\nGoal: understand\n", encoding="utf-8")
    (ws / "analysis" / "demo" / "PREFLIGHT.md").write_text("# Preflight: demo\nVerdict: ready\n", encoding="utf-8")
    ctx = ToolContext(config=config, skills=rt.skills)
    r = asyncio.run(REGISTRY.dispatch(ToolCall("1", "skill_run", {
        "name": "code-modernization", "script": "scripts/build_report.py", "args": "demo"}), ctx))
    assert not r.is_error, r.content
    html = (ws / "analysis" / "demo" / "REPORT.html").read_text(encoding="utf-8")
    assert re.search(r"/code-modernization [a-z-]+ demo", html) and "code-modernization:modernize" not in html
