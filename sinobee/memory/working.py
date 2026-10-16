"""Task (working) memory compression: keep every key, shorten long values to their key points."""
from __future__ import annotations

import json

PROMPT = """This is an AI agent's scratchpad for the task in progress (key -> notes). It is too long. Rewrite it in
at most {limit} characters in total: keep every key, keep the goal, the current plan, unfinished items, decisions,
file paths, numbers and errors still relevant; collapse finished steps into one short line. Same language as the
input. Return ONLY a JSON object with the same keys.

SCRATCHPAD:
{wm}"""


def _tail(value: str, room: int) -> str:
    """Model-free fallback: the first line (usually the headline) and the newest lines that fit."""
    value = str(value)
    if len(value) <= room:
        return value
    lines = value.splitlines() or [value]
    head = lines[0][: max(40, room // 3)]
    keep, used = [], len(head) + 20
    for line in reversed(lines[1:]):
        if used + len(line) + 1 > room:
            break
        keep.insert(0, line)
        used += len(line) + 1
    if not keep and len(lines) == 1:
        return value[: room // 3] + " … " + value[-(room - room // 3 - 3):]
    return "\n".join([head, "…（較早的內容已壓縮）", *keep])


async def compress_working(wm: dict, aux, limit: int) -> dict:
    total = sum(len(str(v)) for v in wm.values())
    if total <= limit or not wm:
        return dict(wm)
    if aux is not None:
        from ..learning.reflector import parse_json
        from ..types import Message
        try:
            resp = await aux.chat([Message("user", PROMPT.format(limit=limit, wm=json.dumps(wm, ensure_ascii=False,
                                                                                             indent=1)))],
                                  None, temperature=0, max_tokens=max(400, limit // 2))
            data = parse_json(resp.message.text())
            if set(data) == set(wm) and sum(len(str(v)) for v in data.values()) < total:
                return {k: str(data[k]) for k in wm}
        except Exception:  # noqa: BLE001 - fall back below
            pass
    share = max(120, limit // len(wm))
    return {k: _tail(v, share) for k, v in wm.items()}
