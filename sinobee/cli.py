"""Command-line interface: chat, run, serve, memory, skills, init, doctor."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time

from pathlib import Path
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


async def _cli_ask(question: str, options: list, multi: bool, allow_text: bool) -> dict | None:
    print(f"\n{YEL}? {question}{RST}")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o['label']}{'（建議）' if o.get('recommended') else ''}" + (f" — {o['description']}" if o.get("description") else ""))
    hint = "number" + ("s, comma separated" if multi else "") + (", or type your own answer" if allow_text else "")
    ans = (await asyncio.to_thread(input, f"Choose ({hint}): ")).strip()
    nums = [x.strip() for x in ans.replace("，", ",").split(",")]
    if ans and all(x.isdigit() and 1 <= int(x) <= len(options) for x in nums):
        return {"choices": [options[int(x) - 1]["label"] for x in nums][: len(options) if multi else 1], "text": ""}
    return {"choices": [], "text": ans} if ans else None


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
    agent = Agent(rt, args.session, emit=_print_event, approve=_cli_approve, ask=_cli_ask)
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
                agent = Agent(rt, emit=_print_event, approve=_cli_approve, ask=_cli_ask)
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
                  approve=_cli_approve if sys.stdin.isatty() else None,
                  ask=_cli_ask if sys.stdin.isatty() else None)
    out = await agent.run(" ".join(args.task))
    print(out)
    await rt.drain(120)
    await browser.shutdown()


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _lan_addresses() -> list[str]:
    """This machine's addresses other devices can reach (best effort, no network traffic)."""
    import socket
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))          # picks the outgoing interface; sends nothing
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return [a for a in dict.fromkeys(found) if not a.startswith("127.")]


def serve_token(cfg, host: str, *, explicit: str | None = None, renew: bool = False) -> tuple[str, str]:
    """(token, source). There is always one (security review 2026-10-24): generated and kept in secrets.json the
    first time. On this computer the page gets it as a cookie by itself; other devices open the URL with ?token= once."""
    import secrets as _secrets
    if explicit:
        cfg.data.setdefault("server", {})["token"] = explicit
        return explicit, "flag"
    if cfg.get("server.token"):
        return cfg.get("server.token"), "env" if "server.token" in cfg.locked else "config"
    tok = None if renew else cfg.secrets.get_secret("server_token")
    if not tok:
        tok = _secrets.token_urlsafe(24)
        cfg.secrets.set_secret("server_token", tok)
        return tok, "generated"
    return tok, "saved"


def _serve(args) -> None:
    import uvicorn
    from .server import create_app
    rt = _runtime(args)
    host = "0.0.0.0" if args.public else (args.host or rt.config.get("server.host", "127.0.0.1"))
    port = args.port or int(rt.config.get("server.port", 10024))
    token, source = serve_token(rt.config, host, explicit=args.token, renew=args.new_token)
    q = f"/?token={token}"
    in_docker = os.path.exists("/.dockerenv") or os.environ.get("SINOBEE_IN_DOCKER") == "1"
    print(f"SinoBee UI → http://localhost:{port}{q if in_docker else ''}")
    if host not in LOOPBACK:
        hosts = _lan_addresses() if host in ("0.0.0.0", "::") else [host]
        for h in hosts:
            print(f"            http://{h}:{port}{q}")
        note = {"generated": "已產生新的存取權杖並存在 secrets.json（--new-token 可更換）",
                "saved": "使用 secrets.json 裡的存取權杖（--new-token 可更換）", "flag": "使用 --token 指定的權杖",
                "env": "使用 SINOBEE_SERVER_TOKEN", "config": "使用設定檔的 server.token"}.get(source, "")
        print(f"對外開放中：其他裝置用上面含 token 的網址開啟一次（之後會記住）。{note}")
        print("提醒：只在信任的網路開放；跨網際網路請改用 HTTPS 通道（例如 Tailscale、Cloudflare Tunnel），"
              f"並保持核准模式為 confirm（目前：{rt.config.get('permissions.approval', 'confirm')}）。")
    uvicorn.run(create_app(rt), host=host, port=port, log_level="info")


def _memory(args) -> None:
    from .memory import MemoryStore
    cfg = Config.load()
    store = MemoryStore(cfg.db_path, core_char_limit=int(cfg.get("memory.core_char_limit", 1500)),
                        short_ttl_days=float(cfg.get("memory.short_ttl_days", 14)))
    if args.action == "list":
        tier = None if args.tier == "permanent" else args.tier
        for m in store.list(tier):
            if args.tier == "permanent" and not m.pinned:
                continue
            exp = f" (expires {time.strftime('%Y-%m-%d', time.localtime(m.expires_at))})" if m.expires_at else ""
            print(f"[{m.id}] {m.label:<9} {m.kind:<11} {m.importance:.2f}  {m.content}{exp}")
    elif args.action == "search":
        for m in store.search(" ".join(args.query), k=20, tiers=("core", "short", "long", "archive"), touch=False):
            print(f"[{m.id}] {m.score:.3f} {m.label:<9} {m.content}")
    elif args.action == "forget":
        print(store.forget(args.query[0], hard=args.hard, force=args.force))
    elif args.action == "pin":
        print(store.update(args.query[0], pinned=True))
    elif args.action == "unpin":
        print(store.update(args.query[0], pinned=False))
    elif args.action == "consolidate":
        print(store.consolidate(archive_after_days=float(cfg.get("memory.archive_after_days", 90))))
    elif args.action == "restore":
        print(store.restore(args.query[0]))
    elif args.action == "maintain":
        from .memory.maintain import maintain
        rt = _runtime(args)
        print(json.dumps(asyncio.run(maintain(rt, reason="cli", force=True)), ensure_ascii=False, indent=1,
                         default=str))
    elif args.action == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=1))
    elif args.action == "compress":
        from .memory.compress import compress, render
        rep = asyncio.run(compress(store, None, query=" ".join(args.query) or None,
                                   tier=None if args.tier == "permanent" else args.tier,
                                   include_pinned=args.tier == "permanent", dry_run=not args.apply))
        print(render(rep))


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
    from .providers import resolve_key, usable_now
    if not cfg.get("model.default"):
        print(f"{ok(False)} no model selected (Web UI → Settings, or -e SINOBEE_MODEL=provider/model)")
    for spec in [s for s in [cfg.get("model.default")] + list(cfg.get("model.fallbacks") or []) if s]:
        prov, _ = split_model(spec)
        key, src = resolve_key(prov, cfg)
        has = usable_now(prov, cfg)
        how = ("SinoBee.ai subscription" + ("" if has else ": run sinobee login")) if prov == "sinobee" else \
            f"key: {src or ('not needed' if has else 'missing')}"
        print(f"{ok(has)} model {spec} ({how})")
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
            from . import profiles
            account.logout(cfg)
            res = profiles.switch_after_logout(cfg)
            print("已登出 sinobee.ai。（裝置權杖可在 sinobee.ai → 帳戶設定 → 已登入的 SinoBee 撤銷）"
                  + ("\n之後使用本機資料；正在執行的 sinobee serve 請重新啟動。" if res["switched"] else ""))
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
        link = s.get("verification_uri_complete") or s["verification_uri"]
        opened = False
        if not args.no_browser and (os.environ.get("DISPLAY") or sys.platform in ("darwin", "win32")) \
                and not os.path.exists("/.dockerenv"):
            try:
                import webbrowser
                opened = webbrowser.open(link)
            except Exception:  # noqa: BLE001 - headless machines
                opened = False
        if not opened:
            print("  或用手機掃描：")
            account.qr_terminal(link)
        print("等待授權中…（Ctrl+C 取消）", flush=True)
        res = account.wait(cfg, s)
    except account.AccountError as e:
        print(f"錯誤：{e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
    if res["status"] == "ok":
        from . import profiles
        a = res["account"] or {}
        print(f"已登入 sinobee.ai：{('@' + a['handle']) if a.get('handle') else a.get('account')}")
        sw = profiles.switch_after_login(cfg, a)
        if sw["switched"]:
            print("已切換到這個帳號自己的資料；正在執行的 sinobee serve 請重新啟動。")
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


def _db_cmd(args) -> None:
    """Read-only database access for scripts and other agents. Writes are refused here (use the Web UI);
    passwords never leave secrets.json."""
    import csv
    import io
    from .config import Config
    from .db import DbError, classify, toolfolder
    from .tools.db_tools import md_table, service
    cfg = Config.load()
    svc = service(cfg)

    def out(columns, rows):
        if args.format == "json":
            print(json.dumps([dict(zip(columns, r)) for r in rows], ensure_ascii=False, default=str, indent=1))
        elif args.format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(columns)
            w.writerows(rows)
            print(buf.getvalue(), end="")
        else:
            print(md_table(columns, rows, max_rows=len(rows)))
    try:
        if args.action in ("migrate", "migrations"):
            return _migrate_cmd(args, svc, out)
        if args.action == "list":
            out(["id", "name", "kind", "label", "read_only"],
                [[c["id"], c["name"], c["kind"], c["label"], not c["allow_write"]] for c in svc.listing()])
            return
        if not args.connection:
            raise SystemExit(f"用法：sinobee db {args.action} <連線>{' <SQL>' if args.action == 'query' else ''}")
        c = svc.get(args.connection)
        if args.action == "queries":
            out(["title", "sql"], [[q["title"], q["sql"]] for q in toolfolder.queries(cfg, c)])
        elif args.action == "schema":
            if args.arg:
                tree = asyncio.run(svc.schema_tree(c.id))
                sch = next((s["name"] for s in tree["schemas"] if any(t["name"] == args.arg for t in s["tables"])), None)
                if not sch:
                    raise SystemExit(f"找不到資料表 {args.arg}")
                info = asyncio.run(svc.table_info(c.id, sch, args.arg))
                out(["column", "type", "nullable", "pk"], [[x["name"], x.get("type"), x.get("nullable"), x.get("pk")] for x in info["columns"]])
            else:
                tree = asyncio.run(svc.schema_tree(c.id))
                out(["schema", "table", "kind", "rows≈"], [[s["name"], t["name"], t["kind"], t.get("rows_estimate")]
                                                          for s in tree["schemas"] for t in s["tables"]])
        else:
            sql = sys.stdin.read() if args.arg in (None, "-") else args.arg
            v = classify(sql, c.kind)
            if not v.is_read:
                raise SystemExit(f"只執行唯讀查詢：{v.reason or v.statement}（寫入請在 Web UI 進行，每次都需要核准）")
            res = asyncio.run(svc.query(c.id, sql, limit=max(1, min(args.limit, 10000)), source="cli"))
            out(res["columns"], res["rows"])
            if res["truncated"]:
                print(f"（已達 {args.limit} 列上限）", file=sys.stderr)
    except DbError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        sys.exit(1)


def _migrate_cmd(args, svc, out) -> None:
    from .db.migration.runner import MigrationError
    mm = svc.migrations
    try:
        if args.action == "migrations":
            out(["id", "status", "source", "target", "rows", "error"],
                [[j["id"], j["status"], j["source"]["name"], j["target"].get("label"),
                  j["progress"]["rows_copied"], (j.get("error") or "")[:60]] for j in mm.list()])
            return
        if args.resume:
            job = mm.get(args.resume)
            mm.resume(job.id, {"skip_bad_rows": True} if args.skip_bad_rows else None)
        else:
            if not args.connection:
                raise SystemExit("用法：sinobee db migrate <來源連線> --to <目的連線> | --sqlite 檔案 | --sql-file 檔案")
            if args.to:
                target = {"type": "connection", "id": svc.get(args.to).id, "schema": args.target_schema}
            elif args.sqlite:
                target = {"type": "sqlite", "path": args.sqlite}
            elif args.sql_file:
                target = {"type": "sqlfile", "path": args.sql_file, "dialect": args.dialect or svc.get(args.connection).kind}
            else:
                raise SystemExit("請指定目的地：--to、--sqlite 或 --sql-file")
            opts = {"tables": [x.strip() for x in (args.tables or "").split(",") if x.strip()] or None,
                    "views": not args.no_views, "if_exists": "replace" if args.replace else "fail",
                    "skip_bad_rows": args.skip_bad_rows}
            plan = mm.plan(svc.get(args.connection).id, target, opts)
            print(f"來源：{plan['source']['name']}（{plan['source']['kind']}，{plan['source']['schema']}）")
            print(f"目的地：{plan['target'].get('label')}（{plan['target']['kind']}）")
            print(f"{len(plan['tables'])} 個資料表、{len(plan['views'])} 個檢視表，約 "
                  f"{sum(t['rows_estimate'] or 0 for t in plan['tables']):,} 列")
            for n in plan["notes"]:
                where = ".".join(x for x in (n["table"], n["column"]) if x)
                print(f"  {'⚠' if n['level'] == 'warn' else '·'} {where + '：' if where else ''}{n['message']}")
            if plan["conflicts"]:
                print(f"目的地已有同名資料表：{', '.join(plan['conflicts'])}" +
                      ("（會先刪除再重建）" if args.replace else "（加上 --replace 才會覆蓋）"))
            if args.plan:
                return
            if not args.yes:
                if input("開始遷移？輸入 yes 確認：").strip().lower() not in ("y", "yes"):
                    print("已取消")
                    return
            job = mm.get(mm.start(plan["source"]["id"], target, opts)["id"])
        last = ""
        while job.thread and job.thread.is_alive():
            d = job.public()
            pr = d["progress"]
            line = f"\r{d['phase']:<13} {pr['tables_done']}/{pr['tables_total']} 表  {pr['rows_copied']:,} 列"
            if line != last:
                print(line, end="", flush=True, file=sys.stderr)
                last = line
            job.thread.join(0.5)
        d = job.public()
        print(file=sys.stderr)
        out(["table", "status", "copied", "source", "target", "sample diff"],
            [[t["name"], t["status"], t["rows_copied"], t["source_rows"], t["target_rows"], t["sample_mismatches"]]
             for t in d["tables"]])
        for v in d.get("value_issues") or []:
            print(f"· {v['table']}.{v['column']}：{v['message']}（{v['rows']} 列）")
        for tname, b in (d.get("skipped_rows") or {}).items():
            print(f"⚠ {tname}：略過 {b['count']} 列，例如 {b['examples'][0]['row']}：{b['examples'][0]['error']}")
        if d["status"] != "done":
            print(f"錯誤：{d.get('error')}\n可修正後執行 sinobee db migrate --resume {d['id']}", file=sys.stderr)
            sys.exit(1)
        if d["target"]["type"] in ("sqlfile", "sqlite"):
            print(f"已寫入 {d['target']['path']}")
    except MigrationError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        mm.stop(job.id)
        job.thread.join(10)
        print(f"\n已停止；之後可執行 sinobee db migrate --resume {job.id}", file=sys.stderr)
        sys.exit(130)


def _share_cmd(args) -> None:
    from . import account, share
    from .config import Config
    cfg = Config.load()
    try:
        if args.action == "list":
            for x in share.mine(cfg):
                if not x.get("revoked"):
                    print(f"{x['url']:<44} {x['visibility']:<7} {x['views']:>5} 次  {x['title']}")
        elif args.action == "import":
            if not args.ref:
                raise SystemExit("用法：sinobee share import <分享連結>")
            from .memory.store import MemoryStore
            mem = MemoryStore(cfg.db_path)
            r = share.import_share(cfg, mem, args.ref)
            print(f"已匯入「{r['title']}」為新對話 {r['session_id']}（{r['messages']} 則訊息、{r['files']} 個附件）")
            print(f"繼續：sinobee chat -s {r['session_id']}")
        elif args.action == "revoke":
            if not args.ref:
                raise SystemExit("用法：sinobee share revoke <id>")
            share.revoke(cfg, args.ref)
            print("已撤回。")
    except (share.ShareError, account.AccountError) as e:
        print(f"錯誤：{e}")
        sys.exit(1)


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


def _work_cmd(args) -> None:
    """Background work from the shell: what ran, what runs now, what is scheduled next.
    Jobs are executed by `sinobee serve` (its scheduler); this command reads and manages them."""
    import time as _t
    from pathlib import Path
    from .config import Config
    from .work import schedule as S
    from .work.graph import catalog
    from .work.store import ACTIVE, WorkStore
    cfg = Config.load()
    store = WorkStore(Path(cfg.home) / "work.db")
    fmt = lambda t: _t.strftime("%m-%d %H:%M", _t.localtime(t)) if t else "—"   # noqa: E731
    act = args.action
    if act in ("status", "jobs"):
        jobs = store.jobs()
        if act == "status":
            running = store.runs(status=ACTIVE, limit=20)
            print("現在：" + ("沒有" if not running else ""))
            for r in running:
                print(f"  [{r['status']}] {r['name']}  {r['id']}  {r.get('activity') or ''}")
            pend = store.approvals("pending")
            if pend:
                print(f"等你核准：{len(pend)} 項（在 Web UI 的「工作」頁核准）")
            print("接下來：" + ("沒有排程" if not any(j['enabled'] and j['next_run_at'] for j in jobs) else ""))
            for j in sorted((j for j in jobs if j["enabled"] and j["next_run_at"]), key=lambda j: j["next_run_at"]):
                print(f"  {fmt(j['next_run_at'])}  {j['name']}（{S.describe(j['schedule'])}）")
            print("過去：")
            for r in store.runs(limit=args.limit if args.limit != 100 else 10):
                if r["status"] in ACTIVE:
                    continue
                print(f"  {fmt(r.get('finished_at') or r['created_at'])}  [{r['status']}] {r['name']}  "
                      f"{(r.get('error') or r.get('summary') or '')[:80]}")
            return
        if not jobs:
            print("（沒有排程工作）")
        for j in jobs:
            print(f"{j['id']}  {'on ' if j['enabled'] else 'off'}  {j['name']}  {S.describe(j['schedule'])}  "
                  f"下次 {fmt(j['next_run_at']) if j['enabled'] else '—'}  上次 {j.get('last_status') or '—'}")
    elif act == "runs":
        for r in store.runs(job_id=args.ref, limit=args.limit):
            print(f"{r['id']}  {fmt(r['created_at'])}  [{r['status']}] {r['name']}  "
                  f"{(r.get('error') or r.get('summary') or r.get('activity') or '')[:100]}")
    elif act == "workflows":
        for w in catalog(Path(cfg.home)).values():
            ins = ", ".join(f"{k}{'' if v.get('required') else '?'}" for k, v in (w.get("inputs") or {}).items())
            print(f"{w['name']}  {w['title']}  ({ins})\n  {w['description']}")
    elif act in ("pause", "resume", "delete"):
        if not args.ref or not store.job(args.ref):
            print("用法：sinobee work " + act + " <job id>（見 sinobee work jobs）")
            sys.exit(1)
        if act == "delete":
            store.delete_job(args.ref)
        else:
            job = store.job(args.ref)
            nxt = S.next_run(job["schedule"], _t.time(), job.get("last_run_at")) if act == "resume" else None
            store.update_job(args.ref, enabled=1 if act == "resume" else 0, next_run_at=nxt)
        print("ok（若 sinobee serve 正在執行，幾秒內生效）")


def _eval_cmd(args) -> None:
    """Evaluations: run fixed tasks in throw-away workspaces and grade them (see sinobee/evals)."""
    import json as _json
    from . import evals as E
    from .config import Config
    if args.action == "list":
        cfg = Config.load()
        from .skills import manager_from_config
        skills = manager_from_config(cfg)
        for s in E.suites(cfg.home, skills):
            print(f"{s['name']:<28} {s['tasks']:>3} tasks  {s['title']}")
        runs = E.results(cfg.home, 10)
        if runs:
            print("\nrecent runs:")
            for r in runs:
                t = r.get("totals") or {}
                print(f"  {r['id']:<34} {r['status']:<8} pass@k {t.get('pass_at_k', 0):.0%} pass^k "
                      f"{t.get('pass_hat_k', 0):.0%} ${t.get('cost_usd', 0):.4f}"
                      + (f"  REGRESSIONS: {', '.join(r['regressions'])}" if r.get("regressions") else ""))
        return
    if args.action == "show":
        cfg = Config.load()
        from .evals.runner import report
        runs = E.results(cfg.home, 1)
        rid = args.target or (runs[0]["id"] if runs else None)
        if not rid:
            raise SystemExit("no eval runs yet")
        doc = E.result(cfg.home, rid)
        print(_json.dumps(doc, ensure_ascii=False, indent=1) if args.json else report(doc))
        return
    if args.action == "export":
        # copy finished runs as JSON into a folder (e.g. to write the evaluation report from them)
        cfg = Config.load()
        out = Path(args.out or ".").expanduser()
        out.mkdir(parents=True, exist_ok=True)
        ids = ([args.target] if args.target else []) + list(args.ids or [])
        for rid in ids or [r["id"] for r in E.results(cfg.home, 1)]:
            doc = E.result(cfg.home, rid)
            (out / f"{rid}.json").write_text(_json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            print(out / f"{rid}.json")
        return
    if args.action == "publish":
        from .evals import publish as P
        cfg = Config.load()
        ids = ([args.target] if args.target else []) + list(args.ids or [])
        if not ids:
            raise SystemExit("give the run ids to publish (runs of one combination are merged into one report): "
                             "sinobee eval publish ID ID ...")
        try:
            res = P.publish(cfg, [E.result(cfg.home, rid) for rid in ids])
        except (E.EvalError, P.PublishError) as e:
            raise SystemExit(f"publish: {e}") from None
        for r in res:
            print(r.get("url") or _json.dumps(r, ensure_ascii=False))
        return
    if args.action == "suites":
        cfg = Config.load()
        from .evals.authoring import list_suites
        for m in list_suites(cfg.home, args.target):
            print(f"suite:{m['hash'][:12]}  {m['skill']}@{m.get('skill_version') or '?'}  {m.get('inputs')} 份輸入 "
                  f"{m.get('questions')} 題  by {m.get('evaluator') or '?'}  {m.get('title') or ''}"
                  + (f"  (derived from {m['derived_from'][:12]})" if m.get("derived_from") else ""))
        return
    if args.action == "evaluator":
        from .evals import authoring as A

        async def check():
            rt = _runtime(args)
            name = args.target or "eval-authoring"
            if args.test:
                res = await A.behavior_test(rt, name)
                print(f"behavior test: {res['status']}" + "".join(f"\n  ✕ {v['tool']}: {v['what']}"
                                                                  for v in res["violations"]))
            return A.security_status(rt.config.home, rt.skills.get(name))
        st = asyncio.run(check())
        if args.json:
            print(_json.dumps(st, ensure_ascii=False, indent=1))
            return
        mark = lambda v: "✓" if v is True else "✕" if v is False else "–"    # noqa: E731
        print(f"{st['name']}@{st['version']}  安全性評鑑 {st['passed']}/4" + ("  （已封鎖）" if st["blocked"] else ""))
        print(f"  {mark(st['static']['ok'])} 靜態掃描" + "".join(f"\n      {x}" for x in st["static"]["issues"]))
        print(f"  {mark(st['behavior'].get('ok'))} 沙箱行為（{st['behavior'].get('runs', 0)} 次）")
        print(f"  {mark(st['suite'].get('ok'))} 題組檢查（{st['suite'].get('checked', 0)} 份）")
        print(f"  {mark(st['review']['ok'])} 人工審核")
        return
    if args.action == "author":
        from .evals import authoring as A
        if not args.target or not args.session:
            raise SystemExit("usage: sinobee eval author <skill> --session <session id> [--evaluator eval-authoring]")

        async def author():
            rt = _runtime(args)
            try:
                mat = A.task_material(rt, args.session)
                return await A.generate_suite(rt, args.evaluator, args.target, request=mat["request"],
                                              outputs=mat["outputs"], answer=mat["answer"], session_id=args.session,
                                              turn_id=mat["turn_id"])
            except E.EvalError as e:
                raise SystemExit(f"author: {e}") from None
        res = asyncio.run(author())
        if res["status"] == "ok":
            print(f"suite:{res['hash'][:12]}  ({res['rounds']} 輪修正)\n"
                  f"run:  sinobee eval run suite:{res['hash'][:12]} --thinking <model> -m <model> -k 3")
        else:
            print(f"{res['status']}:" + "".join(f"\n  - {p}" for p in res.get("problems") or [])
                  + "".join(f"\n  ✕ {v['tool']}: {v['what']}" for v in res.get("violations") or []))
        return
    if args.action == "compare":
        from .evals.compare import compare
        ids = ([args.target] if args.target else []) + list(args.ids or [])

        async def cmp():
            rt = _runtime(args)
            try:
                return await compare(rt, [E.result(rt.config.home, rid) for rid in ids], args.judge)
            except E.EvalError as e:
                raise SystemExit(f"compare: {e}") from None
        res = asyncio.run(cmp())
        qn = res.get("qualified") or {}
        for k, v in res["scores"].items():
            print(f"{k:<60} {v['wins']:>3}/{v['total']:<3} {100 * v['wins'] / (v['total'] or 1):.0f}%"
                  f"   ({qn.get(k, 0)} qualified trials)")
        if not res["pairs"]:
            print("no comparison was made: creativity compares only qualified trials (score >= 70 and no critical "
                  "question failed), and fewer than two combinations had one. See `sinobee eval show <run id>` for "
                  "what failed.")
        return

    async def go():
        from .evals.runner import report, run
        from .tools import browser
        rt = _runtime(args)

        def emit(kind, data):
            if kind == "eval_progress" and not args.json:
                print(f"[{data['done'] + 1}/{data['total']}] {data['task']} …", flush=True)

        try:
            doc = await run(rt, args.target or "core", k=args.k, task_ids=args.task or None, emit=emit, keep=args.keep,
                            model=args.model, judge=args.judge, thinking=args.thinking, judge2=args.judge2)
        except E.EvalError as e:
            raise SystemExit(f"eval: {e}") from None
        finally:
            await browser.shutdown()
        print(_json.dumps(doc, ensure_ascii=False, indent=1) if args.json else report(doc))
        if doc.get("regressions"):
            raise SystemExit(2)
    asyncio.run(go())


def _control_log_cmd(args) -> None:
    """Every consult, skipped consult and stop, with the exact trigger, thresholds and evidence."""
    import json as _json
    import time as _t
    from .agent import progress as P
    from .config import Config
    cfg = Config.load()
    rows = P.read_log(cfg.home, session=args.session, trigger=args.trigger, event=args.event, limit=args.limit)
    if args.stats:
        counts: dict = {}
        for e in P.read_log(cfg.home, limit=100000):
            k = (e.get("event"), e.get("trigger"))
            counts[k] = counts.get(k, 0) + 1
        for (ev, tr), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{n:>6}  {ev:<18} {tr:<20} {P.TRIGGERS.get(tr, '')}")
        return
    if not rows:
        print("沒有紀錄")
    for e in rows:
        if args.json:
            print(_json.dumps(e, ensure_ascii=False))
            continue
        when = _t.strftime("%m-%d %H:%M:%S", _t.localtime(e.get("t") or 0))
        print(f"{when} {str(e.get('session_id'))[:12]:<12} {P.describe(e)}")
        if args.verbose_evidence:
            print("    門檻：" + ", ".join(f"{k}={v}" for k, v in (e.get("thresholds") or {}).items()))
            for line in e.get("evidence") or []:
                print("    " + line)


def _replay_cmd(args) -> None:
    """Replay stored turns through the progress rules: what K / N would have done on real work."""
    import json as _json
    from .agent import replay as R
    from .config import Config
    from .memory.store import MemoryStore
    cfg = Config.load()
    if args.k:
        cfg.data["agent"].setdefault("progress", {})["k"] = args.k
    mem = MemoryStore(cfg.db_path)
    rows = R.replay_sessions(mem, cfg, session=args.session, last=args.last)
    if args.json:
        print(_json.dumps({"summary": R.summary(rows), "turns": [{k: v for k, v in r.items() if k != "trace"}
                                                                 for r in rows]}, ensure_ascii=False, indent=1))
        return
    for r in rows:
        stop = f"  ⏹ {r['stop']['trigger']} @ {r['stop']['step']}" if r["stop"] else ""
        print(f"{r['session_id'][:12]}#{r['turn']:<3} {r['steps']:>4} 步  有進度 {r['progress_steps']:>4}  "
              f"最長無進度 {r['max_no_progress']:>3}  請教 {len(r['decisions'])}{stop}  {r['request'][:50]!r}")
        for d in r["decisions"]:
            print(f"      第 {d['step']} 步 {d['trigger']}: {d['detail'][:120]}")
        if args.steps:
            for t in r["trace"]:
                print(f"      #{t['step']:<4} {'✓' if t['progress'] else '·'} {t['why'][:160]}")
    print("\n" + _json.dumps(R.summary(rows), ensure_ascii=False, indent=1))


def _observe_cmd(args) -> None:
    """Phase 4: what the stop rules did, and data-based recommendations for their thresholds."""
    import json as _json
    from .agent import observe
    from .config import Config
    from .memory.store import MemoryStore
    from .work.store import WorkStore
    cfg = Config.load()
    store = WorkStore(cfg.home / "work.db")
    if args.action == "stats":
        s = observe.stats(cfg.home, store, args.days)
        if args.json:
            print(_json.dumps(s, ensure_ascii=False, indent=1))
            return
        t = s["turns"]
        print(f"最近 {args.days:g} 天：{t['n']} 個回合，步數 p50 {t['steps_p50']}／p90 {t['steps_p90']}／最多 {t['steps_max']}，"
              f"超過 60 步 {t['over_60']} 個；狀態 {t['status']}")
        c = s["consults"]
        print(f"請教思維模型 {c['n']} 次，決定 {c['decisions']}；輔助模型檢查 {c['semantic_checked']} 次，其中認為其實有進展 "
              f"{c['semantic_not_spinning']} 次")
        print(f"安全閥 {len(s['safety_valve'])} 次" + "".join(f"\n  {x['session_id']} 報告 {x['failure_id'] or '（無）'}"
                                                       for x in s["safety_valve"]))
        b = s["budget"]
        print(f"預算檢查點 {b['checkpoints']}；達到上限 {b['caps']} 次、追加 {b['extended']} 次；部分交付 {b['partial_deliveries']} 次")
        print(f"里程碑事件 {s['milestones']}")
        print(f"自動適應的模型參數 {s['models_adapted']}")
        for x in s["counts"][:20]:
            print(f"{x['n']:>6}  {x['event']:<18} {x['trigger']:<22} {x['label']}")
        return
    mem = MemoryStore(cfg.db_path)
    r = observe.tune(cfg.home, store, mem, cfg, args.last)
    if args.apply:
        chosen = {x["key"]: x["suggested"] for x in r["recommendations"] if x["apply"] and
                  (args.apply == ["all"] or x["key"] in args.apply)}
        print("已套用：", observe.apply(cfg, chosen) or "（沒有可套用的建議）")
        return
    if args.json:
        print(_json.dumps(r, ensure_ascii=False, indent=1))
        return
    print(f"重放 {r['turns_replayed']} 個回合（順利 {r['turns_ok']}、未完成 {r['turns_bad']}）")
    for x in r["recommendations"]:
        sug = "—" if x["suggested"] is None else x["suggested"]
        print(f"{x['key']:<32} 目前 {x['current']!s:<6} 建議 {sug!s:<6} 信心 {x['confidence']:<7} {x['evidence']}")
    print("任務類型基準：", _json.dumps(r["baselines"], ensure_ascii=False))
    print("每個任務上限：", r["cap"])
    print("套用：sinobee tune --apply all（或指定 key）")


def _runs_cmd(args) -> None:
    """Run reports: what every task cost (per step) and what it produced."""
    import json as _json
    import time as _t
    from .agent import runreport as RR
    from .config import Config
    cfg = Config.load()
    if args.id:
        try:
            r = RR.get(cfg.home, args.id)
        except KeyError:
            raise SystemExit(f"no run report {args.id}") from None
        print(_json.dumps(r, ensure_ascii=False, indent=1) if args.json else RR.summary_text(r))
        return
    rows = RR.listing(cfg.home, days=args.days, sort=args.sort, limit=args.limit)
    if args.json:
        print(_json.dumps(rows, ensure_ascii=False, indent=1))
        return
    if not rows:
        print("沒有執行報告")
    for r in rows:
        when = _t.strftime("%m-%d %H:%M", _t.localtime(r["created"] or 0))
        out = "沒有產出" if r["no_output"] else f"{r['files']} 檔・清單 {r['checklist']}"
        top = r.get("top_step") or {}
        print(f"{r['id']:<36} {when} {r['status'] or '':<10} {r['tokens'] or 0:>10,} tok {r['steps'] or 0:>4} 步 "
              f"{out:<16} 最貴 #{top.get('step', '-')} {top.get('tokens') or 0:,}  {r['request'][:40]}")


def _failures_cmd(args) -> None:
    """Failure reports: what a task that did not finish did, step by step, and the thinking model's diagnosis."""
    import json as _json
    import time as _t
    from .agent import failures as F
    from .config import Config
    cfg = Config.load()
    if args.action == "list":
        rows = F.listing(cfg.home, args.limit)
        if not rows:
            print("沒有失敗報告")
        for r in rows:
            when = _t.strftime("%m-%d %H:%M", _t.localtime(r["created"] or 0))
            print(f"{r['id']:<36} {when}  {r['status']:<10} {(r.get('category') or '(診斷中)'):<22} {r['summary'][:60]}")
        return
    if not args.id:
        raise SystemExit("give a report id (sinobee failures list)")
    try:
        if args.action == "show":
            r = F.get(cfg.home, args.id)
            if args.json:
                print(_json.dumps(r, ensure_ascii=False, indent=1))
                return
            print(F.summary_text(r, 20000))
            if r.get("diagnosis"):
                print("\nDIAGNOSIS:", _json.dumps(r["diagnosis"], ensure_ascii=False, indent=1))
        elif args.action == "regression":
            print("added regression eval", F.to_regression(cfg.home, args.id)["id"])
        elif args.action == "delete":
            F.delete(cfg.home, args.id)
            print("deleted")
    except KeyError:
        raise SystemExit(f"no failure report {args.id}") from None


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
            sp.add_argument("--host", help="address to listen on (default 127.0.0.1 = this computer only)")
            sp.add_argument("--port", type=int)
            sp.add_argument("--public", action="store_true",
                            help="listen on all interfaces so other devices can connect (requires an access token; "
                                 "one is generated and saved if none is set)")
            sp.add_argument("--token", help="access token for this run (default: SINOBEE_SERVER_TOKEN or the saved one)")
            sp.add_argument("--new-token", action="store_true", help="replace the saved access token")
    m = sub.add_parser("memory")
    m.add_argument("action", choices=["list", "search", "forget", "pin", "unpin", "restore", "consolidate", "maintain",
                                      "stats", "compress"])
    m.add_argument("query", nargs="*")
    m.add_argument("--tier", help="core | short | long | permanent | archive")
    m.add_argument("--hard", action="store_true")
    m.add_argument("--force", action="store_true", help="forget: also remove a permanent memory")
    m.add_argument("--apply", action="store_true", help="compress: apply (default is a preview, extractive merge)")
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
    dbp = sub.add_parser("db", help="query saved database connections from the shell (read-only)")
    dbp.add_argument("action", choices=["list", "schema", "query", "queries", "migrate", "migrations"])
    dbp.add_argument("connection", nargs="?", help="connection id or name (migrate: the source)")
    dbp.add_argument("arg", nargs="?", help="schema: table name; query: SQL (or - for stdin)")
    dbp.add_argument("--format", choices=["md", "csv", "json"], default="md")
    dbp.add_argument("--limit", type=int, default=100)
    mg = dbp.add_argument_group("migrate (copy structure + data)")
    mg.add_argument("--to", help="target: saved connection id or name")
    mg.add_argument("--target-schema", help="PostgreSQL schema in the target (default public)")
    mg.add_argument("--sqlite", help="target: a SQLite file (created if missing)")
    mg.add_argument("--sql-file", help="target: write a .sql (or .sql.gz) script")
    mg.add_argument("--dialect", choices=["mysql", "postgres", "sqlite"], help="SQL dialect of --sql-file")
    mg.add_argument("--tables", help="comma-separated table names (default: all)")
    mg.add_argument("--no-views", action="store_true")
    mg.add_argument("--replace", action="store_true", help="drop and recreate tables that already exist in the target")
    mg.add_argument("--skip-bad-rows", action="store_true", help="skip rows the target refuses instead of stopping")
    mg.add_argument("--plan", action="store_true", help="only show what would happen")
    mg.add_argument("--resume", metavar="JOB", help="continue a stopped or failed migration")
    mg.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sh = sub.add_parser("share", help="conversations shared on sinobee.ai: list, import, revoke")
    sh.add_argument("action", choices=["list", "import", "revoke"])
    sh.add_argument("ref", nargs="?", help="share URL (import) or id (revoke)")
    tl = sub.add_parser("tools", help="database tools on sinobee.ai: list, search, publish, install")
    tl.add_argument("action", choices=["list", "search", "publish", "install"])
    tl.add_argument("ref", nargs="?", help="connection id/name (publish), <publisher>/<name> (install), query (search)")
    tl.add_argument("--public", action="store_true", help="publish for everyone (reviewed; no credentials)")
    tl.add_argument("--sync", action="store_true", help="private only: keep connection + password in your encrypted vault")
    tl.add_argument("--no-schema", action="store_true", help="do not include table/column names")
    wk = sub.add_parser("work", help="background work: status (past/now/next), jobs, runs, workflows, pause/resume/delete")
    wk.add_argument("action", nargs="?", default="status",
                    choices=["status", "jobs", "runs", "workflows", "pause", "resume", "delete"])
    wk.add_argument("ref", nargs="?", help="job id (runs/pause/resume/delete)")
    wk.add_argument("--limit", type=int, default=100)
    ev = sub.add_parser("eval", help="evaluations: run fixed tasks in a throw-away workspace and grade the results")
    ev.add_argument("action", choices=["list", "run", "show", "export", "publish", "compare", "author", "suites",
                                       "evaluator"])
    ev.add_argument("target", nargs="?", help="run: core | regressions | skill:<name> | suite:<hash> | file.json; "
                                              "show: run id; author / suites: skill name; evaluator: its name")
    ev.add_argument("--evaluator", default="eval-authoring", help="author: the evaluation skill that writes the suite")
    ev.add_argument("--session", help="author: the session whose finished task the suite is written from")
    ev.add_argument("--test", action="store_true", help="evaluator: run its sandbox behavior test")
    ev.add_argument("-k", type=int, help="trials per task (pass@k / pass^k); default: each task's own")
    ev.add_argument("--task", action="append", help="only this task id (repeatable)")
    ev.add_argument("--keep", action="store_true", help="keep the trial workspaces for inspection")
    ev.add_argument("--json", action="store_true")
    ev.add_argument("-m", "--model", help="run: the model under test; alone it plans, runs, reviews and assists; "
                                          "with --thinking it is the execution model of the combination")
    ev.add_argument("--thinking", help="run: the thinking model of a skill evaluation's combination (plans, reviews)")
    ev.add_argument("--judge", help="run / compare: the model that grades (default: reviewer / thinking model)")
    ev.add_argument("--judge2", help="run: a second judge (another model family); its agreement is published")
    ev.add_argument("ids", nargs="*", help="export / publish / compare: more run ids")
    ev.add_argument("--out", help="export: folder for the JSON files")
    ev.add_argument("-c", "--config")
    ev.add_argument("--approval", default=None, help=argparse.SUPPRESS)
    fl = sub.add_parser("failures", help="failure reports of tasks that did not finish (work log + diagnosis)")
    fl.add_argument("action", nargs="?", default="list", choices=["list", "show", "regression", "delete"])
    fl.add_argument("id", nargs="?")
    fl.add_argument("--limit", type=int, default=30)
    fl.add_argument("--json", action="store_true")
    rr = sub.add_parser("runs", help="every task's cost and output (most spent for the least first)")
    rr.add_argument("id", nargs="?", help="show one report: the cost of every step")
    rr.add_argument("--days", type=float, default=30)
    rr.add_argument("--sort", choices=["waste", "tokens", "recent"], default="waste")
    rr.add_argument("--limit", type=int, default=20)
    rr.add_argument("--json", action="store_true")
    cl = sub.add_parser("control-log", help="why executions were questioned or stopped (consults, stops, triggers)")
    cl.add_argument("--session")
    cl.add_argument("--trigger")
    cl.add_argument("--event", choices=["consult_requested", "consult", "consult_skipped", "stop"])
    cl.add_argument("--limit", type=int, default=50)
    cl.add_argument("--stats", action="store_true", help="counts per event and trigger")
    cl.add_argument("--evidence", dest="verbose_evidence", action="store_true", help="show thresholds and evidence")
    cl.add_argument("--json", action="store_true")
    rp = sub.add_parser("progress-replay", help="replay stored turns through the progress rules (no model calls)")
    rp.add_argument("--session")
    rp.add_argument("--last", type=int, default=50)
    rp.add_argument("--k", type=int, help="try another K")
    rp.add_argument("--steps", action="store_true", help="every step's verdict")
    rp.add_argument("--json", action="store_true")
    ob = sub.add_parser("observe", help="what the stop rules, milestones and budgets did (counts, safety valve, budget)")
    ob.add_argument("--days", type=float, default=30)
    ob.add_argument("--json", action="store_true")
    ob.set_defaults(action="stats")
    tn = sub.add_parser("tune", help="data-based recommendations for K, N and the budget's synthetic factor")
    tn.add_argument("--last", type=int, default=200, help="turns to replay")
    tn.add_argument("--apply", nargs="*", help="apply recommendations: all, or keys like agent.progress.k")
    tn.add_argument("--json", action="store_true")
    tn.set_defaults(action="tune")
    sb = sub.add_parser("sandbox", help="the Docker sandbox the tools run in (build the image, list, stop, remove)")
    sb.add_argument("action", choices=["build", "status", "stop", "rm", "sweep", "probe-overlay"])
    sb.add_argument("conversation", nargs="?", help="stop / rm: the conversation (session id); probe-overlay: a folder")
    sb.add_argument("--office", action="store_true", help="build: the image with LibreOffice")
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
    elif args.cmd == "share":
        _share_cmd(args)
    elif args.cmd == "db":
        _db_cmd(args)
    elif args.cmd == "work":
        _work_cmd(args)
    elif args.cmd == "eval":
        _eval_cmd(args)
    elif args.cmd == "failures":
        _failures_cmd(args)
    elif args.cmd == "runs":
        _runs_cmd(args)
    elif args.cmd == "control-log":
        _control_log_cmd(args)
    elif args.cmd == "progress-replay":
        _replay_cmd(args)
    elif args.cmd in ("observe", "tune"):
        _observe_cmd(args)
    elif args.cmd == "sandbox":
        _sandbox_cmd(args)


def _sandbox_cmd(args) -> None:
    """sinobee sandbox build | status | stop <conversation> | rm <conversation> | sweep | probe-overlay <folder>"""
    import subprocess
    from pathlib import Path as _P

    from .sandbox import executor_kind, wanted_kind
    from .sandbox.manager import image_name, manager_for
    cfg = Config.load()
    m = manager_for(cfg)
    if args.action == "build":
        root = _P(__file__).resolve().parent.parent
        dockerfile = root / "docker" / "sandbox.Dockerfile"
        if not dockerfile.exists():
            sys.exit(f"{dockerfile} not found: build from a SinoBee source checkout")
        if args.office:
            cfg.data.setdefault("sandbox", {})["image"] = "office"
        tag = image_name(cfg)
        cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag] + (["--build-arg", "OFFICE=1"] if args.office else []) + [str(root)]
        print("building", tag, "(the first time downloads about 2 GB) ...")
        sys.exit(subprocess.call(cmd))
    if args.action == "probe-overlay":
        from .sandbox import overlay
        if not args.conversation:
            sys.exit("usage: sinobee sandbox probe-overlay <folder>")
        res = overlay.probe(args.conversation)
        print(overlay.report(res))
        sys.exit(0 if res["ok"] else 1)
    if args.action == "status":
        why = m.unavailable(max_age=0)
        print(f"wanted: {wanted_kind(cfg)}   running tools on: {executor_kind(cfg)}   image: {image_name(cfg)}")
        print("sandbox: " + ("ready" if not why else f"not available - {why}"))
        if not why:
            print(f"limits per sandbox: {' CPU / '.join(m.limits())}")
            for r in asyncio.run(m.list()):
                print(f"  {r['name']:<40} {r['state']:<9} {r['status']}")
        return
    if args.action in ("stop", "rm"):
        if not args.conversation:
            sys.exit("give the conversation (session id); `sinobee sandbox status` lists them")
        asyncio.run(m.stop(args.conversation) if args.action == "stop" else m.remove(args.conversation))
        print("done")
        return
    if args.action == "sweep":
        print(asyncio.run(m.sweep()))


if __name__ == "__main__":
    main()
