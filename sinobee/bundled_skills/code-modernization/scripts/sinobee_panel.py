"""What SinoBee's 現代化 panel reads and writes for one system (SinoBee edition, CAFECA).

The panel replaces the upstream live pane's review deck and sign-off dialog. Everything it shows is read from files
under analysis/<system>/ (never guessed by a model); every change a person makes is written where the commands read
it: RULE_REVIEWS.json (+ .md), the brief's approval block, the proof's sign-off.

    systems(workspace)                       -> [{name, steps, next, track, rules, proof}]
    detail(workspace, system)                -> progress, rule cards with their reviews, brief and proof state
    review(workspace, system, rule, verdict, note)
    sign_brief(workspace, system, name, covers)       covers: phase-1 | full
    sign_proof(workspace, system, name, role, decision)

Standard library only.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report as br

NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
VERDICTS = {"confirmed": "Right", "wrong": "Wrong, or not wanted", "discuss": "Not sure"}
DECISIONS = ("accept", "accept with conditions", "reject")


class PanelError(ValueError):
    pass


def _adir(workspace, system):
    if not NAME.fullmatch(str(system or "")):
        raise PanelError("system: letters, digits, - and _ only")
    d = os.path.join(str(workspace), "analysis", system)
    if not os.path.isdir(d):
        raise PanelError(f"no analysis/{system}/ in the workspace")
    return d


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _today():
    return datetime.date.today().isoformat()


def refresh_report(workspace, system):
    """Rebuild REPORT.html after a change (a convenience: a failure is returned, never raised)."""
    try:
        return br.main([system, "--workspace", str(workspace)]) == 0
    except Exception:  # noqa: BLE001
        return False


def _glance(workspace, system):
    data = br.build(system, str(workspace), want_data=True) or {}
    g = data.get("glance") or {}
    return {k: g.get(k) for k in ("steps", "next", "track", "rules", "proof", "security", "equivalence")}


def systems(workspace):
    root = os.path.join(str(workspace), "analysis")
    out = []
    try:
        names = sorted(e.name for e in os.scandir(root) if e.is_dir() and NAME.fullmatch(e.name))
    except OSError:
        return []
    for name in names:
        try:
            out.append({"name": name, **_glance(workspace, name)})
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "error": str(e)[:200]})
    return out


def _reviews(adir):
    try:
        obj = json.loads(_read(os.path.join(adir, "RULE_REVIEWS.json")) or "{}")
    except ValueError:
        obj = {}
    rv = obj.get("reviews") if isinstance(obj, dict) else None
    return rv if isinstance(rv, dict) else {}


def _brief_state(adir):
    text = _read(os.path.join(adir, "MODERNIZATION_BRIEF.md"))
    if text is None:
        return {"exists": False}
    m = re.search(r"(?im)^\s*Approved by\s*:\s*(.*)$", text)
    who = re.sub(r"\s+Date\s*:.*$", "", m.group(1)).strip() if m else ""
    signed = bool(who) and not re.fullmatch(r"_+", who)
    cov = re.search(r"(?im)Approval covers\s*:\s*(.*)$", text)
    covers = cov.group(1).strip() if cov else ""
    return {"exists": True, "hasBlock": bool(m), "signed": signed, "approvedBy": who if signed else "",
            "covers": covers}


def _proof_state(adir):
    try:
        v = json.loads(_read(os.path.join(adir, "VERIFICATION.json")) or "null")
    except ValueError:
        v = None
    if not isinstance(v, dict):
        return {"exists": False}
    s = v.get("signoff") if isinstance(v.get("signoff"), dict) else {}
    mods = [{"name": m.get("name"), "verdict": m.get("verdict")} for m in v.get("modules") or [] if isinstance(m, dict)]
    return {"exists": True, "modules": mods, "generated": v.get("generated"), "signoff": s,
            "signed": bool(s.get("name") and s.get("decision"))}


def detail(workspace, system):
    adir = _adir(workspace, system)
    text = _read(os.path.join(adir, "BUSINESS_RULES.md")) or ""
    reviews = _reviews(adir)
    rules = []
    for c in (b for b in br.parse_rules(text) if b.get("k") == "rule"):
        rules.append({"id": c["id"], "title": c["title"], "priority": c["p"], "category": c["cat"],
                      "confidence": c["conf"], "cites": c["cites"], "defect": c["defect"],
                      "body": c["body"][:2500], "review": reviews.get(c["id"])})
    flagged = [r["id"] for r in rules if r["priority"] == "P0" and (r["defect"] or r["confidence"] != "High")]
    return {"system": system, **_glance(workspace, system), "rules": rules, "flagged": flagged,
            "brief": _brief_state(adir), "proof": _proof_state(adir),
            "report": os.path.isfile(os.path.join(adir, "REPORT.html"))}


def _plain(s, n):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def review(workspace, system, rule, verdict, note=""):
    """Record (or with verdict "" remove) a person's verdict on one rule; writes RULE_REVIEWS.json and .md."""
    adir = _adir(workspace, system)
    if not re.fullmatch(br.ID, str(rule or "")):
        raise PanelError("rule: an id such as RULE-004")
    if verdict and verdict not in VERDICTS:
        raise PanelError("verdict: confirmed | wrong | discuss")
    text = _read(os.path.join(adir, "BUSINESS_RULES.md")) or ""
    titles = {c["id"]: c["title"] for c in br.parse_rules(text) if c.get("k") == "rule"}
    if rule not in titles:
        raise PanelError(f"{rule} is not in BUSINESS_RULES.md")
    reviews = _reviews(adir)
    if verdict:
        entry = {"verdict": verdict, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                 "title": _plain(titles[rule], 200), "by": "panel"}
        if _plain(note, 1000):
            entry["note"] = _plain(note, 1000)
        reviews[rule] = entry
    else:
        reviews.pop(rule, None)
    _write(os.path.join(adir, "RULE_REVIEWS.json"), json.dumps({"reviews": reviews}, ensure_ascii=False, indent=2) + "\n")
    num = lambda k: int(re.search(r"(\d+)", k).group(1)) if re.search(r"\d+", k) else 0
    rows = ["| Rule | Verdict | When | Title | Note |", "|---|---|---|---|---|"]
    for k in sorted(reviews, key=num):
        v = reviews[k]
        rows.append("| {} | {} | {} | {} | {} |".format(k, VERDICTS.get(v.get("verdict"), v.get("verdict")),
                                                    str(v.get("at", ""))[:10], _plain(v.get("title"), 120).replace("|", "/"),
                                                    _plain(v.get("note"), 300).replace("|", "/")))
    _write(os.path.join(adir, "RULE_REVIEWS.md"),
           f"# Rule reviews: {system}\n\nA person's verdicts on the business rules in BUSINESS_RULES.md. Right = the rule "
           "is correct and wanted; Wrong = it misdescribes the code or should not be kept; Not sure = needs a "
           f"discussion. {len(reviews)} reviewed.\n\n" + "\n".join(rows) + "\n")
    refresh_report(workspace, system)
    return {"rule": rule, "review": reviews.get(rule), "reviewed": len(reviews)}


def sign_brief(workspace, system, name, covers):
    """Fill the brief's approval block (Python port of the upstream hooks/sign.ts)."""
    adir = _adir(workspace, system)
    who = _plain(name, 120)
    if not who:
        raise PanelError("name: who approves")
    if covers not in ("phase-1", "full"):
        raise PanelError("covers: phase-1 | full")
    path = os.path.join(adir, "MODERNIZATION_BRIEF.md")
    text = _read(path)
    if text is None:
        raise PanelError("no MODERNIZATION_BRIEF.md yet: run the brief step first")
    lines = text.split("\n")
    at = next((i for i, ln in enumerate(lines) if re.match(r"^\s*Approved by\s*:", ln, re.IGNORECASE)), -1)
    if at < 0:
        raise PanelError("the brief has no 'Approved by:' line to sign")
    date, indent = _today(), re.match(r"^\s*", lines[at]).group(0)
    inline = re.search(r"\bDate\s*:", lines[at], re.IGNORECASE)
    lines[at] = f"{indent}Approved by: {who}  Date: {date}" if inline else f"{indent}Approved by: {who}"
    end = min(len(lines), at + 12)
    if not inline:
        d = next((i for i in range(at + 1, end) if re.match(r"^\s*Date\s*:", lines[i], re.IGNORECASE)), -1)
        if d >= 0:
            lines[d] = re.match(r"^(\s*Date\s*:\s*)", lines[d], re.IGNORECASE).group(1) + date
        else:
            lines.insert(at + 1, f"{indent}Date:        {date}")
    word = {"phase-1": "Phase 1 only", "full": "Full plan"}[covers]
    c = next((i for i in range(at, min(len(lines), end + 1)) if re.search(r"Approval covers\s*:", lines[i], re.IGNORECASE)), -1)
    if c >= 0:
        cur = lines[c]
        if re.search(r"\[[ xX]?\]", cur):
            lines[c] = re.sub(r"\[[ xX]?\](\s*)([^[\]|]+)",
                              lambda m: ("[x]" if m.group(2).strip().lower() == word.lower() else "[ ]") + m.group(1) + m.group(2),
                              cur)
        else:
            lines[c] = re.sub(r"(Approval covers\s*:\s*).*$", lambda m: m.group(1) + word, cur, flags=re.IGNORECASE)
    else:
        lines.insert(at + 2, f"{indent}Approval covers: {word}")
    _write(path, "\n".join(lines))
    refresh_report(workspace, system)
    return _brief_state(adir)


def sign_proof(workspace, system, name, role, decision):
    """Fill the sign-off of the current proof (VERIFICATION.json and the table in VERIFICATION.md). A new verify run
    recomputes the proof and leaves the sign-off blank again, by design."""
    adir = _adir(workspace, system)
    who, role = _plain(name, 120), _plain(role, 120)
    if not who:
        raise PanelError("name: who signs")
    if decision not in DECISIONS:
        raise PanelError("decision: " + " | ".join(DECISIONS))
    jpath = os.path.join(adir, "VERIFICATION.json")
    try:
        v = json.loads(_read(jpath) or "null")
    except ValueError:
        v = None
    if not isinstance(v, dict):
        raise PanelError("no VERIFICATION.json yet: run the verify step first")
    s = {"name": who, "role": role, "date": _today(), "decision": decision}
    v["signoff"] = s
    _write(jpath, json.dumps(v, ensure_ascii=False, indent=1) + "\n")
    mpath = os.path.join(adir, "VERIFICATION.md")
    md = _read(mpath)
    if md is not None:
        for label, value in (("Name", who), ("Role", role or "________________"), ("Date", s["date"]),
                             ("Decision", decision)):
            md = re.sub(rf"(?m)^\| {label} \| .* \|$", lambda _m, lab=label, val=value: f"| {lab} | {val} |", md, count=1)
        _write(mpath, md)
    refresh_report(workspace, system)
    return _proof_state(adir)
