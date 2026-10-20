"""The SinoBee agent loop. One class serves every entry point (CLI, Web UI, API)."""
from __future__ import annotations

import asyncio
import itertools
import logging
import json
import re
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Awaitable, Callable

import time

from .. import local
from .. import tools as _tools  # noqa: F401  (registers built-in tools)
from ..accounting import current_scope, scope
from ..pricing import cost_usd, price_for
from ..config import Config
from ..memory.store import MemoryStore
from ..providers import build_auxiliary, build_chain, build_embedder
from ..providers.base import REASONING_EFFORT
from ..skills import manager_from_config
from ..fileaccess import resolve_file
from ..tools import checklist_tools
from ..tools.registry import REGISTRY, ToolContext
from . import artifacts
from . import budget as budget_mod
from . import progress as progress_mod
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

REASONING_MODEL_RE = re.compile(r"(^|/)(o\d|gpt-5|gemini-(2\.5|[3-9])|gemini-[\w.-]*latest|.*thinking|.*reasoner|deepseek-r|"
                                r"qwq|.*-r1)", re.I)
TRUNCATED_NUDGE = ("[SinoBee notice] Your previous reply ran out of output tokens (finish_reason=length) before any "
                   "tool call or text came out: the budget went to thinking and/or one very large tool argument. Do "
                   "not write a large file in one call. Write a short skeleton with write_file first, then add one "
                   "section per edit_file call (replace a placeholder comment such as <!-- SECTION 2 --> with that "
                   "section, under ~250 lines each). Keep your reasoning short and call the next tool now.")
BIG_CALL = 30000
SIZE_OVER = ("[SinoBee notice] Your last tool call carried {n:,} characters - more than one section. Fill only ONE "
             "section (one placeholder) per call from now on.")
SIZE_NEAR = ("[SinoBee notice] Your last reply used {pct}% of the output limit and nearly got cut off. Keep the next "
             "call to one small section and keep your reasoning short.")


def size_reminder(msg, resp, max_tokens: int) -> str | None:
    """Revised U2 (E3): a call that wrote more than one section, or a reply close to the output limit, gets a
    reminder in the next request - never a refusal, the content already arrived."""
    big = 0
    for c in msg.tool_calls:
        a = c.arguments or {}
        big = max(big, len(str(a.get("content") or a.get("new_string") or "")))
    tips = []
    if big > BIG_CALL:
        tips.append(SIZE_OVER.format(n=big))
    out = getattr(getattr(resp, "usage", None), "output_tokens", 0) or 0
    if max_tokens and out > 0.7 * max_tokens:
        tips.append(SIZE_NEAR.format(pct=round(100 * out / max_tokens)))
    return "\n".join(tips) or None


THINKING_FLOOR = 8192


def thinking_room(label: str, max_tokens: int) -> int:
    """A thinking model's short calls (plan 1500, consult 1400, budget check 400 ...) on a reasoning model: its
    hidden thinking counts against the limit, so the plan never came out (E5: gemini-pro-latest at 1500 gave 1 plan
    in 3, at 8192 3 in 3). Reasoning models get at least THINKING_FLOOR, never more than the model's max."""
    if not REASONING_MODEL_RE.search(label or ""):
        return max_tokens
    from ..providers import limits
    mx = limits.max_output(label)
    floor = min(THINKING_FLOOR, mx) if mx else THINKING_FLOOR
    return max(max_tokens, floor)


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
                 thinking_provider=None, reviewer_provider=None, strong_provider=None):
        self.config = config or Config.load()
        self._strong = thinking_provider or strong_provider     # strong_provider: the old name
        self._strong_spec = None
        self._reviewer = reviewer_provider
        self._reviewer_spec = None
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
        if prov == "sinobee" and sc.quota and sc.quota.get("6h") is not None:
            try:
                self.memory.mark_quota(sc.quota["6h"])      # the year heatmap marks days that reached the limit
            except Exception:  # noqa: BLE001
                log.debug("quota mark failed", exc_info=True)
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

    def thinking(self):
        """The thinking model (model.thinking): plans, advises, judges and diagnoses; never runs a step of a task.
        None when it is not configured (or is the execution model itself) - see thinker()."""
        if self._strong is not None and (self._fixed_provider or self._strong_spec is None):
            return self._strong
        spec = self.config.get("model.thinking") or self.config.get("model.escalate")
        if not spec or spec == self.config.get("model.default"):
            return None
        if self._strong_spec != spec:
            try:
                self._strong = build_chain(self.config, spec)
                self._strong.recorder = self._record
                self._strong_spec = spec
            except Exception as e:  # noqa: BLE001
                log.warning("thinking model %s unavailable: %s", spec, e)
                return None
        return self._strong

    strong = thinking          # the old name (model.escalate)

    def thinker(self):
        """The model that does the thinking calls: model.thinking, or the execution model when none is set."""
        return self.thinking() or self.provider

    def judge(self):
        """The model that judges results: model.reviewer, else the thinking model, else the execution model.
        One model per role and no silent hand-over: when it cannot answer, the check is recorded as skipped."""
        return self.reviewer() or self.thinker()

    @staticmethod
    def is_local(prov) -> bool:
        label = str(getattr(prov, "label", "") or "")
        return label.startswith(("ollama/", "local/", "lmstudio/", "vllm/")) or \
            bool(re.search(r"//(localhost|127\.0\.0\.1|host\.docker\.internal)", str(getattr(prov, "base_url", ""))))

    def inline_aux(self):
        """The model for auxiliary work that a running task waits on (compaction, working-memory compression).
        agent.inline_aux: auto (default) uses model.auxiliary unless it runs locally while the main model is in the
        cloud - a small local model there turned every long step into minutes - then the main model is used;
        "auxiliary" or "main" force one. Background work (reflection, memory upkeep) always uses model.auxiliary."""
        mode = str(self.config.get("agent.inline_aux", "auto")).lower()
        if mode == "main" or (mode == "auto" and self.aux is not self.provider
                              and self.is_local(self.aux) and not self.is_local(self.provider)):
            return self.provider
        return self.aux

    def reviewer(self):
        """The model that judges results (model.reviewer): ideally another family than the main model, so the
        acceptance review does not share the execution model's blind spots. None: see judge()."""
        if self._reviewer is not None and (self._fixed_provider or self._reviewer_spec is None):
            return self._reviewer
        spec = self.config.get("model.reviewer")
        if not spec:
            return None
        if self._reviewer_spec != spec:
            try:
                self._reviewer = build_chain(self.config, spec)
                self._reviewer.recorder = self._record
                self._reviewer_spec = spec
            except Exception as e:  # noqa: BLE001
                log.warning("reviewer model %s unavailable: %s", spec, e)
                return None
        return self._reviewer

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
            self._reviewer, self._reviewer_spec = None, None
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


# the file snapshot of the user's current turn; sub-agents started inside it snapshot into the same one
_SNAPSHOT: ContextVar = ContextVar("sinobee_snapshot", default=None)


def _title(text: str) -> str:
    """Session title from the first request: drop markdown image/link targets and collapse whitespace."""
    import re as _re
    t = _re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[\1]", text)
    t = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = " ".join(t.split())
    # "/youtube-analysis <link> 幫我分析": the request is the title, the skills picked for it are not
    skills = _re.match(r"^((?:/[\w.-]+\s*)+)", t)
    if skills and not t.lower().startswith("/learn"):
        rest = t[skills.end():].strip()
        t = rest or "使用技能：" + "、".join(x.lstrip("/") for x in skills.group(1).split())
    return t[:80] or "(附件)"


class Agent:
    def __init__(self, runtime: Runtime, session_id: str | None = None, *, platform: str = "cli",
                 emit: EmitFn | None = None, approve: ApproveFn | None = None, tool_filter=None,
                 system_extra: str = "", max_iterations: int | None = None, board_scope: str | None = None,
                 title: str | None = None, learn: bool = True, depth: int = 0, ask=None, provider=None):
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
        self._provider_override = provider        # e.g. the reviewer sub-agent runs on model.reviewer
        self.learn = learn
        self.max_iterations = max_iterations
        self.tool_filter = tool_filter
        tools = REGISTRY.available(self.config)
        if tool_filter is not None:
            tools = [t for t in tools if tool_filter(t)]
        self.tools = tools
        self.tool_names = {t.name for t in self.tools}
        self.ctx = ToolContext(config=self.config, session_id=self.session_id, memory=runtime.memory,
                               skills=runtime.skills, emit=self._emit, approve=approve, ask=ask)
        self.ctx.state["_runtime"] = runtime
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
                    min_score=float(self.config.get("memory.recall_min_score", 0.15)),
                    min_rel=float(self.config.get("memory.recall_min_rel", 0.3)))
            except Exception:
                log.exception("recall failed")
        skills_index = ""
        if "skill_view" in self.tool_names:
            skills_index = self.rt.skills.index_block(
                query, self.tool_names, self.config.get("skills.index_mode", "auto"),
                int(self.config.get("skills.index_all_threshold", 20)), int(self.config.get("skills.retrieve_n", 6)))
        if recalled:
            from ..memory.neutral import neutral
            self._emit("recall", {"memories": [{**m.brief(), "content": neutral(m.content)} if m.kind == "episode"
                                               else m.brief() for m in recalled]})
        brief = ""
        if "board_read" in self.tool_names:
            try:
                from ..work.hub import get_hub
                brief = get_hub(self.rt).board_brief(self.ctx.state["board_scope"])
            except Exception:  # noqa: BLE001
                log.exception("blackboard unavailable")
        self._ctx_parts = {"recalled": recalled, "skills_index": skills_index, "board": brief,
                           "suggest": self._suggest_skill(query)}
        return self._render_context()

    def _suggest_skill(self, query: str) -> str:
        """A skill whose trigger words appear in the request is named explicitly with its quick start, so the
        model (weak ones especially) picks it instead of improvising. Already active skills are not repeated."""
        if "skill_view" not in self.tool_names or not self.config.get("skills.auto_suggest", True):
            return ""
        try:
            s = self.rt.skills.suggest(query, self.tool_names)
        except Exception:  # noqa: BLE001
            log.exception("skill suggestion failed")
            return ""
        if not s or s.name in self.ctx.state.get("skills_used", set()):
            return ""
        qs = f"\nQuick start: {s.quickstart}" if s.quickstart else ""
        return (f'<suggested_skill name="{s.name}">This request matches the skill "{s.name}". Call '
                f'skill_view(name="{s.name}") first and follow it.{qs}</suggested_skill>')

    def _render_context(self) -> str:
        """Rebuilt before every model call so working-memory changes made during the task are visible at once."""
        p = getattr(self, "_ctx_parts", None) or {"recalled": [], "skills_index": "", "board": "", "suggest": ""}
        index = "\n".join(x for x in (p["suggest"], p["skills_index"]) if x)
        block = build_context_block(recalled=p["recalled"], skills_index=index, working=self.ctx.working,
                                    core_changes=self._core_changes())
        if p["board"]:
            block = block.replace("\n</context>", "\n\n" + p["board"] + "\n</context>")
        return block

    async def _memory_diet(self) -> bool:
        """Context near the model limit: shrink the memory parts first (recalled memories, oversized working
        memory) before older conversation is summarised. Returns True when something was made smaller."""
        changed = False
        p = getattr(self, "_ctx_parts", None)
        if p and len(p["recalled"]) > 2:
            p["recalled"] = p["recalled"][:2]
            changed = True
        limit = int(self.config.get("memory.working_char_limit", 3000))
        wm = self.ctx.working
        if wm and sum(len(str(v)) for v in wm.values()) > limit // 2:
            from ..memory.working import compress_working
            new = await compress_working(wm, self.rt.inline_aux(), limit // 2)
            if new != wm:
                wm.clear()
                wm.update(new)
                self._emit("working_memory", dict(wm))
                self._event("工作記憶過長，已壓縮重點")
                changed = True
        return changed

    # ------------------------------------------------------------------ completion check & model escalation
    def _event(self, text: str) -> None:
        self._emit("status", {"message": text})
        if getattr(self, "_run_rec", None):
            self._run_rec.event(text)

    def _complex_task(self, explicit) -> bool:
        """Skills marked sinobee-complexity: high (long multi-stage pipelines): planned first, larger step budget."""
        names = set(explicit or []) | set(self.ctx.state.get("skills_used", set()))
        return any(str(getattr(self.rt.skills.skills.get(n), "metadata", {}).get("sinobee-complexity", "")).lower()
                   == "high" for n in names if self.rt.skills.skills.get(n))

    # ------------------------------------------------------------------ thinking model: plan, advice, verdict
    async def _thinking_call(self, what: str, messages, max_tokens: int, stats: dict):
        """One call to the thinking model with agent.model_deadline. It never falls back to another model: a call
        that fails or runs out of time is recorded (stats["thinking_skipped"]) and the caller goes on without it."""
        model = self.rt.thinker()
        max_tokens = thinking_room(getattr(model, "label", ""), max_tokens)
        deadline = float(self.config.get("agent.model_deadline", 240)) or None
        try:
            return await asyncio.wait_for(model.chat(messages, None, temperature=0.2, max_tokens=max_tokens), deadline)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            why = "逾時" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
            stats.setdefault("thinking_skipped", []).append(f"{what}: {why}")
            self._event(f"⚠︎ 思維模型 {model.label} 無法完成{what}（{str(why)[:120]}），這一步略過")
            return None

    def _should_plan(self, query: str, explicit, stats: dict) -> bool:
        mode = str(self.config.get("agent.plan", "auto")).lower()
        if self.depth > 0 or mode == "off" or stats.get("learn_command") or not query.strip():
            return False
        if checklist_tools.open_items(checklist_tools.get(self.ctx)):
            return False                      # carrying on with an earlier plan ("繼續")
        if mode == "always":
            return True
        best = stats.get("best_skill")
        return bool(explicit) or self._complex_task(explicit) or len(query) >= 200 or \
            bool(best and self._complex_task([best]))

    async def _plan(self, query: str, context_block: str, stats: dict) -> None:
        """Before the first step: the thinking model writes the approach, the steps and the acceptance checklist;
        the execution model then carries them out."""
        from . import thinking
        names = sorted(self.tool_names)
        skills = []
        for n in list(stats.get("explicit_skills") or []) + ([stats["best_skill"]] if stats.get("best_skill") else []):
            sk = self.rt.skills.skills.get(n)
            if sk and n not in [x[0] for x in skills]:
                skills.append((n, f"- {n}: {sk.description[:400]}" + (f"\n  how to run: {sk.quickstart[:600]}" if sk.quickstart else "")))
        skill_txt = ("SKILLS TO USE:\n" + "\n".join(x[1] for x in skills) + "\n") if skills else ""
        prompt = thinking.PLAN.format(request=query[:6000], context=context_block[:6000], tools=", ".join(names),
                                      skills=skill_txt)
        model = self.rt.thinker()
        self._event(f"思維模型 {model.label} 規劃中")
        resp = await self._thinking_call("規劃", [Message("user", prompt)], 1500, stats)
        plan = thinking.parse_plan(resp.message.text()) if resp else None
        if not plan and resp is not None and str(resp.stop_reason).lower() in ("length", "max_tokens", "max_output_tokens"):
            # cut off while thinking: once more with room (never silently go on without a plan)
            from ..providers import limits
            room = max(thinking_room(model.label, 1500) * 2, 16384)
            mx = limits.max_output(model.label)
            resp = await self._thinking_call("規劃", [Message("user", prompt)], min(room, mx) if mx else room, stats)
            plan = thinking.parse_plan(resp.message.text()) if resp else None
        if not plan:
            why = ("沒有回應" if resp is None else
                   f"回覆不是可用的計畫（finish={resp.stop_reason or 'n/a'}，{len(resp.message.text())} 字）")
            stats.setdefault("thinking_skipped", []).append(f"規劃: {why}")
            stats["plan_failed"] = why
            self._event(f"⚠︎ 思維模型 {model.label} 沒有給出計畫（{why}）：這次不分里程碑，直接由執行模型開始")
            return
        stats["plan"] = plan
        items = checklist_tools.get(self.ctx)
        if plan["checklist"] and not items and "task_checklist" in self.tool_names:
            for it in plan["checklist"]:
                items.append({"id": len(items) + 1, "status": "todo", "evidence": "", "updated": time.time(), **it})
            checklist_tools.save(self.ctx)
        self.history.append(Message("user", thinking.render_plan(plan, model.label)))
        self._persist()
        self._emit("plan", {**plan, "model": model.label})

    async def _semantic_check(self, trigger: str, stats: dict) -> dict | None:
        """Layer 3 of the progress check: when a progress rule asks the thinking model, the auxiliary model first
        reads the recent steps and says whether the work is really spinning in place (and how). Its verdict goes to
        the thinking model and to the control log, where it shows how often a rule fired on real progress (tuning)."""
        if trigger not in progress_mod.SEMANTIC_TRIGGERS or not progress_mod.cfg(self.config).get("semantic_check", True):
            return None
        from . import failures
        model = self.rt.aux
        if model is None or model is self.rt.provider:
            return None                  # only with a separate (cheap) auxiliary model; the thinking model is asked anyway
        trace = "\n".join(f"#{t['step']} {'progress' if t['progress'] else 'none'}: {t['why'][:200]}"
                          for t in (getattr(self, "_progress", None).trace if getattr(self, "_progress", None) else [])[-20:])
        log_rows = failures.work_log(self.history[-60:], [])[-25:]
        work = "\n".join(f"{r['tool']} {r['args'][:200]} -> {'ok' if r.get('ok', True) else 'FAILED'}: "
                         f"{(r.get('result') or '')[:200]}" if "tool" in r else f"NOTE {r['note'][:200]}" for r in log_rows)
        prompt = progress_mod.SEMANTIC.format(request=(stats.get("query") or "")[:2000], trigger=trigger,
                                              trace=trace or "(none)", work=work or "(none)")
        try:
            resp = await asyncio.wait_for(model.chat([Message("user", prompt)], None, temperature=0, max_tokens=400),
                                          float(self.config.get("agent.model_deadline", 240)) / 2)
            data = progress_mod.parse_semantic(resp.message.text())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the thinking model is asked anyway
            stats.setdefault("thinking_skipped", []).append(f"語意檢查: {str(e)[:120]}")
            return None
        if data:
            data["model"] = getattr(model, "label", "")
            stats.setdefault("semantic", []).append({"step": stats.get("step"), "trigger": trigger, **data})
        return data

    def _consult_refusal(self, stats: dict) -> str | None:
        """Why the thinking model cannot be asked now: "max" (agent.consult_max used up), "pending" (already
        asked for this step), "too_soon" (less than 5 steps after the last advice) - or None."""
        if stats.get("consults", 0) >= int(self.config.get("agent.consult_max", 2)):
            return "max"
        if stats.get("consult_pending"):
            return "pending"
        if stats.get("step", 0) - stats.get("consult_at", -99) < 5:
            return "too_soon"
        return None

    def _request_consult(self, why: str, stats: dict, trigger: str | None = None, detail: str | None = None) -> bool:
        """Ask the thinking model for advice at the start of the next step (at most agent.consult_max times per
        task, at least 5 steps apart). The execution model keeps running the task; models are never switched.
        Every request - granted or not - is a control entry with its trigger."""
        refusal = self._consult_refusal(stats)
        step = stats.get("step", 0)
        if trigger:
            if refusal is None:
                self._control(stats, "consult_requested", trigger, detail or why)
            elif stats.get("_last_refusal") != (trigger, refusal):
                stats["_last_refusal"] = (trigger, refusal)
                text = {"max": f"諮詢次數已用完（agent.consult_max={self.config.get('agent.consult_max', 2)}）",
                        "pending": "這一步已經排定請教",
                        "too_soon": f"距離上次請教（第 {stats.get('consult_at')} 步）不到 5 步"}[refusal]
                self._control(stats, "consult_skipped", trigger, f"{detail or why}；{text}", decision=refusal)
        if refusal:
            return False
        stats["consult_pending"] = why
        stats["consult_trigger"] = trigger
        return True

    def _control(self, stats: dict, event: str, trigger: str, detail: str, **extra) -> dict:
        """Record a decision about the execution with its exact trigger: in the turn's stats (failure report), the
        run record, the UI, the server log and $SINOBEE_HOME/logs/control.jsonl."""
        e = progress_mod.entry(event, trigger, stats.get("step", 0), detail, getattr(self, "_progress", None),
                               **extra)
        stats.setdefault("control_log", []).append(e)
        rec = getattr(self, "_run_rec", None)
        progress_mod.append_log(self.config.home, {**e, "session_id": self.session_id, "depth": self.depth,
                                                   "run_id": getattr(rec, "rid", None),
                                                   "model": getattr(getattr(self, "_prov", None), "label", "")})
        line = progress_mod.describe(e)
        progress_mod.log.info("%s session=%s", line, self.session_id)
        self._emit("control", e)
        self._emit("status", {"message": line, "control": True})     # plain clients (CLI) show the line
        if rec:
            rec.control(e)
        return e

    def _stop_control(self, stats: dict, trigger: str, detail: str) -> None:
        self._control(stats, "stop", trigger, detail, decision="failed")

    async def _consult(self, why: str, context_block: str, stats: dict) -> None:
        """The thinking model reads a fork of the full context and advises - and decides whether the execution
        model can still finish. "stop" ends the task as failed (stats["give_up"])."""
        from . import thinking
        model = self.rt.thinker()
        trigger = stats.pop("consult_trigger", None) or "consult"
        sem = await self._semantic_check(trigger, stats)
        if sem:
            why += (f"\nAUXILIARY MODEL'S CHECK OF THE RECENT STEPS: {'spinning in place' if sem['spinning'] else 'making real progress'}"
                    f" - {sem.get('pattern') or ''} (evidence: {sem.get('evidence') or ''})")
        request = [self.system] + self.history + [Message("user", [TextPart(
            context_block + "\n\n" + thinking.CONSULT.format(why=why), volatile=True)])]
        stats["consults"] = stats.get("consults", 0) + 1
        stats["consult_at"] = stats.get("step", 0)
        resp = await self._thinking_call("請教", request, 1400, stats)
        if resp is None:
            stats.setdefault("consult_log", []).append({"step": stats.get("step"), "why": why, "advice": None,
                                                        "trigger": trigger})
            self._control(stats, "consult", trigger, f"思維模型 {model.label} 沒有回覆，照原做法繼續",
                          decision="no_answer")
            return
        advice, decision, reason = thinking.parse_decision(resp.message.text())
        stats.setdefault("consult_log", []).append({"step": stats.get("step"), "why": why, "advice": advice[:3000],
                                                    "decision": decision, "reason": reason, "trigger": trigger})
        self._control(stats, "consult", trigger,
                      f"思維模型 {model.label}：{reason or advice.splitlines()[0][:160] if advice else reason}"
                      + (f"（輔助模型：{'原地打轉' if sem['spinning'] else '有實質進展'}，{sem.get('pattern') or ''}）" if sem else ""),
                      decision=decision, semantic=sem)
        if trigger in progress_mod.ADVISED_TRIGGERS and getattr(self, "_progress", None):
            self._progress.advised(stats.get("step", 0))   # the advice gets K fresh steps
        if decision == "stop":
            stats["give_up"] = reason or "思維模型判斷執行模型無法完成這個任務"
        if advice:
            self.history.append(Message("user", f"[思維模型的建議]（{why}）\n{advice[:4000]}"))
            self._persist()

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
            + (("\nACCEPTANCE CHECKLIST:\n" + checklist_tools.render(checklist_tools.get(self.ctx))[:2000])
               if checklist_tools.get(self.ctx) else "")
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

    # ------------------------------------------------------------------ acceptance checklist & quality gate
    def _checklist_turn_start(self) -> None:
        """A finished list belongs to the last task; an open one (an interrupted task, or one restored after a
        restart from the blackboard) carries on into this turn."""
        items = checklist_tools.get(self.ctx)
        if items and not checklist_tools.open_items(items):
            items.clear()
        if items or "board_read" not in self.tool_names:
            return
        try:
            from ..work.hub import get_hub
            rows = get_hub(self.rt).store.board_read(self.ctx.state["board_scope"], kinds=["plan"], key="checklist", limit=1)
            saved = json.loads(rows[-1]["content"]) if rows else []
            if isinstance(saved, list) and checklist_tools.open_items(saved):
                items.extend(saved)
        except Exception:  # noqa: BLE001
            log.debug("no saved checklist", exc_info=True)

    def _checklist_tail(self, stats: dict, steps: int) -> str:
        items = checklist_tools.get(self.ctx)
        if items:
            out = checklist_tools.render(items)
            if len(items) >= 8 and checklist_tools.open_items(items):
                # long tasks drift when many things are half done: one item at a time, verified, then the next
                out += ("\n<focus>This is a long task. Work on ONE item at a time: mark it doing, finish and verify "
                        "it, mark it done with evidence, then start the next. Do not start several at once.</focus>")
            return out
        many = stats.get("tool_calls", 0) >= int(self.config.get("agent.checklist_after_tool_calls", 4))
        if "task_checklist" in self.tool_names and (many or self._complex_task(stats.get("explicit_skills"))) \
                and steps <= 15 \
                and not stats.get("learn_command"):
            return ("<task_checklist>This is a multi-step task: before going further, write its acceptance "
                    "checklist with task_checklist(action='set') - the outcomes that must be true for the result "
                    "to be right and how each is checked.</task_checklist>")
        return ""

    async def _step_chat(self, request, tool_specs, max_tokens: int, stats: dict):
        """One step of the task, always on the execution model (the provider's own retries and the configured
        outage fallbacks, model.fallbacks, still apply)."""
        return await self._prov.chat(request, tool_specs, temperature=self.config.get("model.temperature", 0.3),
                                     max_tokens=max_tokens)

    async def _guarded(self, what: str, coro, stats: dict):
        """Run one quality step; a bug inside it never turns a finished task into an error. The skip is recorded
        on the turn (stats["quality_skipped"]) and shown, so a result that was not checked never looks checked."""
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("%s crashed", what)
            stats.setdefault("quality_skipped", []).append(f"{what}: {type(e).__name__}: {e}")
            self._event(f"⚠︎ {what}發生內部錯誤，已略過（{type(e).__name__}: {e}）；這次結果沒有經過{what}")
            return None

    async def _quality_gate(self, stats: dict, query: str = "", answer: str = "") -> tuple[str, str] | None:
        """Checks before the task may end: open checklist items; then problems in the files it made (deterministic
        file and page checks, the user's rules, skill checks, the user's language); then, when those pass, a vision
        model's judgment of screenshots of every page, drawing and image it made."""
        open_items = checklist_tools.open_items(checklist_tools.get(self.ctx))
        if open_items:
            lines = "\n".join(f"- #{i['id']} {i['text']}" for i in open_items[:10])
            self._event(f"驗收清單還有 {len(open_items)} 項未完成")
            return "checklist", (f"[自動驗收] 驗收清單還有 {len(open_items)} 項未完成：\n{lines}\n請完成它們並附上證據"
                                 "（task_checklist update status=done），或改成 dropped 並說明原因，再回報結果。")
        paths = []
        for a in stats.get("artifacts", []):
            try:
                paths.append(resolve_file(self.config, a["path"]))
            except Exception:  # noqa: BLE001
                continue
        from . import language, qa, rules
        issues = []
        if self.config.get("agent.language_check", True) and self.depth == 0:
            # deliverables and the answer in the user's language (a sub-agent answers its parent, not the user)
            issues += await asyncio.to_thread(language.check, query, paths, answer)
        if not paths and not issues:
            return None
        if self.config.get("agent.qa", True):
            self._event("成果檢查：檢查本次產出的檔案")
            issues += await qa.check(paths)
        # the user's quality rules (bugs they reported once) and the checks the used skills declare
        issues += await asyncio.to_thread(rules.check, self.config.home, paths)
        used = set(self.ctx.state.get("skills_used", set())) | set(stats.get("explicit_skills") or [])
        if used and self.config.get("agent.skill_checks", True):
            from ..skills import checks
            try:
                issues += await checks.run(self.ctx, used, paths)
            except Exception:  # noqa: BLE001 - a broken skill check must not break the task
                log.exception("skill checks failed")
        if not issues and paths and self.depth == 0 and self.config.get("agent.visual_check", True) \
                and self.config.get("model.vision", True):
            issues += await self._visual_check(query, paths, stats)
        stats["qa_issues"] = issues
        if not issues:
            self._event("成果檢查通過")
            return None
        self._event(f"成果檢查發現 {len(issues)} 個問題")
        self._emit("qa", {"issues": issues[:20]})
        return "qa", ("[自動成果檢查] 交付前檢查本次產出的檔案，發現以下問題：\n" + qa.report(issues)
                      + "\n請修正這些問題（修好後我會再檢查一次），或在回答裡說明為什麼不需要修。")

    async def _visual_check(self, query: str, paths: list, stats: dict) -> list[dict]:
        """Screenshots judged by a vision model. A picture already judged acceptable this turn is not sent again;
        when no model can judge, the turn records the check as skipped instead of passed."""
        from . import language, visual
        try:
            shots = await visual.collect(paths)
        except Exception as e:  # noqa: BLE001 - no browser / broken page: recorded as not checked
            stats.setdefault("quality_skipped", []).append(f"視覺檢查: {type(e).__name__}: {e}")
            return []
        if not shots:
            return []
        cache = stats.setdefault("visual_ok", set())
        todo = [s for s in shots if s["sha"] not in cache]
        if not todo:
            return []
        want = language.wanted(query) or "zh-Hant"
        lang = language.NAMES.get(want, want)
        models = [self.rt.judge()]           # one judge, no hand-over: if it cannot answer, the check is skipped
        self._event(f"視覺檢查：截取 {len(todo)} 張畫面，由 {models[0].label} 判讀")
        deadline = float(self.config.get("agent.model_deadline", 240)) or None
        try:
            items = await visual.judge(models, query, lang, todo, deadline, None)
        except Exception as e:  # noqa: BLE001
            stats.setdefault("quality_skipped", []).append(f"視覺檢查: {type(e).__name__}: {e}")
            self._event(f"⚠︎ 視覺檢查無法執行：{type(e).__name__}")
            return []
        issues = visual.to_issues(todo, items)
        bad = {i["path"] + "|" + i["name"] for i in issues}
        cache.update(s["sha"] for s in todo if s["path"] + "|" + s["label"] not in bad)
        stats["visual"] = [{"label": s["label"], "caption": s.get("caption", "")} for s in todo]
        self._emit("visual", {"shots": len(todo), "issues": issues[:10]})
        self._event(f"視覺檢查：{len(todo)} 張畫面，{len(issues)} 個需要修正" if issues else f"視覺檢查通過（{len(todo)} 張畫面）")
        return issues

    async def _acceptance(self, query: str, final_text: str, stats: dict, turn_start: int):
        """The model-based check for the quality level of this turn. Returns "retry" (a fix list went back to the
        agent), an error_info dict (still not acceptable after the allowed rounds), or None (accepted / skipped)."""
        from . import review
        if "quality" not in stats:
            level, why = review.quality_level(self.config, self._turn_messages(turn_start), stats,
                                              self._complex_task(stats.get("explicit_skills")))
            if self.depth > 0:                # sub-agents: their coordinator (and its reviewer) judges the result
                level, why = "fast", "子代理"
            stats["quality"] = (level, why)
            if level != "fast":
                self._event(f"品質等級：{ {'standard': '標準', 'strict': '嚴謹'}[level] }（{why}）")
        level, _ = stats["quality"]
        if level == "fast":
            return None
        stats["turn_start"] = turn_start
        rounds = {"standard": 1, "strict": 2}[level]
        data = await self._review(query, final_text, stats, level)
        if data is None and not stats.get("reviewer_fallback") and \
                re.search(r"VERDICT:\s*revise", stats.get("reviewer") or "", re.I):
            stats["reviewer_fallback"] = True
            # the judge could not run, but the independent reviewer asked for changes: its findings stand
            # (the thorium run passed as done although the reviewer had said revise)
            data = {"verdict": "revise", "items": [], "notes": [],
                    "summary": "獨立審查員要求修正（驗收審查無法執行）",
                    "problems": [{"what": stats["reviewer"].split("VERDICT:")[-1].strip()[:300],
                                  "evidence": "獨立審查員報告", "fix": stats["reviewer"][:1200]}]}
        if data is None:                      # the reviewer could not run: fall back to the light completion check
            verdict = await self._verify(query, final_text, stats) if self._should_verify(stats) else None
            if verdict and not verdict.get("complete"):
                missing = str(verdict.get("missing") or "任務尚未完成")[:400]
                if stats["verify_rounds"] < 1:
                    stats["verify_rounds"] += 1
                    self._event(f"完成檢查未通過：{missing}；繼續執行")
                    self.history.append(Message("user", f"[自動完成檢查] 任務還沒有完成：{missing}\n"
                                                        "請接著把它完成；如果確實無法完成，請明確說明原因與目前的進度。"))
                    self._persist()
                    return "retry"
                return {"summary": f"完成檢查未通過：{missing}", "hint": "可以回覆「繼續」讓我接著做，或補充更明確的要求。",
                        "detail": ""}
            if verdict:
                self._event("完成檢查通過")
            return None
        stats["review"] = data
        self._emit("review", {"level": level, "verdict": data["verdict"], "summary": data.get("summary", ""),
                              "problems": data.get("problems", [])[:10], "notes": (data.get("notes") or [])[:5]})
        if data["verdict"] == "pass":
            self._event("驗收審查通過" + (f"：{data['summary']}" if data.get("summary") else ""))
            return None
        fixes = review.fix_list(data, checklist_tools.get(self.ctx))
        if stats["verify_rounds"] < rounds:
            stats["verify_rounds"] += 1
            self._event(f"驗收審查未通過（第 {stats['verify_rounds']} 輪）：{data.get('summary') or '有問題需要修正'}")
            self.history.append(Message("user", "[自動驗收審查] 交付前的審查發現以下問題，請修正後再回報：\n" + fixes
                                        + "\n只處理這些會影響正確性或需求的問題，不要額外擴大範圍。"))
            self._persist()
            return "retry"
        return {"summary": "驗收審查未通過：" + (data.get("summary") or "還有問題沒有解決"), "detail": fixes,
                "hint": "可以回覆「繼續」讓我接著修，或告訴我哪些可以接受。"}

    async def _review(self, query: str, final_text: str, stats: dict, level: str) -> dict | None:
        """Fresh-context review by the judge (reviewer, else thinking, else execution model): verdict, items, fixes."""
        from . import review
        paths = []
        for a in stats.get("artifacts", [])[:20]:
            try:
                paths.append(resolve_file(self.config, a["path"]))
            except Exception:  # noqa: BLE001
                continue
        shots: dict = {}
        if paths and any(p.suffix.lower() in (".html", ".htm") for p in paths) and self.config.get("model.vision", True):
            from . import qa
            try:
                await qa.check([p for p in paths if p.suffix.lower() in (".html", ".htm")][:3], shots)
            except Exception:  # noqa: BLE001
                shots = {}
        items = checklist_tools.get(self.ctx)
        turn = self._turn_messages(stats.get("turn_start", 0))
        log_text = review.work_log(turn)
        unbacked = review.unbacked_claims(final_text, turn)
        reviewer = ""
        if level == "strict" and self.depth == 0 and self.config.get("agent.reviewer", True) and "reviewed" not in stats:
            stats["reviewed"] = True
            reviewer = await self._independent_review(query, final_text, items, paths, log_text,
                                                      review.sources_read(turn))
            fake = review.unbacked_claims(reviewer, turn)
            if fake:
                # the reviewer vouched for checks nobody ran: its pass is not evidence
                reviewer += ("\n\n[系統核對] 審查員宣稱以下檢查通過，但本輪工作紀錄沒有執行過，這些宣稱不可採信，"
                             "其 VERDICT 也不能當作通過的證據：\n" + "\n".join(f"- {x}" for x in fake))
                self._event("審查員的部分宣稱沒有工作紀錄支持，已標示為不可採信")
            stats["reviewer"] = reviewer
        elif stats.get("reviewer"):
            reviewer = stats["reviewer"] + "\n(The agent has since tried to fix these; check the current files.)"
        steers = [m.text() for m in self.history if m.role == "user" and isinstance(m.content, str)
                  and m.content.startswith("[使用者在執行中補充指示]")]
        prompt = review.review_prompt(query=query, steers=steers, checklist=checklist_tools.render(items),
                                      qa_text="", files=review.excerpts(paths), answer=final_text,
                                      reviewer=reviewer, level=level, work_log=log_text,
                                      unbacked="\n".join(unbacked))
        parts: list = [TextPart(prompt)]
        for path, img in list(shots.items())[:3]:
            parts += [TextPart(f"Screenshot of {Path(path).name} as the user sees it:"),
                      ImagePart.from_bytes(img, "image/jpeg")]
        model = self.rt.judge()
        self._event(f"驗收審查：由 {model.label} 以全新上下文檢查結果")
        deadline = float(self.config.get("agent.model_deadline", 240)) or None
        try:
            resp = await asyncio.wait_for(model.chat([Message("user", parts)], None, temperature=0,
                                                     max_tokens=1500), deadline)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a failed review never blocks the answer, and is never hidden
            why = "逾時" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
            log.info("acceptance review skipped: %s", why)
            stats.setdefault("quality_skipped", []).append(f"驗收審查（{model.label}）: {why}")
            self._event(f"⚠︎ 驗收審查：{model.label} 無法完成（{str(why)[:120]}），這次結果沒有經過驗收審查")
            return None
        return review.parse(resp.message.text())

    async def _independent_review(self, query: str, final_text: str, items: list, paths: list,
                                  log_text: str = "", sources: list | None = None) -> str:
        """Strict level: a read-only reviewer sub-agent re-checks sources and files before the acceptance review."""
        from . import review
        from ..work.agents import run_subagent
        try:
            from ..work.hub import get_hub
            hub = get_hub(self.rt)
        except Exception:  # noqa: BLE001
            hub = None
        self._event("獨立審查：派出審查員重新核對來源與檔案")
        try:
            res = await run_subagent(self.rt, role="reviewer", board_scope=self.ctx.state["board_scope"],
                                     task=review.reviewer_task(query=query, checklist=checklist_tools.render(items),
                                                               files=[str(p) for p in paths], answer=final_text,
                                                               work_log=log_text, sources=sources),
                                     parent_session=self.session_id, emit=self._emit, hub=hub,
                                     provider=self.rt.reviewer(),
                                     max_iterations=int(self.config.get("agent.reviewer_iterations", 15)))
        except Exception as e:  # noqa: BLE001
            log.info("independent review failed: %s", e)
            return ""
        return str(res.get("result") or "")[:6000]

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
        self._run_rec = None                   # this turn's run record (created right after turn_start)
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
                 "skill_gap": not best or best[0][1] < 1.0, "explicit_skills": explicit,
                 "best_skill": best[0][0].name if best and best[0][1] >= 1.0 and hasattr(best[0][0], "name") else None}
        self._emit("turn_start", {"session_id": self.session_id, "input": query})
        # the run record exists from the first moment, so a turn that dies while it plans or recalls is still found
        # (and resumed) by the recovery check instead of vanishing
        from ..work.chatrun import ChatRun
        self._run_rec = ChatRun(self, query, _title(query))
        if self._run_rec.rid and not self.ctx.state.get("job_run"):
            self.ctx.state["run_id"] = self._run_rec.rid

        context_block = await self._build_context(query)
        final_text = ""
        # no fixed step limit: progress decides when the task is stuck (agent/progress.py). A caller may still
        # give an explicit limit (a sub-agent's or reviewer's budget); 300 steps is the safety valve for everything
        self._progress = progress_mod.Tracker(self.config)
        max_iter = int(self.max_iterations) if self.max_iterations else 0
        safety_max = self._progress.safety_max
        self._prov = self._provider_override or self.rt.provider
        try:                                   # the model's output limit, once per model (U8); never blocks long
            from ..providers import limits
            await limits.discover(getattr(self._prov, "primary", self._prov))
        except Exception:  # noqa: BLE001
            pass
        max_tokens = base_out = self._max_tokens()     # only the default is reserved from the context window
        low_effort_left = 0
        size_tip = None
        empty_replies = 0
        overflow_retried = False
        nudge = False
        tool_specs = self._tool_specs()
        status, error_info, started, steps = "done", None, time.time(), 0
        stats["verify_rounds"] = 0
        stats["gate_rounds"] = {}
        self._checklist_turn_start()
        if self._should_plan(query, explicit, stats):
            await self._guarded("規劃", self._plan(query, context_block, stats), stats)
        self._progress.set_checklist(checklist_tools.get(self.ctx))
        stats["compactions"] = 0
        # a large task runs as milestones (agent/milestones.py): from the plan, or an unfinished plan resumed
        self._msr = None
        if self.depth == 0:
            from . import milestones as ms_mod
            from .milestone_runner import MilestoneRunner
            plan_ms = (stats.get("plan") or {}).get("milestones")
            if plan_ms:
                plan_ms["ask_user"] = (stats.get("plan") or {}).get("ask_user")
                self._msr = MilestoneRunner(self, ms_mod.Plan(plan_ms), query)
                await self._guarded("里程碑", self._msr.begin(stats), stats)
            elif not stats.get("plan") and self._resumes_milestones(query):
                self._msr = MilestoneRunner.resume(self, query)
                if self._msr:
                    await self._guarded("里程碑", self._msr.begin(stats, resumed=True), stats)
            if self._msr:
                tool_specs = self._tool_specs()
                self._progress.set_checklist(checklist_tools.get(self.ctx))
        # the task's budget: baseline for its type (or synthetic), soft checkpoints, the per-task cap (agent/budget.py)
        stats["query"] = query
        self._guard = None
        if self.depth == 0 and budget_mod._cfg(self.config)["enabled"]:
            try:
                self._guard = budget_mod.Guard(self, stats)
            except Exception:  # noqa: BLE001 - no budget is better than a broken turn
                log.exception("budget guard unavailable")
        self._turn_started = started
        self._snap = None
        if self.depth == 0:
            if self.config.get("agent.snapshots", True):
                self._snap = (self.session_id, f"t{int(started * 1000)}")
                self.rt.spawn(self._prune_snapshots())
            _SNAPSHOT.set(self._snap)
        else:
            self._snap = _SNAPSHOT.get()
        stats["artifacts"] = []

        hit_limit = False
        for iteration in itertools.count():
            if max_iter and iteration >= max_iter:
                hit_limit = True
                break
            if iteration >= safety_max:
                stats["step"] = iteration
                detail = (f"單一 Agent 已執行 {iteration} 步（agent.progress.safety_max_steps={safety_max}）。"
                          "正常情況下任務應早已因完成、被判定卡住或拆成里程碑而結束，這代表進度偵測或規劃有缺陷")
                self._stop_control(stats, "safety_valve", detail)
                status = "failed"
                error_info = {"summary": f"已達安全閥（{safety_max} 步），任務被強制停止", "kind": "safety_valve",
                              "category": "system_bug", "detail": detail,
                              "hint": "這是系統異常，已保存失敗報告（設定 →「品質」）供改進。可以回覆「繼續」接著做。"}
                final_text = f"⚠︎ 任務被安全閥停止：已執行 {iteration} 步。已完成的部分已保留。"
                self.history.append(Message("assistant", f"[上一次嘗試被安全閥停止：已執行 {iteration} 步]"))
                break
            steps = iteration + 1
            stats["step"] = steps
            self._step_mark = self._cost_mark()
            self._step_tools_s = 0.0
            if self._msr and not self._cancel:
                await self._guarded("里程碑", self._msr.before_step(steps, stats), stats)
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
            budget = self._context_budget(base_out)
            context_block = self._render_context()
            if estimate_tokens([self.system] + self.history, tool_specs, context_block) > budget * 0.8:
                if await self._memory_diet():
                    context_block = self._render_context()
                if estimate_tokens([self.system] + self.history, tool_specs, context_block) > budget * 0.8:
                    turn_start = await self._compact(budget, tool_specs, context_block, turn_start)
                    stats["compactions"] += 1
                    stats["step"] = steps
                    if steps > 1 and not self._checklist_done():     # compacted by this turn's own work
                        self._request_consult(
                            "里程碑內發生上下文壓縮", stats, "health_compaction",
                            f"這個回合第 {stats['compactions']} 次壓縮上下文（第 {steps} 步），檢視是否需要把工作拆小")
            stats["step"] = steps
            if stats.get("consult_pending"):
                await self._consult(stats.pop("consult_pending"), context_block, stats)
                if stats.get("give_up"):
                    self._stop_control(stats, "thinking_stop", stats["give_up"])
                    status = "failed"
                    error_info = {"summary": "執行模型無法完成這個任務", "kind": "capability",
                                  "detail": stats["give_up"],
                                  "hint": "已保存失敗報告（設定 →「品質」）。可以換用能力更強的執行模型，或補充更明確的做法後再試。"}
                    final_text = f"✕ 任務失敗：{stats['give_up']}"
                    self.history.append(Message("assistant", f"[上一次嘗試失敗：{stats['give_up']}]"))
                    break
            # The history is append-only so the provider's prompt cache keeps hitting; everything that changes
            # between requests (context, checklist, active skill notes, nudges) goes in one tail message that is
            # rebuilt every time and never stored - at the end, where the model attends most during long tasks.
            request = [self.system] + self.history
            tail = [context_block, self._active_skill_notes(), self._checklist_tail(stats, steps),
                    self._msr.panel() if self._msr else ""]
            if nudge:
                tail.append(TRUNCATED_NUDGE if nudge == "length" else EMPTY_NUDGE)
                nudge = False
            if size_tip:
                tail.append(size_tip)
                size_tip = None
            request.append(Message("user", [TextPart("\n\n".join(t for t in tail if t), volatile=True)]))
            hs = self._progress.health_steps
            ref = steps - (self._msr.step0 if self._msr else 0)      # counted per milestone
            if hs and ref > 1 and (ref - 1) % hs == 0:
                items = checklist_tools.get(self.ctx)
                if items and checklist_tools.open_items(items):
                    self._request_consult(f"已執行 {ref - 1} 步仍未通過驗收", stats, "health_steps",
                                          f"{'目前里程碑' if self._msr else '這個回合'}已執行 {ref - 1} 步，驗收清單尚未完成"
                                          f"（健康水位每 {hs} 步檢查一次），檢視是否需要原地拆分")
                elif not items and not self._progress.produced_within(hs):
                    self._request_consult(f"已執行 {steps - 1} 步沒有任何產出", stats, "health_steps",
                                          f"已執行 {steps - 1} 步，沒有驗收清單，最近 {hs} 步也沒有寫出任何檔案"
                                          f"（健康水位每 {hs} 步檢查一次）")
            self._emit("thinking", {"iteration": iteration, "step": steps, "model": self._prov.label})
            t_call = time.monotonic()
            effort_token = REASONING_EFFORT.set("low") if low_effort_left > 0 else None
            if effort_token is not None:
                low_effort_left -= 1
            try:
                resp = await self._step_chat(request, tool_specs, max_tokens, stats)
                stats["last_call_s"] = time.monotonic() - t_call
            except Exception as e:
                if effort_token is not None:
                    REASONING_EFFORT.reset(effort_token)
                    effort_token = None
                for qe in current_scope.get().drain_quota_events():
                    if qe.get("window") == "6h":
                        self.rt.memory.mark_quota(100)
                    self._emit("quota", {**qe, "stopped": True})
                if OVERFLOW_RE.search(str(e)) and not overflow_retried:
                    # the estimate was too optimistic for this model: compact hard and try once more
                    overflow_retried = True
                    log.warning("context overflow (%s); compacting and retrying", str(e)[:200])
                    self._budget_cap = self._overflow_budget(str(e), budget, max_tokens,
                                                             estimate_tokens(request, tool_specs, context_block))
                    budget = self._context_budget(base_out)
                    before = len(self.history)
                    dieted = await self._memory_diet()
                    context_block = self._render_context()
                    turn_start = await self._compact(budget, tool_specs, context_block, turn_start,
                                                     force=True, keep_ratio=0.25, reason="overflow")
                    stats["compactions"] = stats.get("compactions", 0) + 1
                    if dieted or self._last_compaction_changed or len(self.history) != before:
                        continue
                from .errors import explain_error
                error_info = explain_error(e)
                status = "error"
                try:                      # the step that failed still cost time (and retries): keep it in the trace
                    self._progress.trace.append({"step": steps, "progress": False, "strong": False, "file_only": 0,
                                                 "why": f"模型呼叫失敗：{error_info['summary']}", "kind": None,
                                                 "waiting": False, "failed": True, "cost": self._step_cost()})
                    stats["progress_trace"] = self._progress.trace
                except Exception:  # noqa: BLE001
                    pass
                final_text = f"⚠︎ 任務中斷：{error_info['summary']}。{error_info['hint']}"
                # keep a short note (not the raw provider error) so the next turn knows this attempt failed
                self.history.append(Message("assistant", f"[上一次嘗試失敗：{error_info['summary']}]"))
                log.error("model call failed: %s", e)
                break
            for ce in current_scope.get().drain_compat_events():  # a call adapted to what the model accepts
                self._event(f"模型 {ce['model']} 不接受參數 {ce['param']}：已自動改用相容的設定重送，之後不再送這個參數")
            for qe in current_scope.get().drain_quota_events():   # quota used up; a fallback model answered
                if qe.get("window") == "6h":
                    self.rt.memory.mark_quota(100)
                self._emit("quota", {**qe, "stopped": False, "answered_by": resp.model})
            self.usage.add(resp.usage)
            msg = resp.message
            if effort_token is not None:
                REASONING_EFFORT.reset(effort_token)
                effort_token = None
            log.info("model %s replied: %d chars, %d tool calls, finish=%s", resp.model, len(msg.text()),
                     len(msg.tool_calls), resp.stop_reason)
            if not msg.tool_calls and not msg.text().strip():
                # Empty reply: usually the output budget was spent on hidden reasoning (finish=length) or a weak
                # model stalled after a tool result. Never accept it as the final answer.
                empty_replies += 1
                truncated = str(resp.stop_reason).lower() in ("length", "max_tokens", "max_output_tokens")
                stats.setdefault("empty_replies", []).append(
                    {"step": steps, "finish": resp.stop_reason or "", "max_tokens": max_tokens,
                     "output_tokens": getattr(resp.usage, "output_tokens", None),
                     "seconds": round((stats.get("last_call_s") or 0), 1)})
                if truncated:
                    max_tokens = self._more_room(max_tokens, request, tool_specs, context_block)
                    # U3: the hidden thinking filled the limit - think less on the next steps (E1: 0/8 -> 3/3)
                    low_effort_left = int(self.config.get("agent.low_effort_steps", 5))
                    stats["low_effort"] = stats.get("low_effort", 0) + 1
                if empty_replies <= 2:
                    self._emit("status", {"message": f"模型回傳空白回覆（finish_reason={resp.stop_reason or 'n/a'}），"
                                                     f"{f'輸出上限放寬到 {max_tokens}、接下來幾步降低思考，並請它分段寫檔' if truncated else '要求繼續'}"})
                    nudge = "length" if truncated else True
                    continue
                final_text = (f"⚠︎ 模型 {resp.model or self._prov.label} 連續 {empty_replies} 次回傳空白回覆"
                              f"（finish_reason={resp.stop_reason or 'n/a'}），任務沒有完成，已保存失敗報告。建議換用能力較強的執行模型，"
                              "或在設定頁提高輸出上限後重試。")
                self.history.append(Message("assistant", "[上一次嘗試失敗：模型沒有產生回覆]"))
                status = "failed"
                error_info = {"summary": f"執行模型連續 {empty_replies} 次回傳空白回覆", "kind": "capability",
                              "hint": "已保存失敗報告。建議換用能力較強的執行模型，或在設定頁提高輸出上限後重試。",
                              "detail": f"model={resp.model}, finish_reason={resp.stop_reason or 'n/a'}"}
                break
            self.history.append(msg)
            size_tip = size_reminder(msg, resp, max_tokens)
            if size_tip:
                stats["size_reminders"] = stats.get("size_reminders", 0) + 1
            if msg.tool_calls:
                self._persist()
            if msg.text():
                self._emit("assistant", {"text": msg.text(), "final": not msg.tool_calls})
            if not msg.tool_calls:
                final_text = msg.text()
                if self._msr:
                    note = await self._guarded("里程碑", self._msr.on_final(stats), stats)
                    if note:
                        self.history.append(Message("user", note))
                        self._persist()
                        continue
                gate = None if stats.get("consecutive_errors") else await self._guarded("成果檢查", self._quality_gate(stats, query, final_text), stats)
                if gate:
                    kind, text = gate
                    if stats["gate_rounds"].get(kind, 0) < int(self.config.get("agent.quality_rounds", 2)):
                        stats["gate_rounds"][kind] = stats["gate_rounds"].get(kind, 0) + 1
                        self.history.append(Message("user", text))
                        self._persist()
                        continue
                    status = "incomplete"
                    error_info = {"summary": text.split("\n")[0].strip("[] "), "detail": text,
                                  "hint": "可以回覆「繼續」讓我接著修，或告訴我哪些可以不用管。"}
                elif not stats.get("consecutive_errors"):
                    stats["final_text"] = final_text
                    outcome = await self._guarded("驗收審查", self._acceptance(query, final_text, stats, turn_start), stats)
                    if outcome == "retry":
                        continue
                    if isinstance(outcome, dict):
                        status, error_info = "incomplete", outcome
                if stats.get("consecutive_errors"):
                    # the model stopped right after failures: do not report the task as done
                    status = "incomplete"
                    error_info = {"summary": "最後的操作失敗，任務可能沒有完成",
                                  "hint": "請看最後一個 ✕ 的錯誤訊息，或回覆「繼續」讓我換個方法再試。",
                                  "detail": stats.get("last_error", "")}
                break
            self._step_calls = []
            for call in msg.tool_calls:
                result_msg = await self._execute(call, stats)
                self.history.append(result_msg)
            verdict = self._observe_step(steps, stats)
            if self._msr:
                await self._guarded("里程碑", self._msr.after_step(steps, stats), stats)
            if self._guard and await self._guarded("預算", self._guard.check(stats), stats) == "cap" \
                    and not await self._task_cap(stats):
                status = "incomplete"
                error_info = {"summary": f"已達每個任務的花費上限 {self._guard.cap_text()}，任務已暫停", "kind": "budget",
                              "detail": f"已用 {self._guard.spent()['tokens']:,} tokens／"
                                        f"{budget_mod.money(self._guard.spent()['cost_usd'])}",
                              "hint": "已完成的部分整理在下面。回覆「繼續」會接著做；也可以在設定 →「用量」調整每個任務的上限。"}
                final_text = f"⏸ 任務已暫停：已達每個任務的花費上限 {self._guard.cap_text()}。"
                self.history.append(Message("assistant", final_text))
                break
            self._persist()
            limit = int(self.config.get("agent.max_consecutive_errors", 6))
            hard = stats.get("consecutive_errors", 0) >= limit or stats.get("repeats", 0) >= 3
            stop_why = None
            if hard:
                trig = "consecutive_errors" if stats.get("consecutive_errors", 0) >= limit else "repeated_failed_call"
                why = (f"連續 {stats['consecutive_errors']} 次工具呼叫失敗" if trig == "consecutive_errors"
                       else "重複執行已失敗的相同操作")
                detail = (f"連續 {stats.get('consecutive_errors', 0)} 次工具失敗（硬上限 agent.max_consecutive_errors={limit}）"
                          if trig == "consecutive_errors" else
                          f"同一個已失敗的呼叫被重複了 {stats.get('repeats', 0)} 次（上限 3）") + \
                    f"；最後的錯誤：{stats.get('last_error', '')[:160]}"
                if self._request_consult(why, stats, trig, detail):
                    # one more chance with the thinking model's advice before the task is given up
                    stats["consecutive_errors"], stats["repeats"] = 0, 0
                    continue
                stop_why = (trig, why, detail)
            elif verdict.get("loop"):
                lp = verdict["loop"]
                if not self._request_consult(progress_mod.TRIGGERS[lp["trigger"]], stats, lp["trigger"], lp["detail"]) \
                        and self._consult_refusal(stats) == "max":
                    if lp.get("soft"):
                        # information keeps coming, so this is no reason to give up: remind the execution model
                        # once per gap instead (the advice is used up)
                        self._progress.advised(steps)
                        self.history.append(Message("user", f"[進度提醒] {lp['detail']}。請先產出最重要的一項"
                                                            "（寫出檔案或完成一個清單項目），已查到的資料就夠用時不要再查。"))
                        self._persist()
                    else:
                        stop_why = (lp["trigger"], progress_mod.TRIGGERS[lp["trigger"]], lp["detail"])
            elif stats.get("consecutive_errors", 0) >= int(self.config.get("agent.consult_after_errors", 3)) \
                    or stats.get("repeats"):
                trig = "consecutive_errors" if stats.get("consecutive_errors") else "repeated_failed_call"
                self._request_consult(f"連續 {stats.get('consecutive_errors', 0)} 次工具錯誤"
                                      if stats.get("consecutive_errors") else "重複執行已失敗的操作", stats, trig,
                                      f"連續 {stats.get('consecutive_errors', 0)} 次工具失敗（門檻 agent.consult_after_errors="
                                      f"{self.config.get('agent.consult_after_errors', 3)}）：{stats.get('last_error', '')[:160]}"
                                      if trig == "consecutive_errors" else "重複執行已失敗的相同操作")
            elif self._progress.no_progress >= self._progress.k:
                t = self._progress
                detail = (f"連續 {t.no_progress} 步沒有進度（門檻 K={t.k}）；最後一次有進度是第 {t.last_progress_step} 步"
                          if t.last_progress_step else f"連續 {t.no_progress} 步沒有進度（門檻 K={t.k}），本回合尚無任何進度")
                if not self._request_consult(progress_mod.TRIGGERS["no_progress"], stats, "no_progress", detail) \
                        and self._consult_refusal(stats) == "max":
                    stop_why = ("no_progress", "諮詢次數用完後仍沒有進度", detail)
            if stop_why:
                trig, why, detail = stop_why
                self._stop_control(stats, "advice_exhausted",
                                   f"{progress_mod.TRIGGERS.get(trig, trig)}：{detail}；思維模型的諮詢已用完"
                                   f"（{stats.get('consults', 0)}/{self.config.get('agent.consult_max', 2)}）")
                # stuck after the advice allowed: the execution model could not do it
                status = "failed"
                error_info = {"summary": why + "，執行模型無法完成這個任務", "kind": "capability", "trigger": trig,
                              "hint": "已保存失敗報告（設定 →「品質」）。可以換用能力更強的執行模型，或回覆更明確的做法後再試。",
                              "detail": stats.get("last_error", "") or detail}
                final_text = (f"⚠︎ 任務沒有完成：{why}。" + (f"最後的錯誤：\n{stats.get('last_error', '')[:400]}"
                                                             if stats.get("last_error") else detail))
                self.history.append(Message("assistant", f"[上一次嘗試失敗：{why}。{stats.get('last_error', '')[:300] or detail[:300]}]"))
                break
        if self._msr and stats.get("ms_incomplete") and status == "done":
            status = "incomplete"
            error_info = {"summary": f"還有 {len(stats['ms_incomplete'])} 個里程碑沒有完成",
                          "detail": "、".join(stats["ms_incomplete"]),
                          "hint": "回覆「繼續」會從第一個未完成的里程碑接著做。"}
        if self._msr:
            self._msr.end(status)
        if hit_limit:
            status = "incomplete"
            error_info = {"summary": f"已達這個子任務指定的步驟上限（{max_iter} 步）",
                          "hint": "可以回覆「繼續」讓我接著做。", "detail": ""}
            final_text = "（步驟用完，任務尚未完成）"
            self.history.append(Message("assistant", final_text))
        if status in ("incomplete", "failed", "error", "blocked") and self.depth == 0:
            # hand over what is finished: accepted milestones and their outputs, files, what is left
            pd = budget_mod.partial_delivery(self, stats, status, (error_info or {}).get("summary") or status)
            if pd:
                stats["partial"] = pd
                final_text = (final_text + "\n\n" if final_text else "") + pd["text"]
                self.history.append(Message("assistant", pd["text"]))
                self._control(stats, "budget" if (error_info or {}).get("kind") == "budget" else "delivery",
                              "partial_delivery",
                              f"交付 {len(pd['done'])} 個已完成的里程碑、{len(pd['files'])} 個檔案；尚有 "
                              f"{len(pd['left']) + len(pd['open_items'])} 項未完成（{pd['reason'][:120]}）",
                              decision=status)
                self._emit("partial_delivery", pd)

        self._persist()
        try:      # the last step (the answer, its checks) made no tool call, so no verdict recorded its cost
            tr = self._progress.trace
            if steps and (not tr or tr[-1].get("step") != steps) and getattr(self, "_step_mark", None):
                tr.append({"step": steps, "progress": False, "strong": False, "file_only": 0, "output": False,
                           "why": "最後的回答" if status == "done" else f"結束（{status}）", "kind": None,
                           "waiting": False, "final": True, "cost": self._step_cost()})
                stats["progress_trace"] = tr
        except Exception:  # noqa: BLE001
            pass
        turn_usage = usage_scope.as_dict()
        session_usage = self.rt.memory.usage_totals(session_id=self.session_id)
        stats["status"] = status
        self.last_status = {"status": status, "error": error_info}
        turn_skills = list(explicit)
        for m in self._turn_messages(turn_start):
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
        if status != "done":
            self._save_progress(status, error_info, final_text)
        failure = None
        if status in ("failed", "incomplete", "error") and self.depth == 0 and \
                self.config.get("agent.failure_reports", True):
            failure = self._failure_report(status, error_info, final_text, query, stats, turn_start, turn_id,
                                           turn_usage, steps, round(time.time() - started, 1))
            if failure:
                # the control log points at the report, so every stop (the safety valve too) can be traced to it
                stop = next((e for e in reversed(stats.get("control_log") or []) if e.get("event") == "stop"), None)
                progress_mod.append_log(self.config.home, {
                    **progress_mod.entry("report", (stop or {}).get("trigger") or (error_info or {}).get("kind") or status,
                                         steps, f"失敗報告 {failure}（{(error_info or {}).get('summary') or status}）", None),
                    "failure_id": failure, "status": status, "session_id": self.session_id, "depth": self.depth,
                    "run_id": getattr(getattr(self, "_run_rec", None), "rid", None),
                    "model": getattr(getattr(self, "_prov", None), "label", "")})
        run_report = None
        if self.depth == 0 and self.config.get("agent.run_reports", True):
            run_report = self._run_report(status, error_info, final_text, query, stats, turn_start, turn_id,
                                          turn_usage, steps, round(time.time() - started, 1), failure)
        snap = None
        if self.depth == 0 and self._snap:
            from . import snapshots
            try:
                inf = snapshots.info(self.config.home, *self._snap)      # includes the sub-agents' changes
            except Exception:  # noqa: BLE001
                inf = None
            if inf and inf["files"]:
                snap = {"session": self._snap[0], "key": self._snap[1], "files": len(inf["files"])}
        package_hint = False
        if self.depth == 0 and status == "done":
            try:
                from ..skills.package import worth_packaging
                package_hint = worth_packaging(
                    depth=self.depth, status=status, steps=steps, checklist=len(checklist_tools.get(self.ctx) or []),
                    outputs=len(stats.get("artifacts") or []), skills_used=turn_skills, query=query,
                    mgr=getattr(self.rt, "skills", None))
            except Exception:  # noqa: BLE001
                package_hint = False
        self._emit("turn_end", {"text": final_text, "status": status, "error": error_info, "steps": steps,
                                "snapshot": snap, "failure": failure, "run_report": run_report,
                                "plan": bool(stats.get("plan")),
                                "consults": len(stats.get("consult_log") or []),
                                "milestones": self._msr.plan.card({}, status)["milestones"] if self._msr else None,
                                "budget": self._guard.summary() if self._guard else None,
                                "partial": stats.get("partial"),
                                "thinking_skipped": stats.get("thinking_skipped") or [],
                                "turn_id": turn_id, "skills": turn_skills,
                                "duration_s": round(time.time() - started, 1), "usage": turn_usage,
                                "session_cost_usd": session_usage["cost_usd"],
                                "session_tokens": session_usage["total_tokens"],
                                "artifacts": stats.get("artifacts", [])[-30:],
                                "quality_skipped": stats.get("quality_skipped") or [],
                                "package_hint": package_hint})
        if getattr(self, "_run_rec", None):
            if self._run_rec.rid:
                self._run_rec.state["usage"] = budget_mod.usage_record(self._guard, stats, turn_usage, steps)
                if self._guard:
                    self._run_rec.state["budget"] = self._guard.summary()
                if stats.get("partial"):
                    self._run_rec.state["partial"] = stats["partial"]
            self._run_rec.finish(status, final_text, error_info, turn_usage.get("cost_usd", 0), turn_id)
        bs = self.rt.budget_status()
        if bs["warn"]:
            self._emit("budget", bs)
        self.last_turn_usage = turn_usage
        self._maybe_learn(turn_start, stats)
        if self.depth == 0 and "memory_save" in self.tool_names:
            from ..memory.maintain import maybe_maintain
            maybe_maintain(self.rt, reason="turn", emit=self._emit)
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
        aux_spec = (self.config.get("model.auxiliary") if self.rt.inline_aux() is self.rt.aux else None) \
            or self.config.get("model.default")
        aux_window, _ = context_window(self.config, aux_spec)
        try:
            res = await compact([self.system] + self.history, self.rt.inline_aux(), budget=budget, tools=tool_specs,
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
        if res.summary and self.depth == 0 and self.config.get("memory.flush_on_compact", True) \
                and "memory_save" in self.tool_names:
            # what is summarised away stays recallable: its key facts become short-term memories
            from ..memory.maintain import flush_to_memory
            self.rt.spawn(flush_to_memory(self.rt, self.session_id, res.summary))
            self._event("對話接近模型上限：較早內容已摘要，重點存入短期記憶")
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

    def _model_label(self) -> str:
        prov = getattr(self, "_prov", None) or getattr(getattr(self, "rt", None), "provider", None)
        return str(getattr(prov, "label", "") or self.config.get("model.default") or "")

    def _max_tokens(self) -> int:
        """The output limit of an ordinary step (proposal U8): the model's default from providers/limits.py - the
        user's model.max_output_tokens cut to the model's max, or half its max (at most 32k), or, for a model nobody
        knows, the old fixed values (reasoning models spend the budget on hidden thinking, so they get more)."""
        label = self._model_label()
        n = int(self.config.get("model.max_tokens", 8192))
        if REASONING_MODEL_RE.search(label):
            n = max(n, int(self.config.get("model.reasoning_max_tokens", 16384)))
        if local.is_local_spec(label):
            return min(n, max(1024, local.num_ctx(self.config) // 4))
        from ..providers import limits
        return limits.default_output(self.config, label, n)

    def _more_room(self, max_tokens: int, request, tool_specs, context_block: str) -> int:
        """The output limit after a reply was cut off at it: the model's max (U8) where known, else twice the limit
        (at most 32k) - never more than the context window leaves beside this request."""
        from ..providers import limits
        label = self._model_label()
        mx = limits.max_output(label)
        n = mx if mx and mx > max_tokens else min(max_tokens * 2, max(32768, max_tokens))
        window, _ = context_window(self.config, label)
        room = window - estimate_tokens(request, tool_specs, context_block) - max(1024, window // 50)
        return max(max_tokens, min(n, room))

    def _tool_specs(self) -> list:
        """Tool schemas for this turn: constrain skill names to an enum (agentskills.io client guidance) and drop
        activation tools entirely when no skill is visible."""
        import copy
        visible = sorted(s.name for s in self.rt.skills.visible(self.tool_names))
        specs = []
        for t in self.tools:
            if t.name == "milestone" and not getattr(self, "_msr", None):
                continue                      # only for a task that runs as milestones
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

    async def _task_cap(self, stats: dict) -> bool:
        """The hard cap per task is reached: ask to extend it (x agent.budget.cap_extend) or stop. True = go on."""
        g = self._guard
        sp = g.spent()
        detail = f"已用 {sp['tokens']:,} tokens／{budget_mod.money(sp['cost_usd'])}，達到每個任務的上限 {g.cap_text()}"
        ask = getattr(self.ctx, "ask", None)
        if ask is None or self.platform not in ("web", "cli"):
            self._control(stats, "budget", "budget_cap", detail + "；無人可確認，暫停並交付已完成的部分", decision="stop")
            return False
        f = g.cfg["cap_extend"]
        ans = await ask(f"{detail}。要追加上限繼續嗎？", [
            {"label": "停止並交付", "description": "整理已完成的部分交給你，之後可以回覆「繼續」", "recommended": True},
            {"label": f"追加 {f:g} 倍並繼續", "description": "上限提高後照計畫繼續"}], False, False)
        if ans and any(str(c).startswith("追加") for c in ans.get("choices") or []):
            g.extend()
            self._control(stats, "budget", "budget_extended", f"{detail}；使用者追加上限為 {g.cap_text()}", decision="extend")
            return True
        self._control(stats, "budget", "budget_cap", detail + ("；使用者選擇停止" if ans else "；沒有回覆，暫停"),
                      decision="stop")
        return False

    def _turn_messages(self, turn_start: int) -> list:
        """This turn's messages, including those of finished milestones that left the model context."""
        msr = getattr(self, "_msr", None)
        return (list(msr.archive) if msr else []) + self.history[turn_start:]

    def milestone_feedback(self, text: str, mode: str = "boundary") -> bool:
        """A correction to the milestone plan while the task runs: "boundary" (at the next milestone) or "now"."""
        msr = getattr(self, "_msr", None)
        if msr is None:
            return False
        msr.feedback(text, mode)
        self._emit("steer_applied", {"text": text, "milestone_mode": mode})
        return True

    def _resumes_milestones(self, query: str) -> bool:
        """Carry on an unfinished milestone plan: a resume, or the turn restored its open milestone checklist."""
        if query.lstrip().startswith(("[自動續跑]", "[續跑]")) or query.strip() in ("繼續", "继续", "continue"):
            return True
        return any(i.get("milestone") for i in checklist_tools.open_items(checklist_tools.get(self.ctx)))

    def _checklist_done(self) -> bool:
        items = checklist_tools.get(self.ctx)
        return bool(items) and not checklist_tools.open_items(items)

    @staticmethod
    def _cost_mark() -> dict:
        sc = current_scope.get()
        return {"calls": sc.calls, "in": sc.input_tokens, "out": sc.output_tokens, "cache": sc.cache_read_tokens,
                "usd": sc.cost_usd, "q6h": sc.quota_6h_pct, "model_s": sc.model_seconds, "retries": sc.retries,
                "t": time.monotonic()}

    def _step_cost(self) -> dict:
        """What this step cost: every model call made during it (the step itself, advice, checks), the time spent
        waiting for models and running tools, and the calls that had to be sent again."""
        a, b = getattr(self, "_step_mark", None) or self._cost_mark(), self._cost_mark()
        sc = current_scope.get()
        retried = b["retries"] - a["retries"]
        return {"calls": b["calls"] - a["calls"], "input": b["in"] - a["in"], "output": b["out"] - a["out"],
                "cache_read": b["cache"] - a["cache"], "tokens": (b["in"] - a["in"]) + (b["out"] - a["out"]),
                "usd": round(b["usd"] - a["usd"], 6), "quota_6h_pct": round(b["q6h"] - a["q6h"], 2),
                "model_s": round(b["model_s"] - a["model_s"], 1), "tools_s": round(getattr(self, "_step_tools_s", 0.0), 1),
                "wall_s": round(b["t"] - a["t"], 1), "retries": retried,
                "retry_errors": [r["error"][:100] for r in sc.retry_events[-retried:]] if retried > 0 else []}

    def _observe_step(self, step: int, stats: dict) -> dict:
        """The step's progress verdict (agent/progress.py); kept for the failure report and the run record."""
        from ..tools.files import resolve as resolve_path
        try:
            verdict = self._progress.observe(step, getattr(self, "_step_calls", None) or [],
                                             checklist_tools.get(self.ctx), lambda p: resolve_path(self.ctx, p))
        except Exception:  # noqa: BLE001 - progress bookkeeping must never break a task
            log.exception("progress check failed")
            return {}
        try:
            verdict["cost"] = self._step_cost()          # the same dict is the trace entry
        except Exception:  # noqa: BLE001
            log.debug("step cost failed", exc_info=True)
        stats["progress_trace"] = self._progress.trace
        rec = getattr(self, "_run_rec", None)
        if rec:
            rec.progress(self._progress.trace)
        return verdict

    async def _execute(self, call: ToolCall, stats: dict) -> Message:
        self._emit("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments})
        t0 = time.time()
        rec = getattr(self, "_run_rec", None)
        if rec:
            rec.tool_started(call.name)
        sk = (call.arguments or {}).get("name") if call.name in ("skill_view", "skill_run") else None
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
            if call.name in ("write_file", "edit_file") and getattr(self, "_snap", None):
                self._snapshot((call.arguments or {}).get("path"), stats)
            result = await REGISTRY.dispatch(call, self.ctx, self.tool_names)
        elapsed = round(time.time() - t0, 1)
        self._step_tools_s = getattr(self, "_step_tools_s", 0.0) + (time.time() - t0)
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
        calls = getattr(self, "_step_calls", None)
        if calls is not None:
            seen_text = preview + (f"\n[image {hash(image_b64) & 0xffffffff:08x}]" if image_b64 else "")   # a new picture is new
            calls.append((call.name, call.arguments or {}, seen_text, bool(result.is_error) and not denied))
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

    def _snapshot(self, path, stats: dict) -> None:
        """Keep the file as it is before this turn changes it (so the turn can be undone)."""
        if not path:
            return
        from . import snapshots
        try:
            from ..tools.files import resolve as resolve_write
            target = resolve_write(self.ctx, str(path))        # the same path write_file / edit_file will use
            if snapshots.take(self.config.home, *self._snap, target):
                stats["snapshot_files"] = stats.get("snapshot_files", 0) + 1
        except Exception:  # noqa: BLE001 - never block a write because the copy failed
            log.debug("snapshot failed for %s", path, exc_info=True)

    async def _prune_snapshots(self) -> None:
        from . import snapshots
        try:
            await asyncio.to_thread(snapshots.prune, self.config.home, float(self.config.get("agent.snapshot_days", 14)))
        except Exception:  # noqa: BLE001
            log.debug("snapshot prune failed", exc_info=True)

    def _save_progress(self, status: str, error_info: dict | None, final_text: str) -> None:
        """A task that stopped before it was done leaves a progress note on the blackboard, so the next turn (or
        a resumed run) knows what is finished, what is left and why it stopped."""
        items = checklist_tools.get(self.ctx)
        left = checklist_tools.open_items(items)
        if not left:
            return
        done = [i for i in items if i.get("status") == "done"]
        lines = [f"任務未完成（{status}）：{(error_info or {}).get('summary') or final_text[:200]}",
                 f"已完成 {len(done)}/{len(items)} 項：" + "；".join(i["text"][:60] for i in done[:8]),
                 "尚未完成：" + "；".join(f"#{i['id']} {i['text'][:80]}" for i in left[:10])]
        try:
            from ..work.hub import get_hub
            get_hub(self.rt).store.board_write(self.ctx.state["board_scope"], "note", "\n".join(lines),
                                               key="progress", author="agent")
        except Exception:  # noqa: BLE001
            log.debug("could not save progress note", exc_info=True)

    def _failure_report(self, status, error_info, final_text, query, stats, turn_start, turn_id, usage, steps,
                        duration) -> str | None:
        """Keep what is needed to understand a failed task (work log, plan, advice, checks, models); the thinking
        model's diagnosis is added in the background."""
        from . import failures
        try:
            rep = failures.save(self.config.home, failures.build(
                self, status=status, error_info=error_info, final_text=final_text, query=query, stats=stats,
                turn_start=turn_start, turn_id=turn_id, usage=usage, steps=steps, duration=duration))
        except Exception:  # noqa: BLE001 - the report must never break the turn
            log.exception("could not save the failure report")
            return None
        if self.config.get("agent.failure_diagnosis", True):
            self.rt.spawn(failures.diagnose(self.rt, self.config.home, rep["id"], emit=self._emit))
        return rep["id"]

    def _run_report(self, status, error_info, final_text, query, stats, turn_start, turn_id, usage, steps, duration,
                    failure) -> str | None:
        """Every task's cost and output (agent/runreport.py), finished or not."""
        from . import runreport
        try:
            rep = runreport.save(self.config.home, runreport.build(
                self, status=status, error_info=error_info, final_text=final_text, query=query, stats=stats,
                turn_start=turn_start, turn_id=turn_id, usage=usage, steps=steps, duration=duration,
                failure_id=failure))
        except Exception:  # noqa: BLE001 - the report must never break the turn
            log.exception("could not save the run report")
            return None
        return rep["id"]

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
        turn = self._turn_messages(turn_start)
        self._emit("status", {"message": "reflecting (learning in background)"})
        self.rt.spawn(reflect(self.rt, self.session_id, turn, stats, sorted(unrated), emit=self._emit))
