#!/usr/bin/env python3
"""Does the thinking model return a plan within the planning call's output limit? (E5, 2026-10-20)

    python3 scripts/exp_plan_budget.py <out.json> <model> <max_tokens> [runs]

Sends SinoBee's own PLAN prompt for the wind-turbine task (with the anatomy skill) through SinoBee's provider, as
agent/loop.py _plan does (temperature 0.2), and records whether thinking.parse_plan gets a plan and its milestones."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sinobee.agent import thinking
from sinobee.config import Config
from sinobee.providers import build_provider
from sinobee.types import Message

TASK = "用動畫展與工程圖示介紹風力發電機組成與原理，並描述各項參數"
TOOLS = ("read_file, write_file, edit_file, list_dir, search_files, shell, web_search, web_fetch, browser, skill_view, "
         "skill_run, task_checklist, milestone, delegate")


async def one(p, prompt, n):
    t = time.monotonic()
    try:
        r = await p.chat([Message("user", prompt)], None, temperature=0.2, max_tokens=n, retries=0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "s": round(time.monotonic() - t, 1)}
    plan = thinking.parse_plan(r.message.text())
    return {"ok": bool(plan), "s": round(time.monotonic() - t, 1), "finish": r.stop_reason, "chars": len(r.message.text()),
            "out_tokens": r.usage.output_tokens, "milestones": len((plan or {}).get("milestones") or []) if plan else 0,
            "task_type": (plan or {}).get("task_type")}


async def main(out, spec, n, runs):
    cfg = Config.load()
    p = build_provider(spec, cfg)
    sk = "SKILLS TO USE:\n- anatomy: Build crafted, highly detailed, interactive isometric SVG figures that explain a technical idea as a real physical object ... standalone HTML page.\n"
    prompt = thinking.PLAN.format(request=TASK, context="", tools=TOOLS, skills=sk)
    res = await asyncio.gather(*[one(p, prompt, n) for _ in range(runs)])
    json.dump({"model": spec, "max_tokens": n, "runs": res}, open(out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"model": spec, "max_tokens": n, "runs": res}, ensure_ascii=False))


asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3))
