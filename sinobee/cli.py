"""Command-line interface: chat, run, serve, memory, skills, init, doctor."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys

from .config import Config, sinobee_home

DIM, BOLD, YEL, RED, GRN, RST = "\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"
if not sys.stdout.isatty():
    DIM = BOLD = YEL = RED = GRN = RST = ""

DEFAULT_CONFIG = """# SinoBee configuration  (docs/ARCHITECTURE.md)
# Most settings - models, API keys, permissions, budgets, prices - are easier to set in the Web UI (Settings).
# Values saved there go to settings.json and override this file; startup env vars (docker -e) override both.
# API keys never belong in this file: use the Web UI, `sinobee keys set <provider>`, or docker -e.
model:
  default: null                      # provider/model, e.g. anthropic/claude-sonnet-4-5, gemini/gemini-2.5-pro, ollama/qwen3.5:9b
  fallbacks: []                      # tried in order if the default fails
  auxiliary: null                    # cheaper model for reflection/compression
  embedding: null                    # e.g. openai/text-embedding-3-small (optional, improves recall)
  vision: true
providers: {}
#  mylab:  {base_url: http://gpu-box:8000/v1, api_key_env: MYLAB_KEY}
# local:  {base_url: http://gpu-box:11434, num_ctx: auto}   # Ollama; default: auto-discover
permissions:
  toolsets: [shell, files, desktop, browser, web, memory, skills, usage]
  approval: confirm                  # auto | confirm | readonly
  allowed_paths: []
memory:
  core_char_limit: 1500
  recall_k: 6
skills:
  index_mode: auto
  auto_create: true
learning:
  enabled: true
"""


def _print_event(kind: str, d: dict) -> None:
    if kind == "tool_call":
        args = json.dumps(d["arguments"], ensure_ascii=False)
        print(f"{DIM}  ⚙ {d['name']} {args[:160]}{RST}")
    elif kind == "tool_result":
        prev = (d.get("preview") or "").strip().splitlines()
        first = prev[0][:160] if prev else ""
        color = RED if d.get("error") else DIM
        print(f"{color}    ↳ {first}{' …' if len(prev) > 1 else ''}{RST}")
    elif kind == "assistant" and not d.get("final"):
        print(f"{d['text']}")
    elif kind == "recall":
        print(f"{DIM}  🧠 recalled {len(d['memories'])} memories{RST}")
    elif kind == "learned":
        bits = []
        if d.get("memories"):
            bits.append(f"{len(d['memories'])} memories")
        if d.get("skill"):
            bits.append(f"skill {d['skill'].get('name')} {d['skill'].get('status')}")
        if bits:
            print(f"{GRN}  ✨ learned: {', '.join(bits)}{RST}")
    elif kind == "error":
        print(f"{RED}  ! {d['message']}{RST}")


async def _cli_approve(name: str, args: dict, reason: str) -> bool:
    print(f"\n{YEL}⚠  {name} needs approval: {reason}{RST}\n{json.dumps(args, ensure_ascii=False, indent=2)[:1500]}")
    ans = await asyncio.to_thread(input, "Allow? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


def _runtime(args):
    from .agent import Runtime
    cfg = Config.load(getattr(args, "config", None))
    if getattr(args, "model", None):
        cfg.data["model"]["default"] = args.model
    if getattr(args, "approval", None):
        cfg.data["permissions"]["approval"] = args.approval
    return Runtime(cfg)


async def _chat(args) -> None:
    from .agent import Agent
    from .tools import browser
    rt = _runtime(args)
    agent = Agent(rt, args.session, emit=_print_event, approve=_cli_approve)
    print(f"{BOLD}SinoBee{RST} · {rt.provider.label} · session {agent.session_id} · "
          f"approval={rt.config.get('permissions.approval')}  (/exit, /new, /memory, /skills)")
    try:
        while True:
            try:
                text = await asyncio.to_thread(input, f"\n{YEL}›{RST} ")
            except EOFError:
                break
            text = text.strip()
            if not text:
                continue
            if text in ("/exit", "/quit"):
                break
            if text == "/new":
                agent = Agent(rt, emit=_print_event, approve=_cli_approve)
                print(f"{DIM}new session {agent.session_id}{RST}")
                continue
            if text == "/memory":
                print(rt.memory.render_core() or "(no core memory)")
                for m in rt.memory.list("long", 20):
                    print(f"  [{m.id}] {m.content}")
                continue
            if text == "/skills":
                print(rt.skills.index_block("", None, "all"))
                continue
            try:
                out = await agent.run(text)
            except KeyboardInterrupt:
                agent.cancel()
                continue
            print(f"\n{out}")
    finally:
        print(f"{DIM}finishing background learning…{RST}")
        await rt.drain(120)
        await browser.shutdown()


async def _run(args) -> None:
    from .agent import Agent
    from .tools import browser
    rt = _runtime(args)
    agent = Agent(rt, args.session, emit=_print_event if not args.quiet else None,
                  approve=_cli_approve if sys.stdin.isatty() else None)
    out = await agent.run(" ".join(args.task))
    print(out)
    await rt.drain(120)
    await browser.shutdown()


def _serve(args) -> None:
    import uvicorn
    from .server import create_app
    rt = _runtime(args)
    host = args.host or rt.config.get("server.host", "0.0.0.0")
    port = args.port or int(rt.config.get("server.port", 8000))
    print(f"SinoBee UI → http://{'localhost' if host == '0.0.0.0' else host}:{port}")
    uvicorn.run(create_app(rt), host=host, port=port, log_level="info")


def _memory(args) -> None:
    from .memory import MemoryStore
    cfg = Config.load()
    store = MemoryStore(cfg.db_path)
    if args.action == "list":
        for m in store.list(args.tier):
            print(f"[{m.id}] {m.tier:<7} {m.kind:<11} {m.importance:.2f}  {m.content}")
    elif args.action == "search":
        for m in store.search(" ".join(args.query), k=20, tiers=("core", "long", "archive"), touch=False):
            print(f"[{m.id}] {m.score:.3f} {m.tier:<7} {m.content}")
    elif args.action == "forget":
        print(store.forget(args.query[0], hard=args.hard))
    elif args.action == "consolidate":
        print(store.consolidate())


def _skills(args) -> None:
    from .memory import MemoryStore
    from .skills import LABELS, hub, manager_from_config
    from .skills.manager import SkillError
    cfg = Config.load()
    if getattr(args, "project", None):
        os.environ["SINOBEE_PROJECT"] = args.project
    mgr = manager_from_config(cfg, MemoryStore(cfg.db_path))
    a = args.action
    try:
        if a == "list":
            print(f"project: {mgr.project_dir}  (trusted: {mgr.projects.is_trusted(mgr.project_dir)})")
            for s in sorted(mgr.all_skills(), key=lambda s: (s.name, s not in mgr.skills.values())):
                st = mgr.stats(s.name)
                rate = "" if st.get("success_rate") is None else f" {int(st['success_rate'] * 100)}%"
                flag = " (shadowed)" if s not in mgr.skills.values() else ""
                flag += " [experimental]" if st.get("status") == "candidate" else ""
                flag += " [disabled]" if s.disabled else ""
                print(f"{s.name:<28} {s.scope:<7} {LABELS[s.trust]:<5} v{s.version:<7} uses={st.get('uses', 0)}{rate}"
                      f"{flag}\n    {s.description[:110]}" + (f"\n    ! {s.reason}" if s.trust in ("untrusted", "blocked") else ""))
        elif a == "show":
            print(mgr.view(args.name, record=False))
        elif a == "validate":
            from .skills import spec
            target = mgr.find(args.name) if args.name in mgr.skills else None
            path = target.path if target else os.path.abspath(args.name)
            from pathlib import Path
            md = next((Path(path) / n for n in spec.SKILL_FILENAMES if (Path(path) / n).exists()), None)
            if not md:
                sys.exit(f"no SKILL.md in {path}")
            parsed = spec.parse(md.read_text(encoding="utf-8"))
            errs = spec.validate(parsed.frontmatter, Path(path).name)
            warns = parsed.diagnostics + spec.quality_warnings(parsed.frontmatter, parsed.body)
            print("\n".join([f"ERROR {e}" for e in errs] + [f"warn  {w}" for w in warns]) or "valid Agent Skill")
            if errs:
                sys.exit(1)
        elif a == "doctor":
            for d in mgr.diagnostics:
                print(f"{d['level']:<7} {d['path']}: {d['message']}")
            print("no issues" if not mgr.diagnostics else "")
        elif a == "rollback":
            print(mgr.rollback(args.name, actor="user"))
        elif a == "approve":
            print(mgr.approve(args.name))
        elif a == "trust":
            print(mgr.trust_project(args.name))
        elif a == "untrust":
            print(mgr.untrust_project(args.name))
        elif a == "delete":
            print(mgr.delete(args.name))
        elif a == "fork":
            print(mgr.fork(args.name, args.new_name, scope=args.scope, actor="user"))
        elif a == "install":
            print(hub.install(mgr, args.name, config=cfg, scope=args.scope, trust=args.trust, force=args.force))
        elif a == "search":
            for it in hub.hub_client(cfg).search(args.name or ""):
                print(f"{it.get('id'):<40} {it.get('trust', ''):<10} {it.get('latest', '')}  {it.get('description', '')[:80]}")
        elif a == "outdated":
            for r in hub.check_updates(mgr, cfg):
                print(r)
        elif a == "update":
            print(hub.update(mgr, args.name, cfg, force=args.force))
        elif a == "export":
            from pathlib import Path
            bundle, manifest = hub.export(mgr, args.name, Path(args.out))
            print(f"{bundle}\n{manifest['content_hash']}")
        elif a == "publish":
            print(hub.publish(mgr, args.name, cfg))
        elif a == "sync-revocations":
            print(hub.sync_revocations(mgr, cfg))
    except SkillError as e:
        sys.exit(f"error: {e}")


def _keys(args) -> None:
    from .providers import provider_names, resolve_key
    from .secrets import mask
    cfg = Config.load()
    if args.action == "list":
        for name in provider_names(cfg):
            key, src = resolve_key(name, cfg)
            if key:
                print(f"{name:<12} {mask(key):<12} ({'startup env' if src == 'env' else src})")
        return
    if not args.provider:
        sys.exit("provider name required, e.g. sinobee keys set openai")
    if args.action == "set":
        import getpass
        key = args.key or getpass.getpass(f"{args.provider} API key (hidden): ")
        cfg.secrets.set(args.provider, key)
        print(f"saved key for {args.provider} -> {cfg.home / 'secrets.json'}")
    elif args.action == "rm":
        cfg.secrets.set(args.provider, None)
        print(f"removed key for {args.provider}")


def _usage(args) -> None:
    from .agent.loop import Runtime
    from .memory import MemoryStore
    cfg = Config.load()
    store = MemoryStore(cfg.db_path)
    since = Runtime.period_start(args.period)
    t = store.usage_totals(since)
    print(f"{args.period}: {t['calls']} calls · in {t['input_tokens']:,} (cached {t['cache_read_tokens']:,}) · "
          f"out {t['output_tokens']:,} tokens · US${t['cost_usd']:.4f}")
    for r in store.usage_grouped(args.by, since):
        cost = "unpriced" if r["unpriced_calls"] == r["calls"] else f"US${r['cost_usd']:.4f}"
        print(f"  {str(r['key']):<44} {r['calls']:>5} calls  {r['input_tokens'] + r['output_tokens']:>12,} tok  {cost}")
    missing = store.unpriced_models()
    if missing:
        print(f"no price for: {', '.join(missing)} (set in Web UI → Settings → Pricing)")


def _init(args) -> None:
    path = sinobee_home() / "config.yaml"
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    print(f"wrote {path}")


def _doctor(args) -> None:
    from .providers import split_model
    from .tools import desktop
    cfg = Config.load()
    ok = lambda b: f"{GRN}✓{RST}" if b else f"{RED}✗{RST}"  # noqa: E731
    print(f"home: {cfg.home}\nworkspace: {cfg.workspace}")
    from .providers import needs_key, resolve_key
    if not cfg.get("model.default"):
        print(f"{ok(False)} no model selected (Web UI → Settings, or -e SINOBEE_MODEL=provider/model)")
    for spec in [s for s in [cfg.get("model.default")] + list(cfg.get("model.fallbacks") or []) if s]:
        prov, _ = split_model(spec)
        key, src = resolve_key(prov, cfg)
        has = bool(key) or not needs_key(prov, cfg)
        print(f"{ok(has)} model {spec} (key: {src or ('not needed' if has else 'missing')})")
    try:
        import playwright  # noqa: F401
        pw = True
    except ImportError:
        pw = False
    print(f"{ok(pw)} playwright (browser tool)")
    print(f"{ok(desktop.available())} desktop control (DISPLAY={os.environ.get('DISPLAY')}, "
          f"xdotool={'yes' if shutil.which('xdotool') else 'no'})")
    print(f"approval mode: {cfg.get('permissions.approval')}; toolsets: {cfg.get('permissions.toolsets')}")


def _account_cmd(args) -> None:
    from . import account
    from .config import Config
    cfg = Config.load()
    try:
        if args.cmd == "logout":
            account.logout(cfg)
            print("已登出 sinobee.ai。（裝置權杖可在 sinobee.ai → 帳戶設定 → 已登入的 SinoBee 撤銷）")
            return
        if args.cmd == "whoami":
            me = account.whoami(cfg)
            if not me:
                print("尚未登入。執行 sinobee login。")
                sys.exit(1)
            who = f"@{me['handle']}" if me.get("handle") else me.get("account")
            print(f"{who}  ({me.get('account')})")
            print(f"publisher: {me.get('publisher') or '—'}   scopes: {' '.join(me.get('scopes') or [])}"
                  + ("   (離線，顯示快取資料)" if me.get("offline") else ""))
            return
        s = account.start(cfg)
        print(f"\n  在瀏覽器開啟  {s['verification_uri']}\n  輸入代碼      {s['user_code']}\n")
        if not args.no_browser:
            try:
                import webbrowser
                webbrowser.open(s.get("verification_uri_complete") or s["verification_uri"])
            except Exception:  # noqa: BLE001 - headless machines
                pass
        print("等待授權中…（Ctrl+C 取消）", flush=True)
        res = account.wait(cfg, s)
    except account.AccountError as e:
        print(f"錯誤：{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
    if res["status"] == "ok":
        a = res["account"] or {}
        print(f"已登入 sinobee.ai：{('@' + a['handle']) if a.get('handle') else a.get('account')}")
    else:
        print({"denied": "登入要求被拒絕。", "expired": "代碼已過期，請重新執行 sinobee login。"}.get(res["status"],
              f"登入失敗：{res.get('error')}"))
        sys.exit(1)


def _vault_unlock(cfg) -> None:
    import getpass
    from .vault import VaultClient
    v = VaultClient(cfg)
    st = v.status()
    if st["unlocked"]:
        return
    if st["exists"]:
        v.unlock(getpass.getpass("保險箱密語："))
    else:
        p1 = getpass.getpass("設定保險箱密語（至少 8 字元；遺失無法復原）：")
        if p1 != getpass.getpass("再輸入一次："):
            raise SystemExit("兩次密語不同")
        v.setup(p1)


def _tools_cmd(args) -> None:
    from .config import Config
    from .db import hub as dbhub
    from .tools.db_tools import service
    from .vault import VaultError
    from . import account
    cfg = Config.load()
    svc = service(cfg)
    try:
        if args.action == "list":
            for t in dbhub.mine(cfg):
                print(f"{t['id']:<40} {t.get('version') or t.get('latest') or '':<10} {t['visibility']:<8} {t.get('status', '')}")
        elif args.action == "search":
            for t in dbhub.search(cfg, args.ref or ""):
                print(f"{t['id']:<40} {t.get('latest') or '':<10} {t.get('title') or ''}")
        elif args.action == "publish":
            if not args.ref:
                raise SystemExit("用法：sinobee tools publish <連線名稱或 id> [--public] [--sync]")
            c = svc.get(args.ref)
            if args.sync:
                _vault_unlock(cfg)
            res = asyncio.run(dbhub.publish(cfg, svc, c, visibility="PUBLIC" if args.public else "PRIVATE",
                                            sync_secret=args.sync, include_schema=not args.no_schema))
            print(f"已登錄 {res['id']}@{res['version']}（{'公開，審核中' if res['status'] == 'pending_review' else '私人'}）")
            print(res["url"])
        elif args.action == "install":
            if not args.ref:
                raise SystemExit("用法：sinobee tools install <publisher>/<name>")
            try:
                res = dbhub.install(cfg, svc, args.ref)
            except dbhub.HubToolError as e:
                if "保險箱" not in str(e):
                    raise
                _vault_unlock(cfg)
                res = dbhub.install(cfg, svc, args.ref)
            print(f"已加入連線「{res['name']}」（{res['connection']}）")
            if res["needs_credentials"]:
                print("請在 Web UI 的「資料庫」分頁填入帳號密碼。")
    except (dbhub.HubToolError, VaultError, account.AccountError, Exception) as e:  # noqa: BLE001
        if isinstance(e, SystemExit):
            raise
        print(f"錯誤：{e}")
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sinobee", description="SinoBee autonomous agent")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    for name in ("chat", "run", "serve"):
        sp = sub.add_parser(name)
        sp.add_argument("-m", "--model")
        sp.add_argument("--approval", choices=["auto", "confirm", "readonly"])
        sp.add_argument("-c", "--config")
        if name != "serve":
            sp.add_argument("-s", "--session")
        if name == "run":
            sp.add_argument("task", nargs="+")
            sp.add_argument("-q", "--quiet", action="store_true")
        if name == "serve":
            sp.add_argument("--host")
            sp.add_argument("--port", type=int)
    m = sub.add_parser("memory")
    m.add_argument("action", choices=["list", "search", "forget", "consolidate"])
    m.add_argument("query", nargs="*")
    m.add_argument("--tier")
    m.add_argument("--hard", action="store_true")
    s = sub.add_parser("skills", help="Agent Skills: list, validate, trust, install, publish ...")
    s.add_argument("action", choices=["list", "show", "validate", "doctor", "rollback", "approve", "trust", "untrust",
                                      "delete", "fork", "install", "search", "outdated", "update", "export", "publish",
                                      "sync-revocations"])
    s.add_argument("name", nargs="?", help="skill name, path, install ref (hub:pub/name, github:..., URL) or project")
    s.add_argument("new_name", nargs="?")
    s.add_argument("--scope", default="global", choices=["global", "project"])
    s.add_argument("--trust", choices=["local", "community"])
    s.add_argument("--force", action="store_true")
    s.add_argument("--project", help="project directory (default: current directory)")
    s.add_argument("-o", "--out", default=".")
    i = sub.add_parser("init")
    i.add_argument("--force", action="store_true")
    k = sub.add_parser("keys", help="manage API keys stored for the GUI (secrets.json)")
    k.add_argument("action", choices=["list", "set", "rm"])
    k.add_argument("provider", nargs="?")
    k.add_argument("key", nargs="?", help="omit to be prompted (keeps it out of shell history)")
    u = sub.add_parser("usage", help="token usage and cost")
    u.add_argument("period", nargs="?", default="month", choices=["today", "7d", "30d", "month", "all"])
    u.add_argument("--by", default="model", choices=["model", "purpose", "day", "session", "provider"])
    sub.add_parser("doctor")
    lg = sub.add_parser("login", help="sign in to sinobee.ai (device code)")
    lg.add_argument("--no-browser", action="store_true", help="only print the link")
    sub.add_parser("logout", help="sign out of sinobee.ai on this machine")
    sub.add_parser("whoami", help="show the signed-in sinobee.ai account")
    tl = sub.add_parser("tools", help="database tools on sinobee.ai: list, search, publish, install")
    tl.add_argument("action", choices=["list", "search", "publish", "install"])
    tl.add_argument("ref", nargs="?", help="connection id/name (publish), <publisher>/<name> (install), query (search)")
    tl.add_argument("--public", action="store_true", help="publish for everyone (reviewed; no credentials)")
    tl.add_argument("--sync", action="store_true", help="private only: keep connection + password in your encrypted vault")
    tl.add_argument("--no-schema", action="store_true", help="do not include table/column names")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.cmd in (None, "chat"):
        if args.cmd is None:
            args = p.parse_args(["chat"])
        asyncio.run(_chat(args))
    elif args.cmd == "run":
        asyncio.run(_run(args))
    elif args.cmd == "serve":
        _serve(args)
    elif args.cmd == "memory":
        _memory(args)
    elif args.cmd == "skills":
        _skills(args)
    elif args.cmd == "init":
        _init(args)
    elif args.cmd == "keys":
        _keys(args)
    elif args.cmd == "usage":
        _usage(args)
    elif args.cmd == "doctor":
        _doctor(args)
    elif args.cmd in ("login", "logout", "whoami"):
        _account_cmd(args)
    elif args.cmd == "tools":
        _tools_cmd(args)


if __name__ == "__main__":
    main()
