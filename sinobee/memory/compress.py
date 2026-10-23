"""Memory compression: group related memories, merge each group into fewer statements, keep the history.

Flow (memory_compress tool, `sinobee memory compress`):
  select (query / tier / kind / tag / ids)  ->  cluster (same kind + lexical overlap)  ->  merge each cluster
  (auxiliary model; extractive fallback)  ->  safety check (numbers, paths and names must survive)  ->  preview
  (dry_run) or apply (one new memory supersedes its sources; the originals stay in the version history).

Permanent (pinned) memories are left out unless include_pinned=True, and a merged memory that absorbed a pinned one
stays pinned.
"""
from __future__ import annotations

import json
import re
import time

from .store import RANK, Memory, MemoryStore, tokenize

LONG_ITEM_CHARS = 400          # a single memory longer than this is condensed on its own
_KEEP = re.compile(r"(?:[A-Za-z]:)?[\w~.\-]*[/\\][\w~./\\\-]+|https?://\S+|\b\d[\d.,:\-/]*\b|`[^`]+`")

MERGE_PROMPT = """You compress an AI assistant's memory. Merge the statements below into {n_out} self-contained
statement(s) in the same language as the input. Rules:
- Keep every distinct fact, number, path, name, date and condition. Drop only repetition and filler.
- If statements contradict each other, keep the newest one (higher number = newer).
- Do not add anything that is not in the input.
- Stay under {limit} characters in total.
Return ONLY JSON: {{"statements": ["..."]}}

STATEMENTS:
{items}"""


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / max(1, len(a | b))


def cluster(mems: list[Memory], threshold: float = 0.2) -> list[list[Memory]]:
    """Greedy single-link clustering: same kind and enough shared words (or a shared tag plus some overlap)."""
    toks = {m.id: set(tokenize(m.content)) for m in mems}
    groups: list[list[Memory]] = []
    for m in sorted(mems, key=lambda x: (-x.importance, x.created_at)):
        best, best_s = None, 0.0
        for g in groups:
            if g[0].kind != m.kind:
                continue
            if (g[0].scope, g[0].status, g[0].source if g[0].scope == "conversation" else None) != \
                    (m.scope, m.status, m.source if m.scope == "conversation" else None):
                continue                     # never merge across scopes: a conversation's memory stays its own
            s = max(_jaccard(toks[m.id], toks[o.id]) for o in g)
            shared_tag = any(set(m.tags) & set(o.tags) for o in g)
            if (s >= threshold or (shared_tag and s >= threshold / 2)) and s > best_s:
                best, best_s = g, s
        if best is not None:
            best.append(m)
        else:
            groups.append([m])
    return groups


def _keepers(text: str) -> set[str]:
    return {k.strip("`.,;:") for k in _KEEP.findall(text or "") if len(k.strip("`.,;:")) > 1}


def lost_details(sources: list[Memory], merged: str) -> list[str]:
    """Numbers, paths, URLs and code spans present in the sources but missing from the merged text."""
    have = merged.lower()
    out = []
    for m in sources:
        for k in _keepers(m.content):
            if k.lower() not in have and k not in out:
                out.append(k)
    return out


def extractive(group: list[Memory]) -> str:
    """Model-free merge: most detailed first (newest on ties), drop statements whose words are already (>= 80%)
    covered by the kept ones. Never rewrites text, so nothing can be invented."""
    kept, covered = [], set()
    for m in sorted(group, key=lambda x: (-len(set(tokenize(x.content))), -x.updated_at)):
        t = set(tokenize(m.content))
        if t and len(t & covered) / len(t) >= 0.8:
            continue
        kept.append(m.content.strip().rstrip("。.;；"))
        covered |= t
    return "；".join(kept)


async def _model_merge(aux, group: list[Memory], limit: int) -> str | None:
    from ..learning.reflector import parse_json
    from ..types import Message
    ordered = sorted(group, key=lambda x: x.updated_at)
    items = "\n".join(f"{i + 1}. {m.content}" for i, m in enumerate(ordered))
    prompt = MERGE_PROMPT.format(n_out=1, limit=limit, items=items)
    try:
        resp = await aux.chat([Message("user", prompt)], None, temperature=0, max_tokens=900)
    except Exception:  # noqa: BLE001 - fall back to the extractive merge
        return None
    data = parse_json(resp.message.text())
    st = [s.strip() for s in (data.get("statements") or []) if isinstance(s, str) and s.strip()]
    return " ".join(st) or None


def _merged_meta(group: list[Memory]) -> dict:
    tags: list[str] = []
    for m in group:
        tags += [t for t in m.tags if t not in tags]
    tier = max((m.tier for m in group if m.tier != "archive"), key=lambda t: RANK[t], default="long")
    return {"kind": group[0].kind, "tier": tier, "tags": tags[:8], "importance": max(m.importance for m in group),
            "pinned": any(m.pinned for m in group)}


async def compress(store: MemoryStore, aux=None, *, query: str | None = None, ids: list[str] | None = None,
                   tier: str | None = None, kind: str | None = None, tag: str | None = None,
                   target_chars: int | None = None, include_pinned: bool = False, dry_run: bool = True,
                   limit: int = 200, threshold: float = 0.2, min_age_days: float = 0.0,
                   exclude_core: bool = False) -> dict:
    """min_age_days / exclude_core are used by automatic maintenance: it leaves memories that were just written or
    changed (they may belong to the task in progress) and the core prompt block (its size has its own rule)."""
    mems = store.select(query=query, ids=ids, tier=tier, kind=kind, tag=tag, limit=limit)
    mems = [m for m in mems if m.tier != "archive" or tier == "archive"]
    if min_age_days:
        cutoff = time.time() - min_age_days * 86400
        mems = [m for m in mems if m.updated_at <= cutoff]
    if exclude_core:
        mems = [m for m in mems if m.tier != "core"]
    skipped_pinned = [m.id for m in mems if m.pinned and not include_pinned]
    mems = [m for m in mems if include_pinned or not m.pinned]
    before = sum(len(m.content) for m in mems)
    plans = []
    for g in cluster(mems, threshold):
        if len(g) < 2 and len(g[0].content) <= LONG_ITEM_CHARS:
            continue
        src_chars = sum(len(m.content) for m in g)
        limit_chars = max(60, int(src_chars * 0.7))
        merged, method = None, "model"
        if aux is not None:
            merged = await _model_merge(aux, g, limit_chars)
        lost = lost_details(g, merged) if merged else []
        if not merged or lost or len(merged) >= src_chars:
            merged, method = extractive(g), "extractive"
            lost = lost_details(g, merged)
        if len(merged) >= src_chars or lost:
            continue                                # nothing safe to gain
        plans.append({"sources": [m.id for m in g], "source_texts": [m.content for m in g], "merged": merged,
                      "method": method, "saved_chars": src_chars - len(merged), **_merged_meta(g)})
    after = before - sum(p["saved_chars"] for p in plans)
    report = {"selected": len(mems), "chars_before": before, "chars_after": after, "clusters": plans,
              "skipped_pinned": skipped_pinned, "applied": False}
    if target_chars and after > target_chars:
        # still too big: suggest (never perform) removing the least valuable non-pinned memories
        merged_away = {i for p in plans for i in p["sources"]}
        rest = sorted((m for m in mems if m.id not in merged_away and not m.pinned),
                      key=lambda m: (m.importance, m.access_count, m.last_access))
        need, cands = after - target_chars, []
        for m in rest:
            if need <= 0:
                break
            cands.append({"id": m.id, "content": m.content, "importance": round(m.importance, 2)})
            need -= len(m.content)
        report["over_target_by"] = after - target_chars
        report["forget_candidates"] = cands
    if dry_run or not plans:
        return report
    for p in plans:
        res = await _apply(store, p)
        p["new_id"] = res["id"]
    report["applied"] = True
    return report


async def _apply(store: MemoryStore, plan: dict) -> dict:
    emb = await store._embed(plan["merged"])
    return store.replace_many(plan["sources"], plan["merged"], kind=plan["kind"], tier=plan["tier"],
                              tags=plan["tags"], importance=plan["importance"], pinned=plan["pinned"], embedding=emb)


def render(report: dict) -> str:
    """Compact text for the model / CLI."""
    slim = {**report, "clusters": [{k: v for k, v in c.items() if k != "source_texts"} for c in report["clusters"]]}
    return json.dumps(slim, ensure_ascii=False, indent=1)
