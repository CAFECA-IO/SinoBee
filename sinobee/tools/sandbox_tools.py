"""request_network: the sandbox's network is off except for allowed domains; this asks you for more (plan C2).

The answer is pushed to the egress proxy at once (sandbox/manager.py):
    只這次任務   these domains, for this conversation until the task (turn) ends
    1 小時內     these domains, for this conversation for the next hour (security plan stage 2)
    這個對話一直允許 these domains, kept in the conversation's settings (convconf.py)
    記住這些網域 these domains, added to sandbox.network.remembered (every sandbox, from now on)
    全部網路     everything, for this conversation until the task ends
    不允許       nothing changes
"""
from __future__ import annotations

import re

from .registry import ToolContext, ToolResult, tool

ONCE, HERE, REMEMBER, ALL, NO = "只這次任務允許", "這個對話一直允許", "所有對話都允許", "這次任務允許全部網路", "不允許"
HOUR = "這個對話 1 小時內允許"
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
    have = list(allowed["domains"]) + list((allowed.get("until") or {}).keys())
    if allowed["all"] or all(any(n == d or n.endswith("." + d) for d in have) for n in names):
        return f"already allowed: {', '.join(names)}"
    if ctx.ask is None:
        return ToolResult.error("nobody is there to allow network access now; continue without these domains")
    mounts = [str(x.inner) for x in ex.paths.mounts if str(x.inner).startswith("/host/")]
    question = (f"沙盒要連到 {', '.join(names)}。\n理由：{reason or '（未說明）'}"
                + (f"\n注意：這個任務可以讀到 {', '.join(mounts)}，連網後資料有可能被傳出。" if mounts else ""))
    if ctx.state.get("task_secrets"):
        question += "\n注意：這個任務持有金鑰；金鑰只能送到它自己的網站，其他網站收到含金鑰的請求會被擋下。"
    options = [{"label": ONCE, "description": "這次任務結束就收回", "recommended": True},
               {"label": HOUR, "description": "一小時後自動收回（這個對話的其他任務也可用）"},
               {"label": HERE, "description": "這個對話之後的任務也不再問（對話設定可移除）"},
               {"label": REMEMBER, "description": "每個對話都不再問（設定 → 沙盒的網路可移除）"},
               {"label": ALL, "description": "這次任務不限制連線"},
               {"label": NO, "description": "SinoBee 會不連網繼續，或說明做不到的部分"}]
    ans = await ctx.ask(question, options, False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if choice == NO or ans is None:
        return f"The user did not allow {', '.join(names)}. Continue without them, or explain what cannot be done."
    if choice == ALL:
        m.open_all.add(conv)
    elif choice == HOUR:
        import time
        m.until.setdefault(conv, {}).update({n: time.time() + 3600 for n in names})
    else:
        m.grants.setdefault(conv, set()).update(names)
        if choice == HERE:
            from ..sandbox import convconf
            convconf.update(ctx.config, conv, lambda d: d["network"].__setitem__(
                "domains", d["network"]["domains"] + [n for n in names if n not in d["network"]["domains"]]))
        if choice == REMEMBER:
            from ..sandbox.manager import REMEMBERED
            current = ctx.config.get("sandbox.network.remembered")
            current = list(current) if current is not None else list(REMEMBERED)
            ctx.config.save_gui_settings({"sandbox": {"network": {"remembered": current + [n for n in names
                                                                                          if n not in current]}}})
    await m.push_policy(conv)
    scope = {ONCE: "for this task", HOUR: "for the next hour", HERE: "for this conversation", REMEMBER: "from now on",
             ALL: "all network, for this task"}[choice]
    return f"allowed {scope}: {', '.join(names) if choice != ALL else 'everything'}. Run the command again."


# ----------------------------------------------------------------- folders of this computer (plan E1; A, B, D)
ALLOW, READ = "允許", "只允許讀取"
MODE_TEXT = {"review": "審核後寫回：沙盒改的是副本，你看過變更後才會寫回原資料夾",
             "review-overlay": "審核後寫回（疊加）：檔案太多不複製，沙盒看到你的資料夾、改動另外存放，你看過變更後才會寫回",
             "review-git": "審核後寫回（git）：沙盒在這個 git 專案的副本上工作，變更以 git diff 給你審核後才寫回",
             "ro": "唯讀：沙盒只能讀",
             "rw": "直接寫入：沙盒的修改直接改到原資料夾；每個任務開始前自動建立檢查點，可在對話設定還原"}
STRATEGIES = ("auto", "review", "direct", "ro")


def mode_text(f: dict) -> str:
    if f.get("mode") == "review" and f.get("via") in ("git", "overlay"):
        return MODE_TEXT[f"review-{f['via']}"]
    return MODE_TEXT[f.get("mode", "review")]


def plan_folder(config, target, strategy: str, conversation: str | None) -> tuple[str, str | None, str | None]:
    """(mode, via, why-not) for giving `target` with `strategy`: auto follows 寫入前審核 (this conversation's or
    Settings'); review is by git for a git repository, else by a copy (too many files: why-not says so)."""
    from ..sandbox import folders as fd
    if strategy == "ro":
        return "ro", None, None
    if strategy == "direct":
        return "rw", None, None
    if strategy == "auto" and not fd.review_writes(config, conversation):
        return "rw", None, None
    via = fd.review_via(target)
    if via == "copy":
        n = fd.count_files(target, max(fd.review_max(config), fd.overlay_min(config)))
        if n > fd.overlay_min(config):
            from ..sandbox import overlay
            if overlay.available(config):           # too many files to copy well: your folder below, changes above
                return "review", "overlay", None
        if n > fd.review_max(config):
            return "review", via, (f"{target} 不是 git 專案，且有超過 {fd.review_max(config):,} 個要審核的檔案（不含 node_modules、"
                                   ".git 與快取），太大無法做審核用的副本")
    return "review", via, None


@tool("request_host_access", "shell",
      "Ask the user to let this conversation's sandbox use a folder of their computer (the sandbox sees only "
      "/workspace and the folders listed in the environment). Before choosing, load the skill host-folder-strategies "
      "when the folder is a large project, a git repository or a real environment to fix. strategy: auto (default: "
      "the user's 寫入前審核 setting decides), review (your changes are reviewed before they reach the folder: by git "
      "for a git repository - any size - else by a copy, up to 200,000 files), direct (write in place; a checkpoint "
      "is taken before each task and with folder_checkpoint, for a project too big to copy without git or a real "
      "environment to fix), ro (read-only). Give the folder as the user knows it (e.g. ~/Documents/報告) and why. "
      "Files the user attaches are already in /workspace/uploads.",
      {"properties": {
          "path": {"type": "string", "description": "a folder of the user's computer, e.g. ~/Documents/報告"},
          "strategy": {"type": "string", "enum": list(STRATEGIES)},
          "mode": {"type": "string", "enum": ["write", "ro"], "description": "older form: ro = strategy ro"},
          "reason": {"type": "string", "description": "what you need it for, in the user's language"},
      }, "required": ["path", "reason"]}, check=available)
async def request_host_access(ctx: ToolContext, path: str, reason: str = "", mode: str = "write",
                              strategy: str = "auto"):
    import asyncio
    from pathlib import Path

    from ..sandbox import executor_of
    from ..sandbox import folders as fd
    ex = executor_of(ctx)
    if ex.kind != "sandbox":
        return "The tools run on this computer: folders are not limited."
    strategy = "ro" if mode == "ro" else (strategy if strategy in STRATEGIES else "auto")
    why = fd.refuse(ctx.config, path)
    if why:
        return ToolResult.error(f"{why}（{path}）")
    target = Path(path).expanduser().resolve()
    want, via, too_big = await asyncio.to_thread(plan_folder, ctx.config, target, strategy, ex.conversation)
    if too_big:
        return ToolResult.error(too_big + "。Ask again with strategy=direct (written in place, with checkpoints - "
                                "say so to the user) or strategy=ro, or work on a smaller subfolder.")
    for f in fd.folders_for(ctx.config, ex.conversation):
        if Path(f["path"]) == target and (f["mode"] == want or want == "ro"):
            return f"already available: /host/{f['name']} ({mode_text(f)})"
    if ctx.ask is None:
        return ToolResult.error("nobody is there to allow access now; continue without this folder")
    home = str(Path.home())
    shown = "~" + str(target)[len(home):] if str(target).startswith(home + "/") else str(target)
    how = mode_text({"mode": want, "via": via})
    options = [{"label": ALLOW, "description": how + "（只在這個對話）", "recommended": True}]
    if want != "ro":
        options.append({"label": READ, "description": "沙盒只能讀，不能修改"})
    options.append({"label": NO, "description": "SinoBee 會不用這個資料夾繼續"})
    ans = await ctx.ask(f"這個對話要使用你電腦上的資料夾 {shown}。\n方式：{how}\n理由：{reason or '（未說明）'}",
                        options, False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if ans is None or choice == NO:
        return f"The user did not allow {shown}. Continue without it, or ask them to attach the files you need."
    return await grant(ctx.config, ex, target, "ro" if choice == READ else want, via)


async def grant(config, ex, target, mode: str, via: str | None = None) -> str:
    """Give the folder to the executor's conversation now: a review copy appears at once, a mount restarts the
    sandbox, a folder written directly gets its first checkpoint. Used by request_host_access and 對話設定."""
    import asyncio
    from pathlib import Path

    from ..sandbox import checkpoints, staging
    from ..sandbox import folders as fd
    target = Path(target)
    if mode == "review" and via is None:
        via = await asyncio.to_thread(fd.review_via, target)
    fd.add_grant(config, ex.conversation, fd.entry(target, mode, via=via))
    final = next(f for f in fd.folders_for(config, ex.conversation) if Path(f["path"]) == target)
    inner = f"/host/{final['name']}"
    if final["mode"] == "review":
        await asyncio.to_thread(staging.ensure_copy, config, ex.conversation, final)
    first = None
    if final["mode"] == "rw":
        try:
            first = await asyncio.to_thread(checkpoints.make, config, ex.conversation, final, "授權時")
        except Exception:  # noqa: BLE001 - the checkpoint before the next task tries again
            first = None
    replaced = False
    if final["mode"] in ("ro", "rw") and ex.conversation in ex.manager.boxes:
        replaced = await ex.manager.refresh(ex.conversation)
    home = str(Path.home())
    shown = "~" + str(target)[len(home):] if str(target).startswith(home + "/") else str(target)
    note = (" The sandbox was restarted to mount it: programs you started there (servers, the browser) stopped - "
            "start them again if you still need them." if replaced else "")
    how = {"review": "a copy you work on; the user reviews your changes before they reach " + shown
                     + (" (reviewed by git: commit freely, only the working tree is reviewed; files git ignores are "
                        "never written back)" if final.get("via") == "git" else
                        " (node_modules and build caches are not copied: install them in the sandbox)"),
           "ro": "read-only",
           "rw": "read/write, changes land directly in " + shown
                 + ("; a checkpoint was taken now and one is taken before each task - use folder_checkpoint before "
                    "a risky step" if first else "; it is too big for a copy checkpoint: be careful, nothing to "
                    "restore from")}[final["mode"]]
    return f"{shown} is now at {inner} ({how}).{note}"


@tool("folder_checkpoint", "shell",
      "Checkpoints of a folder this conversation writes directly (直接寫入, /host/<name>): make one before a risky "
      "step (action=make, with a label), list them, see what going back would change (action=diff), or go back "
      "(action=restore: files made since go to the Trash; a checkpoint of the state before is taken first, so it can "
      "be undone). One is taken automatically before each task.",
      {"properties": {
          "folder": {"type": "string", "description": "its name or /host/<name>"},
          "action": {"type": "string", "enum": ["make", "list", "diff", "restore"]},
          "id": {"type": "string", "description": "the checkpoint (diff, restore)"},
          "label": {"type": "string", "description": "what is about to happen (make)"},
          "paths": {"type": "array", "items": {"type": "string"}, "description": "restore only these (relative)"},
      }, "required": ["folder", "action"]}, check=available)
async def folder_checkpoint(ctx: ToolContext, folder: str, action: str, id: str = "", label: str = "",
                            paths: list | None = None):
    import asyncio
    import time

    from ..sandbox import checkpoints, executor_of
    from ..sandbox import folders as fd
    ex = executor_of(ctx)
    name = str(folder).strip().rstrip("/").removeprefix("/host/")
    f = next((x for x in fd.folders_for(ctx.config, ex.conversation) if x["name"] == name), None)
    if f is None or f["mode"] != "rw":
        return ToolResult.error(f"/host/{name} is not a folder written directly in this conversation "
                                "(checkpoints are for 直接寫入 folders; review folders are reviewed instead)")
    try:
        if action == "make":
            c = await asyncio.to_thread(checkpoints.make, ctx.config, ex.conversation, f, label)
            return f"checkpoint {c['id']} ({c['kind']})" if c else ToolResult.error("too many files for a copy checkpoint")
        if action == "list":
            items = checkpoints.listing(ctx.config, ex.conversation, name)
            return "\n".join(f"{c['id']}  {time.strftime('%m/%d %H:%M', time.localtime(c['t']))}  {c.get('label', '')}"
                             for c in reversed(items)) or "no checkpoints yet"
        if action == "diff":
            d = await asyncio.to_thread(checkpoints.diff, ctx.config, ex.conversation, f, id)
            return "\n".join(f"{c['kind']:9} {c['path']}" for c in d[:300]) or "no difference"
        if action == "restore":
            r = await asyncio.to_thread(checkpoints.restore, ctx.config, ex.conversation, f, id, paths or None)
            return (f"restored {len(r['restored'])} files, {len(r['trashed'])} made since went to the Trash; "
                    f"the state before is checkpoint {r['before']}"
                    + (f"; skipped (changed while restoring): {', '.join(r['conflicts'])}" if r.get("conflicts") else ""))
    except Exception as e:  # noqa: BLE001
        return ToolResult.error(str(e))
    return ToolResult.error("action: make | list | diff | restore")


@tool("host_folder_info", "shell",
      "Before asking for a folder of the user's computer: whether it exists and can be given, whether it is a git "
      "repository, how many files a review by copy would compare (node_modules, .git and caches not counted), which "
      "dependency folders it has, and how this conversation already has it. No names or contents are shown. See the "
      "skill host-folder-strategies for choosing review / direct / ro.",
      {"properties": {"path": {"type": "string", "description": "e.g. ~/Projects/app"}}, "required": ["path"]},
      check=available)
async def host_folder_info(ctx: ToolContext, path: str):
    import asyncio
    import json
    from pathlib import Path

    from ..sandbox import executor_of
    from ..sandbox import folders as fd
    from ..sandbox.clone import DEPS
    from ..sandbox.gitreview import is_repo
    ex = executor_of(ctx)
    why = fd.refuse(ctx.config, path)
    if why:
        return json.dumps({"path": path, "can_give": False, "why": why}, ensure_ascii=False)
    target = Path(path).expanduser().resolve()
    limit = fd.review_max(ctx.config)
    n = await asyncio.to_thread(fd.count_files, target, limit)
    try:
        deps = sorted(e.name for e in target.iterdir() if e.is_dir() and e.name in DEPS)
    except OSError:
        deps = []
    have = next((f for f in fd.folders_for(ctx.config, ex.conversation) if Path(f["path"]) == target), None)
    git = is_repo(target)
    return json.dumps({
        "path": path, "can_give": True, "git_repository": git,
        "reviewed_files": n if n <= limit else f"more than {limit}", "copy_review_limit": limit,
        "dependency_folders": deps, "review_writes_setting": fd.review_writes(ctx.config, ex.conversation),
        "already": ({"at": f"/host/{have['name']}", "mode": have["mode"], "via": have.get("via")} if have else None),
        "suggested": "ro if you only read; " + ("review (by git)" if git else
                                                 "review (by copy)" if n <= limit else "direct, or git init / a subfolder"),
    }, ensure_ascii=False)


# ----------------------------------------------------------------- your Mac's screen and browser (plan stage 3, F1)
LEND, NOT_LEND = "只這次任務允許", "不允許"
HOST_WHAT = {"desktop": "你的螢幕、滑鼠與鍵盤", "browser": "你 Mac 上 SinoBee 的瀏覽器（保留你登入過的網站）"}


def host_screen_available(config=None) -> bool:
    if not available(config):
        return False
    from .desktop import host_available
    try:
        import playwright  # noqa: F401
        has_browser = True
    except ImportError:
        has_browser = False
    return host_available() or has_browser


def lend_end(ctx, emit=None, stopped: bool = False) -> bool:
    """Take back what the task was lent (the task ended, or you pressed 停止). True when something was lent."""
    had = sorted(ctx.state.pop("host_screen", None) or [])
    if not had:
        return False
    if stopped:
        for k in had:
            ctx.state[f"host_{k}_stopped"] = True
    if emit:
        emit("host_screen", {"on": False, "what": had, "stopped": stopped})
    return True


@tool("request_host_screen", "shell",
      "Ask the user to lend this task their own computer: what='browser' (SinoBee's browser on their Mac - sites they "
      "are signed in to there stay signed in) and/or what='desktop' (their screen, mouse and keyboard). Only when the "
      "sandbox cannot do it: a site that needs their login, an app installed on their Mac. After a yes, the browser / "
      "desktop tools act on their computer until this task ends (they see a banner and can press 停止).",
      {"properties": {
          "what": {"type": "array", "items": {"type": "string", "enum": ["browser", "desktop"]}},
          "reason": {"type": "string", "description": "what you need it for, in the user's language"},
      }, "required": ["what", "reason"]}, check=host_screen_available)
async def request_host_screen(ctx: ToolContext, what: list, reason: str = ""):
    from ..sandbox import executor_of
    if executor_of(ctx).kind != "sandbox":
        return "The tools already run on the user's computer: use the browser / desktop tools directly."
    wanted = [w for w in (what if isinstance(what, list) else [what]) if w in HOST_WHAT]
    if not wanted:
        return ToolResult.error("what must be ['browser'] and/or ['desktop']")
    from .desktop import host_available
    if "desktop" in wanted and not host_available():
        return ToolResult.error("this computer's screen cannot be driven by SinoBee (on a Mac: allow 輔助使用 and "
                                "螢幕錄製 for SinoBee's terminal in System Settings → Privacy & Security)")
    have = set(ctx.state.get("host_screen") or ())
    if set(wanted) <= have:
        return f"already lent for this task: {', '.join(sorted(have))}"
    if ctx.ask is None:
        return ToolResult.error("nobody is there to lend their computer now; continue in the sandbox")
    question = ("要讓這個任務使用" + "、".join(HOST_WHAT[w] for w in wanted) + "嗎？\n"
                f"用途：{reason or '（未說明）'}\n"
                "允許後畫面上方會一直顯示提示，你可以隨時按「停止」；任務結束就收回。"
                + ("\n注意：SinoBee 會看到並操作你整個螢幕，請先關掉不想讓它看到的視窗。" if "desktop" in wanted else ""))
    ans = await ctx.ask(question, [{"label": LEND, "description": "這次任務結束就收回", "recommended": True},
                                   {"label": NOT_LEND, "description": "SinoBee 會留在沙盒裡繼續，或說明做不到的部分"}],
                        False, False)
    if ((ans or {}).get("choices") or [NOT_LEND])[0] != LEND:
        return "The user did not lend their computer. Continue in the sandbox, or explain what cannot be done."
    now = sorted(have | set(wanted))
    ctx.state["host_screen"] = set(now)
    for w in wanted:
        ctx.state.pop(f"host_{w}_stopped", None)
    if ctx.emit:
        ctx.emit("host_screen", {"on": True, "what": now, "reason": reason})
    tips = []
    if "browser" in wanted:
        tips.append("the browser tool now drives SinoBee's browser on the user's Mac (their logins there)")
    if "desktop" in wanted:
        tips.append("the desktop tools (screenshot, mouse, keyboard) now act on the user's real screen - take a "
                    "screenshot first; do not open or read anything unrelated to the task")
    return "lent for this task: " + "; ".join(tips) + ". Files are still reached through the sandbox."
