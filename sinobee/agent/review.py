"""Acceptance review: before a task is reported done, a reviewer with a fresh context (the stronger model when one is
configured) checks the actual result against the request and the acceptance checklist.

Quality levels (agent.quality: auto | fast | standard | strict)
  fast      only the deterministic gates (open checklist items, file checks) - no model is asked
  standard  + the acceptance review: request, later instructions, checklist with evidence, file-check results,
              excerpts of the produced files and screenshots of produced pages, the final answer -> verdict with
              a fix list; one round of fixes
  strict    + an independent read-only reviewer sub-agent first re-checks claims against the sources (opens the
              cited pages, re-runs read-only queries, reads the files); the acceptance review then decides which
              of its findings block delivery (findings that do not affect correctness or the user's requirements
              are dropped, so review does not turn into over-engineering); two rounds of fixes
  auto      picks per task: strict for code changes, research written up as a document, numbers from databases,
              and high-complexity skills; standard when files were made or several tools used; fast otherwise
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LEVELS = ("fast", "standard", "strict")
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".cs",
            ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".sql", ".sh", ".vue", ".svelte"}
DOC_EXT = {".md", ".markdown", ".html", ".htm", ".pdf", ".docx", ".pptx", ".xlsx", ".csv"}
TEXT_EXT = {".md", ".markdown", ".html", ".htm", ".txt", ".csv", ".json", ".yaml", ".yml", ".srt", ".svg"} | CODE_EXT
EXCERPT_CHARS = 6000
EXCERPT_TOTAL = 24000


def _calls(history) -> list:
    return [c for m in history for c in (m.tool_calls or [])]


def quality_level(config, history, stats: dict, complex_task: bool) -> tuple[str, str]:
    """(level, why) for this turn; `history` is the turn's messages."""
    want = str(config.get("agent.quality", "auto") or "auto").lower()
    if not config.get("agent.verify", True):
        return "fast", "已關閉完成檢查"
    if want in LEVELS:
        return want, "設定指定"
    calls = _calls(history)
    names = [c.name for c in calls]
    arts = [Path(a["path"]).suffix.lower() for a in stats.get("artifacts", [])]
    edited = [str((c.arguments or {}).get("path") or "") for c in calls if c.name in ("write_file", "edit_file")]
    if any(Path(p).suffix.lower() in CODE_EXT for p in edited):
        return "strict", "修改了程式碼"
    if complex_task:
        return "strict", "使用了高複雜度技能"
    research = sum(n in ("web_search", "web_fetch", "browser") for n in names)
    if research >= 2 and (any(e in DOC_EXT for e in arts) or len(stats.get("final_text", "")) > 1500):
        return "strict", "整理研究資料成報告"
    if "db_query" in names and re.search(r"\d", stats.get("final_text", "")):
        return "strict", "回報資料庫查詢的數字"
    if arts or stats.get("tool_calls", 0) >= int(config.get("agent.verify_min_tool_calls", 3)):
        return "standard", "產出了檔案" if arts else "多步驟任務"
    return "fast", "簡單任務"


def excerpts(paths: list[Path]) -> str:
    out, total = [], 0
    for p in paths:
        if total >= EXCERPT_TOTAL:
            out.append(f"--- {p.name}: (not shown, excerpt budget used)")
            continue
        if not p.is_file():
            out.append(f"--- {p.name}: MISSING")
            continue
        size = p.stat().st_size
        if p.suffix.lower() not in TEXT_EXT or size > 4 * 1024 * 1024:
            out.append(f"--- {p.name}: binary or large file, {size} bytes")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if p.suffix.lower() in (".html", ".htm"):
            text = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", text, flags=re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text))
        cut = text[:EXCERPT_CHARS]
        total += len(cut)
        more = f"\n…（共 {len(text)} 字元，只顯示開頭）" if len(text) > EXCERPT_CHARS else ""
        out.append(f"--- {p.name} ({size} bytes):\n{cut}{more}")
    return "\n".join(out)


REVIEW_RULES = """You are the acceptance reviewer for an AI agent's task. You see the request, the agreed acceptance
checklist, the deterministic check results, the produced files (excerpts, page screenshots) and the final answer.
Judge the RESULT, not the effort:
- Is every requirement in the request (and later instructions) met by what was actually produced?
- Is every checklist item really satisfied by the evidence and the files? Mark items whose evidence does not hold.
- Is the final answer truthful about what was done (no claims the files or steps do not support)?
- Are there errors a careful expert would catch: wrong numbers, contradictions, unsupported factual claims, broken
  structure, missing sections, visibly wrong rendering in a screenshot?
Only BLOCKING problems go back to the agent: those that make the result wrong or miss a stated requirement. Style
preferences, optional extras and things outside the request are NOT blocking (list them under "notes", or omit).
Asking the user a necessary question, or clearly explaining an external blocker, counts as acceptable.
Reply with JSON only:
{"verdict": "pass" | "revise",
 "items": [{"id": <checklist id>, "ok": true|false, "note": "<why, short>"}],
 "problems": [{"what": "<the problem>", "evidence": "<where you saw it>", "fix": "<what to do>"}],
 "notes": ["<non-blocking remark>"],
 "summary": "<one sentence in the user's language>"}"""


def review_prompt(*, query: str, steers: list[str], checklist: str, qa_text: str, files: str, answer: str,
                  reviewer: str = "", level: str = "standard") -> str:
    parts = [REVIEW_RULES, f"\nQUALITY LEVEL: {level}", f"\nUSER REQUEST:\n{query[:4000]}"]
    if steers:
        parts.append("LATER INSTRUCTIONS FROM THE USER:\n" + "\n".join(steers)[:2000])
    parts.append("ACCEPTANCE CHECKLIST:\n" + (checklist[:3000] if checklist else "(none was written)"))
    parts.append("DETERMINISTIC FILE CHECKS:\n" + (qa_text or "passed (no problems found)"))
    if reviewer:
        parts.append("INDEPENDENT REVIEWER'S FINDINGS (they re-checked sources and files; decide which ones are "
                     "blocking - drop the ones that do not affect correctness or the requirements):\n" + reviewer[:6000])
    parts.append("PRODUCED FILES:\n" + (files or "(no files)"))
    parts.append(f"FINAL ANSWER:\n{answer[:5000]}")
    return "\n\n".join(parts)


def parse(text: str) -> dict | None:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("pass", "revise"):
        return None
    data["problems"] = [p for p in data.get("problems") or [] if isinstance(p, dict) and p.get("what")]
    data["items"] = [i for i in data.get("items") or [] if isinstance(i, dict)]
    if data["verdict"] == "revise" and not data["problems"] and not any(i.get("ok") is False for i in data["items"]):
        data["verdict"] = "pass"           # "revise" with nothing to fix is a pass
    return data


def fix_list(data: dict, checklist_items: list[dict]) -> str:
    lines = []
    for i in data.get("items") or []:
        if i.get("ok") is False:
            text = next((c["text"] for c in checklist_items if c.get("id") == i.get("id")), "")
            lines.append(f"- 驗收清單 #{i.get('id')} {text}：{i.get('note') or '證據不足'}")
    for p in data.get("problems") or []:
        line = f"- {p['what']}"
        if p.get("evidence"):
            line += f"（{p['evidence']}）"
        if p.get("fix"):
            line += f" → {p['fix']}"
        lines.append(line)
    return "\n".join(lines[:15])


def reviewer_task(*, query: str, checklist: str, files: list[str], answer: str) -> str:
    return (f"Independently check the result of this task before it is delivered.\n\nUSER REQUEST:\n{query[:3000]}\n\n"
            f"ACCEPTANCE CHECKLIST:\n{checklist[:2500] or '(none)'}\n\nFILES PRODUCED:\n"
            + ("\n".join(files[:20]) or "(none)") + f"\n\nTHE AGENT'S FINAL ANSWER:\n{answer[:4000]}\n\n"
            "Re-check the important claims yourself: open the cited sources (web_fetch), re-run read-only queries, "
            "read the files and recompute key numbers. Report only problems that make the result wrong or miss a "
            "stated requirement, each with the evidence; ignore style and optional extras. Keep the report under "
            "300 words and end with 'VERDICT: pass' or 'VERDICT: revise - <reason>'.")
