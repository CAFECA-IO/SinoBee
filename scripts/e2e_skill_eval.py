#!/usr/bin/env python3
"""End-to-end check of "package a task into a skill, then evaluate it" (skill-pack M5), against a sinobee.ai.

    # with the models of a real SinoBee home (its settings.json and secrets.json are copied; the home is not changed)
    python scripts/e2e_skill_eval.py --from-home ~/.sinobee --out /tmp/sb-e2e \\
        --combo anthropic/claude-opus-5-5+anthropic/claude-sonnet-5-5 --combo gemini/gemini-3-pro+gemini/gemini-3.8-flash \\
        -k 3 --judge gemini/gemini-3-pro --visibility private

    # the whole flow with scripted models (CI / a local sinobee.ai), including a second account and a community
    # evaluation skill
    python scripts/e2e_skill_eval.py --scripted --hub http://127.0.0.1:3201 --token sbh_… --token2 sbh_… --out /tmp/e2e

Steps (each one records checks; a failed check does not stop the later ones unless they depend on it):
  A  a meeting-minutes task with no skill → package it (privacy check, parameters, files) → skill (experimental)
  B  a task that uses the skill → the default evaluation skill writes a suite in the sandbox → a campaign over the
     combinations (interleaved, within budget) → the four answers and the checks before submitting → publish
  C  a second account: the shared suite is downloaded and checked, one more combination is run and published; the
     reports of both accounts land on the same suite                             (needs --token2 and a public B)
  D  a community evaluation skill: static scan, sandbox behavior test, published to Skill Hub, listed for the skill,
     installed by the second account                                             (needs --token2)
Writes report.md and report.json to --out. Exit code 1 when a check failed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# fictional meetings (no real people): the task input, a second meeting for the evaluated task
WEEK1 = """週會 2026-01-07
主持：林主任；出席：王工程師、陳設計師、趙業務
1. 新版發布
林主任：新版下週三發布，發布前要完成回歸測試。
王工程師：回歸測試我來，週一前完成。
2. 文件
陳設計師：使用說明的截圖要更新，月底前。
3. 客戶回饋
趙業務：兩家客戶反映匯出 CSV 欄位太多，希望能選欄位。
林主任：先記下，下次討論優先順序。
決議：新版下週三發布；CSV 欄位選擇列入下次議程。
"""
WEEK2 = """週會 2026-01-14
主持：林主任；出席：王工程師、陳設計師、周測試
1. 發布檢討
王工程師：發布延後一天，因為回歸測試發現兩個問題，已修好。
2. CSV 欄位選擇
林主任：這個功能排進二月，由王工程師負責，二月底前完成設計。
周測試：我需要測試資料，下週五前準備。
3. 其他
陳設計師：新首頁草稿下週二給大家看。
決議：CSV 欄位選擇二月完成；下次週會改到週四。
"""


class Report:
    def __init__(self, out: Path):
        self.out = out
        self.items: list[dict] = []
        self.data: dict = {}
        self.started = time.time()

    def check(self, step: str, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append({"step": step, "check": name, "ok": bool(ok), "detail": str(detail)[:600]})
        print(f"[{step}] {'✓' if ok else '✕'} {name}{' — ' + str(detail)[:200] if detail else ''}", flush=True)
        return bool(ok)

    def failed(self) -> int:
        return sum(1 for i in self.items if not i["ok"])

    def write(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        doc = {"started": self.started, "finished": time.time(), "checks": self.items, "data": self.data,
               "failed": self.failed()}
        (self.out / "report.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        lines = [f"# 打包技能與評鑑：端到端檢查（{time.strftime('%Y-%m-%d %H:%M')}）", "",
                 f"{len(self.items) - self.failed()}/{len(self.items)} 項通過，耗時 {round(time.time() - self.started)} 秒。", ""]
        step = None
        for i in self.items:
            if i["step"] != step:
                step = i["step"]
                lines += ["", f"## {step}", ""]
            lines.append(f"- {'✓' if i['ok'] else '✕'} {i['check']}" + (f"：{i['detail']}" if i["detail"] else ""))
        board = self.data.get("board")
        if board:
            lines += ["", "## 結果（四個問題）", ""]
            for axis, label in (("speed", "最快"), ("cost", "最省"), ("quality", "品質最好"), ("creativity", "最有創意")):
                a = board["awards"].get(axis) or {}
                lines.append(f"- {label}：{a.get('winner') or '—'}" + (f"（並列：{'、'.join(a['tied'])}）" if a.get("tied") else ""))
        (self.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- homes

def make_home(path: Path, args, token: str | None):
    """A SinoBee home for this run: the real home's settings and keys (copied), or scripted defaults."""
    path.mkdir(parents=True, exist_ok=True)
    if args.from_home:
        src = Path(args.from_home).expanduser()
        for f in ("settings.json", "secrets.json"):
            if (src / f).is_file() and not (path / f).exists():
                shutil.copyfile(src / f, path / f)
    os.environ["SINOBEE_HOME"] = str(path)
    os.environ["SINOBEE_PROJECT"] = str(path)
    os.environ.pop("SINOBEE_WORKSPACE", None)
    from sinobee import account
    from sinobee.config import Config
    cfg = Config.load()
    cfg.data["permissions"]["approval"] = "auto"
    cfg.data["permissions"]["toolsets"] = ["files", "shell", "skills", "memory"]   # no browser or desktop control
    cfg.data["permissions"]["toolsets_v"] = 2
    cfg.data["memory"].update(auto_maintain=False, schedule=False)
    if args.hub:
        cfg.data.setdefault("skills", {})["hub_url"] = args.hub
    if args.scripted:
        cfg.data["agent"].update(verify=False, plan="off", failure_diagnosis=False)
        cfg.data["memory"].update(flush_on_compact=False)
        cfg.data["model"].update(default="anthropic/claude-sonnet-5-5", thinking="anthropic/claude-opus-5-5",
                                 reviewer="gemini/gemini-3-pro", fallbacks=[])
        import scripted
        cfg.data["pricing"] = scripted.prices()
    if token:
        cfg.secrets.set_secret(account.TOKEN, token)
        cfg.secrets.set_secret(account.ACCOUNT, json.dumps({"scopes": account.SCOPES.split()}))
        try:
            me = account.whoami(cfg, remote=True)
        except Exception:  # noqa: BLE001
            me = None
        if me and me.get("publisher") is None and not me.get("offline"):
            print(f"note: the account at {account.base_url(cfg)} has no publisher id (needed to publish skills)")
    return cfg


def runtime(cfg, args, step: str):
    from sinobee.agent import Runtime
    if not args.scripted:
        return Runtime(cfg)
    from scripted import main_provider
    return Runtime(cfg, **main_provider(step))


# ---------------------------------------------------------------- steps

async def run_task(rt, sid: str, prompt: str, files: dict[str, str]) -> dict:
    from sinobee.agent import Agent
    for name, text in files.items():
        (rt.config.workspace / name).write_text(text, encoding="utf-8")
    events: list = []

    async def approve(*_a, **_k):
        return True
    agent = Agent(rt, session_id=sid, platform="web", emit=lambda k, d: events.append((k, d)), approve=approve)
    await agent.run(prompt)
    return next((d for k, d in reversed(events) if k == "turn_end"), {})


async def step_a(rep: Report, rt, args) -> str | None:
    from sinobee.skills import package
    end = await run_task(rt, "e2e-a", "把 week1.txt 這份逐字稿整理成會議紀錄 minutes.md（出席、議題、決議、待辦），待辦要有負責人和期限"
                         "（日期以會議日期換算），另外寫一支 to_csv.py 把待辦輸出成 actions.csv。", {"week1.txt": WEEK1})
    rep.check("A 打包技能", "任務完成", end.get("status") == "done", end.get("status"))
    rep.check("A 打包技能", "任務沒有用到技能", not end.get("skills"), ", ".join(end.get("skills") or []))
    rep.data["a_package_hint"] = end.get("package_hint")
    try:
        d = await package.draft_from_session(rt, "e2e-a")
    except Exception as e:  # noqa: BLE001
        rep.check("A 打包技能", "產生技能草稿", False, e)
        return None
    rep.check("A 打包技能", "產生技能草稿", bool(d.get("body")), f"{d['name']}：{len(d.get('params') or [])} 個參數、"
              f"{sum(1 for f in d['files'] if f['include'])} 個檔案收進技能")
    text = json.dumps({k: d.get(k) for k in ("description", "body", "params", "title", "triggers")}, ensure_ascii=False)
    leaked = [n for n in ("林主任", "王工程師", "陳設計師", "趙業務") if n in text]
    rep.check("A 打包技能", "草稿不含這次會議的人名", not leaked, "、".join(leaked))
    rep.check("A 打包技能", "這次的具體值變成參數", bool(d.get("params")), ", ".join(p["name"] for p in d.get("params") or []))
    rep.check("A 打包技能", "輸入檔沒有收進技能",
              not any(f["include"] for f in d["files"] if f["source"].endswith("week1.txt")))
    name = args.skill_name
    try:
        res = package.save_draft(rt, d["id"], {"name": name, "publish": bool(args.publish_skill)})
    except Exception as e:  # noqa: BLE001
        rep.check("A 打包技能", "儲存技能", False, e)
        return None
    rt.skills.refresh()
    st = rt.skills.stats(name).get("status")
    rep.check("A 打包技能", "儲存為實驗中的本機技能", rt.skills.get(name).trust == "local" and st == "candidate", st)
    if args.publish_skill:
        rep.check("A 打包技能", "發布到 Skill Hub", bool(res.get("published")) and not res.get("publish_error"),
                  res.get("publish_error") or res.get("published"))
    return name


async def step_b(rep: Report, rt, args, skill: str) -> dict | None:
    from sinobee.evals import EvalError, authoring as A, campaign as C
    end = await run_task(rt, "e2e-b", f"/{skill} 整理 week2.txt，會議日期 2026-01-14", {"week2.txt": WEEK2})
    rep.check("B 評鑑", "用技能完成任務", end.get("status") == "done" and skill in (end.get("skills") or []),
              f"{end.get('status')} {end.get('skills')}")
    ev = rt.skills.get(args.evaluator)
    st = A.security_status(rt.config.home, ev)
    rep.check("B 評鑑", f"評鑑技能 {ev.name} 沒有被封鎖", not st["blocked"], f"安全性評鑑 {st['passed']}/4")
    mat = A.task_material(rt, "e2e-b")
    res = await A.generate_suite(rt, args.evaluator, skill, request=mat["request"], outputs=mat["outputs"],
                                 answer=mat["answer"], session_id="e2e-b", turn_id=mat["turn_id"])
    if not rep.check("B 評鑑", "評鑑技能在沙箱中出題並通過題組檢查", res["status"] == "ok",
                     res.get("hash") or "; ".join(res.get("problems") or [str(v) for v in res.get("violations") or []])):
        return None
    ref = "suite:" + res["hash"]
    desc = A.describe(rt.config.home, res["hash"])
    rep.check("B 評鑑", "題組有安全性分類與注入輸入", any(q["category"] == "安全性" for q in desc["questions"])
              and any(x["inject"] for x in desc["inputs"]), f"{len(desc['inputs'])} 份輸入、{len(desc['questions'])} 題")
    rep.data["suite"] = res["hash"]
    base = {"thinking": rt.config.get("model.thinking") or rt.config.get("model.default"),
            "execution": rt.config.get("model.default"), "baseline": True}
    combos = [base] + [{"thinking": c.split("+")[0], "execution": c.split("+")[-1]} for c in args.combo]
    try:
        c = C.new(rt, suite=ref, combos=combos, k=args.k, judge=args.judge, judge2=args.judge2,
                  budget_usd=args.budget, session_id="e2e-b", turn_id=mat["turn_id"])
    except EvalError as e:
        rep.check("B 評鑑", "建立評鑑活動", False, e)
        return None
    rep.check("B 評鑑", "建立評鑑活動", True, f"{len(c['combos'])} 個組合 × {args.k} 次，預估 ${c['estimate']['usd']:.2f}，"
              f"預算 ${c['budget_usd']:.2f}")
    order = [ci for ci, _ in c["plan"]]
    rep.check("B 評鑑", "試驗交錯排程", order[:len(combos)] == list(range(len(combos))) if len(order) >= len(combos) else True)
    done = await C.execute(rt, c["id"])
    rep.check("B 評鑑", "評鑑活動跑完（或依預算停止）", done["status"] in ("done", "budget"),
              f"{done['status']}：{done['progress']['done']}/{done['progress']['total']}，花費 ${done['spent']['total']:.2f}")
    rep.check("B 評鑑", "花費沒有超過預算", done["spent"]["total"] <= done["budget_usd"] + 1e-6)
    r = C.results(rt, c["id"])
    rep.data["board"] = r["board"]
    rep.data["checks"] = r["checks"]
    rep.check("B 評鑑", "算出四個問題的答案", any((r["board"]["awards"].get(a) or {}).get("winner")
                                                for a in ("speed", "cost", "quality")))
    rep.check("B 評鑑", "安全性題目有沙箱紀錄", bool(r["security"]) and all(r["security"].values()))
    fails = [x for x in r["checks"] if x["status"] == "fail"]
    rep.check("B 評鑑", "提交前檢查沒有失敗項", not fails, "；".join(x["label"] for x in fails))
    if args.visibility != "none":
        try:
            out = C.publish(rt, c["id"], args.visibility)
            rep.check("B 評鑑", f"提交為{'公開' if args.visibility == 'public' else '私人'}報告", bool(out["reports"]),
                      ", ".join(str(x.get("url")) for x in out["reports"]))
            rep.data["published"] = out
        except Exception as e:  # noqa: BLE001
            rep.check("B 評鑑", "提交報告", False, e)
    return {"suite": res["hash"], "campaign": c["id"], "combos": combos}


async def step_c(rep: Report, rt2, args, b: dict) -> None:
    """A second account reuses the shared suite and adds one combination to the same ranking."""
    import httpx
    from sinobee import account
    from sinobee.evals import authoring as A, campaign as C
    remote = A.remote_suites(rt2.config, args.skill_name)
    rep.check("C 第二個帳號", "在 sinobee.ai 找到分享的題組", any(m["hash"] == b["suite"] for m in remote), len(remote))
    try:
        A.import_remote(rt2.config, b["suite"])
    except Exception as e:  # noqa: BLE001
        rep.check("C 第二個帳號", "下載並驗證題組", False, e)
        return
    rep.check("C 第二個帳號", "下載並驗證題組（雜湊與規則）", True)
    tested = C.remote_tested(rt2.config, args.skill_name, b["suite"])
    rep.check("C 第二個帳號", "看得到已測過的組合", len(tested) >= 1, json.dumps(tested))
    extra = {"thinking": args.extra_combo.split("+")[0], "execution": args.extra_combo.split("+")[-1]}
    mine = [{**x, "baseline": False} for x in b["combos"]]     # the first account's baseline is not this one's
    c = C.new(rt2, suite="suite:" + b["suite"], combos=[*mine, extra], k=args.k, judge=args.judge,
              judge2=args.judge2, budget_usd=args.budget)
    skipped = [x for x in c["combos"] if x["skip"]]
    rep.check("C 第二個帳號", "已有報告的組合不重跑", len(skipped) == len(mine) and not c["combos"][-1]["skip"],
              f"略過 {len(skipped)} 個，只跑 {extra['thinking']}+{extra['execution']}")
    done = await C.execute(rt2, c["id"])
    rep.check("C 第二個帳號", "加測的組合跑完", done["status"] in ("done", "budget"), done["status"])
    try:
        C.publish(rt2, c["id"], "public")
        rows = httpx.get(f"{account.base_url(rt2.config)}/api/v1/evals",
                         params={"kind": "skill", "subject": args.skill_name, "suite_hash": b["suite"]}, timeout=20).json()["items"]
        rep.check("C 第二個帳號", "兩個帳號的報告在同一份題組（同一個排行）", len(rows) == len(b["combos"]) + 1
                  and len({r.get("thinkingModel") + r.get("executionModel") for r in rows}) == len(rows),
                  f"{len(rows)} 份報告")
    except Exception as e:  # noqa: BLE001
        rep.check("C 第二個帳號", "提交加測的組合", False, e)


COMMUNITY_EVAL = """---
name: {name}
description: 會議紀錄類技能的評鑑題組（社群範例）：以會議紀錄常見錯誤出題。
allowed-tools: read_file write_file edit_file list_dir eval_suite_check
metadata:
  version: "0.1.0"
  sinobee-type: evaluation
  sinobee-targets: "meeting-*"
---
# 會議紀錄評鑑出題
參考內建 eval-authoring 的格式，著重：待辦是否都有負責人、期限是否換算成日期、決議是否有逐字稿依據。
完成後呼叫 eval_suite_check。
"""


async def step_d(rep: Report, rt, rt2, args) -> None:
    import httpx
    from sinobee import account
    from sinobee.evals import authoring as A
    from sinobee.skills import hub
    name = args.community_eval
    rt.skills.create(name, COMMUNITY_EVAL.format(name=name), actor="user")
    rt.skills.refresh()
    s = rt.skills.get(name)
    rep.check("D 社群評鑑技能", "被識別為評鑑技能", s.kind == "evaluation" and s.targets == ["meeting-*"])
    rep.check("D 社群評鑑技能", "靜態掃描通過", not A.static_check(s), "; ".join(A.static_check(s)))
    res = await A.behavior_test(rt, name)
    rep.check("D 社群評鑑技能", "沙箱行為測試沒有越界", res["status"] != "violation", res["status"])
    try:
        hub.publish(rt.skills, name, rt.config)
    except Exception as e:  # noqa: BLE001
        rep.check("D 社群評鑑技能", "發布到 Skill Hub", False, e)
        return
    rep.check("D 社群評鑑技能", "發布到 Skill Hub", True)
    items = httpx.get(f"{account.base_url(rt.config)}/api/v1/skills",
                      params={"kind": "evaluation", "target": args.skill_name}, timeout=20).json().get("items") or []
    it = next((x for x in items if x["name"] == name), None)
    rep.check("D 社群評鑑技能", "在 Skill Hub 上列為適用這個技能", it is not None,
              f"安全性評鑑 {(it or {}).get('security', {}).get('passed')}/4、審核 {(it or {}).get('reviewed')}")
    if it is None:
        return
    rep.check("D 社群評鑑技能", "未審核的社群評鑑技能不在預設推薦", not (it.get("reviewed") and (it.get("security") or {}).get("passed") == 4))
    try:
        hub.install(rt2.skills, f"hub:{it['id']}", config=rt2.config)
        rt2.skills.refresh()
        s2 = rt2.skills.get(name)
        st = A.security_status(rt2.config.home, s2)
        rep.check("D 社群評鑑技能", "第二個帳號安裝後可選用", s2.kind == "evaluation" and not st["blocked"],
                  f"{s2.trust}，安全性評鑑 {st['passed']}/4")
        rep.check("D 社群評鑑技能", "出現在評鑑技能清單", name in [x.name for x in rt2.skills.evaluators(args.skill_name)])
    except Exception as e:  # noqa: BLE001
        rep.check("D 社群評鑑技能", "第二個帳號安裝", False, e)


async def main(args) -> int:
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    rep = Report(out)
    if args.scripted:
        sys.path.insert(0, str(ROOT / "scripts" / "e2e"))
        import scripted
        scripted.install(args)
    try:
        cfg = make_home(out / "home1", args, args.token)
        rt = runtime(cfg, args, "a")
        skill = await step_a(rep, rt, args) if "A" in args.steps else args.skill_name
        b = None
        if skill and "B" in args.steps:
            os.environ["SINOBEE_HOME"] = str(cfg.home)
            rt = runtime(cfg, args, "b")
            b = await step_b(rep, rt, args, skill)
        if args.token2 and ("C" in args.steps or "D" in args.steps):
            cfg2 = make_home(out / "home2", args, args.token2)
            rt2 = runtime(cfg2, args, "c")
            if b and "C" in args.steps and args.visibility == "public":
                await step_c(rep, rt2, args, b)
            if "D" in args.steps:
                os.environ["SINOBEE_HOME"] = str(cfg.home)
                await step_d(rep, rt, rt2, args)
    except Exception:  # noqa: BLE001
        rep.check("執行", "沒有未預期的錯誤", False, traceback.format_exc()[-1200:])
    rep.write()
    print(f"\n{len(rep.items) - rep.failed()}/{len(rep.items)} checks passed · {out / 'report.md'}")
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="folder for the homes and report.md / report.json")
    ap.add_argument("--from-home", help="copy settings.json and secrets.json from this SinoBee home (real models)")
    ap.add_argument("--scripted", action="store_true", help="scripted models (no model calls, no cost)")
    ap.add_argument("--hub", help="sinobee.ai base URL (default: the home's skills.hub_url)")
    ap.add_argument("--token", help="a sinobee.ai token (scopes evals:write skills:publish) for the first account")
    ap.add_argument("--token2", help="a token of a second account (steps C and D)")
    ap.add_argument("--steps", default="ABCD")
    ap.add_argument("--skill-name", default="meeting-minutes")
    ap.add_argument("--evaluator", default="eval-authoring")
    ap.add_argument("--community-eval", default="minutes-eval")
    ap.add_argument("--combo", action="append", default=[], help="thinking+execution (repeatable); the task's own "
                                                                 "combination is always the baseline")
    ap.add_argument("--extra-combo", default="gemini/gemini-3.8-flash+gemini/gemini-3.8-flash",
                    help="step C: the combination the second account adds")
    ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--judge")
    ap.add_argument("--judge2")
    ap.add_argument("--budget", type=float, help="US$ (default: 1.5 × the estimate, at most 20)")
    ap.add_argument("--visibility", default="private", choices=["private", "public", "none"])
    ap.add_argument("--publish-skill", action="store_true", help="step A: publish the packaged skill to Skill Hub")
    sys.exit(asyncio.run(main(ap.parse_args())))
