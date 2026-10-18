"""Checks a feasibility work folder before delivery: feasibility.json, report.md and the built page."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SHARED = {"helpers.mjs", "README.md", "fingerprint.json"}        # shared library code, not analysis
NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?")
SCALES = (1, 1e3, 1e-3, 1e5, 1e-5, 1e6, 1e-6)    # unit prefixes, L <-> m3, bar <-> Pa
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,})")

VERDICTS = {"feasible", "limited", "infeasible"}
STATUS = {"ok", "limited", "impossible", "unverified"}
REPORT_HEADINGS = ["判定", "主張", "工程原理", "物理極限", "假設", "來源"]
REPORT_HEADINGS_EN = ["Verdict", "Claims", "How it works", "Physical limits", "Assumptions", "Sources"]


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _example_hashes() -> dict[str, str]:
    """sha256 -> 'example-x/file' for every worked example's own analysis files."""
    out = {}
    for d in sorted((SKILL / "references").glob("example-*")):
        for f in d.iterdir():
            if f.is_file() and f.name not in SHARED:
                out[_sha(f)] = f"{d.name}/{f.name}"
    return out


def _norm(text: str) -> str:
    text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]|>>", " ", text.lower())
    text = re.sub(r"[\"'“”‘’`.,!?;:()\[\]…—–\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUM.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _used(value: float, numbers: list[float]) -> bool:
    for n in numbers:
        for k in SCALES:
            v = value * k
            if v and abs(n - v) <= 0.02 * abs(v):
                return True
    return False


def _transcript(folder: Path, f: dict) -> Path | None:
    src = f.get("source") or {}
    ws = folder.parents[2] if len(folder.parents) > 2 else folder.parent
    cands = []
    t = str(src.get("transcript") or "")
    if t and not t.startswith("TODO"):
        cands += [Path(t).expanduser(), ws / t, folder / t]
    m = VIDEO_ID.search(str(src.get("ref") or ""))
    if m:
        cands.append(ws / "reports" / "youtube" / m.group(1) / "transcript.md")
    for c in cands:
        if c.is_file():
            return c
    return None


def provenance(folder: Path, f: dict) -> list[str]:
    """The folder must hold this subject's own analysis: not a worked example, not the untouched template."""
    problems = []
    examples = _example_hashes()
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name not in SHARED and p.suffix in (".mjs", ".js", ".json", ".html", ".md"):
            hit = examples.get(_sha(p))
            if hit:
                problems.append(f"{p.name} is identical to the worked example references/{hit}: an example's analysis "
                                "is not this subject's. Scaffold a fresh folder (e.g. <slug>-2) and build this subject's own model and figures from its source_facts")
    for d in sorted((SKILL / "references").glob("example-*")):
        fp = d / "fingerprint.json"
        if not fp.is_file():
            continue
        ref = json.loads(fp.read_text(encoding="utf-8"))
        mine = {(str(t.get("value")), str(t.get("label"))) for t in f.get("tiles") or []}
        same = [t for t in ref.get("tiles", []) if tuple(t) in mine]
        if len(same) >= 2 or (ref.get("summary") and str(f.get("summary", "")).startswith(ref["summary"])):
            problems.append(f"feasibility.json repeats the worked example {d.name} (tiles {', '.join(t[0] for t in same) or 'summary'}): "
                            "compute this subject's numbers from its own sources")
    prov = folder / ".scaffold.json"
    if prov.is_file():
        files = json.loads(prov.read_text(encoding="utf-8")).get("files", {})
        if "model.mjs" in files and (folder / "model.mjs").is_file() and _sha(folder / "model.mjs") == files["model.mjs"]:
            problems.append("model.mjs is still the template: write this subject's model (one function per claim)")
    todo = [k for k, v in f.items() if isinstance(v, str) and v.startswith("TODO")]
    if todo:
        problems.append(f"feasibility.json still has template placeholders in: {', '.join(todo)}")
    return problems


def source_facts(folder: Path, f: dict) -> list[str]:
    """The source's own numbers must be quoted exactly and either used by the model or overridden with a reason."""
    problems = []
    kind = str((f.get("source") or {}).get("kind", ""))
    facts = [x for x in (f.get("source_facts") or []) if not str(x.get("quote", "")).startswith("TODO")]
    if kind in ("video", "article") and not facts:
        return ["source_facts: quote every number the source itself states (quote, at, value, unit, use) before "
                "assuming anything; a video's facts come from its transcript"]
    text = None
    tr = _transcript(folder, f)
    if kind == "video":
        if tr is None:
            problems.append("source.transcript: the youtube-analysis transcript.md was not found; set its path")
        else:
            text = _norm(tr.read_text(encoding="utf-8", errors="replace"))
    corpus = ""
    for p in sorted(folder.glob("*.mjs")):
        if p.name not in SHARED and not p.name.startswith("fig") and p.name != "page.mjs":
            corpus += p.read_text(encoding="utf-8", errors="replace")
    corpus += json.dumps(f.get("assumptions"), ensure_ascii=False)      # what the model is built from
    numbers = _numbers(corpus)
    for x in facts:
        fid = x.get("id", "?")
        q = _norm(str(x.get("quote", "")))
        if text is not None and q and q not in text:
            problems.append(f"source_facts {fid}: the quote is not in the transcript ({str(x.get('quote'))[:60]}…): copy the exact words")
        v = x.get("value")
        if not _num(v):
            problems.append(f"source_facts {fid}: value must be the number the quote states")
            continue
        if not _used(float(v), numbers) and not x.get("override"):
            problems.append(f"source_facts {fid}: the source says {v} {x.get('unit', '')} but neither model.mjs nor the "
                            "assumptions use it; use it, or set override with the evidence for a different value")
    return problems


def audits(folder: Path) -> list[str]:
    """Each figure needs the log `run.py audit` writes: its header names the figure's current hash and exit 0."""
    problems = []
    for fig in sorted(folder.glob("fig*.mjs")):
        log = folder / "audits" / f"{fig.stem}.txt"
        if not log.is_file():
            problems.append(f"{fig.name}: not audited; run skill_run(engineering-feasibility, \"audit <folder>\")")
            continue
        text = log.read_text(encoding="utf-8", errors="replace")
        head = re.match(r"# run\.py audit · (\S+) · sha256 ([0-9a-f]{64}) · exit (\d+)", text)
        if not head:
            problems.append(f"audits/{fig.stem}.txt was not written by `run.py audit`; a hand-written log is not an "
                            "audit. Run the audit command")
        elif head.group(2) != _sha(fig):
            problems.append(f"audits/{fig.stem}.txt audited an older {fig.name}; audit again")
        elif head.group(3) != "0" or "audit passed" not in text:
            problems.append(f"audits/{fig.stem}.txt: the audit did not pass; fix the geometry and audit again")
    return problems


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
        problems.append("feasibility.json still has \"example\": true — replace the template with this subject's analysis")
    problems += provenance(folder, f)
    problems += source_facts(folder, f)
    problems += audits(folder)
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
