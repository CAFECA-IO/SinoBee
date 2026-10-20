#!/usr/bin/env python3
"""Check that the models you configured answer the same with and without streaming, using your own keys.

    python3 scripts/stream-check.py                      # the default, thinking and auxiliary models
    python3 scripts/stream-check.py gemini/gemini-pro-latest openai/gpt-5-mini

For each model, with stream off and on: a short answer, one tool call sent back with its result (Gemini's
thought_signature must survive the round trip), two tool calls at once, and a long answer (about 1500 characters).
About 10 short calls per model; prints a table and exits 1 when streaming answers differently from not streaming.
Nothing is written to your SinoBee data.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sinobee.config import Config  # noqa: E402
from sinobee.providers import build_provider  # noqa: E402
from sinobee.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from sinobee.types import Message, ToolSpec  # noqa: E402

WEATHER = ToolSpec("get_weather", "Current weather for a city.",
                   {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]})


async def timed(p, messages, tools=None, max_tokens=2048):
    t = time.monotonic()
    try:
        r = await p.chat(messages, tools, temperature=0.2, max_tokens=max_tokens, retries=1)
    except Exception as e:  # noqa: BLE001
        return None, round(time.monotonic() - t, 1), f"{type(e).__name__}: {str(e)[:300]}"
    return r, round(time.monotonic() - t, 1), None


async def one(p) -> dict:
    out = {}
    r, s, err = await timed(p, [Message("user", "用一句話說明風力發電機怎麼發電。")])
    out["text"] = {"ok": bool(r and r.message.text().strip()), "s": s, "finish": r and r.stop_reason,
                   "out_tokens": r and r.usage.output_tokens, "err": err}

    ask = [Message("user", "台北現在天氣如何？請呼叫 get_weather 查詢後再回答。")]
    r, s, err = await timed(p, ask, [WEATHER])
    calls = r.message.tool_calls if r else []
    row = {"ok": bool(calls) and calls[0].name == "get_weather" and bool(calls[0].arguments.get("city")), "s": s,
           "args": calls[0].arguments if calls else None, "signature": bool(calls and calls[0].extra), "err": err}
    if row["ok"]:
        follow = ask + [r.message, Message("tool", json.dumps({"city": calls[0].arguments["city"], "temp_c": 27,
                                                               "sky": "晴"}, ensure_ascii=False),
                                           tool_call_id=calls[0].id, name="get_weather")]
        r2, s2, err2 = await timed(p, follow, [WEATHER])
        row.update(round_trip=bool(r2 and r2.message.text().strip()) and "27" in (r2.message.text() if r2 else ""),
                   s2=s2, err2=err2)
        row["ok"] = row["round_trip"]
    out["tool"] = row

    r, s, err = await timed(p, [Message("user", "請同時查詢台北和高雄的天氣：一次發出兩個 get_weather 呼叫。")], [WEATHER])
    cities = sorted(str(c.arguments.get("city")) for c in (r.message.tool_calls if r else []))
    out["two_tools"] = {"ok": len(cities) == 2, "cities": cities, "s": s, "err": err}

    r, s, err = await timed(p, [Message("user", "以繁體中文寫約 1500 字，依序介紹風力發電機的葉片、輪轂、主軸、齒輪箱、"
                                                 "發電機、偏航系統、塔架，每段附一個典型參數。只寫純文字。")],
                            max_tokens=8192)
    text = r.message.text() if r else ""
    out["long"] = {"ok": len(text) >= 1000, "chars": len(text), "s": s, "finish": r and r.stop_reason,
                   "out_tokens": r and r.usage.output_tokens, "err": err}
    return out


async def main(specs: list[str]) -> int:
    cfg = Config.load()
    specs = specs or [x for x in dict.fromkeys([cfg.get("model.default"), cfg.get("model.thinking"),
                                                cfg.get("model.auxiliary")]) if x]
    bad = 0
    for spec in specs:
        p = build_provider(spec, cfg)
        if not isinstance(p, OpenAICompatProvider) or p.provider_name == "sinobee":
            print(f"\n== {spec}: not a direct OpenAI-compatible provider, skipped")
            continue
        res = {}
        for stream in (False, True):
            p.stream = stream
            res[stream] = await one(p)
        print(f"\n== {spec}")
        print(f"{'check':<10} {'no stream':<44} stream")
        for k in res[False]:
            a, b = res[False][k], res[True][k]
            fmt = lambda d: ("✓" if d["ok"] else "✕") + " " + json.dumps({x: y for x, y in d.items() if x != "ok" and y is not None},
                                                                          ensure_ascii=False)[:120]
            print(f"{k:<10} {fmt(a):<44} {fmt(b)}")
            if a["ok"] and not b["ok"]:
                bad += 1
    print("\nresult:", "streaming answers like not streaming" if not bad else f"{bad} check(s) fail only when streaming")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
