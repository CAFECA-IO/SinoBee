#!/usr/bin/env python3
"""taiwan-honest-human: find (and safely fix) simplified characters, mainland-China wording, non-Taiwan
punctuation and empty "AI-sounding" phrases in Chinese text.

    run.py check <file>                 report everything, change nothing
    run.py fix <in> <out>               write a copy with the safe fixes applied, report the rest
    run.py terms <詞>                   look a word up in the cross-strait term list

Safe fixes = simplified characters that have exactly one traditional form, mainland terms marked R with exactly
one Taiwan form, and punctuation next to Chinese text. Everything that needs judgment (characters with several
traditional forms, V/P terms, terms with several Taiwan forms, style) is only reported, with line and column.
Fenced code blocks, inline code and URLs are never touched.

JSON on stdout with "needs_review": true when anything is left to look at. Exit 0 when the command ran (findings
are not an error: skill_run reports a non-zero exit as a failure), 1 when a term lookup finds nothing, 2 on a
usage or file error.
Data: data/terms.tsv and data/style.tsv (CAFECA, MIT); data/s2t.json is derived from OpenCC (Apache-2.0,
see data/OPENCC-LICENSE).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
CJK_RE = re.compile(f"[{CJK}]")
LIMIT = 60          # findings listed per kind (counts are always complete)


def out(d: dict, code: int = 0) -> None:
    print(json.dumps(d, ensure_ascii=False, indent=1))
    sys.exit(code)


def load_terms() -> list[dict]:
    rows = []
    for line in (DATA / "terms.tsv").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        c = (line.split("\t") + ["", "", "", ""])[:4]
        rows.append({"from": c[0], "to": c[1], "mode": c[2], "note": c[3]})
    rows.sort(key=lambda r: -len(r["from"]))          # longest first: 視頻會議 before 視頻
    return rows


def load_style() -> list[tuple[re.Pattern, str]]:
    rows = []
    for line in (DATA / "style.tsv").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            pat, tip = line.split("\t", 1)
            rows.append((re.compile(pat), tip))
    return rows


S2T = json.loads((DATA / "s2t.json").read_text(encoding="utf-8"))
SINGLE = {k: v for k, v in S2T["single"].items() if k not in set(S2T["also_traditional"])}
MULTI = S2T["multi"]                                         # every character with several forms
GUESS = {k: v for k, v in MULTI.items() if v[0] != k}        # ...whose usual form differs from itself
TWV = {k: v for k, v in S2T["tw"].items() if k not in SINGLE}
PHRASES = {k: v for k, v in S2T["phrases"].items() if len(k) == len(v)}
MAXPH = max(map(len, PHRASES), default=1)


def protected(text: str) -> list[bool]:
    """True for characters inside fenced code, inline code or URLs: never reported, never changed."""
    mask = [False] * len(text)
    for pat in (r"```.*?```", r"~~~.*?~~~", r"`[^`\n]+`", r"https?://[^\s)\]>」]+", r"\]\([^)\s]+\)"):
        for m in re.finditer(pat, text, re.S):
            for i in range(m.start(), m.end()):
                mask[i] = True
    return mask


def where(text: str, i: int) -> tuple[int, int]:
    line = text.count("\n", 0, i) + 1
    return line, i - (text.rfind("\n", 0, i) + 1) + 1


def context(text: str, i: int, n: int) -> str:
    return text[max(0, i - 8): i + n + 8].replace("\n", " ")


def convert_chars(text: str, mask: list[bool], mainland: bool) -> tuple[str, int, list[dict]]:
    """Simplified -> traditional, one character for one character (positions stay the same).

    Characters with a single traditional form are always converted. Characters with several forms (发 -> 發/髮,
    里 -> 裡/里, 台 -> 臺/台/颱) are also valid traditional characters, so they are converted only when the text is a
    mainland source: by OpenCC's phrase list where a phrase decides (头发 -> 頭髮, 公里 -> 公里), otherwise by the
    usual first form, and every such default choice is returned for review."""
    chars, n, guesses = list(text), 0, []
    i = 0
    while i < len(chars):
        if mask[i]:
            i += 1
            continue
        if mainland and text[i] in MULTI:
            decided = False
            for size in range(min(MAXPH, len(text) - i), 1, -1):
                for start in range(max(0, i - size + 1), i + 1):
                    w = text[start:start + size]
                    if w in PHRASES and not any(mask[start:start + size]):
                        if chars[i] == text[i]:
                            chars[i] = PHRASES[w][i - start]
                            n += chars[i] != text[i]
                        decided = True
                        break
                if decided:
                    break
            if not decided and text[i] in GUESS:     # no phrase decided: usual form, flagged for review
                chars[i] = MULTI[text[i]][0]
                n += 1
                line, col = where(text, i)
                guesses.append({"line": line, "col": col, "char": text[i], "chose": chars[i],
                                "options": MULTI[text[i]], "context": context(text, i, 1)})
        elif text[i] in SINGLE:
            chars[i] = SINGLE[text[i]]
            n += 1
        i += 1
    return "".join(chars), n, guesses


def find_terms(text: str, mask: list[bool], terms: list[dict]) -> list[dict]:
    """Longest-first, non-overlapping matches outside protected spans."""
    taken = [False] * len(text)
    hits = []
    for t in terms:
        start = 0
        while True:
            i = text.find(t["from"], start)
            if i < 0:
                break
            span = range(i, i + len(t["from"]))
            if not any(mask[j] or taken[j] for j in span):
                for j in span:
                    taken[j] = True
                hits.append({**t, "at": i})
            start = i + 1
    return sorted(hits, key=lambda h: h["at"])


PUNCT = [
    # (pattern, replacement, why) - only where Chinese text is right next to the mark
    (re.compile(f"(?<=[{CJK}]),"), "，", "半形逗號"),
    (re.compile(f"(?<=[{CJK}]);"), "；", "半形分號"),
    (re.compile(f"(?<=[{CJK}]):(?!//)"), "：", "半形冒號"),
    (re.compile(f"(?<=[{CJK}])\\?"), "？", "半形問號"),
    (re.compile(f"(?<=[{CJK}])!"), "！", "半形驚嘆號"),
    (re.compile(f"(?<=[{CJK}])\\.(?=\\s|$|[{CJK}])", re.M), "。", "半形句點"),
    (re.compile(f"(?<=[{CJK}])\\(([^()\\n]{{1,80}})\\)"), "（\\1）", "半形括號"),
    (re.compile(f"“([^”\\n]*[{CJK}][^”\\n]*)”"), "「\\1」", "彎引號改「」"),
    (re.compile(f"‘([^’\\n]*[{CJK}][^’\\n]*)’"), "『\\1』", "單引號改『』"),
    (re.compile(f"(?<![A-Za-z0-9\"=])\"([^\"\\n]*[{CJK}][^\"\\n]*)\"(?![A-Za-z0-9\"])"), "「\\1」", "直引號改「」"),
    (re.compile(f"(?<=[{CJK}，。])(?:\\.{{3,}}|。{{3,}}|…(?!…))"), "……", "刪節號為兩個「…」"),
    (re.compile(f"(?<=[{CJK}]) +(?=[，。；：？！、」』）])"), "", "全形標點前不留空格"),
]


def fix_punct(text: str, mask: list[bool]) -> tuple[str, list[dict]]:
    changes = []
    for pat, rep, why in PUNCT:
        mask = protected(text)
        pieces, last = [], 0
        for m in pat.finditer(text):
            if any(mask[j] for j in range(m.start(), m.end())):
                continue
            new = m.expand(rep)
            line, col = where(text, m.start())
            changes.append({"line": line, "col": col, "from": m.group(0), "to": new, "why": why})
            pieces += [text[last:m.start()], new]
            last = m.end()
        text = "".join(pieces) + text[last:]
    return text, changes


def is_mainland(text: str, mask: list[bool], simp: list, terms: list[dict]) -> bool:
    """A mainland source: simplified characters, or at least two different R terms."""
    if len(simp) >= 3:
        return True
    converted = "".join(SINGLE.get(c, c) if not mask[i] else c for i, c in enumerate(text))
    return len({h["from"] for h in find_terms(converted, mask, terms) if h["mode"] == "R"}) >= 2


def analyse(text: str) -> dict:
    mask = protected(text)
    simp = [(i, ch) for i, ch in enumerate(text) if not mask[i] and ch in SINGLE]
    terms = load_terms()
    mainland = is_mainland(text, mask, simp, terms)
    converted, _, guesses = convert_chars(text, mask, mainland)
    hits = find_terms(converted, mask, terms)
    rep = {"simplified": [], "multi": [], "tw_variant": [], "replace": [], "suggest": [], "review": [],
           "region": [], "style": [], "tai": {}}
    grouped: dict[str, dict] = {}
    for i, ch in simp:                       # one row per character: where it first appears and how often
        if ch not in grouped:
            line, col = where(text, i)
            grouped[ch] = {"char": ch, "to": SINGLE[ch], "count": 0, "first": f"{line}:{col}"}
        grouped[ch]["count"] += 1
    rep["simplified"] = list(grouped.values())
    rep["multi"] = guesses
    for i, ch in enumerate(text):
        if mask[i]:
            continue
        if ch in TWV:
            line, col = where(text, i)
            rep["tw_variant"].append({"line": line, "col": col, "char": ch, "taiwan": TWV[ch],
                                      "context": context(text, i, 1)})
    for h in hits:
        line, col = where(converted, h["at"])
        item = {"line": line, "col": col, "term": h["from"], "taiwan": h["to"], "note": h["note"],
                "context": context(converted, h["at"], len(h["from"]))}
        if h["mode"] == "K":
            continue
        if h["mode"] == "R" and "/" not in h["to"]:
            rep["replace"].append(item)
        elif h["mode"] == "R":
            rep["suggest"].append(item)
        elif h["mode"] == "V":
            rep["review"].append(item)
        elif mainland:
            rep["region"].append(item)
    plain = "".join(" " if mask[i] else ch for i, ch in enumerate(text))
    for pat, tip in load_style():
        for m in pat.finditer(plain):
            line, col = where(text, m.start())
            rep["style"].append({"line": line, "col": col, "text": m.group(0)[:40], "tip": tip})
    rep["style"].sort(key=lambda s: (s["line"], s["col"]))
    _, punct = fix_punct(text, mask)
    rep["punctuation"] = punct
    n_tai, n_tai2 = plain.count("台"), plain.count("臺")
    if n_tai and n_tai2:
        rep["tai"] = {"台": n_tai, "臺": n_tai2, "note": "「台」「臺」混用：同一份文字統一一種寫法（專有名詞、品牌照登記名稱）"}
    cjk = len(CJK_RE.findall(plain))
    counts = {k: len(v) for k, v in rep.items() if isinstance(v, list)}
    counts["simplified"] = len(simp)
    for k, v in rep.items():
        if isinstance(v, list):
            rep[k] = v[:LIMIT]
    return {"cjk_chars": cjk, "mainland_source": mainland, "counts": counts, **rep,
            "em_dashes_per_1000": round(plain.count("——") * 1000 / cjk, 1) if cjk else 0,
            "exclamations": plain.count("！") + plain.count("!")}


def apply_fixes(text: str) -> tuple[str, dict]:
    mask = protected(text)
    simp = [i for i, ch in enumerate(text) if not mask[i] and ch in SINGLE]
    mainland = is_mainland(text, mask, simp, load_terms())
    text, n_chars, guesses = convert_chars(text, mask, mainland)
    mask = protected(text)
    hits = find_terms(text, mask, load_terms())          # all modes take part, so V/P words block R overlaps
    out_text, last, replaced = [], 0, []
    for h in hits:
        if h["mode"] != "R" or "/" in h["to"]:
            continue
        line, col = where(text, h["at"])
        replaced.append({"line": line, "col": col, "from": h["from"], "to": h["to"]})
        out_text += [text[last:h["at"]], h["to"]]
        last = h["at"] + len(h["from"])
    text = "".join(out_text) + text[last:]
    text, punct = fix_punct(text, protected(text))
    return text, {"mainland_source": mainland, "chars_converted": n_chars,
                  "multi_form_guesses": guesses[:LIMIT], "multi_form_guesses_total": len(guesses),
                  "terms_replaced": replaced[:LIMIT], "terms_replaced_total": len(replaced),
                  "punctuation_fixed": len(punct)}


def read(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        out({"error": f"file not found: {path}"}, 2)
    if p.stat().st_size > 20 * 1024 * 1024:
        out({"error": f"{p.name} is larger than 20 MB; split it first"}, 2)
    return p.read_text(encoding="utf-8", errors="replace")


def left(rep: dict) -> int:
    c = rep["counts"]
    return sum(c[k] for k in ("simplified", "multi", "replace", "suggest", "review", "region", "punctuation"))


def main(argv: list[str]) -> None:
    if len(argv) >= 2 and argv[0] == "check":
        rep = analyse(read(argv[1]))
        out({"file": argv[1], "needs_review": bool(left(rep) or rep["counts"]["style"]), **rep})
    if len(argv) >= 3 and argv[0] == "fix":
        src, dst = Path(argv[1]), Path(argv[2])
        if src.resolve() == dst.resolve():
            out({"error": "write the fixed copy to a new file (fix <in> <out>); the original stays as it is"}, 2)
        new, done = apply_fixes(read(argv[1]))
        dst.write_text(new, encoding="utf-8")
        rep = analyse(new)
        out({"file": str(dst), "needs_review": bool(left(rep) or rep["counts"]["style"]), "fixed": done,
             "still_to_review": rep})
    if len(argv) >= 2 and argv[0] == "terms":
        q = argv[1]
        q2 = "".join(SINGLE.get(c, c) for c in q)
        found = [t for t in load_terms() if q2 in (t["from"], t["to"]) or q2 in t["to"].split("/")]
        out({"query": q, "matches": found}, 0 if found else 1)
    out({"usage": __doc__}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
