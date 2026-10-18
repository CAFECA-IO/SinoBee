"""Episode summaries state facts, never grades.

A summary such as "The agent successfully utilized the anatomy skill ... fully meets the standard" comes back
through recall as if it were an established result, and anchors the next run on the same subject to reuse it
(the Roadster run of 2026-10-18 recalled five of them). Only the user's feedback and the checks grade a run, so
self-evaluation is stripped when an episode is written and again when one is recalled."""
from __future__ import annotations

import re

PRAISE = re.compile(
    r"\b(?:successfully|successful(?:ly)?|perfectly|flawlessly|seamlessly|accurately|correctly|thoroughly|"
    r"comprehensive(?:ly)?|robust(?:ly)?|high[- ]quality|excellent(?:ly)?|impressive(?:ly)?|"
    r"rigorous(?:ly)?|advanced|modern web design|visually enhanced)\b\s*"
    r"|(?:成功地|成功|完美地?|完全符合|圓滿|出色地?|精確地|嚴謹地|高品質的?|全面地)",
    re.I)
VERDICTS = re.compile(r"(?:,?\s*(?:which|that)\s+(?:fully\s+)?(?:meets|met|exceeds?)\s+[^.;。]*)|(?:，?(?:完全)?符合交付標準)", re.I)


def neutral(text: str) -> str:
    """The same summary without self-praise or self-grading."""
    out = VERDICTS.sub("", text or "")
    out = PRAISE.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:，。；])", r"\1", out)
    out = re.sub(r"\s*\b(?:and|or)\s*(?=[.,;。，；]|$)", "", out)        # "showcasing X and [praise]." -> "showcasing X."
    out = re.sub(r"\ba ([aeiouAEIOU]|HTML\b)", r"an \1", out)
    return out.strip()
