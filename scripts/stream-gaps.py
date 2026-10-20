#!/usr/bin/env python3
"""How long a streamed answer stays silent: first byte, first content, the longest gap between events, the total.
    python3 scripts/stream-gaps.py [model] [max_tokens]     (default: model.default, 16384; PROBE_PROMPT=... to change the task)
One call that makes the model think and then write a large file through a tool call - the case that timed out."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import httpx
from sinobee.config import Config
from sinobee.providers import build_provider
from sinobee.providers.openai_compat import to_openai_messages
from sinobee.types import Message

PROMPT = os.environ.get("PROBE_PROMPT") or ("Create wind_turbine.html: a complete, self-contained interactive page with an isometric SVG of a wind turbine "
          "(blades, hub, nacelle cutaway with gearbox and generator, tower), CSS animation of the rotor, a wind-speed "
          "slider that changes the rotor speed and a live power readout using P = 0.5*rho*A*v^3*Cp. Write the whole file "
          "with ONE write_file call.")
TOOL = {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}}

async def main():
    cfg = Config.load()
    spec = sys.argv[1] if len(sys.argv) > 1 else cfg.get("model.default")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 16384
    p = build_provider(spec, cfg)
    payload = {"model": p.model, "messages": to_openai_messages([Message("user", PROMPT)], True, True),
               "max_completion_tokens": n, "tools": [TOOL], "tool_choice": "auto", "stream": True,
               "stream_options": {"include_usage": True}}
    t0 = time.monotonic(); last = t0; first = first_out = None; gap = 0.0; chars = 0; finish = None; usage = None
    async with httpx.AsyncClient(timeout=900) as c:
        async with c.stream("POST", f"{p.base_url}/chat/completions", json=payload, headers=p._headers()) as r:
            print("HTTP", r.status_code, r.headers.get("content-type"))
            async for line in r.aiter_lines():
                now = time.monotonic()
                if not line.strip():
                    continue
                first = first or now - t0
                gap = max(gap, now - last); last = now
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                ev = json.loads(line[5:])
                usage = ev.get("usage") or usage
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    piece = (d.get("content") or "") + "".join((tc.get("function") or {}).get("arguments") or "" for tc in d.get("tool_calls") or [])
                    if piece:
                        first_out = first_out or now - t0; chars += len(piece)
                    finish = ch.get("finish_reason") or finish
    print(json.dumps({"model": spec, "max_tokens": n, "first_byte_s": round(first or 0, 1), "first_output_s": round(first_out or 0, 1),
                      "longest_gap_s": round(gap, 1), "total_s": round(time.monotonic() - t0, 1), "chars": chars,
                      "finish": finish, "usage": usage}, ensure_ascii=False))

asyncio.run(main())
