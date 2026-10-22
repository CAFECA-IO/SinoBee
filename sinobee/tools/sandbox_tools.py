"""request_network: the sandbox's network is off except for allowed domains; this asks you for more (plan C2).

The answer is pushed to the egress proxy at once (sandbox/manager.py):
    只這次任務   these domains, for this conversation until the task (turn) ends
    記住這些網域 these domains, added to sandbox.network.remembered (every sandbox, from now on)
    全部網路     everything, for this conversation until the task ends
    不允許       nothing changes
"""
from __future__ import annotations

import re

from .registry import ToolContext, ToolResult, tool

ONCE, REMEMBER, ALL, NO = "只這次任務允許", "記住這些網域", "這次任務允許全部網路", "不允許"
DOMAIN = re.compile(r"^(\*\.)?([a-z0-9-]+\.)+[a-z]{2,63}$|^\d{1,3}(\.\d{1,3}){3}$")


def available(config=None) -> bool:
    if config is None:
        return False
    from ..sandbox import executor_kind
    return executor_kind(config) == "sandbox"


def clean(domains) -> list[str]:
    out = []
    for d in domains if isinstance(domains, list) else str(domains or "").replace(",", " ").split():
        d = str(d).strip().lower()
        d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split(":")[0].rstrip(".")
        if d and DOMAIN.match(d) and d not in out:
            out.append(d)
    return out


@tool("request_network", "shell",
      "Ask the user to let the sandbox reach more internet domains (its network is off except pypi.org, "
      "files.pythonhosted.org and registry.npmjs.org, plus what the user allowed). Use it when a command failed "
      "with a SinoBee 403 naming a domain, or before a step that clearly needs a site. Give the exact domains "
      "(a domain also allows its subdomains) and why. The user may refuse: then work without it.",
      {"properties": {
          "domains": {"type": "array", "items": {"type": "string"}, "description": "e.g. [\"github.com\"]"},
          "reason": {"type": "string", "description": "what you need them for, in the user's language"},
      }, "required": ["domains", "reason"]}, check=available)
async def request_network(ctx: ToolContext, domains: list, reason: str = ""):
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    if ex.kind != "sandbox":
        return "The tools are not in the sandbox: the network is not limited."
    names = clean(domains)
    if not names:
        return ToolResult.error("give domain names such as github.com (not URLs with paths, not wildcards only)")
    m, conv = ex.manager, ex.conversation
    allowed = m.policy(conv)
    if allowed["all"] or all(any(n == d or n.endswith("." + d) for d in allowed["domains"]) for n in names):
        return f"already allowed: {', '.join(names)}"
    if ctx.ask is None:
        return ToolResult.error("nobody is there to allow network access now; continue without these domains")
    mounts = [str(x.inner) for x in ex.paths.mounts if str(x.inner).startswith("/host/")]
    question = (f"沙盒要連到 {', '.join(names)}。\n理由：{reason or '（未說明）'}"
                + (f"\n注意：這個任務可以讀到 {', '.join(mounts)}，連網後資料有可能被傳出。" if mounts else ""))
    ans = await ctx.ask(question, [ONCE, REMEMBER, ALL, NO], False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if choice == NO or ans is None:
        return f"The user did not allow {', '.join(names)}. Continue without them, or explain what cannot be done."
    if choice == ALL:
        m.open_all.add(conv)
    else:
        m.grants.setdefault(conv, set()).update(names)
        if choice == REMEMBER:
            current = ctx.config.get("sandbox.network.remembered")
            current = list(current) if current is not None else list(allowed["domains"])
            ctx.config.save_gui_settings({"sandbox": {"network": {"remembered": current + [n for n in names
                                                                                          if n not in current]}}})
    await m.push_policy(conv)
    scope = {ONCE: "for this task", REMEMBER: "from now on", ALL: "all network, for this task"}[choice]
    return f"allowed {scope}: {', '.join(names) if choice != ALL else 'everything'}. Run the command again."
