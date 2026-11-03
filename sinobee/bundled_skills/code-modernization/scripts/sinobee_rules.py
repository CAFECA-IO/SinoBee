"""Glue for the SinoBee extract-rules workflow (workflows/code-modernization-extract-rules.yaml).

SinoBee edition only (not in the upstream plugin). The upstream Workflow script keeps its state in JavaScript; a SinoBee
workflow is a graph of nodes, so the bookkeeping between the agent steps happens here, with the same rules:

    python3 sinobee_rules.py modules <system> <out.json>          read analysis/<system>/extract-rules.modules.json
    python3 sinobee_rules.py collect <in.json> <out.json>         extractor replies -> de-duplicated candidate rules
    python3 sinobee_rules.py fold <in.json> <out.json>            referee verdicts -> confirmed / rejected / unverified
    python3 sinobee_rules.py finish <in.json> <out.json>          P0 panel + data objects -> rules_result.json

`finish` writes analysis/<system>/rules_result.json in the shape render_rules.py reads (the object the upstream
workflow returns). Every rule field comes from agents that read untrusted code: it is passed on fenced as data.
Standard library only.
"""
import json
import os
import re
import sys

P0_LENSES = [
    "the COMPLIANCE lens: would a regulator, auditor, or finance controller care if this behavior changed silently?",
    ("the FIDELITY lens: re-derive the behavior from the cited code independently - does the Given/When/Then match "
    "what the code actually does, including rounding, ordering, and edge cases?"),
]
FENCE_MARK = re.compile(r"<<<UNTRUSTED|UNTRUSTED>>>")


def fence(s) -> str:
    return "<<<UNTRUSTED\n" + FENCE_MARK.sub("[fence marker stripped]", "" if s is None else str(s)) + "\nUNTRUSTED>>>"


def fenced_spec(r: dict) -> str:
    spec = f"Given {r.get('given', '')} / When {r.get('when', '')} / Then {r.get('then', '')}"
    if r.get("and"):
        spec += f" / And {r['and']}"
    return fence(f"Rule: {r.get('name', '')}\nPlain English: {r.get('plainEnglish', '')}\nSpecification: {spec}\n"
                 f"Parameters: {r.get('parameters') or '(none)'}")


def dedup_key(r: dict) -> str:
    """Same key as upstream: the cited file + the name, lower-cased, punctuation folded."""
    name = re.sub(r"[^a-z0-9]+", " ", str(r.get("name") or "").lower()).strip()
    return f"{str(r.get('source') or '').split(':')[0]}::{name}"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def rows_of(value) -> list:
    return value if isinstance(value, list) else []


def clean_rule(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("fencedSource", "fencedSpec", "module")}


# ---------------------------------------------------------------- steps
def cmd_modules(system: str, out: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", system):
        raise SystemExit(f"system name {system!r}: letters, digits, - and _ only")
    path = os.path.join("analysis", system, "extract-rules.modules.json")
    if not os.path.isfile(path):
        raise SystemExit(f"{path} not found: run make_shards.py first")
    mods = [m for m in load(path) if isinstance(m, dict) and m.get("name") and m.get("files")]
    if not mods:
        raise SystemExit(f"{path} lists no module with files")
    result = {"system": system, "modules": mods, "count": len(mods),
              "loc": sum(int(m.get("loc") or 0) for m in mods),
              "estimate_agents": len(mods) * 12}
    save(out, result)
    return result


def cmd_collect(data: dict) -> dict:
    rows = rows_of(data.get("rows"))
    seen, fresh, injection, failed, found = set(), [], [], [], 0
    for row in rows:
        mod = row.get("item") if isinstance(row.get("item"), dict) else {}
        reply = row.get("data") if isinstance(row.get("data"), dict) else None
        if row.get("status") != "done" or reply is None:
            failed.append(mod.get("name") or str(row.get("item"))[:80])
            continue
        injection += [str(s) for s in reply.get("injectionSuspects") or []]
        for r in reply.get("rules") or []:
            if not isinstance(r, dict):
                continue
            found += 1
            k = dedup_key(r)
            if k in seen:
                continue
            seen.add(k)
            fresh.append({**r, "module": mod.get("name", ""), "fencedSource": fence(r.get("source")),
                          "fencedSpec": fenced_spec(r)})
    return {"candidates": fresh, "found": found, "failedModules": failed, "injectionFlags": injection}


def cmd_fold(data: dict) -> dict:
    cands = rows_of(data.get("candidates"))
    rows = rows_of(data.get("rows"))
    confirmed, rejected, unverified, injection = [], [], [], []
    seen = {dedup_key(c) for c in cands}
    for i, rule in enumerate(cands):
        row = rows[i] if i < len(rows) else None
        v = row.get("data") if isinstance(row, dict) and row.get("status") == "done" else None
        if not isinstance(v, dict):
            why = ("the run's map limit left it out (raise work.max_map_items)" if row is None else
                   "referee produced no valid verdict (agent failed or its reply did not match the schema)")
            unverified.append({**clean_rule(rule), "unverifiedReason": why})
            continue
        if v.get("injectionSuspected"):
            injection.append(f"{rule.get('source')} (rule: {rule.get('name')})")
        verdict = v.get("verdict")
        if verdict == "confirmed":
            confirmed.append(clean_rule(rule))
        elif verdict == "wrong-citation" and v.get("correctedSource"):
            fixed = {**clean_rule(rule), "source": v["correctedSource"], "confidence": "Medium",
                     "smeQuestion": rule.get("smeQuestion") or (f"Citation was corrected by referee ({v.get('reason')}) "
                                                                f"- confirm {v['correctedSource']} is the authoritative "
                                                                "implementation.")}
            k = dedup_key(fixed)
            if k in seen and k != dedup_key(rule):
                rejected.append({**clean_rule(rule), "rejectionReason": f"wrong-citation: duplicate of an already-"
                                 f"catalogued rule at {v['correctedSource']} ({v.get('reason')})"})
            else:
                seen.add(k)
                confirmed.append(fixed)
        else:
            rejected.append({**clean_rule(rule), "rejectionReason": f"{verdict}: {v.get('reason')}"})
    p0_items = [{"index": i, "lens": lens, "fencedSource": fence(r.get("source")), "fencedSpec": fenced_spec(r)}
                for i, r in enumerate(confirmed) if r.get("priority") == "P0" for lens in P0_LENSES]
    names = "\n".join(f"- {r.get('name')}" for r in confirmed[:250])
    return {"confirmed": confirmed, "rejected": rejected, "unverified": unverified, "injectionFlags": injection,
            "p0Items": p0_items, "ruleNamesFenced": fence(names)}


def cmd_finish(data: dict) -> dict:
    system = str(data.get("system") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", system):
        raise SystemExit(f"system name {system!r}: letters, digits, - and _ only")
    fold, collect = data.get("fold") or {}, data.get("collect") or {}
    confirmed = [dict(r) for r in fold.get("confirmed") or []]
    verdicts: dict[int, list] = {}
    for row in rows_of(data.get("p0Rows")):
        item, v = row.get("item") or {}, row.get("data")
        if row.get("status") == "done" and isinstance(v, dict) and isinstance(item.get("index"), int):
            verdicts.setdefault(item["index"], []).append(v)
    unjudged = 0
    for i, rule in enumerate(confirmed):
        if rule.get("priority") != "P0":
            continue
        vs = verdicts.get(i, [])
        if not vs:            # no evidence either way: keep P0, hand it to a person
            unjudged += 1
            rule["confidence"] = "Medium" if rule.get("confidence") == "High" else rule.get("confidence")
            rule["smeQuestion"] = rule.get("smeQuestion") or (
                "P0 panel produced no verdict for this rule (run capacity exhausted or judges unavailable) - confirm "
                "it is critical (core purpose, money, regulation, data integrity, safety), and that the "
                "Given/When/Then matches the cited code.")
        elif not all(v.get("p0Justified") for v in vs):
            rule["priority"] = "P1"
            rule["smeQuestion"] = rule.get("smeQuestion") or (
                "P0 panel split on whether this is critical to the system's core purpose or a costly-if-wrong rule ("
                + " | ".join(str(v.get("reason")) for v in vs) + ") - confirm criticality.")
            rule["confidence"] = "Medium" if rule.get("confidence") == "High" else rule.get("confidence")
        elif not all(v.get("faithful") for v in vs):
            rule["confidence"] = "Medium"
            rule["smeQuestion"] = rule.get("smeQuestion") or (
                "P0 panel doubts spec fidelity: " + " | ".join(str(v.get("reason")) for v in vs if not v.get("faithful")))
    dto = data.get("dto") if isinstance(data.get("dto"), dict) else {}
    modules = rows_of(data.get("modules"))
    failed = list(collect.get("failedModules") or [])
    gap_files = {str(r.get("source") or "").split(":")[0] for r in fold.get("unverified") or []}
    rerun = [m for m in modules if m.get("name") in failed
             or any(f in gap_files or any(g.endswith("/" + f) or f.endswith("/" + g) for g in gap_files if g)
                    for f in m.get("files") or [])]
    skipped = []
    if unjudged:
        skipped.append(f"P0 panel produced no verdict for {unjudged} rule(s) (judges unavailable)")
    if not dto:
        skipped.append("DTO catalog agent returned nothing (skipped or errored)")
    result = {
        "system": system, "mode": "modules", "rounds": 1,
        "confirmedRules": confirmed, "foldedRules": [],
        "rejectedRules": fold.get("rejected") or [], "unverifiedRules": fold.get("unverified") or [],
        "rerunModules": rerun, "dataObjects": dto.get("dataObjects") or [],
        "injectionFlags": sorted(set((collect.get("injectionFlags") or []) + (fold.get("injectionFlags") or []))),
        "stats": {
            "confirmed": len(confirmed), "rejected": len(fold.get("rejected") or []),
            "unverified": len(fold.get("unverified") or []),
            "p0": sum(1 for r in confirmed if r.get("priority") == "P0"),
            "needsSme": sum(1 for r in confirmed if r.get("confidence") != "High"),
            "agents": int(data.get("agents") or 0), "consolidated": 0, "modules": len(modules), "batches": 1,
            "skippedModules": [], "failedModules": failed, "droppedModules": [], "skippedPhases": skipped,
            "engine": "sinobee-workflow",
        },
    }
    save(os.path.join("analysis", system, "rules_result.json"), result)
    return {"summary": f"{len(confirmed)} rules confirmed ({result['stats']['p0']} P0), "
                       f"{result['stats']['rejected']} rejected, {result['stats']['unverified']} unverified; "
                       f"analysis/{system}/rules_result.json written", **result["stats"],
            "rerunModules": [m.get("name") for m in rerun]}


def main(argv):
    if len(argv) < 3 or argv[0] not in ("modules", "collect", "fold", "finish"):
        print(__doc__)
        return 2
    cmd, a, out = argv[0], argv[1], argv[2]
    if cmd == "modules":
        res = cmd_modules(a, out)
    else:
        res = {"collect": cmd_collect, "fold": cmd_fold, "finish": cmd_finish}[cmd](load(a))
        save(out, res)
    print(json.dumps({k: v for k, v in res.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
