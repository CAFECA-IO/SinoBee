#!/usr/bin/env python3
"""What the sinobee.ai edge (Cloudflare → NGINX → relay) does to a long streamed answer, measured from this computer
with your SinoBee.ai sign-in. Nothing is changed on the server.

    python3 scripts/edge-probe.py buffering            # small answer: do events arrive as they are made? compressed?
    python3 scripts/edge-probe.py silence [chars]      # one big write_file without eager streaming: how long silent, cut?
    python3 scripts/edge-probe.py eager [chars]        # the same with eager_input_streaming: true
    python3 scripts/edge-probe.py all

Prints the response headers that tell who answered, every gap longer than 5 s, the longest gap, SSE comments seen
(": ping" heartbeats once the relay sends them) and how the stream ended. Model: PROBE_MODEL (default
anthropic/claude-haiku-5-5).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import httpx

from sinobee import account
from sinobee.config import Config

MODEL = os.environ.get("PROBE_MODEL", "anthropic/claude-haiku-5-5")
SHOW = ("server", "cf-ray", "cf-cache-status", "content-encoding", "content-type", "cache-control", "transfer-encoding",
        "x-accel-buffering", "via", "alt-svc")


def write_file_tool(eager: bool) -> dict:
    t = {"name": "write_file", "description": "Create or overwrite a file.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                          "required": ["path", "content"]}}
    if eager:
        t["eager_input_streaming"] = True
    return t


def run(cfg, label: str, body: dict) -> dict:
    url = f"{account.relay_base(cfg)}/anthropic/v1/messages"
    raw = json.dumps(body).encode()
    headers = {**account.relay_headers(cfg, "POST", url, raw), "content-type": "application/json",
               "anthropic-version": "2023-06-01", "accept-encoding": "gzip, br"}
    t0 = time.monotonic()
    last = t0
    first = None
    gaps, comments, events, deltas = [], [], 0, 0
    arrivals = []
    longest = 0.0
    end = "complete"
    stop = None
    out_chars = 0
    print(f"\n== {label}  ({MODEL})")
    try:
        with httpx.Client(timeout=httpx.Timeout(900, connect=20)) as c, \
                c.stream("POST", url, content=raw, headers=headers) as r:
            print("HTTP", r.status_code, f"{r.http_version}")
            for k in SHOW:
                if k in r.headers:
                    print(f"  {k}: {r.headers[k]}")
            if r.status_code != 200:
                print(r.read().decode()[:500])
                return {"label": label, "status": r.status_code}
            for line in r.iter_lines():
                now = time.monotonic()
                if not line.strip():
                    continue
                g = now - last
                if g > 5:
                    gaps.append((round(last - t0, 1), round(g, 1)))
                longest = max(longest, g)
                last = now
                first = first if first is not None else now - t0
                if line.startswith(":"):
                    comments.append((round(now - t0, 1), line[:40]))
                    continue
                if not line.startswith("data:"):
                    continue
                events += 1
                arrivals.append(now - t0)
                try:
                    ev = json.loads(line[5:])
                except ValueError:
                    continue
                if ev.get("type") == "content_block_delta":
                    deltas += 1
                    d = ev.get("delta") or {}
                    out_chars += len(d.get("text") or d.get("partial_json") or "")
                if ev.get("type") == "message_delta":
                    stop = (ev.get("delta") or {}).get("stop_reason")
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as e:
        end = f"cut: {type(e).__name__}: {e}"
    total = time.monotonic() - t0
    res = {"label": label, "first_byte_s": round(first or 0, 1), "total_s": round(total, 1),
           "longest_gap_s": round(longest, 1), "gaps_over_5s": gaps[:12], "events": events, "deltas": deltas,
           "arrivals_10_50_90_s": [round(arrivals[int(len(arrivals) * q)], 1) for q in (0.1, 0.5, 0.9)] if arrivals else [],
           "chars": out_chars, "comments": len(comments), "first_comments": comments[:4], "stop": stop, "end": end}
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


def buffering(cfg):
    body = {"model": MODEL, "max_tokens": 1200, "stream": True, "messages": [{"role": "user", "content":
            "Write the numbers one to three hundred in English words, one per line, nothing else."}]}
    return run(cfg, "buffering", body)


def big_write(cfg, eager: bool, chars: int):
    lines = max(20, chars // 110)
    prompt = (f"Create data.txt with ONE write_file call. The file has exactly {lines} lines, numbered 1 to {lines}: "
              "'line N: ' followed by a different sentence of 15 to 20 words about a city. Write every line in full; "
              "do not abbreviate, skip or use an ellipsis. Do not write anything before or after the tool call.")
    body = {"model": MODEL, "max_tokens": 32000, "stream": True, "tools": [write_file_tool(eager)],
            "tool_choice": {"type": "auto"},
            "messages": [{"role": "user", "content": prompt}]}
    return run(cfg, "eager" if eager else "silence", body)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "buffering"
    chars = int(sys.argv[2]) if len(sys.argv) > 2 else 60000
    cfg = Config.load()
    out = []
    if what in ("buffering", "all"):
        out.append(buffering(cfg))
    if what in ("silence", "all"):
        out.append(big_write(cfg, False, chars))
    if what in ("eager", "all"):
        out.append(big_write(cfg, True, chars))
    print("\nJSON", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
