"""request_secret: a task asks for one of the keys you keep in SinoBee (task_secrets.py)."""
from __future__ import annotations

from .registry import ToolContext, ToolResult, tool

ALLOW, NO = "只這次任務允許", "不允許"


def available(config=None) -> bool:
    try:
        return bool(config is not None and config.secrets.task_secrets())
    except Exception:  # noqa: BLE001
        return False


@tool("request_secret", "shell",
      "Ask the user to give this task one of the keys they keep in SinoBee (API keys, tokens). If allowed, the key is "
      "in the environment of your shell commands as the variable it names, for this task only, and may only be sent to "
      "its own sites - you never see its value (outputs show [key NAME]). Never ask the user to paste a key into the "
      "chat. Call it with no name to list the keys there are.",
      {"properties": {"name": {"type": "string", "description": "the key's name as listed"},
                      "reason": {"type": "string", "description": "what you need it for, in the user's language"}},
       "required": []}, check=available)
async def request_secret(ctx: ToolContext, name: str = "", reason: str = ""):
    from .. import task_secrets as ts
    keys = {k["name"]: k for k in ts.listing(ctx.config)}
    if not name or name not in keys:
        listed = "; ".join(f"{k['name']} (env {k['env']}, for {', '.join(k['domains'])})" for k in keys.values())
        pre = f"There is no key named {name!r}. " if name else ""
        return (f"{pre}Keys the user keeps in SinoBee: {listed or 'none'}. If the one you need is not there, ask the "
                "user to add it in Settings → 權限與安全 → 任務金鑰 (not in the chat).")
    k = keys[name]
    if name in (ctx.state.get("task_secrets") or {}):
        return f"already given: ${k['env']} is set in your shell commands for this task."
    if ctx.ask is None:
        return ToolResult.error("nobody is there to allow a key now; continue without it")
    question = (f"要給這個任務使用金鑰「{name}」嗎？\n用途：{reason or '（未說明）'}\n"
                f"它會以環境變數 ${k['env']} 提供給這次任務的指令，只能送到 {', '.join(k['domains'])}；"
                "SinoBee 看不到它的值，任務結束就收回。")
    ans = await ctx.ask(question, [{"label": ALLOW, "description": "這次任務結束就收回", "recommended": True},
                                   {"label": NO, "description": "SinoBee 會不用這把金鑰繼續，或說明做不到的部分"}],
                        False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if choice != ALLOW:
        return f"The user did not give the key {name}. Continue without it, or explain what cannot be done."
    ts.grant(ctx, name)
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    if ex.kind == "sandbox":
        await ex.manager.push_policy(ex.conversation)
    return (f"given for this task: ${k['env']} is set in your shell commands (use it as \"${k['env']}\", never print "
            f"it). It may only be sent to {', '.join(k['domains'])}.")
