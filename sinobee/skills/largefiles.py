"""Skills that produce large files must say how to write them in parts (proposal "large tasks no longer stop half-way",
U7, 2026-10-21).

A whole interactive page written in one tool call is what stopped the anatomy wind-turbine task: the reply ran into
the output limit twice and then the read timeout. The agent's own rule (agent/prompt.py, U2) says to write a skeleton
and fill it one component at a time, but a skill that says "write index.html" with no word about parts pulls the
model the other way. So:

- the skill check flags a skill whose SKILL.md promises a page / large single file and says nothing about parts
  (shown under 格式提醒 in the skill detail, and by `sinobee skills validate`);
- when such a skill is activated, SinoBee adds its own note after the skill's text - the skill itself is not
  changed (anatomy is a third-party skill: its text stays as its author wrote it).
"""
from __future__ import annotations

import re

# the skill's deliverable is a page or one large file (not merely mentioning HTML as an alternative)
LARGE = re.compile(r"(standalone|stand-alone|single|self-contained|獨立|單一|完整|整頁|一頁式)[^\n.。]{0,30}"
                   r"(HTML|page|頁面|網頁|file|檔案)|"
                   r"(write|writes|produce|produces|publish|deliver|產出|輸出|寫出|生成)[^\n.。]{0,30}`?[\w./-]*\.html?\b", re.I)
# it says how to write that file in parts
PARTS = re.compile(r"分段寫|分段寫入|逐段|一次只(寫|填)一|先寫骨架|骨架|skeleton|section by section|"
                   r"one (section|component|part|block|figure)? ?at a time|never the whole (page|file) in one|in parts|append\s*=\s*true|append\s*:\s*true", re.I)

MESSAGE = ("large-file: 這個技能會產出網頁或大型單一檔案，但 SKILL.md 沒有寫明怎麼分段寫入"
           "（先寫骨架、每個元件一個區塊、一次只填一塊）。一次寫完整頁容易被輸出上限截斷或逾時。")

NOTE = """<sinobee_note about="large files">
SinoBee note (added by SinoBee, not part of this skill): this skill produces a page or a large single file. Do not
write it in one call. First write a skeleton with every component as an empty, clearly marked placeholder; then fill
one placeholder per call with edit_file (or write_file with append=true), keeping each call to roughly 30,000
characters; never rewrite the whole file after the skeleton. Check the result once at the end.
</sinobee_note>"""


def needs_note(body: str) -> bool:
    """The skill promises a page or a large single file and says nothing about writing it in parts."""
    text = body or ""
    return bool(LARGE.search(text)) and not PARTS.search(text)


def diagnostics(body: str) -> list[str]:
    return [MESSAGE] if needs_note(body) else []
