"""A judge that thinks before it answers (gemini-*-pro) returned nothing with a few hundred tokens: every rubric
question failed, no trial qualified, and the creativity comparison had nothing to compare (bench-pilot, 2026-10-20).
Judges now get room to think and a second ask; a judge that still answers nothing is a judge error - never the
contestant's failure - and a run whose judge answers no anchor stops before its trials."""
import json

import pytest

from sinobee import evals as E
from sinobee.agent import Runtime
from sinobee.evals import graders, runner
from sinobee.evals import publish as P
from sinobee.evals.graders import Trial
from sinobee.types import ChatResponse, Message, Usage

from conftest import ScriptedProvider


class Judge:
    """Answers from a list of texts; records max_tokens and whether the reminder was added."""
    label = "gemini/gemini-pro-latest"

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    async def chat(self, messages, tools=None, **kw):
        self.calls.append({"max_tokens": kw.get("max_tokens"), "retry": "previous reply" in messages[-1].text()})
        return ChatResponse(Message("assistant", self.texts.pop(0) if self.texts else ""), Usage(10, 1))


async def test_an_empty_answer_is_asked_again_with_more_room():
    j = Judge(["", json.dumps({"pass": True, "reason": "ok", "evidence": "x"})])
    data = await graders.ask_json(j, "grade this", "pass")
    assert data["pass"] is True
    assert [c["retry"] for c in j.calls] == [False, True]
    assert j.calls[0]["max_tokens"] >= 2048 and j.calls[1]["max_tokens"] > j.calls[0]["max_tokens"]


async def test_a_judge_that_never_answers_is_a_judge_error_not_a_fail(tmp_path):
    t = Trial(prompt="p", workspace=tmp_path, answer="a", judge=Judge([]))
    r = await graders.grade_full({"type": "llm", "rubric": "r", "votes": 3}, t)
    assert r["ok"] is False and r["judge_error"] is True and "沒有給出判定" in r["detail"]
    # votes that were given decide; the missing ones are counted, not read as "no"
    yes = json.dumps({"pass": True, "reason": "fine", "evidence": "q"})
    t2 = Trial(prompt="p", workspace=tmp_path, answer="a", judge=Judge([yes, "", "", yes]))
    r2 = await graders.grade_full({"type": "llm", "rubric": "r", "votes": 3, "require_evidence": True}, t2)
    assert r2["ok"] is True and r2["votes"] == [2, 2] and r2["judge_missing"] == 1


async def test_a_run_stops_before_its_trials_when_the_judge_answers_no_anchor(home, config, tmp_path, monkeypatch):
    from test_bench import _suite
    started = []
    monkeypatch.setattr(runner, "child_runtime", lambda rt, cfg: started.append(1) or Runtime(cfg, provider=ScriptedProvider()))
    import sinobee.providers as providers
    monkeypatch.setattr(providers, "build_provider", lambda spec, cfg: Judge([]))
    rt = Runtime(config, provider=ScriptedProvider())
    with pytest.raises(E.EvalError, match="錨題都沒有給出判定"):
        await runner.run(rt, str(_suite(tmp_path)), model="gemini/gemini-flash-latest", judge="gemini/gemini-pro-latest")
    assert started == []
    doc = E.results(config.home, 1)[0]
    assert doc["status"] == "error"


def test_runs_with_judge_errors_are_not_published():
    run = {"id": "r1", "status": "done", "bench": {"kind": "model"}, "suite": "s", "suite_hash": "h", "judge": "j",
           "tasks": [{"id": "t", "trials": [{"grades": [{"ok": False, "judge_error": True}]}]}], "totals": {"judge_errors": 1}}
    with pytest.raises(P.PublishError, match="no verdict"):
        P.payloads([run])


async def test_thinking_models_get_room_in_short_calls(monkeypatch):
    from sinobee.providers.openai_compat import OpenAICompatProvider
    sent = []

    async def post(self, url, payload, headers):
        sent.append(payload.get("max_tokens") or payload.get("max_completion_tokens"))
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}
    monkeypatch.setattr(OpenAICompatProvider, "_post", post)
    for model in ("gemini-pro-latest", "gemini-3.8-flash", "gpt-4.1"):
        p = OpenAICompatProvider(model, "https://example.invalid/v1", "k", provider_name="gemini")
        await p._chat([Message("user", "hi")], None, temperature=0, max_tokens=300)
    assert sent == [2048, 2048, 300]


def test_a_tilde_word_is_not_a_home_directory(home, config):
    from sinobee.agent import artifacts
    from sinobee.fileaccess import FileAccessError, resolve_file
    seen = set()
    assert artifacts.collect(config, "shell", {"command": "echo ~250kg ~nosuchuser/x"}, "推力 ~250 kg，見 ~nosuchuser/a.md",
                             seen) == []
    with pytest.raises(FileAccessError):
        resolve_file(config, "~nosuchuser/report.md")
