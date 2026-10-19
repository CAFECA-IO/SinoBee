"""Context window management: token estimation, per-model limits, image pruning and compaction.

How a long session is kept inside the model's context window:

1. every tool result is capped (agent.tool_output_limit) and old screenshots are dropped (prune_images);
2. before each model call the whole request is estimated - messages, tool schemas and the per-turn context block -
   with a CJK-aware estimate (one CJK character is about one token, other text about 3.5 characters per token);
3. above 80 % of the budget (the model's context window minus the answer room) `compact` summarises the older part
   and keeps the most recent messages that fit in ~40 % of the budget; oversized recent messages are trimmed;
4. when the provider still rejects the request as too long, the agent compacts harder and retries once;
5. the summary is saved as a checkpoint so a reopened session continues from it instead of re-summarising.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..types import ImagePart, Message, TextPart

IMAGE_TOKENS = 1600
SUMMARY_PREFIX = "[Summary of the earlier part of this session]"
SUMMARY_ACK = "Understood. Continuing from the summary."
SUMMARY_PROMPT = """Summarize the earlier part of an agent's working session so the agent can continue the task.
Keep: the user's goals and constraints, decisions made, facts discovered (paths, URLs, names, values),
what has been completed, what failed and why, and open next steps. Be concise but specific. Output plain text.
Write in the language the user used."""

# Han, Hiragana/Katakana, Hangul, CJK punctuation and full-width forms: roughly one token per character
_CJK = re.compile(r"[ᄀ-ᇿ⺀-⿟　-〿぀-ヿ㄀-ㇿ㐀-䶿一-鿿"
                  r"ꥠ-꥿가-힯豈-﫿︰-﹏＀-￯\U00020000-\U0002fa1f]")
OVERFLOW_RE = re.compile(r"context.{0,20}(length|window)|too many tokens|maximum context|prompt is too long|"
                         r"input is too long|reduce the length|exceeds? the (model'?s )?(maximum|context)|"
                         r"request too large|num_ctx", re.I)


def text_tokens(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK.findall(text))
    return cjk * 1.0 + (len(text) - cjk) / 3.5


def message_tokens(m: Message) -> int:
    total = 4.0
    if isinstance(m.content, str):
        total += text_tokens(m.content)
    else:
        for p in m.content:
            total += IMAGE_TOKENS if isinstance(p, ImagePart) else text_tokens(p.text)
    for t in m.tool_calls:
        total += text_tokens(json.dumps(t.arguments, ensure_ascii=False)) + 10
    return int(total)


def estimate_tokens(messages: list[Message], tools: list | None = None, extra: str = "") -> int:
    """Tokens of a request: messages + tool schemas (+ text appended at call time, e.g. the context block)."""
    total = sum(message_tokens(m) for m in messages) + int(text_tokens(extra))
    for t in tools or []:
        total += int(text_tokens(t.name + t.description + json.dumps(t.parameters, ensure_ascii=False))) + 8
    return total


# ------------------------------------------------------------------ context windows
# (pattern, tokens) - first match wins; matched against the model part of "provider/model", lower-case.
CONTEXT_WINDOWS: list[tuple[str, int]] = [
    (r"claude-(sonnet|opus)-4.*\[1m\]|claude.*-1m", 1_000_000),
    (r"claude", 200_000),
    (r"gpt-4\.1", 1_047_576),
    (r"gpt-5", 272_000),           # input limit (400k total incl. output)
    (r"(^|/)o[134](-|$)", 200_000),
    (r"gpt-4o|gpt-4-turbo|chatgpt-4o", 128_000),
    (r"gpt-4(?!\.|o|-turbo)", 8_192),
    (r"gpt-3\.5", 16_385),
    (r"gpt-oss", 131_072),
    (r"gemini-1\.0|gemini-pro$|gemini-pro-vision", 32_768),   # the 2023 Gemini 1.0 names
    (r"gemini", 1_048_576),        # 1.5 and later, and the -latest aliases (gemini-pro-latest, gemini-flash-latest)
    (r"grok-4", 256_000),
    (r"grok", 131_072),
    (r"deepseek", 128_000),
    (r"kimi-k2|moonshot.*(128k|k2)", 128_000),
    (r"qwen3?-?(max|plus|turbo|coder)|qwen3", 128_000),
    (r"glm-4\.[5-9]|glm-[5-9]", 128_000),
    (r"mistral-large|mistral-medium|codestral|magistral", 128_000),
    (r"llama-?3\.[1-9]|llama-?4", 128_000),
]
DEFAULT_WINDOW = 128_000


def known_window(model_spec: str | None) -> int | None:
    name = str(model_spec or "").lower()
    for pattern, tokens in CONTEXT_WINDOWS:
        if re.search(pattern, name):
            return tokens
    return None


def context_window(config, model_spec: str | None = None) -> tuple[int, str]:
    """(tokens, source) for the model: user override > local num_ctx > known model > default."""
    spec = model_spec if model_spec is not None else config.get("model.default")
    override = config.get("model.context_window", "auto")
    try:
        if override not in (None, "", "auto") and int(override) > 0:
            return max(2048, int(override)), "setting"
    except (TypeError, ValueError):
        pass
    from .. import local
    if local.is_local_spec(spec):
        return local.num_ctx(config), "local"
    known = known_window(spec)
    return (known, "model") if known else (DEFAULT_WINDOW, "default")


def context_budget(config, max_output: int, model_spec: str | None = None) -> int:
    """Tokens available for the request: the window minus answer room and a safety margin,
    optionally capped by agent.context_budget (a number; "auto" = no cap)."""
    window, _ = context_window(config, model_spec)
    budget = window - max_output - max(1024, window // 50)
    cap = config.get("agent.context_budget", "auto")
    try:
        if cap not in (None, "", "auto") and int(cap) > 0:
            budget = min(budget, int(cap))
    except (TypeError, ValueError):
        pass
    return max(2048, budget)


# ------------------------------------------------------------------ pruning
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


def _cut_text(text: str, max_tokens: int) -> str:
    """Keep the start and the end of `text` so it fits in about `max_tokens`."""
    if text_tokens(text) <= max_tokens:
        return text
    ratio = max_tokens / max(1.0, text_tokens(text))
    keep = max(200, int(len(text) * ratio) - 80)
    head, tail = int(keep * 0.7), keep - int(keep * 0.7)
    return f"{text[:head]}\n\n...[{len(text) - keep} characters removed to fit the context window]...\n\n" \
           f"{text[-tail:] if tail else ''}"


def shrink_message(m: Message, max_tokens: int) -> Message:
    """A copy of `m` whose text fits in `max_tokens` (images are kept; tool calls untouched)."""
    if message_tokens(m) <= max_tokens:
        return m
    if isinstance(m.content, str):
        content = _cut_text(m.content, max_tokens)
    else:
        texts = [p for p in m.content if isinstance(p, TextPart)]
        each = max(200, (max_tokens - IMAGE_TOKENS * (len(m.content) - len(texts))) // max(1, len(texts)))
        content = [TextPart(_cut_text(p.text, each)) if isinstance(p, TextPart) else p for p in m.content]
    return Message(m.role, content, list(m.tool_calls), m.tool_call_id, m.name)


# ------------------------------------------------------------------ compaction
def is_summary(m: Message) -> bool:
    return (m.role == "user" and m.text().startswith(SUMMARY_PREFIX)) or \
           (m.role == "assistant" and m.text() == SUMMARY_ACK and not m.tool_calls)


def _safe_cut(messages: list[Message], idx: int) -> int:
    """Move a cut index forward so we never separate tool results from their assistant tool_call."""
    while idx < len(messages) and messages[idx].role == "tool":
        idx += 1
    return idx


def _digest(messages: list[Message], limit_tokens: int = 40000) -> str:
    """Text for the summariser: an earlier summary is kept whole, then as many recent messages as fit."""
    first = [m.text() for m in messages if is_summary(m) and m.role == "user"]
    rest = [m for m in messages if not is_summary(m)]
    lines = []
    for m in rest:
        line = f"{m.role.upper()}: {m.text()[:2000]}"
        for t in m.tool_calls:
            line += f"\n  -> {t.name}({json.dumps(t.arguments, ensure_ascii=False)[:300]})"
        lines.append(line)
    goal = next((f"FIRST USER REQUEST: {m.text()[:1500]}" for m in rest if m.role == "user"), "")
    used = sum(text_tokens(x) for x in first) + text_tokens(goal)
    kept: list[str] = []
    for line in reversed(lines):
        cost = text_tokens(line)
        if used + cost > limit_tokens:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    if len(kept) < len(lines):  # the oldest messages did not fit: keep at least the original request
        kept = ([goal] if goal else []) + ["...[older messages omitted]"] + kept
    return "\n".join(first + kept)


@dataclass
class Compaction:
    messages: list[Message]       # [system, summary note, (ack), *tail]
    changed: bool
    summary: str = ""
    tail_len: int = 0             # messages after the summary that came from the original history
    trimmed: int = 0              # recent messages that had to be shortened


async def compact(messages: list[Message], provider, *, budget: int, tools: list | None = None, extra: str = "",
                  keep_ratio: float = 0.4, force: bool = False, summary_tokens: int = 1500,
                  digest_tokens: int | None = None) -> Compaction:
    """messages[0] is the system prompt. Summarises the older messages and keeps the newest ones that fit in
    `keep_ratio` of the budget (at least the last message group). Oversized kept messages are trimmed."""
    fixed = estimate_tokens(messages[:1], tools, extra)
    if not force and fixed + sum(message_tokens(m) for m in messages[1:]) < budget * 0.8:
        return Compaction(messages, False)
    system, body = messages[0], messages[1:]
    keep_budget = max(1000, int(budget * keep_ratio))
    # newest messages first, until keep_budget is used; never start the tail with tool results or a summary
    cut, used = len(body), 0
    while cut > 0:
        cost = message_tokens(body[cut - 1])
        if used + cost > keep_budget and cut < len(body):
            break
        used += cost
        cut -= 1
    cut = _safe_cut(body, cut)
    while cut < len(body) and is_summary(body[cut]):
        cut += 1
    if cut >= len(body):  # the last group alone is too big: keep it (trimmed below) and summarise the rest
        cut = len(body) - 1
        while cut > 0 and body[cut].role == "tool":
            cut -= 1
    head, tail = body[:cut], body[cut:]
    # trim oversized kept messages (a pasted document, a long tool result) so the tail fits its share
    trimmed = 0
    if tail and sum(message_tokens(m) for m in tail) > keep_budget:
        per = max(800, keep_budget // len(tail))
        new_tail = []
        for m in tail:
            s = shrink_message(m, per)
            trimmed += s is not m
            new_tail.append(s)
        tail = new_tail
    if not head or all(is_summary(m) for m in head):
        changed = trimmed > 0
        return Compaction([system, *body[:cut], *tail], changed, tail_len=len(tail), trimmed=trimmed)
    room = digest_tokens or max(2000, min(40000, int(budget * 0.6)))
    try:
        resp = await provider.chat([Message("system", SUMMARY_PROMPT), Message("user", _digest(head, room))],
                                   temperature=0.2, max_tokens=summary_tokens)
        summary = resp.message.text().strip() or "(empty summary)"
    except Exception as e:  # noqa: BLE001
        prev = next((m.text()[len(SUMMARY_PREFIX):].strip() for m in head if is_summary(m) and m.role == "user"), "")
        summary = (prev + "\n\n" if prev else "") + \
            f"(summary unavailable: {e}). Earlier messages were dropped to fit the context window."
    # Agent Skills guidance: activated skill instructions are durable - never summarize them away.
    kept_skills = [m.text() for m in head if m.role == "tool" and m.name == "skill_view" and "<skill_content" in m.text()]
    kept_skills += [p.text for m in head if m.role == "user" and isinstance(m.content, list)
                    for p in m.content if isinstance(p, TextPart) and p.text.startswith("<skill_content")]
    if kept_skills and "[Active skill instructions preserved from earlier]" not in summary:
        summary += "\n\n[Active skill instructions preserved from earlier]\n" + "\n\n".join(dict.fromkeys(kept_skills))
    return Compaction(with_summary(system, summary, tail), True, summary, len(tail), trimmed)


def with_summary(system: Message, summary: str, tail: list[Message]) -> list[Message]:
    note = Message("user", f"{SUMMARY_PREFIX}\n{summary}")
    if tail and tail[0].role == "assistant":
        return [system, note, *tail]
    return [system, note, Message("assistant", SUMMARY_ACK), *tail]


async def compress(messages: list[Message], provider, *, budget: int, keep_recent: int | None = None,
                   **kw) -> tuple[list[Message], bool]:
    """Back-compatible wrapper: (new_messages, changed)."""
    res = await compact(messages, provider, budget=budget, **kw)
    return res.messages, res.changed
