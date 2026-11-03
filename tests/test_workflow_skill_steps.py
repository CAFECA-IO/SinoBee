"""Workflow engine for skills with several steps (code-modernization phase 2): step workflows (sinobee-workflows),
skill-defined agent roles (sinobee-agents), script nodes, JSON output schemas with one retry, and large maps."""
from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from sinobee.agent import Runtime
from sinobee.types import Message
from sinobee.work.agents import WRITE_TOOLS, resolve_role, skill_roles, tool_filter_for
from sinobee.work.graph import WorkflowError, check_schema, validate
from sinobee.work.hub import get_hub
from tests.test_work import RoleModel, last_user

RULES_SCHEMA = {"type": "object", "required": ["rules"],
                "properties": {"rules": {"type": "array", "items": {"type": "object", "required": ["id", "text"]}}}}

WORKFLOW = """
name: steps-demo-mine
title: 規則萃取示範
inputs:
  modules: {type: text, label: modules}
nodes:
  - id: extract
    type: map
    role: steps-demo:extractor
    over: "{{inputs.modules}}"
    max_items: 50
    output: json
    schema: RULES_SCHEMA_JSON
    prompt: "Module {{item}}: list its rules."
  - id: dedupe
    type: script
    needs: [extract]
    script: scripts/dedupe.py
    input: "{{nodes.extract.output}}"
    args: "{{input_file}} {{output_file}}"
    output: json
    schema: {type: object, required: [rules, count], properties: {count: {type: integer}}}
  - id: judge
    type: agent
    needs: [dedupe]
    output: json
    schema: {type: object, required: [verdict], properties: {verdict: {type: string, enum: [ok, revise]}}}
    prompt: "JUDGE these {{nodes.dedupe.output.count}} rules: {{nodes.dedupe.output.rules}}"
""".replace("RULES_SCHEMA_JSON", json.dumps(RULES_SCHEMA))

DEDUPE = """
import json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))
seen = {}
for row in rows:
    for r in (row.get("data") or {}).get("rules", []):
        seen.setdefault(r["id"], r)
json.dump({"rules": list(seen.values()), "count": len(seen)}, open(sys.argv[2], "w", encoding="utf-8"))
print("deduped", len(seen))
"""


def make_skill(home):
    d = home / "skills" / "steps-demo"
    (d / "workflows").mkdir(parents=True)
    (d / "agents").mkdir()
    (d / "scripts").mkdir()
    (d / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: steps-demo
        description: Demo skill with step workflows and its own agent roles. Use when testing workflows.
        metadata:
          sinobee-workflows: steps-demo-mine, missing-one
          sinobee-agents: agents/*.md
        ---
        # Steps demo
        Run the mining step with run_workflow.
        """), encoding="utf-8")
    (d / "workflows" / "steps-demo-mine.yaml").write_text(WORKFLOW, encoding="utf-8")
    (d / "scripts" / "dedupe.py").write_text(DEDUPE, encoding="utf-8")
    (d / "agents" / "extractor.md").write_text("---\nname: extractor\nbase: analyst\nreadonly: true\n"
                                                "description: mines rules\n---\nPERSONA-EXTRACTOR: you mine rules.\n",
                                                encoding="utf-8")
    (d / "agents" / "writer2.md").write_text("---\nname: writer2\nbase: general\n---\nPERSONA-WRITER\n",
                                             encoding="utf-8")


def handler(system, messages, tools):
    last = last_user(messages)
    if "PERSONA-EXTRACTOR" in system:
        if "Module m1" in last:
            return '{"rules": [{"id": "R1", "text": "rate is 5%"}]}'
        if "Module m2" in last:
            return '```json\n{"rules": [{"id": "R1", "text": "rate is 5%"}, {"id": "R2", "text": "cap 100"}]}\n```'
        return "I could not find rules."                        # m3: no JSON -> invalid item
    if "JUDGE" in last:
        if "did not match the required JSON" in last:
            return '{"verdict": "ok"}'
        return '{"verdict": "maybe"}'                           # not in the enum -> one retry
    return "done"


@pytest.fixture
def steps_config(config):
    config.data["permissions"]["toolsets"] = ["files", "shell", "web", "memory", "skills", "work"]
    config.data["permissions"]["toolsets_v"] = 2
    make_skill(config.home)
    return config


def test_check_schema():
    assert check_schema({"rules": [{"id": "a", "text": "b"}]}, RULES_SCHEMA) == []
    assert "missing required field 'text'" in check_schema({"rules": [{"id": "a"}]}, RULES_SCHEMA)[0]
    assert check_schema(True, {"type": "integer"})            # a bool is not an integer
    assert check_schema(3, {"type": "number"}) == [] and check_schema("x", {"enum": ["y"]})
    assert check_schema([], {"type": "array", "minItems": 1})


def test_two_templates_in_one_string_render_as_text():
    from sinobee.work.graph import render
    assert render("{{a}} {{b}}", {"a": "x", "b": [1]}) == 'x [1]'
    assert render("{{b}}", {"b": [1]}) == [1]


def test_script_args_are_quoted_one_word_each():
    from sinobee.work.graph import shell_args
    assert shell_args("run {{a}} {{b}} --x", {"a": "x; rm -rf ~", "b": ""}) == "run 'x; rm -rf ~'  --x"
    assert shell_args("{{f}}", {"f": "/tmp/in.json"}) == "/tmp/in.json"


def test_validate_script_nodes():
    with pytest.raises(WorkflowError):
        validate({"name": "x-y", "nodes": [{"id": "a", "type": "script"}]})
    with pytest.raises(WorkflowError):
        validate({"name": "x-y", "nodes": [{"id": "a", "type": "agent", "schema": "nope"}]})


def test_skill_declares_step_workflows_and_roles(steps_config):
    rt = Runtime(steps_config, provider=RoleModel(handler))
    sk = rt.skills.skills["steps-demo"]
    assert [n for n, _ in sk.workflows] == ["steps-demo-mine"]          # a missing file is ignored
    assert "steps-demo-mine" in get_hub(rt).workflows()
    assert "Step workflows" in sk.quickstart and "steps-demo:extractor" in sk.quickstart
    roles = {k for k in skill_roles(rt) if k.startswith("steps-demo:")}
    assert roles == {"steps-demo:extractor", "steps-demo:writer2"}
    _name, spec = resolve_role(rt, "steps-demo:extractor")
    ok = tool_filter_for(spec)

    class T:
        def __init__(self, n):
            self.name = n
    assert ok(T("read_file")) and ok(T("shell")) and not any(ok(T(w)) for w in WRITE_TOOLS)
    assert not ok(T("delegate")) and not ok(T("web_search"))            # never more than the base role (analyst)
    assert resolve_role(rt, "nope:x")[0] == "general"


async def test_map_script_and_schema_retry(steps_config):
    rt = Runtime(steps_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    run = hub.start_workflow("steps-demo-mine", {"modules": ["m1", "m2", "m3"]}, trigger="manual")
    await asyncio.wait_for(hub.tasks[run["id"]], 60)
    r = hub.store.run(run["id"])
    assert r["status"] == "done", r["error"]
    nodes = r["state"]["nodes"]
    ex = nodes["extract"]
    assert ex["progress"] == {"done": 3, "total": 3} and ex["invalid"] == 1
    assert [row["status"] for row in ex["output"]] == ["done", "done", "invalid"]
    assert nodes["dedupe"]["output"]["count"] == 2                       # R1 from two modules counted once
    assert nodes["judge"]["output"] == {"verdict": "ok"} and len(nodes["judge"]["sessions"]) == 2


async def test_map_is_capped_and_says_so(steps_config):
    steps_config.data["work"]["max_map_items"] = 2
    rt = Runtime(steps_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    run = hub.start_workflow("steps-demo-mine", {"modules": ["m1", "m2", "m3"]}, trigger="manual")
    await asyncio.wait_for(hub.tasks[run["id"]], 60)
    ex = hub.store.run(run["id"])["state"]["nodes"]["extract"]
    assert ex["truncated"] == {"total": 3, "kept": 2} and len(ex["output"]) == 2


async def test_script_nodes_need_a_skill(steps_config):
    rt = Runtime(steps_config, provider=RoleModel(handler))
    hub = get_hub(rt)
    wf = validate({"name": "loose-script", "nodes": [{"id": "s", "type": "script", "script": "scripts/x.py"}]})
    hub._wf_cache = (9e18, {**hub.workflows(), "loose-script": wf})
    run = hub.start_workflow("loose-script", {}, trigger="manual")
    await asyncio.wait_for(hub.tasks[run["id"]], 30)
    r = hub.store.run(run["id"])
    assert r["status"] == "failed" and "技能附帶的流程" in r["error"]


async def test_delegate_rejects_unknown_roles(steps_config):
    from sinobee.tools import REGISTRY, ToolContext
    from sinobee.types import ToolCall
    rt = Runtime(steps_config, provider=RoleModel(lambda *a: Message("assistant", "ok")))
    ctx = ToolContext(config=steps_config, skills=rt.skills, state={"runtime": rt}, session_id="s1")
    r = await REGISTRY.dispatch(ToolCall("1", "delegate", {"tasks": [{"role": "hacker", "task": "x"}]}), ctx)
    assert "unknown role" in r.content and "steps-demo:extractor" in r.content
