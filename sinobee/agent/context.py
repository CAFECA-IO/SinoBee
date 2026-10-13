"""Token estimation, image pruning and lossy compression of long conversations."""
from __future__ import annotations

import json

from ..types import ImagePart, Message, TextPart

IMAGE_TOKENS = 1600
SUMMARY_PROMPT = """Summarize the earlier part of an agent's working session so the agent can continue the task.
Keep: the user's goals and constraints, decisions made, facts discovered (paths, URLs, names, values),
what has been completed, what failed and why, and open next steps. Be concise but specific. Output plain text."""


def estimate_tokens(messages: list[Message]) -> int:
    total = 0
    for m in messages:
        if isinstance(m.content, str):
            total += len(m.content) / 3.2
        else:
            for p in m.content:
                total += IMAGE_TOKENS if isinstance(p, ImagePart) else len(p.text) / 3.2
        for t in m.tool_calls:
            total += len(json.dumps(t.arguments, ensure_ascii=False)) / 3.2 + 10
        total += 4
    return int(total)


def prune_images(messages: list[Message], keep_last: int = 2) -> None:
    """Old screenshots are the biggest token cost in computer use; keep only the most recent ones."""
    seen = 0
    for m in reversed(messages):
        if not isinstance(m.content, list):
            continue
        new = []
        for p in reversed(m.content):
            if isinstance(p, ImagePart):
                seen += 1
                if seen > keep_last:
                    new.append(TextPart("[older screenshot removed]"))
                    continue
            new.append(p)
        m.content = list(reversed(new))


def _safe_cut(messages: list[Message], idx: int) -> int:
    """Move a cut index forward so we never separate tool results from their assistant tool_call."""
    while idx < len(messages) and messages[idx].role == "tool":
        idx += 1
    return idx


def _digest(messages: list[Message], limit: int = 60000) -> str:
    out = []
    for m in messages:
        line = f"{m.role.upper()}: {m.text()[:2000]}"
        for t in m.tool_calls:
            line += f"\n  -> {t.name}({json.dumps(t.arguments, ensure_ascii=False)[:300]})"
        out.append(line)
    text = "\n".join(out)
    return text[-limit:]


async def compress(messages: list[Message], provider, *, budget: int, keep_recent: int = 12) -> tuple[list[Message], bool]:
    """messages[0] is the system prompt. Returns (new_messages, changed)."""
    if estimate_tokens(messages) < budget * 0.8 or len(messages) < keep_recent + 4:
        return messages, False
    system, body = messages[0], messages[1:]
    cut = _safe_cut(body, max(1, len(body) - keep_recent))
    head, tail = body[:cut], body[cut:]
    if not head:
        return messages, False
    try:
        resp = await provider.chat([Message("system", SUMMARY_PROMPT), Message("user", _digest(head))],
                                   temperature=0.2, max_tokens=1500)
        summary = resp.message.text()
    except Exception as e:
        summary = f"(summary unavailable: {e}). Earlier messages were dropped to fit the context window."
    # Agent Skills guidance: activated skill instructions are durable - never summarize them away.
    kept_skills = [m.text() for m in head if m.role == "tool" and m.name == "skill_view" and "<skill_content" in m.text()]
    kept_skills += [p.text for m in head if m.role == "user" and isinstance(m.content, list)
                    for p in m.content if isinstance(p, TextPart) and p.text.startswith("<skill_content")]
    if kept_skills:
        summary += "\n\n[Active skill instructions preserved from earlier]\n" + "\n\n".join(dict.fromkeys(kept_skills))
    note = Message("user", f"[Summary of the earlier part of this session]\n{summary}")
    ack = Message("assistant", "Understood. Continuing from the summary.")
    if tail and tail[0].role == "assistant":
        return [system, note] + tail, True
    return [system, note, ack] + tail, True
