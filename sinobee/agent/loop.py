"""The SinoBee agent loop. One class serves every entry point (CLI, Web UI, API)."""
from __future__ import annotations

import asyncio
import logging
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
from ..types import ImagePart, Message, TextPart, ToolCall, Usage
from .context import compress, estimate_tokens, prune_images
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

    def __init__(self, config: Config | None = None, *, provider=None, aux_provider=None, embedder=None):
        self.config = config or Config.load()
        self._fixed_provider = provider is not None
        self.provider_error: str | None = None
        if provider is not None:
            self.provider, self.aux, emb = provider, aux_provider or provider, embedder
        else:
            self.provider, self.aux, emb = self._build_models()
        self.memory = MemoryStore(self.config.db_path,
                                  core_char_limit=int(self.config.get("memory.core_char_limit", 1500)),
                                  half_life_days=float(self.config.get("memory.half_life_days", 30)),
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
                 emit: EmitFn | None = None, approve: ApproveFn | None = None):
        self.rt = runtime
        self.config = runtime.config
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.platform = platform
        self.emit_fn = emit
        self.usage = Usage()
        self._cancel = False
        self.tools = REGISTRY.available(self.config)
        self.tool_names = {t.name for t in self.tools}
        self.ctx = ToolContext(config=self.config, session_id=self.session_id, memory=runtime.memory,
                               skills=runtime.skills, emit=self._emit, approve=approve)
        runtime.memory.ensure_session(self.session_id, platform)
        self.history: list[Message] = runtime.memory.load_messages(self.session_id)
        self._core_at_start = {m.id: m.content for m in runtime.memory.core()}
        self.system = Message("system", build_system_prompt(self.config, runtime.memory.render_core(),
                                                            sorted(self.tool_names)))
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
                    query, k=int(self.config.get("memory.recall_k", 6)), tiers=("long",),
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
        return build_context_block(recalled=recalled, skills_index=skills_index, working=self.ctx.working,
                                   core_changes=self._core_changes())

    # ------------------------------------------------------------------ main loop
    async def run(self, user_input: str | list) -> str:
        with scope(self.session_id, "agent") as usage_scope:
            return await self._run(user_input, usage_scope)

    async def _run(self, user_input: str | list, usage_scope) -> str:
        self._cancel = False
        self.tools = REGISTRY.available(self.config)   # settings may have changed toolsets
        try:
            self.rt.skills.refresh()
        except Exception:
            log.exception("skill refresh failed")
        self.tool_names = {t.name for t in self.tools}
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
        max_iter = int(self.config.get("agent.max_iterations", 60))
        max_tokens = self._max_tokens()
        empty_replies = 0
        nudge = False
        tool_specs = self._tool_specs()
        status, error_info, started, steps = "done", None, time.time(), 0

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
            prune_images(self.history, keep_last=2)
            budget = self._context_budget()
            msgs = [self.system] + self.history
            if estimate_tokens(msgs) > budget * 0.8:
                self._emit("status", {"message": "compressing context"})
                usage_scope.purpose = "compression"
                self._persist()   # the DB keeps the full transcript; only the in-memory context is compressed
                try:
                    new_msgs, changed = await compress(msgs, self.rt.aux, budget=budget)
                finally:
                    usage_scope.purpose = "agent"
                if changed:
                    self.history = new_msgs[1:]
                    self._persisted = len(self.history)
                    turn_start = 0
                    msgs = new_msgs
            # attach volatile context to the newest *real* user message of this turn
            request = list(msgs)
            idx = next((i for i in range(len(request) - 1, 0, -1)
                        if request[i].role == "user"), None)
            if idx is not None:
                um = request[idx]
                parts = um.content if isinstance(um.content, list) else [TextPart(um.content)]
                request[idx] = Message("user", list(parts) + [TextPart(context_block)])

            if nudge:  # transient: not stored in history
                request.append(Message("user", EMPTY_NUDGE))
                nudge = False
            self._emit("thinking", {"iteration": iteration, "step": steps, "model": self.rt.provider.label})
            try:
                resp = await self.rt.provider.chat(request, tool_specs,
                                                   temperature=self.config.get("model.temperature", 0.3),
                                                   max_tokens=max_tokens)
            except Exception as e:
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
                final_text = (f"⚠︎ 模型 {resp.model or self.rt.provider.label} 連續 {empty_replies} 次回傳空白回覆"
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
                break
            for call in msg.tool_calls:
                result_msg = await self._execute(call, stats)
                self.history.append(result_msg)
            self._persist()
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
                                                 status, self.rt.provider.label, turn_skills, stats["tool_calls"],
                                                 turn_usage.get("cost_usd", 0), round(time.time() - started, 1))
        except Exception:
            log.exception("could not record turn")
        self._emit("turn_end", {"text": final_text, "status": status, "error": error_info, "steps": steps,
                                "turn_id": turn_id, "skills": turn_skills,
                                "duration_s": round(time.time() - started, 1), "usage": turn_usage,
                                "session_cost_usd": session_usage["cost_usd"],
                                "session_tokens": session_usage["total_tokens"]})
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

    def _context_budget(self) -> int:
        budget = int(self.config.get("agent.context_budget", 120000))
        if local.is_local_spec(self.config.get("model.default")):
            # a local runtime truncates silently past num_ctx: compress before that
            nctx = local.num_ctx(self.config)
            budget = min(budget, nctx - nctx // 4 - 4000)  # minus answer room and tool schemas
        return budget

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
        result = await REGISTRY.dispatch(call, self.ctx, self.tool_names)
        elapsed = round(time.time() - t0, 1)
        stats["tool_calls"] += 1
        if result.is_error:
            stats["errors"] += 1
        elif stats["errors"]:
            stats["recovered"] = True
        preview = result.content if isinstance(result.content, str) else \
            "\n".join(p.text for p in result.content if isinstance(p, TextPart))
        has_image = isinstance(result.content, list) and any(isinstance(p, ImagePart) for p in result.content)
        image_b64 = next((p.data for p in result.content if isinstance(p, ImagePart)), None) if has_image else None
        self._emit("tool_result", {"id": call.id, "name": call.name, "error": result.is_error, "duration_s": elapsed,
                                   "preview": preview[:3000], "image": image_b64})
        return Message("tool", result.content, tool_call_id=call.id, name=call.name)

    def _persist(self) -> None:
        new = self.history[self._persisted:]
        if new:
            self.rt.memory.append_messages(self.session_id, new)
            self._persisted = len(self.history)

    # ------------------------------------------------------------------ learning
    def _maybe_learn(self, turn_start: int, stats: dict) -> None:
        if not self.config.get("learning.enabled", True):
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
