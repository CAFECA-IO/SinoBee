"""Glue for the SinoBee harden-scan and uplift-deltas workflows (workflows/code-modernization-*.yaml).

SinoBee edition only (not in the upstream plugin): the bookkeeping that the upstream Workflow scripts
(harden-scan.js, uplift-deltas.js) do in JavaScript between agent steps, with the same rules.

    python3 sinobee_scan.py harden-classes <in.json> <out.json>    {classes?} -> the finder classes to run
    python3 sinobee_scan.py harden-collect <in.json> <out.json>    finder replies -> de-duplicated findings
    python3 sinobee_scan.py harden-fold <in.json> <out.json>       refuter verdicts -> survivors / refuted / unverified
    python3 sinobee_scan.py harden-finish <in.json> <out.json>     second judges -> analysis/<system>/harden_result.json
    python3 sinobee_scan.py uplift-categories <in.json> <out.json> {source, target, projectPattern?} -> finder briefs
    python3 sinobee_scan.py uplift-collect <in.json> <out.json>    finder replies -> de-duplicated deltas
    python3 sinobee_scan.py uplift-finish <in.json> <out.json>     referee verdicts -> analysis/<system>/uplift_deltas.json

Every field from an agent came from reading untrusted code, so it is passed on fenced as data. Standard library only.
"""
import json
import os
import re
import sys

from sinobee_rules import fence, load, rows_of, save

SEV = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
CLASSES = [
    ("injection", ("injection of every kind relevant to this stack: SQL/NoSQL, OS command, LDAP, XPath, template. Trace "
                  "user-controlled input to every sink, including dynamic SQL and shell-outs.")),
    ("auth", ("authentication, session handling, and access control: hardcoded creds, weak/missing session handling, "
             "missing auth checks on sensitive routes/transactions/jobs, privilege boundaries.")),
    ("secrets", ("hardcoded secrets and sensitive data exposure: credentials in source/config, secrets in logs, "
                "sensitive data stored or transmitted unprotected.")),
    ("deps", ("vulnerable dependency versions: run available audit tooling (npm audit, pip-audit, OWASP "
             "dependency-check) and map manifests to known CVEs. Include installed vs fixed versions.")),
    ("input", "missing input validation, path traversal, insecure deserialization, and unsafe file handling."),
]
REFUTE = ("You are an adversarial reviewer trying to REFUTE one reported security finding. Look for reasons it is a "
          "false positive: input already sanitized upstream, code path unreachable, test fixture not production code, "
          "version not actually vulnerable.")
CONFIRM = ("You are independently CONFIRMING one Critical/High security finding that already survived a refutation "
           "pass. Your job is calibration: is it really this severe, here, in this deployment shape? Confirm real=true "
           "only if you can articulate the concrete exploit path yourself.")


def system_of(data: dict) -> str:
    s = str(data.get("system") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", s):
        raise SystemExit(f"system name {s!r}: letters, digits, - and _ only")
    return s


def finding_fence(f: dict) -> str:
    return fence(f"CWE: {f.get('cwe')}\nLocation (open this): {f.get('source')}\nTitle: {f.get('title')}\n"
                 f"Exploit scenario: {f.get('exploitScenario')}\nEvidence: {f.get('maskedEvidence') or '(none provided)'}")


def strip(f: dict) -> dict:
    return {k: v for k, v in f.items() if k not in ("fenced", "stance", "index")}


# ---------------------------------------------------------------- harden
def harden_classes(data: dict) -> dict:
    want = [c.strip() for c in str(data.get("classes") or "").split(",") if c.strip()]
    unknown = sorted(set(want) - {k for k, _ in CLASSES})
    if unknown:
        raise SystemExit(f"unknown finder class {unknown}: valid classes are {', '.join(k for k, _ in CLASSES)}")
    return {"classes": [{"key": k, "brief": b} for k, b in CLASSES if not want or k in want]}


def harden_collect(data: dict) -> dict:
    by_key, injection, tools, dead = {}, [], [], []
    for row in rows_of(data.get("rows")):
        reply = row.get("data") if row.get("status") == "done" else None
        if not isinstance(reply, dict):
            dead.append((row.get("item") or {}).get("key", "?"))
            continue
        injection += [str(s) for s in reply.get("injectionSuspects") or []]
        if reply.get("toolOutput"):
            tools.append(str(reply["toolOutput"]))
        for f in reply.get("findings") or []:
            if isinstance(f, dict) and f.get("severity") in SEV:
                by_key.setdefault(f"{f.get('source')}::{f.get('cwe')}", f)
    findings = [{**f, "fenced": finding_fence(f), "stance": REFUTE} for f in by_key.values()]
    return {"findings": findings, "deadFinders": dead, "injectionFlags": injection, "toolOutputs": tools}


def harden_fold(data: dict) -> dict:
    found, rows = rows_of(data.get("findings")), rows_of(data.get("rows"))
    survivors, refuted, unverified = [], [], []
    for i, f in enumerate(found):
        row = rows[i] if i < len(rows) else None
        v = row.get("data") if isinstance(row, dict) and row.get("status") == "done" else None
        if not isinstance(v, dict):
            unverified.append({**strip(f), "unverifiedReason": "no refuter verdict (agent failed, its reply did not "
                               "match the schema, or the map limit left it out)"})
        elif v.get("real"):
            g = strip(f)
            if v.get("adjustedSeverity") in SEV:
                g.update(severity=v["adjustedSeverity"], severityNote=v.get("reason"))
            survivors.append(g)
        else:
            refuted.append({**strip(f), "refutationReason": v.get("reason")})
    unverified.sort(key=lambda f: SEV.get(f.get("severity"), 9))
    confirm = [{"index": i, "fenced": finding_fence(f), "severity": f["severity"], "stance": CONFIRM}
               for i, f in enumerate(survivors) if SEV[f["severity"]] <= 1]
    return {"survivors": survivors, "refuted": refuted, "unverified": unverified, "confirmItems": confirm}


def harden_finish(data: dict) -> dict:
    system = system_of(data)
    fold, collect = data.get("fold") or {}, data.get("collect") or {}
    survivors = [dict(f) for f in fold.get("survivors") or []]
    for row in rows_of(data.get("confirmRows")):
        item, v = row.get("item") or {}, row.get("data")
        if row.get("status") != "done" or not isinstance(v, dict) or not isinstance(item.get("index"), int):
            continue
        f = survivors[item["index"]]
        if not v.get("real"):
            f["severity"] = "Medium"
            f["severityNote"] = (f"Split verdict - refuter kept it, confirmer disagreed: {v.get('reason')}. Human "
                                 "triage required before patching.")
        elif v.get("adjustedSeverity") in SEV and SEV[v["adjustedSeverity"]] > SEV[f["severity"]]:
            f.update(severity=v["adjustedSeverity"], severityNote=v.get("reason"))
    survivors.sort(key=lambda f: SEV.get(f.get("severity"), 9))
    refuted, unverified = fold.get("refuted") or [], fold.get("unverified") or []
    judged = len(survivors) + len(refuted)
    by_sev: dict = {}
    for f in survivors:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    result = {
        "system": system, "findings": survivors, "refuted": refuted, "unverified": unverified,
        "deadFinders": collect.get("deadFinders") or [],
        "credentialFindings": [f for f in survivors if f.get("isCredential")],
        "toolOutputs": collect.get("toolOutputs") or [],
        "injectionFlags": sorted(set(collect.get("injectionFlags") or [])),
        "stats": {"bySeverity": by_sev, "falsePositiveRate": f"{round(len(refuted) / judged * 100)}%" if judged else "n/a",
                  "finders": len(rows_of(data.get("classes"))), "deadFinders": collect.get("deadFinders") or [],
                  "distinctFindings": len(rows_of(collect.get("findings"))), "judged": judged,
                  "unverified": len(unverified), "engine": "sinobee-workflow"},
    }
    save(os.path.join("analysis", system, "harden_result.json"), result)
    return {"summary": f"{len(survivors)} findings ({by_sev}), {len(refuted)} refuted, {len(unverified)} unverified; "
                       f"analysis/{system}/harden_result.json written", "findings": len(survivors)}


# ---------------------------------------------------------------- uplift
def uplift_categories(data: dict) -> dict:
    src, tgt = str(data.get("source") or "").strip(), str(data.get("target") or "").strip()
    if not src or not tgt:
        raise SystemExit("uplift-categories needs source and target versions")
    briefs = [
        ("api-removed", "API-removed",
         (f"APIs (types, methods, signatures) that exist in {src} but are removed/changed in {tgt} AND are referenced by "
         "this code: .NET AppDomain/Remoting/WCF-server/System.Web/BinaryFormatter; Java javax.*->jakarta.*, removed JDK "
         "APIs. ALSO HUNT reflection and strong-encapsulation breakage, the most common silent-at-runtime surprise: Java "
         "17 JPMS strong encapsulation (setAccessible/deep reflection on JDK internals), and .NET trimming/AOT breaking "
         "Type.GetType(string)/DI/serializers. Grep usages; cite each.")),
        ("behavioral", "Behavioral-silent",
         (f"Changes that COMPILE AND RUN but produce a DIFFERENT RESULT on {tgt} vs {src} - the dangerous, silent class. "
         "Probe globalization/locale and text first for this stack (.NET 5+ ICU vs NLS; Java 9+ CLDR locale data and "
         "JDK 18+ default UTF-8; Hibernate 5->6 id generation and type mapping; Spring/Boot binding, path matching and "
         "Jackson defaults; Python 2->3 str/bytes, division, dict ordering, default encoding; Node and PHP coercion, "
         "JSON and dates). Then default encoding, TLS defaults, serialization, DateTime/timezone, floating point, async "
         "context, collection ordering. For each, name the exact characterization test to write before touching it.")),
        ("project-system", "Project-system",
         (f"Build/project-system changes from {src} to {tgt}: packages.config->PackageReference, SDK-style projects, "
         "target-framework monikers, build props; the hosting/runtime-config model (Global.asax/IIS->Program.cs/"
         "Kestrel, ConfigurationManager.AppSettings->IConfiguration); analyzer/compiler tightening that yields new build "
         "failures (nullable reference types, warnings-as-errors, implicit usings, internal JDK APIs under --release). "
         "Cite the files.")),
        ("dependency", "Dependency",
         (f"Third-party dependencies that block or complicate the move to {tgt}: no {tgt} support, a major bump with its "
         f"own breaking changes (EF6->EF Core), or no {tgt} equivalent. Read the manifests. ALWAYS scan the TEST "
         "project manifests too and report the test framework/runner as its own delta: a runner that cannot execute on "
         f"{tgt} is the highest-blast-radius dependency delta and forces an EARLY phase. Do not under-report.")),
    ]
    scope = f" Focus on projects/modules matching {data['projectPattern']}." if data.get("projectPattern") else ""
    return {"categories": [{"key": k, "label": lab, "brief": b + scope} for k, lab, b in briefs],
            "source": src, "target": tgt}


def uplift_collect(data: dict) -> dict:
    by_key, injection, tools = {}, [], []
    for row in rows_of(data.get("rows")):
        reply = row.get("data") if row.get("status") == "done" else None
        if not isinstance(reply, dict):
            continue
        injection += [str(s) for s in reply.get("injectionSuspects") or []]
        if reply.get("toolReport"):
            tools.append(str(reply["toolReport"]))
        for d in reply.get("deltas") or []:
            if isinstance(d, dict):
                by_key.setdefault(f"{d.get('source_site')}::{str(d.get('name') or '').lower()}", d)
    deltas = [{**d, "fenced": fence(f"Cited site (open this): {d.get('source_site')}\nDelta: {d.get('name')}\n"
                                   f"{d.get('oldToNew')}\nSuggested fix: {d.get('suggestedFix') or '(none)'}")}
              for d in by_key.values()]
    return {"deltas": deltas, "injectionFlags": injection, "toolReports": tools}


def uplift_finish(data: dict) -> dict:
    system = system_of(data)
    collect = data.get("collect") or {}
    deltas, rows = rows_of(collect.get("deltas")), rows_of(data.get("rows"))
    confirmed, dropped, unverified = [], [], []
    for i, d in enumerate(deltas):
        d = {k: v for k, v in d.items() if k != "fenced"}
        row = rows[i] if i < len(rows) else None
        v = row.get("data") if isinstance(row, dict) and row.get("status") == "done" else None
        if not isinstance(v, dict):
            unverified.append({**d, "unverifiedReason": "no referee verdict"})
            continue
        if v.get("fixClassCorrection") in ("Mechanical", "Judgment"):
            d["fixClass"] = v["fixClassCorrection"]
        if v.get("verdict") == "confirmed":
            confirmed.append(d)
        elif v.get("verdict") == "wrong-site" and v.get("correctedSite"):
            confirmed.append({**d, "source_site": v["correctedSite"], "confidence": "Medium"})
        else:
            dropped.append({**d, "dropReason": f"{v.get('verdict')}: {v.get('reason')}"})
    rank = {"API-removed": 0, "Behavioral-silent": 1, "Dependency": 2, "Project-system": 3}
    confirmed.sort(key=lambda d: rank.get(d.get("category"), 9))

    def sites(d):
        n = d.get("siteCount")
        return n if isinstance(n, (int, float)) and n > 0 else 1
    total = sum(sites(d) for d in confirmed)
    judg = [d for d in confirmed if d.get("fixClass") == "Judgment"]
    by_cat: dict = {}
    for d in confirmed:
        by_cat[d.get("category")] = by_cat.get(d.get("category"), 0) + 1
    signal = ("no deltas found - verify the version pair and whether the migration tool could actually run"
              if not confirmed else
              f"{total} touched sites across {len(confirmed)} deltas ({sum(sites(d) for d in judg)} of them at "
              "judgment-class sites). Compare totalTouchedSites against the codebase size from assess: if it "
              "approaches most of the tree, this is a rewrite - recommend /code-modernization transform. Judgment share "
              f"({round(len(judg) / len(confirmed) * 100)}% of cards) is a secondary effort signal, not the gate.")
    result = {"system": system, "source": data.get("source"), "target": data.get("target"), "deltas": confirmed,
              "dropped": dropped, "unverified": unverified, "toolReports": collect.get("toolReports") or [],
              "injectionFlags": sorted(set(collect.get("injectionFlags") or [])),
              "stats": {"byCategory": by_cat, "mechanical": len(confirmed) - len(judg), "judgment": len(judg),
                        "totalTouchedSites": total, "judgmentTouchedSites": sum(sites(d) for d in judg),
                        "engine": "sinobee-workflow"},
              "upliftVsRewriteSignal": signal}
    save(os.path.join("analysis", system, "uplift_deltas.json"), result)
    return {"summary": f"{len(confirmed)} deltas confirmed, {len(dropped)} dropped, {len(unverified)} unverified; "
                       f"analysis/{system}/uplift_deltas.json written", "deltas": len(confirmed)}


STEPS = {"harden-classes": harden_classes, "harden-collect": harden_collect, "harden-fold": harden_fold,
         "harden-finish": harden_finish, "uplift-categories": uplift_categories, "uplift-collect": uplift_collect,
         "uplift-finish": uplift_finish}


def main(argv):
    if len(argv) != 3 or argv[0] not in STEPS:
        print(__doc__)
        return 2
    res = STEPS[argv[0]](load(argv[1]))
    save(argv[2], res)
    print(json.dumps({k: v for k, v in res.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
