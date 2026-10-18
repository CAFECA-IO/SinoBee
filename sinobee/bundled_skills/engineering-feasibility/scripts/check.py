"""Checks a feasibility work folder before delivery: feasibility.json, report.md and the built page."""
from __future__ import annotations

import json
import re
from pathlib import Path

VERDICTS = {"feasible", "limited", "infeasible"}
STATUS = {"ok", "limited", "impossible", "unverified"}
REPORT_HEADINGS = ["判定", "主張", "工程原理", "物理極限", "假設", "來源"]
REPORT_HEADINGS_EN = ["Verdict", "Claims", "How it works", "Physical limits", "Assumptions", "Sources"]


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check(folder: Path) -> dict:
    problems: list[str] = []
    fj = folder / "feasibility.json"
    if not fj.is_file():
        return {"status": "fix", "problems": ["feasibility.json is missing (run scaffold)"]}
    try:
        f = json.loads(fj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"status": "fix", "problems": [f"feasibility.json is not valid JSON: {e}"]}
    if f.get("example"):
        problems.append("feasibility.json still has \"example\": true — replace the sample with this subject's analysis")
    verdict = f.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"verdict must be one of {sorted(VERDICTS)}")
    if not f.get("summary"):
        problems.append("summary: one sentence that states the verdict and the deciding number")
    claims = f.get("claims") or []
    if len(claims) < 2:
        problems.append("claims: list every claim of the source that physics can test (at least 2)")
    impossible = []
    for c in claims:
        cid = c.get("id", "?")
        for key in ("claim", "source", "physics", "status"):
            if not c.get(key):
                problems.append(f"claim {cid}: {key} is missing")
        if c.get("status") not in STATUS:
            problems.append(f"claim {cid}: status must be one of {sorted(STATUS)}")
        if c.get("status") != "unverified":
            for side in ("required", "limit"):
                v = c.get(side) or {}
                if not _num(v.get("value")) or not v.get("unit"):
                    problems.append(f"claim {cid}: {side} needs a numeric value and a unit")
            req, lim = (c.get("required") or {}).get("value"), (c.get("limit") or {}).get("value")
            if _num(req) and _num(lim) and not _num(c.get("margin")):
                problems.append(f"claim {cid}: margin (limit / required, or required / limit for upper bounds) is missing")
        if c.get("status") == "impossible" and c.get("core", True):
            impossible.append(cid)
    if verdict == "feasible" and any(c.get("status") in ("impossible", "limited") and c.get("core", True) for c in claims):
        problems.append("verdict feasible, but a core claim is limited or impossible: use limited or infeasible")
    if verdict == "infeasible" and not impossible:
        problems.append("verdict infeasible needs at least one core claim with status impossible")
    if not f.get("assumptions"):
        problems.append("assumptions: list every input that is not published (mass, sizes, efficiencies) with a reason")
    if len(f.get("limits") or []) < 3:
        problems.append("limits: at least 3 physical limits with numbers")
    srcs = f.get("sources") or []
    if len(srcs) < 2:
        problems.append("sources: at least 2 independent sources with URL")
    for s in srcs:
        if not str(s.get("url", "")).startswith(("http://", "https://")):
            problems.append(f"source {s.get('title', '?')}: url missing")
    figs = f.get("figures") or []
    if not figs:
        problems.append("figures: plan at least one anatomy figure")
    shown = {x for fig in figs for x in (fig.get("claims") or [])}
    if verdict == "infeasible":
        for cid in impossible:
            if cid not in shown:
                problems.append(f"claim {cid} is impossible but no figure shows why (figures[].claims)")
        if not any(fig.get("mode") in ("limit", "failure") for fig in figs):
            problems.append("an infeasible verdict needs figures in mode limit or failure")
    page = folder / f.get("page", "page.html")
    if not page.is_file():
        problems.append(f"{page.name} is missing: build it (node page.mjs)")
    else:
        html = page.read_text(encoding="utf-8", errors="replace")
        for fig in figs:
            if fig.get("fig") and fig["fig"] not in html:
                problems.append(f"{page.name}: {fig['fig']} is planned but not on the page")
        if re.search(r"\bTODO\b|lorem ipsum|__DATA\w*__", html, re.I):
            problems.append(f"{page.name}: placeholder text left (TODO, lorem ipsum or an unreplaced __DATA__)")
        if "glLayer({ canvas" in html and "data-gl" not in html:
            problems.append(f"{page.name}: a shader page must mark the stage with data-gl when WebGL runs")
        mb = page.stat().st_size / 1e6
        if mb > 15:
            problems.append(f"{page.name} is {mb:.1f} MB; keep it under 15 MB")
    rep = folder / "report.md"
    if not rep.is_file():
        problems.append("report.md is missing (references/report-template.md)")
    else:
        text = rep.read_text(encoding="utf-8")
        heads = [h.strip("# ").strip() for h in re.findall(r"^#{2}\s.+$", text, re.M)]
        for zh, en in zip(REPORT_HEADINGS, REPORT_HEADINGS_EN):
            if not any(zh in h or en.lower() in h.lower() for h in heads):
                problems.append(f"report.md: section「{zh} / {en}」is missing")
        for s in srcs:
            if s.get("url") and s["url"] not in text:
                problems.append(f"report.md does not cite {s['url']}")
        if verdict and verdict not in text and {"feasible": "可行", "limited": "有條件", "infeasible": "不可行"}[verdict] not in text:
            problems.append("report.md does not state the verdict")
    return {"status": "fix" if problems else "ok", "problems": problems, "verdict": verdict,
            "claims": len(claims), "figures": len(figs)}
