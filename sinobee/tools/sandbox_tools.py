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
    options = [{"label": ONCE, "description": "這次任務結束就收回", "recommended": True},
               {"label": REMEMBER, "description": "之後的任務也不再問（設定 → 沙盒的網路可移除）"},
               {"label": ALL, "description": "這次任務不限制連線"},
               {"label": NO, "description": "SinoBee 會不連網繼續，或說明做不到的部分"}]
    ans = await ctx.ask(question, options, False, False)
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


# ----------------------------------------------------------------- folders of this computer (plan E1)
ALLOW, READ = "允許", "只允許讀取"
MODE_TEXT = {"review": "審核後寫回：沙盒改的是副本，你看過變更後才會寫回原資料夾",
             "ro": "唯讀：沙盒只能讀", "rw": "直接寫入：沙盒的修改直接改到原資料夾"}


@tool("request_host_access", "shell",
      "Ask the user to let this conversation's sandbox use a folder of their computer (the sandbox sees only "
      "/workspace and the folders listed in the environment). mode: write (default; whether your changes are "
      "reviewed before they reach the folder is the user's setting) or ro (read-only). Give the folder as the user "
      "knows it (e.g. ~/Documents/報告) and why. Files the user attaches are already in /workspace/uploads.",
      {"properties": {
          "path": {"type": "string", "description": "a folder of the user's computer, e.g. ~/Documents/報告"},
          "mode": {"type": "string", "enum": ["write", "ro"]},
          "reason": {"type": "string", "description": "what you need it for, in the user's language"},
      }, "required": ["path", "reason"]}, check=available)
async def request_host_access(ctx: ToolContext, path: str, reason: str = "", mode: str = "write"):
    from pathlib import Path

    from ..sandbox import executor_of
    from ..sandbox import folders as fd
    ex = executor_of(ctx)
    if ex.kind != "sandbox":
        return "The tools run on this computer: folders are not limited."
    want = fd.write_mode(ctx.config, "ro" if mode == "ro" else "write")
    why = fd.refuse(ctx.config, path)
    if why:
        return ToolResult.error(f"{why}（{path}）")
    target = Path(path).expanduser().resolve()
    for f in fd.folders_for(ctx.config, ex.conversation):
        if Path(f["path"]) == target and (f["mode"] == want or want == "ro"):
            return f"already available: /host/{f['name']} ({MODE_TEXT[f['mode']]})"
    if want == "review":
        import asyncio
        n = await asyncio.to_thread(fd.count_files, target, fd.review_max(ctx.config))
        if n > fd.review_max(ctx.config):
            return ToolResult.error(f"{target} has more than {fd.review_max(ctx.config):,} files: too many to copy for "
                                    "review. Ask again with mode=ro, or ask the user to turn off 寫入前審核 in "
                                    "Settings → 權限與安全 if your changes should land directly.")
    if ctx.ask is None:
        return ToolResult.error("nobody is there to allow access now; continue without this folder")
    home = str(Path.home())
    shown = "~" + str(target)[len(home):] if str(target).startswith(home + "/") else str(target)
    options = [{"label": ALLOW, "description": MODE_TEXT[want] + "（只在這個對話）", "recommended": True}]
    if want != "ro":
        options.append({"label": READ, "description": "沙盒只能讀，不能修改"})
    options.append({"label": NO, "description": "SinoBee 會不用這個資料夾繼續"})
    ans = await ctx.ask(f"這個對話要使用你電腦上的資料夾 {shown}。\n方式：{MODE_TEXT[want]}\n理由：{reason or '（未說明）'}",
                        options, False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if ans is None or choice == NO:
        return f"The user did not allow {shown}. Continue without it, or ask them to attach the files you need."
    msg = await grant(ctx.config, ex, target, "ro" if choice == READ else want)
    return msg


async def grant(config, ex, target, mode: str) -> str:
    """Give the folder to the executor's conversation now: a review copy appears at once, a mount restarts the
    sandbox. Used by request_host_access and the conversation's 📁 資料夾 button."""
    import asyncio
    from pathlib import Path

    from ..sandbox import folders as fd
    from ..sandbox import staging
    target = Path(target)
    fd.add_grant(config, ex.conversation, fd.entry(target, mode))
    final = next(f for f in fd.folders_for(config, ex.conversation) if Path(f["path"]) == target)
    inner = f"/host/{final['name']}"
    if final["mode"] == "review":
        await asyncio.to_thread(staging.ensure_copy, config, ex.conversation, final)
    replaced = False
    if final["mode"] in ("ro", "rw") and ex.conversation in ex.manager.boxes:
        replaced = await ex.manager.refresh(ex.conversation)
    home = str(Path.home())
    shown = "~" + str(target)[len(home):] if str(target).startswith(home + "/") else str(target)
    note = (" The sandbox was restarted to mount it: programs you started there (servers, the browser) stopped - "
            "start them again if you still need them." if replaced else "")
    how = {"review": "a copy you work on; the user reviews your changes before they reach " + shown,
           "ro": "read-only", "rw": "read/write, changes land directly in " + shown}[final["mode"]]
    return f"{shown} is now at {inner} ({how}).{note}"
