"""Check a finished report before it is delivered: structure, category section, fact-check table, images, links."""
from __future__ import annotations

import json
import re
from pathlib import Path

from categories import CATEGORIES, VERDICTS

REQUIRED = ["## 影片資訊", "## 這部影片在說什麼", "## 重點時間軸", "## 分類分析", "## 結論", "## 資料來源與方法"]


def section(text: str, heading: str) -> str:
    m = re.search(rf"^{re.escape(heading)}.*$", text, re.M)
    if not m:
        return ""
    nxt = re.search(r"^## ", text[m.end():], re.M)
    return text[m.end(): m.end() + nxt.start()] if nxt else text[m.end():]


def table_rows(block: str) -> list[list[str]]:
    rows = [ln.strip().strip("|").split("|") for ln in block.splitlines() if ln.strip().startswith("|")]
    rows = [[c.strip() for c in r] for r in rows if not all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in r)]
    return rows


def check(folder: Path) -> dict:
    problems: list[str] = []
    report = folder / "report.md"
    if not report.exists():
        return {"status": "fix", "problems": ["report.md is missing: write it in the folder first"]}
    text = report.read_text(encoding="utf-8")
    spec = json.loads((folder / "cover.json").read_text(encoding="utf-8")) if (folder / "cover.json").exists() else {}
    cat = spec.get("category")
    if cat not in CATEGORIES:
        problems.append(f"cover.json needs a category, one of: {', '.join(CATEGORIES)}")
    if not ((folder / "cover.png").exists() or (folder / "cover.svg").exists()):
        problems.append("no cover yet: write cover.json and run `cover <folder>`")
    head = text[:600]
    if not re.search(r"!\[[^\]]*\]\(cover\.(png|svg)\)", head):
        problems.append("the report must open with the cover image: ![封面](cover.png) right under the title")
    if not re.search(r"^# \S", text, re.M):
        problems.append("missing the title line (# ...)")
    if not re.search(r"^> \S", text[:1500], re.M):
        problems.append("missing the one-sentence summary as a quote (> ...) under the cover")
    for h in REQUIRED:
        if not re.search(rf"^{re.escape(h)}", text, re.M):
            problems.append(f"missing section {h}")
    if cat in CATEGORIES:
        zh, _, level = CATEGORIES[cat]
        if not re.search(rf"^## 分類分析：{re.escape(zh)}", text, re.M):
            problems.append(f"the category section must be titled '## 分類分析：{zh}' (category {cat})")
        if level != "none":
            block = section(text, "## 論述查核")
            if not block:
                problems.append(f"{zh} needs a '## 論述查核' section with a claims table (see references/fact-check.md)")
            else:
                rows = table_rows(block)
                header, body = (rows[0], rows[1:]) if rows else ([], [])
                if "判定" not in "".join(header):
                    problems.append("the fact-check table needs a 判定 column")
                least = 3 if level == "full" else 1
                if len(body) < least:
                    problems.append(f"the fact-check table has {len(body)} claims; check at least {least} for {zh}")
                vi = next((i for i, c in enumerate(header) if "判定" in c), None)
                for r in body:
                    verdict = r[vi] if vi is not None and vi < len(r) else ""
                    if not any(v in verdict for v in VERDICTS):
                        problems.append(f"claim row without a valid 判定 ({' / '.join(VERDICTS)}): {' | '.join(r)[:80]}")
                    elif not any(v in verdict for v in ("無法查證", "觀點")) and "http" not in " ".join(r):
                        problems.append(f"claim judged '{verdict}' has no source link: {' | '.join(r)[:80]}")
    for alt, src in re.findall(r"!\[([^\]]*)\]\(([^)\s]+)\)", text):
        if not src.startswith("http") and not (folder / src).exists():
            problems.append(f"image not found: {src}")
    stamps = re.findall(r"youtube\.com/watch\?v=[\w-]{11}&t=\d+s|youtu\.be/[\w-]{11}\?t=\d+", text)
    if len(stamps) < 3:
        problems.append("link at least 3 moments to the video (…watch?v=ID&t=SECONDSs) in 重點時間軸")
    frames = re.findall(r"\]\((frames/[^)]+)\)", text)
    if not frames:
        problems.append("embed at least one still from frames/ where it shows what is being said")
    for bad in ("TODO", "待補", "lorem", "{{", "XXX"):
        if bad in text:
            problems.append(f"placeholder left in the report: {bad}")
    if len(text) < 1500:
        problems.append("the report is very short; cover each section properly")
    return {"status": "ok" if not problems else "fix", "problems": problems, "report": str(report),
            "words": len(re.sub(r"\s", "", text))}
