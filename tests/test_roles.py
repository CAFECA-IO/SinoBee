"""The thinking model as the elephant (agent/roles.py): fitness, the plan's output files, consults with a brief."""
import json

from sinobee.agent import Agent, Runtime
from sinobee.agent import roles
from sinobee.types import ChatResponse, Message, ToolCall, Usage


class Local:
    """A local model: answers side calls (plan, consult) with `side`, steps with `steps`."""
    label = "ollama/gemma4:e4b"
    provider_name = "ollama"
    model = "gemma4:e4b"

    def __init__(self, side=None, steps=None):
        self.side, self.steps = list(side or []), list(steps or [])
        self.side_requests, self.recorder = [], None

    async def chat(self, messages, tools=None, **kw):
        if tools is None:
            self.side_requests.append(messages)
            text = self.side.pop(0) if self.side else "{}"
            return ChatResponse(Message("assistant", text), Usage(10, 5), "stop")
        step = self.steps.pop(0) if self.steps else "完成"
        if isinstance(step, tuple):
            return ChatResponse(Message("assistant", "", [ToolCall("c1", step[0], step[1])]), Usage(10, 5), "tool_calls")
        return ChatResponse(Message("assistant", step), Usage(10, 5), "stop")

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


def _local(config):
    config.data["model"]["default"] = "ollama/gemma4:e4b"
    config.data["local"]["num_ctx"] = 65536
    return config


def test_the_execution_models_own_local_model_is_a_weak_elephant(config):
    fit = roles.thinking_fitness(Runtime(_local(config), provider=Local()))
    assert fit["weak"] and "沿用本地執行模型" in fit["reasons"][0]


def test_a_cloud_thinking_model_with_a_large_window_is_fit(config, provider):
    rt = Runtime(config, provider=provider)
    rt.thinking = lambda: type("T", (), {"label": "anthropic/claude-sonnet-4-6"})()
    fit = roles.thinking_fitness(rt)
    assert not fit["weak"] and fit["window"] >= 32768


def test_a_small_window_is_weak(config, provider):
    config.data["agent"]["thinking_min_window"] = 10 ** 7
    assert roles.thinking_fitness(Runtime(config, provider=provider))["weak"]


def test_every_output_gets_a_file_and_every_milestone_an_outcome():
    plan = {"milestones": {"milestones": [
        {"id": "m1", "title": "Research", "goal": "list the parts", "outputs": ["SMR 技術事實表 (Text)"],
         "acceptance": ["5+ parts"]},
        {"id": "m2", "title": "Diagram", "goal": "isometric diagram", "outputs": ["Isometric SVG Diagram"],
         "acceptance": []},
        {"id": "m3", "title": "Report", "goal": "write it", "outputs": ["report saved as final/report.md"],
         "acceptance": ["done"]}]}}
    assert roles.concretize_outputs(plan) == 2
    m1, m2, m3 = plan["milestones"]["milestones"]
    assert m1["output_files"] == ["m1-1.md"] and "save as m1-1.md" in m1["outputs"][0]
    assert m2["output_files"] == ["m2-1.svg"] and m2["acceptance"] == ["m2-1.svg written"]
    assert m3["output_files"] == ["final/report.md"] and m3["outputs"] == ["report saved as final/report.md"]


PLAN = json.dumps({
    "understanding": "two parts", "approach": "research, then write", "steps": ["research", "write"],
    "task_type": "research", "checklist": [],
    "milestones": [{"id": "m1", "title": "Research", "goal": "collect the parts", "outputs": ["parts list"],
                    "acceptance": ["5 parts listed"]},
                   {"id": "m2", "title": "Report", "goal": "write the report", "depends_on": ["m1"],
                    "outputs": ["final report"], "acceptance": ["report covers both parts"]}]})


async def test_a_weak_elephants_plan_gets_output_files_and_a_check(config):
    _local(config)
    config.data["agent"]["plan"] = "always"
    prov = Local(side=[PLAN])
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("研究釷熔鹽堆的關鍵零部件並寫成報告")
    from sinobee.tools import checklist_tools
    items = checklist_tools.get(agent.ctx)
    m1 = [i for i in items if i.get("milestone") == "m1"]
    assert m1 and "m1-1.md" in m1[0]["check"]


async def test_a_weak_elephant_is_consulted_with_a_brief(config):
    _local(config)
    prov = Local(side=["先把內容寫進檔案\nDECISION: continue"])
    agent = Agent(Runtime(config, provider=prov))
    agent._fitness = {"weak": True, "reasons": ["x"], "window": 65536}
    agent.history = [Message("user", "做報告"), Message("assistant", "", [ToolCall("c9", "read_file", {"path": "a"})]),
                     Message("tool", "ERROR: not found", tool_call_id="c9")]
    stats = {"query": "做報告", "step": 3}
    await agent._consult("工具連續失敗", "<context/>", stats)
    sent = prov.side_requests[-1]
    assert len(sent) == 1 and "RECENT STEPS" in sent[0].text() and "read_file" in sent[0].text()
    assert stats["consult_brief"] == 1


async def test_a_local_model_runs_independent_milestones_one_after_another(config):
    """Sub-agents in parallel would only queue for the same local model: the plan runs in order instead."""
    _local(config)
    config.data["agent"]["plan"] = "always"
    plan = json.loads(PLAN)
    plan["milestones"][1]["depends_on"] = []
    plan["milestones"][0]["parallel"] = True        # independent research: would go to a sub-agent
    plan["milestones"].append({"id": "m3", "title": "Final", "goal": "combine", "depends_on": ["m1", "m2"],
                               "outputs": ["report"], "acceptance": ["combined"]})
    prov = Local(side=[json.dumps(plan)])
    agent = Agent(Runtime(config, provider=prov))
    await agent.run("研究釷熔鹽堆的關鍵零部件並寫成報告")
    ms = agent._msr.plan.milestones
    assert not any(m["parallel"] for m in ms) and not any(m["status"] == "delegated" for m in ms)
