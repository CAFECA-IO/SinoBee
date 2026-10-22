"""Dependency-free tool registry: schema collection, availability, approval, dispatch."""
from __future__ import annotations

import asyncio
import inspect
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..types import ImagePart, TextPart, ToolCall, ToolResult, ToolSpec

log = logging.getLogger("sinobee.tools")
NEW_IN_V2 = {"work"}


@dataclass
class Tool:
    name: str
    toolset: str
    description: str
    parameters: dict
    handler: Callable[..., Any]
    writes: bool = False                                   # blocked in readonly mode
    dangerous: Callable[[dict], str | None] | None = None  # returns reason if needs approval
    check: Callable[[], bool] | None = None                # availability probe
    always_confirm: bool = False                           # ask even in auto mode / with skill pre-approval

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


# tools whose whole effect stays inside the sandbox when the tools run there (approval mode "sandbox")
CONTAINED = {"shell", "read_file", "write_file", "edit_file", "list_dir", "search_files", "skill_run"}


@dataclass
class ToolContext:
    """Everything a tool handler may need. Created per agent session."""
    config: Any
    session_id: str = ""
    memory: Any = None
    skills: Any = None
    working: dict = field(default_factory=dict)  # L1 working memory
    emit: Callable[[str, dict], Any] | None = None
    state: dict = field(default_factory=dict)    # per-session scratch for tools (browser handle, etc.)
    approve: Callable[[str, dict, str], Awaitable[bool]] | None = None
    # ask(question, options, multi, allow_text) -> {"choices": [...], "text": str} | None; None = nobody to ask
    ask: Callable[[str, list, bool, bool], Awaitable[dict | None]] | None = None


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self.tools[tool.name] = tool
        return tool

    def tool(self, name: str, toolset: str, description: str, parameters: dict | None = None, **kw):
        params = parameters or {"type": "object", "properties": {}}
        params.setdefault("type", "object")
        params.setdefault("properties", {})

        def deco(fn):
            self.register(Tool(name, toolset, description, params, fn, **kw))
            return fn
        return deco

    def available(self, config) -> list[Tool]:
        enabled = set(config.get("permissions.toolsets") or [])
        if int(config.get("permissions.toolsets_v") or 1) < 2:
            enabled |= NEW_IN_V2          # toolsets added later are on for settings saved before they existed
        out = []
        for t in self.tools.values():
            if t.toolset != "core" and t.toolset not in enabled:     # core tools (ask_user) are always there
                continue
            if t.check is not None:
                try:
                    if not t.check():
                        continue
                except Exception:
                    continue
            out.append(t)
        return out

    async def dispatch(self, call: ToolCall, ctx: ToolContext, allowed: set[str] | None = None) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None or (allowed is not None and call.name not in allowed):
            return ToolResult.error(f"unknown or disabled tool '{call.name}'")
        args = dict(call.arguments or {})
        if "_truncated" in args:
            return ToolResult.error(
                f"this {call.name} call was cut off at the output limit after {args['_truncated']} characters of "
                "arguments, so nothing was done. Write it in smaller parts: create the file with the first part, then "
                "add each further part with edit_file (or append) in separate calls.")
        if "_raw" in args:
            return ToolResult.error(f"could not parse tool arguments as JSON: {args['_raw'][:300]}")

        sandbox = getattr(ctx.config, "sandbox", None)      # an evaluation trial (sinobee/evals/sandbox.py)
        if sandbox is not None:
            refused = sandbox.before(tool.name, tool.toolset, args)
            if refused:
                return ToolResult.error(refused)
        mode = ctx.config.get("permissions.approval", "confirm")
        if mode == "sandbox":
            # the sandbox: what stays inside it needs no approval; anything else is asked as in "confirm"
            from ..sandbox import executor_of
            contained = tool.name in CONTAINED and executor_of(ctx).kind == "sandbox"
            mode = "auto" if contained and not tool.always_confirm else "confirm"
        if mode == "readonly" and tool.writes:
            return ToolResult.error(f"'{tool.name}' is blocked: SinoBee is running in readonly mode")
        reason = None
        if tool.dangerous is not None:
            try:
                reason = tool.dangerous(args)
            except Exception:
                reason = None
        if reason and mode == "confirm" and not tool.always_confirm and ctx.skills is not None \
                and ctx.state.get("preapproved"):
            from ..skills.manager import SkillManager
            if SkillManager.matches_preapproval(tool.name, args, ctx.state["preapproved"]):
                if ctx.emit:
                    ctx.emit("status", {"message": f"{tool.name} pre-approved by an active skill's allowed-tools"})
                reason = None
        if reason and (mode != "auto" or tool.always_confirm):
            ok = False
            if ctx.approve is not None:
                ok = await ctx.approve(tool.name, args, reason)
            if not ok:
                return ToolResult.error(f"user did not approve '{tool.name}' ({reason}). "
                                        "Choose a safer approach or ask the user.")

        try:
            sig = inspect.signature(tool.handler)
            accepted = {k: v for k, v in args.items()
                        if k in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())}
            if inspect.iscoroutinefunction(tool.handler):
                value = await tool.handler(ctx, **accepted)
            else:
                value = await asyncio.to_thread(tool.handler, ctx, **accepted)
        except TypeError as e:
            return ToolResult.error(f"bad arguments for {tool.name}: {e}")
        except Exception as e:  # tools must never crash the loop
            log.debug("tool %s failed\n%s", tool.name, traceback.format_exc())
            return ToolResult.error(f"{type(e).__name__}: {e}")
        if sandbox is not None:
            from ..evals.sandbox import text_of
            sandbox.after(tool.name, args, text_of(value))
        return _normalise(value, int(ctx.config.get("agent.tool_output_limit", 12000)))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n\n...[{len(text) - limit} chars truncated]...\n\n{text[-tail:]}"


def _normalise(value: Any, limit: int) -> ToolResult:
    if isinstance(value, ToolResult):
        res = value
    elif isinstance(value, list) and any(isinstance(p, (ImagePart, TextPart)) for p in value):
        res = ToolResult(value)
    else:
        res = ToolResult.ok(value if value is not None else "ok")
        # many tools report failure as a plain "ERROR: …" string; flag it so the loop, the UI and the
        # repeat guard all see it as a failure instead of a ✓
        if isinstance(res.content, str) and res.content.lstrip().startswith("ERROR"):
            res.is_error = True
    if isinstance(res.content, str):
        res.content = _truncate(res.content, limit)
    else:
        from ..images import fit_content       # a full-page screenshot is cut into strips / scaled to fit
        res.content = fit_content([TextPart(_truncate(p.text, limit)) if isinstance(p, TextPart) else p
                                   for p in res.content])
    return res


REGISTRY = Registry()
tool = REGISTRY.tool
