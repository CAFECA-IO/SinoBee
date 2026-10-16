"""The SinoBee agent loop. One class serves every entry point (CLI, Web UI, API)."""
from __future__ import annotations

import asyncio
import logging
import json
import re
import uuid
from typing import Any, Awaitable, Callable

import time

from .. import local
from .. import tools as _tools  # noqa: F401  (registers built-in tools)
from ..accounting import current_scope, scope
from ..pricing import cost_usd, price_for
from ..config import Config
from ..memory.store import MemoryStore
from ..providers import build_auxiliary, build_chain, build_embedder
from ..skills import manager_from_config
from ..tools.registry import REGISTRY, ToolContext
from . import artifacts
from ..types import ImagePart, Message, TextPart, ToolCall, ToolResult, Usage
from .context import OVERFLOW_RE, compact, context_budget, context_window, estimate_tokens, prune_images, with_summary
from .prompt import build_context_block, build_system_prompt

log = logging.getLogger("sinobee.agent")

EmitFn = Callable[[str, dict], Any]
ApproveFn = Callable[[str, dict, str], Awaitable[bool]]

SKILL_REQUEST_RE = re.compile(r"(記住(這個|這些)?(做法|流程|步驟|方法)|做成技能|存成技能|學起來|學會這個|"
                              r"remember (this|how)|save (this|it) as a skill|make (this|it) a skill)", re.I)
SKILL_ARG_TOOLS = ("skill_view", "skill_feedback", "skill_fork", "skill_patch", "skill_write_file", "skill_run",
                   "skill_rollback")
LEARN_TEMPLATE = """[/learn] Create or improve an Agent Skill from the following source:
{arg}

Do this:
1. skill_view("skill-authoring") and follow it.
2. Gather the real material: web_fetch / browser for URLs, read_file / list_dir for paths, or this conversation and
   past sessions (session_search) when the source is a description of something we did.
3. skill_search for overlapping skills; prefer skill_patch / skill_fork over a near-duplicate.
4. skill_create with a spec-compliant SKILL.md (choose scope=project if it only applies to this project). Put long
   material in references/ and helpers in scripts/ with skill_write_file.
5. Reply with the skill name, scope and a 2-line summary of when it will be used."""

REASONING_MODEL_RE = re.compile(r"(^|/)(o\d|gpt-5|gemini-(2\.5|3)|.*thinking|.*reasoner|deepseek-r|qwq|.*-r1)", re.I)
EMPTY_NUDGE = ("[SinoBee notice] Your previous reply was empty. Continue the task now: call the next tool you need, "
               "or - if you already have enough information - write the complete answer for the user in their "
               "language. Do not reply with an empty message.")

CORRECTION_RE = re.compile(r"(不是|不對|錯了|應該是|不要再|別再|你忘了|我說過|wrong|that's not|incorrect|"
                           r"you should have|don't do that|i told you|actually,? )", re.I)


class _Unconfigured:
    """Placeholder used when the model is not configured yet."""

    def __init__(self, reason: str):
        self.reason = reason
        self.label = "(not configured)"
        self.providers: list = []

    async def chat(self, *a, **kw):
        from ..providers import ProviderError
        raise ProviderError(f"model not configured: {self.reason}. Open Settings in the Web UI, "
                            "or start with -e SINOBEE_MODEL=provider/model -e <PROVIDER>_API_KEY=...")

    async def embed(self, texts):
        raise RuntimeError("not configured")


class Runtime:
    """Shared, long-lived resources (one per process)."""

    def __init__(self, config: Config | None = None, *, provider=None, aux_provider=None, embedder=None,
                 strong_provider=None):
        self.config = config or Config.load()
        self._strong = strong_provider
        self._strong_spec = None
        self._fixed_provider = provider is not None
        self.provider_error: str | None = None
        if provider is not None:
            self.provider, self.aux, emb = provider, aux_provider or provider, embedder
        else:
            self.provider, self.aux, emb = self._build_models()
        self.memory = MemoryStore(self.config.db_path,
                                  core_char_limit=int(self.config.get("memory.core_char_limit", 1500)),
                                  half_life_days=float(self.config.get("memory.half_life_days", 30)),
                                  short_ttl_days=float(self.config.get("memory.short_ttl_days", 14)),
                                  embedder=emb)
        self.skills = manager_from_config(self.config, self.memory)
        self.background: set[asyncio.Task] = set()
        self._wire_recorders(emb)

    # ------------------------------------------------------------------ cost accounting
    def _wire_recorders(self, emb=None) -> None:
        for obj in (self.provider, self.aux, emb):
            if obj is not None:
                try:
                    obj.recorder = self._record
                except AttributeError:
                    pass

    def _record(self, provider, usage, purpose: str | None = None) -> None:
        sc = current_scope.get()
        prov = getattr(provider, "provider_name", "unknown")
        model = getattr(provider, "model", "unknown")
        price, source = price_for(prov, model, self.config.get("pricing") or {})
        cost = cost_usd(price, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
                        usage.cache_write_tokens)
        self.memory.record_usage(session_id=sc.session_id, purpose=purpose or sc.purpose, provider=prov, model=model,
                                 input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                                 cache_read_tokens=usage.cache_read_tokens,
                                 cache_write_tokens=usage.cache_write_tokens, cost_usd=cost, price_source=source)
        sc.calls += 1
        sc.input_tokens += usage.input_tokens
        sc.output_tokens += usage.output_tokens
        sc.cache_read_tokens += usage.cache_read_tokens
        if cost is None:
            sc.unpriced = True
        else:
            sc.cost_usd += cost

    @staticmethod
    def period_start(period: str) -> float:
        now = time.time()
        lt = time.localtime(now)
        midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return {"today": midnight,
                "month": time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1)),
                "7d": now - 7 * 86400, "30d": now - 30 * 86400, "all": 0}.get(period, 0)

    def budget_status(self) -> dict:
        out = {"exceeded": False, "warn": False, "enforce": bool(self.config.get("usage.enforce_budget", False))}
        for name, period in (("daily", "today"), ("monthly", "month")):
            limit = self.config.get(f"usage.{name}_budget_usd")
            spent = self.memory.usage_totals(self.period_start(period))["cost_usd"]
            item = {"limit": limit, "spent": round(spent, 4)}
            if limit:
                item["pct"] = round(100 * spent / float(limit), 1)
                out["exceeded"] |= spent >= float(limit)
                out["warn"] |= spent >= 0.8 * float(limit)
            out[name] = item
        return out

    def strong(self):
        """The stronger model to escalate to (model.escalate), or None when none is configured / it is the same."""
        if self._strong is not None and (self._fixed_provider or self._strong_spec is None):
            return self._strong
        spec = self.config.get("model.escalate")
        if not spec or spec == self.config.get("model.default"):
            return None
        if self._strong_spec != spec:
            try:
                self._strong = build_chain(self.config, spec)
                self._strong.recorder = self._record
                self._strong_spec = spec
            except Exception as e:  # noqa: BLE001
                log.warning("escalation model %s unavailable: %s", spec, e)
                return None
        return self._strong

    def _build_models(self):
        """Build provider chain; never crash startup - an unconfigured model surfaces in the UI instead."""
        try:
            chain = build_chain(self.config)
            aux = build_auxiliary(self.config)
            emb = build_embedder(self.config)
            self.provider_error = None
            return chain, aux, emb
        except Exception as e:
            self.provider_error = str(e)
            broken = _Unconfigured(str(e))
            return broken, broken, None

    def reload(self) -> None:
        """Apply new settings (GUI save / key change) without restarting."""
        self.config.reload()
        if not self._fixed_provider:
            self.provider, self.aux, emb = self._build_models()
            self._strong, self._strong_spec = None, None      # rebuilt on demand with the new settings
            self.memory.embedder = emb
            self._wire_recorders(emb)
        self.memory.core_char_limit = int(self.config.get("memory.core_char_limit", 1500))
        self.skills.write_approval = self._skill_write_approval()
        self.skills.disabled = set(self.config.get("skills.disabled") or [])
        self.skills.interop = bool(self.config.get("skills.interop", True))
        new_project = self.config.project_dir.expanduser().resolve()
        if self.skills.project_dir != new_project:
            self.skills.project_dir = new_project
        self.skills.reload()

    def _skill_write_approval(self) -> bool:
        return bool(self.config.get("skills.write_approval", False)) or \
            str(self.config.get("skills.auto_create", "auto")).lower() == "suggest"

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.background.add(t)
        t.add_done_callback(self.background.discard)
        return t

    async def drain(self, timeout: float = 120) -> None:
        if self.background:
            await asyncio.wait(list(self.background), timeout=timeout)


def _title(text: str) -> str:
    """Session title from the first request: drop markdown image/link targets and collapse whitespace."""
    import re as _re
    t = _re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[\1]", text)
    t = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = " ".join(t.split())
    return t[:80] or "(附件)"


class Agent:
    def __init__(self, runtime: Runtime, session_id: str | None = None, *, platform: str = "cli",
                 emit: EmitFn | None = None, approve: ApproveFn | None = None, tool_filter=None,
                 system_extra: str = "", max_iterations: int | None = None, board_scope: str | None = None,
                 title: str | None = None, learn: bool = True, depth: int = 0):
        """tool_filter(tool) -> bool narrows the tools (sub-agents / workflow roles); board_scope is the shared
        blackboard this agent reads and writes (default: its own session); depth > 0 marks a sub-agent."""
        self.rt = runtime
        self.config = runtime.config
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.platform = platform
        self.emit_fn = emit
        self.usage = Usage()
        self._cancel = False
        self._inbox: list[str] = getattr(self, "_inbox", [])
        self.depth = depth
        self.learn = learn
        self.max_iterations = max_iterations
        self.tool_filter = tool_filter
        tools = REGISTRY.available(self.config)
        if tool_filter is not None:
            tools = [t for t in tools if tool_filter(t)]
        self.tools = tools
        self.tool_names = {t.name for t in self.tools}
        self.ctx = ToolContext(config=self.config, session_id=self.session_id, memory=runtime.memory,
                               skills=runtime.skills, emit=self._emit, approve=approve)
        self.ctx.state["runtime"] = runtime
        self.ctx.state["board_scope"] = board_scope or f"session:{self.session_id}"
        self.ctx.state["depth"] = depth
        self.ctx.state["agent"] = self
        runtime.memory.ensure_session(self.session_id, platform, title=title)
        self.history: list[Message] = self._load_history()
        self._core_at_start = {m.id: m.content for m in runtime.memory.core()}
        system = build_system_prompt(self.config, runtime.memory.render_core(), sorted(self.tool_names))
        if system_extra:
            system += "\n\n" + system_extra
        self.system = Message("system", system)
        self._persisted = len(self.history)

    # ------------------------------------------------------------------ utils
    def _emit(self, kind: str, data: dict) -> None:
        if self.emit_fn:
            try:
                res = self.emit_fn(kind, data)
                if asyncio.iscoroutine(res):
                    asyncio.ensure_future(res)
            except Exception:
                log.exception("emit failed")

    def cancel(self) -> None:
        self._cancel = True

    def steer(self, text: str) -> None:
        """A message the user typed while this task is running: applied before the next model call."""
        self._inbox.append(text)

    def _drain_inbox(self) -> None:
        while self._inbox:
            text = self._inbox.pop(0)
            self.history.append(Message("user", f"[使用者在執行中補充指示] {text}\n"
                                                "（請把這個補充納入目前的任務：調整計畫後繼續；若它要求停止或改做別的事，照做。）"))
            self._persist()
            if getattr(self, "_run_rec", None):
                self._run_rec.steer(text)
            self._emit("steer_applied", {"text": text})

    def _core_changes(self) -> list[str]:
        now = {m.id: m.content for m in self.rt.memory.core()}
        changes = [f"+ [{k}] {v}" for k, v in now.items() if self._core_at_start.get(k) != v]
        changes += [f"- [{k}] (removed from core)" for k in self._core_at_start if k not in now]
        return changes

    async def _build_context(self, query: str) -> str:
        recalled = []
        if "memory_search" in self.tool_names or "memory_save" in self.tool_names:
            try:
                recalled = await self.rt.memory.asearch(
                    query, k=int(self.config.get("memory.recall_k", 6)), tiers=("short", "long"),
                    min_score=float(self.config.get("memory.recall_min_score", 0.15)))
            except Exception:
                log.exception("recall failed")
        skills_index = ""
        if "skill_view" in self.tool_names:
            skills_index = self.rt.skills.index_block(
                query, self.tool_names, self.config.get("skills.index_mode", "auto"),
                int(self.config.get("skills.index_all_threshold", 20)), int(self.config.get("skills.retrieve_n", 6)))
        if recalled:
            self._emit("recall", {"memories": [m.brief() for m in recalled]})
        block = build_context_block(recalled=recalled, skills_index=skills_index, working=self.ctx.working,
                                    core_changes=self._core_changes())
        if "board_read" in self.tool_names:
            try:
                from ..work.hub import get_hub
                brief = get_hub(self.rt).board_brief(self.ctx.state["board_scope"])
            except Exception:  # noqa: BLE001
                log.exception("blackboard unavailable")
                brief = ""
            if brief:
                block = block.replace("\n</context>", "\n\n" + brief + "\n</context>")
        return block

    # ------------------------------------------------------------------ completion check & model escalation
    def _event(self, text: str) -> None:
        self._emit("status", {"message": text})
        if getattr(self, "_run_rec", None):
            self._run_rec.event(text)

    def _complex_task(self, explicit) -> bool:
        """Skills marked sinobee-complexity: high (long multi-stage pipelines) start on the stronger model."""
        names = set(explicit or []) | set(self.ctx.state.get("skills_used", set()))
        return any(str(getattr(self.rt.skills.skills.get(n), "metadata", {}).get("sinobee-complexity", "")).lower()
                   == "high" for n in names if self.rt.skills.skills.get(n))

    def _escalate(self, why: str, stats: dict) -> bool:
        strong = self.rt.strong()
        if strong is None or strong is self._prov or stats.get("escalated"):
            return False
        stats["escalated"] = True
        old = self._prov.label
        self._prov = strong
        self._event(f"改用較強的模型 {strong.label}（{why}；原本 {old}）")
        return True

    def _should_verify(self, stats: dict) -> bool:
        return bool(self.config.get("agent.verify", True)) and \
            stats["tool_calls"] >= int(self.config.get("agent.verify_min_tool_calls", 3)) and \
            not stats.get("learn_command")

    async def _verify(self, query: str, answer: str, stats: dict) -> dict | None:
        """Ask a (cheap) model whether the turn really did what was asked. None = could not check."""
        steps = []
        for m in self.history[-60:]:
            for c in m.tool_calls:
                steps.append(f"- {c.name} {json.dumps(c.arguments or {}, ensure_ascii=False)[:160]}")
            if m.role == "tool":
                t = m.text()
                steps.append(f"  -> {'FAILED: ' if t.lstrip().startswith('ERROR') else ''}{t[:200]}")
        steers = [m.text() for m in self.history if m.role == "user" and
                  isinstance(m.content, str) and m.content.startswith("[使用者在執行中補充指示]")]
        files = [a["path"] for a in stats.get("artifacts", [])][:20]
        prompt = (
            "You check whether an AI assistant really finished the user's task. Judge only from the evidence.\n"
            f"USER REQUEST:\n{query[:3000]}\n"
            + ("LATER INSTRUCTIONS:\n" + "\n".join(steers)[:1500] + "\n" if steers else "")
            + "TOOL STEPS (newest last):\n" + "\n".join(steps[-40:])[:6000]
            + (f"\nFILES PRODUCED: {', '.join(files)}" if files else "")
            + f"\nFINAL ANSWER:\n{answer[:3000]}\n\n"
            "Complete means: what was asked was actually done and the answer reports it truthfully. Also complete: "
            "the assistant asks the user a needed question, or clearly explains an external blocker it cannot "
            "remove. Not complete: it claims success the steps do not show, stops halfway, or only describes "
            "what it would do.\nReply with JSON only: {\"complete\": true|false, \"missing\": \"<what is not done, "
            "in the user's language, one sentence>\"}")
        try:
            resp = await self.rt.aux.chat([Message("user", prompt)], None, temperature=0, max_tokens=300)
            m = re.search(r"\{.*\}", resp.message.text(), re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as e:  # noqa: BLE001 - a failed check never blocks the answer
            log.info("completion check skipped: %s", e)
            return None

    def _active_skill_notes(self) -> str:
        """How to invoke each skill in use, repeated every step so it survives compaction and long tool output."""
        lines = []
        for name in sorted(self.ctx.state.get("skills_used", set())):
            s = self.rt.skills.skills.get(name)
            if s is not None and s.quickstart:
                lines.append(f"- {name}: {s.quickstart}")
        return ("<active_skills>\n" + "\n".join(lines) + "\n</active_skills>") if lines else ""

    # ------------------------------------------------------------------ main loop
    async def run(self, user_input: str | list) -> str:
        with scope(self.session_id, "agent") as usage_scope:
            return await self._run(user_input, usage_scope)

    async def _run(self, user_input: str | list, usage_scope) -> str:
        self._cancel = False
        self.tools = REGISTRY.available(self.config)   # settings may have changed toolsets
        if self.tool_filter is not None:
            self.tools = [t for t in self.tools if self.tool_filter(t)]
        try:
            self.rt.skills.refresh()
        except Exception:
            log.exception("skill refresh failed")
        self.tool_names = {t.name for t in self.tools}
        user_input = self._redact_secrets(user_input)
        user_input, explicit, learn = self._slash_commands(user_input)
        user_input = self._attach_images(user_input)
        user_msg = Message("user", user_input)
        query = next((p.text for p in reversed(user_msg.content) if isinstance(p, TextPart)), "") \
            if isinstance(user_msg.content, list) else user_msg.content
        if not self.history:
            self.rt.memory.ensure_session(self.session_id, self.platform, title=_title(query))
        self.history.append(user_msg)
        self._persist()   # persist step by step so a reloaded page / crash never loses an in-progress turn
        turn_start = len(self.history) - 1
        db_start = self.rt.memory.last_index(self.session_id)
        best = self.rt.skills.search(query, 1, self.tool_names) if "skill_view" in self.tool_names else []
        stats = {"tool_calls": 0, "errors": 0, "recovered": False, "correction": bool(CORRECTION_RE.search(query)),
                 "skill_request": bool(SKILL_REQUEST_RE.search(query)) and not learn, "learn_command": learn,
                 "skill_gap": not best or best[0][1] < 1.0, "explicit_skills": explicit}
        self._emit("turn_start", {"session_id": self.session_id, "input": query})

        context_block = await self._build_context(query)
        final_text = ""
        max_iter = int(self.max_iterations or self.config.get("agent.max_iterations", 60))
        max_tokens = self._max_tokens()
        empty_replies = 0
        overflow_retried = False
        nudge = False
        tool_specs = self._tool_specs()
        status, error_info, started, steps = "done", None, time.time(), 0
        self._prov = self.rt.provider
        stats["verify_rounds"] = 0
        if self._complex_task(explicit):
            self._escalate("任務屬於複雜類型", stats)
        self._turn_started = started
        stats["artifacts"] = []
        from ..work.chatrun import ChatRun
        self._run_rec = ChatRun(self, query, _title(query))
        if self._run_rec.rid and not self.ctx.state.get("job_run"):
            self.ctx.state["run_id"] = self._run_rec.rid

        for iteration in range(max_iter):
            steps = iteration + 1
            if self._cancel:
                final_text = "（已依你的要求停止）"
                status = "stopped"
                self.history.append(Message("assistant", final_text))
                break
            budget_state = self.rt.budget_status()
            if budget_state["exceeded"] and budget_state["enforce"]:
                final_text = ("已達到設定的花費上限，暫停呼叫模型。請到「用量」頁調整預算。"
                              f" (daily {budget_state['daily']}, monthly {budget_state['monthly']})")
                self._emit("budget", budget_state)
                self.history.append(Message("assistant", final_text))
                status, error_info = "blocked", {"summary": "已達花費上限", "hint": "到「用量」或「設定」調整預算。",
                                                 "detail": final_text}
                break
            self._drain_inbox()
            prune_images(self.history, keep_last=2)
            budget = self._context_budget(max_tokens)
            if estimate_tokens([self.system] + self.history, tool_specs, context_block) > budget * 0.8:
                turn_start = await self._compact(budget, tool_specs, context_block, turn_start)
            msgs = [self.system] + self.history
            # attach volatile context to the newest *real* user message of this turn
            request = list(msgs)
            idx = next((i for i in range(len(request) - 1, 0, -1)
                        if request[i].role == "user"), None)
            if idx is not None:
                um = request[idx]
                parts = um.content if isinstance(um.content, list) else [TextPart(um.content)]
                request[idx] = Message("user", list(parts) + [TextPart(context_block)])

            pinned = self._active_skill_notes()
            if pinned and idx is not None:
                um = request[idx]
                request[idx] = Message("user", list(um.content) + [TextPart(pinned)])
            if nudge:  # transient: not stored in history
                request.append(Message("user", EMPTY_NUDGE))
                nudge = False
            if steps > int(self.config.get("agent.escalate_after_steps", 25)) and not stats.get("escalated"):
                self._escalate(f"已進行 {steps - 1} 步仍未完成", stats)
            self._emit("thinking", {"iteration": iteration, "step": steps, "model": self._prov.label})
            try:
                resp = await self._prov.chat(request, tool_specs,
                                                   temperature=self.config.get("model.temperature", 0.3),
                                                   max_tokens=max_tokens)
            except Exception as e:
                if OVERFLOW_RE.search(str(e)) and not overflow_retried:
                    # the estimate was too optimistic for this model: compact hard and try once more
                    overflow_retried = True
                    log.warning("context overflow (%s); compacting and retrying", str(e)[:200])
                    self._budget_cap = self._overflow_budget(str(e), budget, max_tokens,
                                                             estimate_tokens(request, tool_specs, context_block))
                    budget = self._context_budget(max_tokens)
                    before = len(self.history)
                    turn_start = await self._compact(budget, tool_specs, context_block, turn_start,
                                                     force=True, keep_ratio=0.25, reason="overflow")
                    if self._last_compaction_changed or len(self.history) != before:
                        continue
                from .errors import explain_error
                error_info = explain_error(e)
                status = "error"
                final_text = f"⚠︎ 任務中斷：{error_info['summary']}。{error_info['hint']}"
                # keep a short note (not the raw provider error) so the next turn knows this attempt failed
                self.history.append(Message("assistant", f"[上一次嘗試失敗：{error_info['summary']}]"))
                log.error("model call failed: %s", e)
                break
            self.usage.add(resp.usage)
            msg = resp.message
            log.info("model %s replied: %d chars, %d tool calls, finish=%s", resp.model, len(msg.text()),
                     len(msg.tool_calls), resp.stop_reason)
            if not msg.tool_calls and not msg.text().strip():
                # Empty reply: usually the output budget was spent on hidden reasoning (finish=length) or a weak
                # model stalled after a tool result. Never accept it as the final answer.
                empty_replies += 1
                truncated = str(resp.stop_reason).lower() in ("length", "max_tokens", "max_output_tokens")
                if truncated:
                    max_tokens = min(max_tokens * 2, 32768)
                if empty_replies <= 2:
                    self._emit("status", {"message": f"模型回傳空白回覆（finish_reason={resp.stop_reason or 'n/a'}），"
                                                     f"{'提高輸出上限並' if truncated else ''}要求繼續"})
                    nudge = True
                    continue
                final_text = (f"⚠︎ 模型 {resp.model or self._prov.label} 連續 {empty_replies} 次回傳空白回覆"
                              f"（finish_reason={resp.stop_reason or 'n/a'}），任務沒有完成。建議改用能力較強的模型，"
                              "或在設定頁提高輸出上限後重試。")
                self.history.append(Message("assistant", "[上一次嘗試失敗：模型沒有產生回覆]"))
                status = "incomplete"
                error_info = {"summary": f"模型連續 {empty_replies} 次回傳空白回覆",
                              "hint": "建議改用能力較強的模型，或在設定頁提高輸出上限後重試。",
                              "detail": f"model={resp.model}, finish_reason={resp.stop_reason or 'n/a'}"}
                break
            self.history.append(msg)
            if msg.tool_calls:
                self._persist()
            if msg.text():
                self._emit("assistant", {"text": msg.text(), "final": not msg.tool_calls})
            if not msg.tool_calls:
                final_text = msg.text()
                if not stats.get("consecutive_errors") and self._should_verify(stats):
                    verdict = await self._verify(query, final_text, stats)
                    if verdict and not verdict.get("complete"):
                        missing = str(verdict.get("missing") or "任務尚未完成")[:400]
                        if stats["verify_rounds"] < 1:
                            stats["verify_rounds"] += 1
                            self._event(f"完成檢查未通過：{missing}；繼續執行")
                            self._escalate("完成檢查未通過", stats)
                            self.history.append(Message("user", f"[自動完成檢查] 任務還沒有完成：{missing}\n"
                                                                "請接著把它完成；如果確實無法完成，請明確說明原因與目前的進度。"))
                            self._persist()
                            continue
                        status = "incomplete"
                        error_info = {"summary": f"完成檢查未通過：{missing}",
                                      "hint": "可以回覆「繼續」讓我接著做，或補充更明確的要求。", "detail": ""}
                    elif verdict:
                        self._event("完成檢查通過")
                if stats.get("consecutive_errors"):
                    # the model stopped right after failures: do not report the task as done
                    status = "incomplete"
                    error_info = {"summary": "最後的操作失敗，任務可能沒有完成",
                                  "hint": "請看最後一個 ✕ 的錯誤訊息，或回覆「繼續」讓我換個方法再試。",
                                  "detail": stats.get("last_error", "")}
                break
            for call in msg.tool_calls:
                result_msg = await self._execute(call, stats)
                self.history.append(result_msg)
            self._persist()
            if (stats.get("consecutive_errors", 0) >= int(self.config.get("agent.escalate_after_errors", 3))
                    or stats.get("repeats")) and not stats.get("escalated"):
                self._escalate(f"連續 {stats.get('consecutive_errors', 0)} 次工具錯誤", stats)
            limit = int(self.config.get("agent.max_consecutive_errors", 6))
            if stats.get("consecutive_errors", 0) >= limit or stats.get("repeats", 0) >= 3:
                status = "incomplete"
                why = (f"連續 {stats['consecutive_errors']} 次工具呼叫失敗" if stats.get("consecutive_errors", 0) >= limit
                       else "重複執行已失敗的相同操作")
                error_info = {"summary": why + "，任務沒有完成",
                              "hint": "請看最後的錯誤訊息；可以換用能力較強的模型，或回覆更明確的指示後再試。",
                              "detail": stats.get("last_error", "")}
                final_text = (f"⚠︎ 任務沒有完成：{why}。最後的錯誤：\n{stats.get('last_error', '')[:400]}")
                self.history.append(Message("assistant", f"[上一次嘗試失敗：{why}。最後的錯誤：{stats.get('last_error', '')[:300]}]"))
                break
        else:
            status = "incomplete"
            error_info = {"summary": f"已達單次任務的步驟上限（{max_iter} 步）",
                          "hint": "可以回覆「繼續」讓我接著做，或在設定提高 agent.max_iterations。", "detail": ""}
            final_text = "（步驟用完，任務尚未完成）"
            self.history.append(Message("assistant", final_text))

        self._persist()
        turn_usage = usage_scope.as_dict()
        session_usage = self.rt.memory.usage_totals(session_id=self.session_id)
        stats["status"] = status
        self.last_status = {"status": status, "error": error_info}
        turn_skills = list(explicit)
        for m in self.history[turn_start:]:
            for c in m.tool_calls:
                if c.name in ("skill_view", "skill_run") and c.arguments.get("name") and \
                        c.arguments["name"] not in turn_skills:
                    turn_skills.append(c.arguments["name"])
        turn_id = None
        try:
            turn_id = self.rt.memory.record_turn(self.session_id, db_start, self.rt.memory.last_index(self.session_id),
                                                 status, self._prov.label, turn_skills, stats["tool_calls"],
                                                 turn_usage.get("cost_usd", 0), round(time.time() - started, 1))
        except Exception:
            log.exception("could not record turn")
        self._emit("turn_end", {"text": final_text, "status": status, "error": error_info, "steps": steps,
                                "turn_id": turn_id, "skills": turn_skills,
                                "duration_s": round(time.time() - started, 1), "usage": turn_usage,
                                "session_cost_usd": session_usage["cost_usd"],
                                "session_tokens": session_usage["total_tokens"],
                                "artifacts": stats.get("artifacts", [])[-30:]})
        if getattr(self, "_run_rec", None):
            self._run_rec.finish(status, final_text, error_info, turn_usage.get("cost_usd", 0), turn_id)
        bs = self.rt.budget_status()
        if bs["warn"]:
            self._emit("budget", bs)
        self.last_turn_usage = turn_usage
        self._maybe_learn(turn_start, stats)
        return final_text

    IMG_REF = re.compile(r"!\[[^\]]*\]\(([^)\s]+\.(?:png|jpe?g|gif|webp))\)", re.I)

    def _attach_images(self, user_input):
        """Images the user attached (markdown refs to local files) are also sent to the model as pictures."""
        if not isinstance(user_input, str) or not self.config.get("model.vision", True):
            return user_input
        from pathlib import Path
        images = []
        for ref in self.IMG_REF.findall(user_input)[:4]:
            p = Path(ref).expanduser()
            if not p.is_absolute():
                p = self.config.workspace / p
            try:
                if p.is_file() and p.stat().st_size < 5_000_000:
                    mt = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                          ".webp": "image/webp"}.get(p.suffix.lower(), "image/png")
                    images.append(ImagePart.from_bytes(p.read_bytes(), mt))
            except OSError:
                continue
        return [TextPart(user_input), *images] if images else user_input

    def _redact_secrets(self, user_input):
        """Never store or send known secrets: stored API keys / DB passwords and passwords inside DSNs."""
        from ..db import redact_dsn
        values = self.config.secrets.all_values()

        def clean(text: str) -> str:
            text = redact_dsn(text)
            for v in values:
                if v in text:
                    text = text.replace(v, "••••")
            return text
        if isinstance(user_input, str):
            return clean(user_input)
        if isinstance(user_input, list):
            return [TextPart(clean(p.text)) if isinstance(p, TextPart) else p for p in user_input]
        return user_input

    _budget_cap: int | None = None   # learned from a "context too long" error; lasts for this agent

    def _context_budget(self, max_tokens: int | None = None) -> int:
        """Request budget for the main model: its context window minus the answer room (see context.py)."""
        budget = context_budget(self.config, max_tokens or self._max_tokens())
        return min(budget, self._budget_cap) if self._budget_cap else budget

    @staticmethod
    def _overflow_budget(error: str, budget: int, max_tokens: int, estimate: int) -> int:
        """A smaller budget (in SinoBee's estimate units) after the provider rejected the request as too long.
        "210000 tokens > 200000 maximum": scale the stated limit by how far off the estimate was;
        one number: treat it as the limit; none: 60 % of what was sent."""
        nums = sorted({int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{3,}", error)
                       if int(n.replace(",", "")) >= 2048}, reverse=True)
        if len(nums) >= 2:
            sent, limit = nums[0], nums[1]
            cap = (limit - max_tokens - max(1024, limit // 50)) * estimate / sent
        elif nums:
            limit = nums[0]
            cap = min(limit - max_tokens - max(1024, limit // 50), estimate * 0.8)
        else:
            cap = estimate * 0.6
        return max(2048, min(budget, int(cap)))

    def _load_history(self) -> list[Message]:
        """The model context of this session: the latest compaction summary + the messages after it."""
        mem = self.rt.memory
        cp = mem.checkpoint(self.session_id)
        if cp and 0 < cp["upto_idx"] <= mem.last_index(self.session_id) + 1:
            tail = mem.load_messages(self.session_id, since=cp["upto_idx"])
            return with_summary(Message("system", ""), cp["summary"], tail)[1:]
        return mem.load_messages(self.session_id)

    _last_compaction_changed = False

    async def _compact(self, budget: int, tool_specs, context_block: str, turn_start: int, *,
                       force: bool = False, keep_ratio: float = 0.4, reason: str = "budget") -> int:
        """Summarise older messages; saves a checkpoint and returns the adjusted start of the current turn."""
        self._emit("status", {"message": "對話內容較長，正在整理較早的內容（壓縮上下文）"})
        usage_scope = current_scope.get()
        if usage_scope is not None:
            usage_scope.purpose = "compression"
        self._persist()   # the DB keeps the full transcript; only the model context is compacted
        aux_spec = self.config.get("model.auxiliary") or self.config.get("model.default")
        aux_window, _ = context_window(self.config, aux_spec)
        try:
            res = await compact([self.system] + self.history, self.rt.aux, budget=budget, tools=tool_specs,
                                extra=context_block, force=force, keep_ratio=keep_ratio,
                                digest_tokens=max(2000, min(40000, int(aux_window * 0.6) - 2000)))
        finally:
            if usage_scope is not None:
                usage_scope.purpose = "agent"
        self._last_compaction_changed = res.changed
        if not res.changed:
            return turn_start
        in_turn = len(self.history) - turn_start
        self.history = res.messages[1:]
        self._persisted = len(self.history)
        if res.summary:
            upto = self.rt.memory.last_index(self.session_id) + 1 - res.tail_len
            try:
                self.rt.memory.save_checkpoint(self.session_id, upto, res.summary, self.rt.provider.label)
            except Exception:  # noqa: BLE001 - a missing checkpoint only costs a re-summary later
                log.exception("could not save context checkpoint")
        log.info("context compacted (%s): %d messages kept, %d trimmed, budget %d", reason, res.tail_len,
                 res.trimmed, budget)
        first_real = len(self.history) - res.tail_len
        return max(first_real, len(self.history) - in_turn)

    def _max_tokens(self) -> int:
        """Reasoning models spend the output budget on hidden thinking; give them room for the visible answer."""
        n = int(self.config.get("model.max_tokens", 8192))
        if REASONING_MODEL_RE.search(str(self.config.get("model.default") or "")):
            n = max(n, int(self.config.get("model.reasoning_max_tokens", 16384)))
        if local.is_local_spec(self.config.get("model.default")):
            n = min(n, max(1024, local.num_ctx(self.config) // 4))
        return n

    def _tool_specs(self) -> list:
        """Tool schemas for this turn: constrain skill names to an enum (agentskills.io client guidance) and drop
        activation tools entirely when no skill is visible."""
        import copy
        visible = sorted(s.name for s in self.rt.skills.visible(self.tool_names))
        specs = []
        for t in self.tools:
            sp = t.spec()
            if t.name in SKILL_ARG_TOOLS:
                if not visible:
                    continue
                if len(visible) <= 300:
                    sp.parameters = copy.deepcopy(sp.parameters)
                    sp.parameters["properties"]["name"]["enum"] = visible
            specs.append(sp)
        return specs

    def _slash_commands(self, user_input):
        """`/skill-name ...` activates skills explicitly (up to 5); `/learn <source>` creates a skill."""
        if not isinstance(user_input, str) or not user_input.lstrip().startswith("/"):
            return user_input, [], False
        text = user_input.strip()
        if re.match(r"^/learn\b", text, re.I):
            arg = text[6:].strip() or "what we just did in this conversation"
            return LEARN_TEMPLATE.format(arg=arg), [], True
        names, rest = [], text
        while rest.startswith("/") and len(names) < 5:
            tok, _, tail = rest[1:].partition(" ")
            if tok in self.rt.skills.skills and self.rt.skills.skills[tok].policy.visible:
                names.append(tok)
                rest = tail.strip()
            else:
                break
        if not names:
            return user_input, [], False
        blocks = []
        for n in names:
            try:
                blocks.append(self.rt.skills.view(n))
                self.ctx.state.setdefault("skills_used", set()).add(n)
                self.ctx.state.setdefault("preapproved", []).extend(self.rt.skills.preapprovals(n))
            except Exception as e:
                blocks.append(f"(could not load skill {n}: {e})")
        self._emit("status", {"message": "activated skills: " + ", ".join(names)})
        body = rest or f"Use the {', '.join(names)} skill(s)."
        return [TextPart("\n\n".join(blocks)), TextPart(f"[User explicitly activated: {', '.join(names)}]"),
                TextPart(body)], names, False

    async def _execute(self, call: ToolCall, stats: dict) -> Message:
        self._emit("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments})
        t0 = time.time()
        rec = getattr(self, "_run_rec", None)
        if rec:
            rec.tool_started(call.name)
        sk = (call.arguments or {}).get("name") if call.name in ("skill_view", "skill_run") else None
        if sk and self._complex_task([sk]) and not stats.get("escalated"):
            self._escalate(f"技能 {sk} 是多階段的複雜任務", stats)
        sig = call.name + json.dumps(call.arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
        failed = stats.setdefault("failed_calls", {})
        if sig in failed:
            # weak models retry the identical failing call over and over; refuse and say so instead
            stats["repeats"] = stats.get("repeats", 0) + 1
            result = ToolResult.error(
                "REPEATED CALL - you already made exactly this call in this task and it failed with:\n"
                f"{failed[sig]}\nRunning it again will fail the same way. Read that error, then change the "
                "arguments (for skill_run: `script` must be a file inside the skill, put folders/files in `args`) "
                "or take a different approach. If you cannot, tell the user plainly what failed.")
        else:
            result = await REGISTRY.dispatch(call, self.ctx, self.tool_names)
        elapsed = round(time.time() - t0, 1)
        stats["tool_calls"] += 1
        denied = result.is_error and "user did not approve" in str(result.content)[:300]
        if result.is_error and not denied:   # a refused approval is the user's decision, not a failure
            stats["errors"] += 1
            stats["consecutive_errors"] = stats.get("consecutive_errors", 0) + 1
            stats["last_error"] = (result.content if isinstance(result.content, str) else "")[:600]
            if sig not in failed:
                failed[sig] = stats["last_error"]
        elif not denied:
            stats["consecutive_errors"] = 0
            stats["last_error"] = ""
            if stats["errors"]:
                stats["recovered"] = True
        preview = result.content if isinstance(result.content, str) else \
            "\n".join(p.text for p in result.content if isinstance(p, TextPart))
        has_image = isinstance(result.content, list) and any(isinstance(p, ImagePart) for p in result.content)
        image_b64 = next((p.data for p in result.content if isinstance(p, ImagePart)), None) if has_image else None
        self._emit("tool_result", {"id": call.id, "name": call.name, "error": result.is_error, "duration_s": elapsed,
                                   "preview": preview[:3000], "image": image_b64})
        if rec:
            rec.step(call.name, call.arguments or {}, not result.is_error, elapsed,
                     preview[:300] if result.is_error else "")
        if not result.is_error:
            try:
                arts = artifacts.collect(self.config, call.name, call.arguments or {}, preview,
                                         self.ctx.state.setdefault("artifact_seen", set()),
                                         since=getattr(self, "_turn_started", None))
            except Exception:  # noqa: BLE001 - showing links must never break a task
                log.exception("artifact scan failed")
                arts = []
            if arts:
                stats.setdefault("artifacts", []).extend(arts)
                if rec:
                    rec.files(arts)
                self._emit("artifact", {"id": call.id, "tool": call.name, "files": arts})
        return Message("tool", result.content, tool_call_id=call.id, name=call.name)

    def _persist(self) -> None:
        new = self.history[self._persisted:]
        if new:
            self.rt.memory.append_messages(self.session_id, new)
            self._persisted = len(self.history)

    # ------------------------------------------------------------------ learning
    def _maybe_learn(self, turn_start: int, stats: dict) -> None:
        if not self.config.get("learning.enabled", True) or not self.learn:
            return
        if stats.get("status", "done") != "done":
            return  # failed / stopped turns teach nothing reliable (and must not rate skills)
        unrated = self.ctx.state.get("skills_used", set()) - self.ctx.state.get("skills_rated", set())
        if stats.get("learn_command"):
            return  # the agent authored the skill itself this turn
        stats["created_this_turn"] = sorted(self.ctx.state.get("skills_created", set()))
        signal = (stats["tool_calls"] >= int(self.config.get("learning.min_tool_calls", 4))
                  or stats["recovered"] or stats["correction"] or stats["skill_request"] or bool(unrated))
        if not signal:
            return
        from ..learning.reflector import reflect
        turn = self.history[turn_start:]
        self._emit("status", {"message": "reflecting (learning in background)"})
        self.rt.spawn(reflect(self.rt, self.session_id, turn, stats, sorted(unrated), emit=self._emit))
