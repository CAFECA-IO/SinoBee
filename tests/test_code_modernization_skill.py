"""code-modernization (Apache-2.0, by Anthropic) ships as a SinoBee edition: spec-valid, credited, every reference
and script the router names exists, nothing that needs Claude Code alone (Workflow scripts, hooks, telemetry) is
shipped, the Skill Hub scan has nothing to flag, and the upstream scripts' own unit tests pass."""
import json
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
        assert (SK / "agents" / f"{a}.md").is_file(), a
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
    assert not {"hooks", ".claude-plugin", "telemetry.py", "telemetry.sh", "media"} & names
    assert not list((SK / "workflows").glob("*.js"))                    # only the SinoBee ports (YAML)
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



# ---------------------------------------------------------------- phase 2: workflows and agent roles
WORKFLOWS = ["code-modernization-extract-rules", "code-modernization-harden-scan", "code-modernization-uplift-deltas"]


def test_workflows_and_roles_are_registered(config):
    from sinobee.agent import Runtime
    from sinobee.work.agents import skill_roles
    from sinobee.work.hub import get_hub
    rt = Runtime(config)
    wfs = get_hub(rt).workflows()
    for w in WORKFLOWS:
        assert wfs[w]["skill"] == "code-modernization" and wfs[w]["bundled"], w
    roles = skill_roles(rt)
    assert {f"code-modernization:{a}" for a in AGENTS} <= set(roles)
    for a in ("legacy-analyst", "business-rules-extractor", "security-auditor"):
        assert roles[f"code-modernization:{a}"]["deny"], a                # read-only roles cannot write files
    assert not roles["code-modernization:test-engineer"]["deny"]
    for f in (SK / "workflows").glob("*.yaml"):                            # every role a workflow names exists
        for role in re.findall(r"role: (\S+)", f.read_text(encoding="utf-8")):
            assert role in roles, (f.name, role)


LEGACY = {
    "batch/INTCALC.cob": "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. INTCALC.\n"
                         "       PROCEDURE DIVISION.\n           COMPUTE WS-INT = WS-BAL * WS-RATE / 1200.\n",
    "online/ACCTVAL.cob": "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. ACCTVAL.\n"
                          "       PROCEDURE DIVISION.\n           IF WS-ACCT-ID NOT NUMERIC MOVE 'N' TO WS-OK.\n",
}


def extract_handler(system, messages, tools):
    from tests.test_work import last_user
    task = last_user(messages)
    if "Mine business rules" in task:
        if "INTCALC" in task:
            return json.dumps({"rules": [
                {"name": "Monthly interest", "category": "Calculation", "priority": "P0",
                 "source": "batch/INTCALC.cob:4-4", "plainEnglish": "Interest is balance times rate over 1200.",
                 "given": "a balance and an annual rate", "when": "the monthly job runs",
                 "then": "interest = balance * rate / 1200", "confidence": "High"},
                {"name": "Comment-only rule", "category": "Policy", "priority": "P2",
                 "source": "batch/INTCALC.cob:1-1", "plainEnglish": "x", "given": "a", "when": "b", "then": "c",
                 "confidence": "Low", "smeQuestion": "?"}],
                "coveredAreas": ["batch/INTCALC.cob"], "injectionSuspects": []})
        return json.dumps({"rules": [
            {"name": "Account id must be numeric", "category": "Validation", "priority": "P1",
             "source": "online/ACCTVAL.cob:4-4", "plainEnglish": "A non-numeric account id is rejected.",
             "given": "an account id", "when": "it is validated", "then": "non-numeric ids are rejected",
             "confidence": "High"}], "coveredAreas": ["online/ACCTVAL.cob"]})
    if "refereeing one extracted business rule" in task:
        if "Comment-only rule" in task:
            return '{"verdict": "refuted", "reason": "only a comment", "injectionSuspected": false}'
        return '{"verdict": "confirmed", "reason": "the cited line computes it"}'
    if "Judge one P0-rated" in task:
        return '{"p0Justified": true, "faithful": true, "reason": "moves money"}'
    if "Catalog the core data" in task:
        return json.dumps({"dataObjects": [{"name": "ACCOUNT", "source": "batch/INTCALC.cob:1",
                                            "fields": [{"name": "WS-BAL", "type": "decimal"}],
                                            "consumedBy": ["Monthly interest"]}]})
    return "done"


async def test_extract_rules_workflow_end_to_end(config):
    """The ported workflow on a two-program legacy tree with a scripted model: shards, extraction, the citation
    referee drops a comment-only rule, the P0 panel runs, rules_result.json and BUSINESS_RULES.md are written."""
    import asyncio

    from sinobee.agent import Runtime
    from sinobee.work.hub import get_hub
    from tests.test_work import RoleModel
    config.data["permissions"]["toolsets"] = ["files", "shell", "web", "memory", "skills", "work"]
    config.data["permissions"]["toolsets_v"] = 2
    ws = config.workspace
    for rel, text in LEGACY.items():
        f = ws / "legacy" / "demo" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    (ws / "analysis" / "demo").mkdir(parents=True, exist_ok=True)
    rt = Runtime(config, provider=RoleModel(extract_handler))
    hub = get_hub(rt)
    run = hub.start_workflow("code-modernization-extract-rules", {"system": "demo"}, trigger="manual")
    await asyncio.wait_for(hub.tasks[run["id"]], 120)
    r = hub.store.run(run["id"])
    assert r["status"] == "done", r["error"]
    nodes = r["state"]["nodes"]
    assert nodes["confirm_size"]["status"] == "skipped"                   # 2 modules: no approval needed
    assert len(nodes["extract"]["output"]) == 2 and nodes["extract"]["invalid"] == 0
    result = json.loads((ws / "analysis" / "demo" / "rules_result.json").read_text(encoding="utf-8"))
    assert result["stats"]["confirmed"] == 2 and result["stats"]["rejected"] == 1 and result["stats"]["p0"] == 1
    assert result["dataObjects"][0]["name"] == "ACCOUNT"
    rules = (ws / "analysis" / "demo" / "BUSINESS_RULES.md").read_text(encoding="utf-8")
    assert "### RULE-001:" in rules and "Monthly interest" in rules and "Comment-only rule" not in rules


def test_scan_glue_harden_and_uplift(tmp_path, monkeypatch):
    """The harden / uplift bookkeeping between agent steps, on synthetic agent replies."""
    sys.path.insert(0, str(SK / "scripts"))
    import sinobee_scan as S
    monkeypatch.chdir(tmp_path)
    classes = S.harden_classes({"classes": "secrets, auth"})["classes"]
    assert [c["key"] for c in classes] == ["auth", "secrets"]
    f1 = {"cwe": "CWE-798", "severity": "High", "source": "a.php:3", "title": "key", "exploitScenario": "x",
          "recommendedFix": "y", "isCredential": True}
    f2 = {"cwe": "CWE-89", "severity": "Critical", "source": "b.php:9", "title": "sqli", "exploitScenario": "x",
          "recommendedFix": "y"}
    col = S.harden_collect({"rows": [{"item": classes[0], "status": "done", "data": {"findings": [f1, f2, f1]}},
                                     {"item": classes[1], "status": "failed"}]})
    assert len(col["findings"]) == 2 and col["deadFinders"] == ["secrets"]
    fold = S.harden_fold({"findings": col["findings"], "rows": [
        {"status": "done", "data": {"real": True, "reason": "r"}},
        {"status": "done", "data": {"real": False, "reason": "sanitized"}}]})
    assert len(fold["survivors"]) == 1 and len(fold["refuted"]) == 1 and len(fold["confirmItems"]) == 1
    out = S.harden_finish({"system": "demo", "classes": classes, "collect": col, "fold": fold, "confirmRows": [
        {"item": fold["confirmItems"][0], "status": "done", "data": {"real": False, "reason": "test only"}}]})
    res = json.loads((tmp_path / "analysis/demo/harden_result.json").read_text())
    assert out["findings"] == 1 and res["findings"][0]["severity"] == "Medium" and res["credentialFindings"]
    assert res["stats"]["falsePositiveRate"] == "50%"
    cats = S.uplift_categories({"source": "Java 8", "target": "Java 17"})["categories"]
    assert len(cats) == 4 and "Java 17" in cats[0]["brief"]
    d = {"name": "javax to jakarta", "category": "API-removed", "source_site": "A.java:1", "oldToNew": "javax->jakarta",
         "fixClass": "Mechanical", "confidence": "High", "siteCount": 59}
    col = S.uplift_collect({"rows": [{"status": "done", "data": {"deltas": [d, d]}}]})
    S.uplift_finish({"system": "demo", "source": "Java 8", "target": "Java 17", "collect": col,
                     "rows": [{"status": "done", "data": {"verdict": "confirmed", "reason": "used"}}]})
    up = json.loads((tmp_path / "analysis/demo/uplift_deltas.json").read_text())
    assert up["stats"]["totalTouchedSites"] == 59 and "/code-modernization transform" in up["upliftVsRewriteSignal"]
