"""Privacy: find personal data and secrets in text, and replace them consistently.

Used where something made from the user's own work leaves it:
  - packaging a task into a skill (skills/package.py): a skill describes how, never this task's people or files
  - evaluation inputs and published reports (evals): test inputs are de-identified before anyone else sees them

scan(text, names=())       -> [Finding]: emails, phone numbers, national IDs, card numbers, API keys / tokens,
                              absolute home paths, IP addresses, and the given names
deidentify(text, mapping)  -> (text, mapping): every finding replaced by a stable placeholder; the same value always
                              gets the same placeholder (與會者 A, <email-1>, …) so the text still reads coherently

Detection is pattern based and errs on the side of reporting; names come from the caller (a roster, or names a
model marked) because no pattern finds them reliably.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = {
    "email": "Email", "phone": "電話", "national_id": "身分證號", "card": "信用卡號", "secret": "金鑰或權杖",
    "path": "本機路徑", "ip": "IP 位址", "name": "人名",
}

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("secret", re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,}|sbh_[A-Za-z0-9_\-]{20,}|"
                          r"gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|"
                          r"xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("path", re.compile(r"(?:/Users/[^/\s\"'`，。]+|/home/[^/\s\"'`，。]+|[A-Za-z]:\\Users\\[^\\\s\"'`，。]+)(?:[/\\][^\s\"'`<>|，。、；：！？（）「」『』【】]*)?")),
    ("national_id", re.compile(r"\b[A-Z][12]\d{8}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("phone", re.compile(r"(?<![\w-])(?:\+?886[ -]?9\d{2}[ -]?\d{3}[ -]?\d{3}|09\d{2}[ -]?\d{3}[ -]?\d{3}|"
                         r"\(?0\d{1,2}\)?[ -]?\d{3,4}[ -]?\d{4}|\+\d{1,3}[ -]?\d{2,4}[ -]?\d{3,4}[ -]?\d{3,4})(?![\w-])")),
    ("ip", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
]


@dataclass(frozen=True)
class Finding:
    kind: str
    value: str
    start: int
    end: int

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": KINDS[self.kind], "value": self.value, "start": self.start, "end": self.end}


def _luhn(digits: str) -> bool:
    nums = [int(c) for c in digits][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2) for i, n in enumerate(nums))
    return total % 10 == 0


def scan(text: str, names: tuple[str, ...] | list[str] = ()) -> list[Finding]:
    """Every finding, earliest first; overlaps keep the earlier, longer match."""
    text = text or ""
    found: list[Finding] = []
    for kind, rx in _PATTERNS:
        for m in rx.finditer(text):
            v = m.group(0)
            if kind == "card":
                digits = re.sub(r"\D", "", v)
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            if kind == "ip" and v.startswith(("0.", "127.", "255.")):
                continue
            found.append(Finding(kind, v, m.start(), m.end()))
    for n in sorted({str(x).strip() for x in names if str(x).strip()}, key=len, reverse=True):
        if len(n) < 2:
            continue
        for m in re.finditer(re.escape(n), text):
            found.append(Finding("name", n, m.start(), m.end()))
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out: list[Finding] = []
    for f in found:
        if out and f.start < out[-1].end:
            continue
        out.append(f)
    return out


def _placeholder(kind: str, n: int) -> str:
    if kind == "name":
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return "人員 " + (letters[n] if n < 26 else f"{letters[n // 26 - 1]}{letters[n % 26]}")
    if kind == "path":
        return f"<路徑-{n + 1}>"
    return f"<{kind}-{n + 1}>"


def deidentify(text: str, mapping: dict[str, str] | None = None,
               names: tuple[str, ...] | list[str] = ()) -> tuple[str, dict[str, str]]:
    """Replace every finding by a placeholder that is stable across calls sharing `mapping`."""
    mapping = dict(mapping or {})
    counts: dict[str, int] = {}
    for v, ph in mapping.items():
        k = "name" if ph.startswith("人員 ") else "path" if ph.startswith("<路徑-") else ph.strip("<>").rsplit("-", 1)[0]
        counts[k] = counts.get(k, 0) + 1
    parts, last = [], 0
    for f in scan(text, names):
        if f.value not in mapping:
            mapping[f.value] = _placeholder(f.kind, counts.get(f.kind, 0))
            counts[f.kind] = counts.get(f.kind, 0) + 1
        parts.append(text[last:f.start])
        parts.append(mapping[f.value])
        last = f.end
    parts.append(text[last:])
    return "".join(parts), mapping


def summary(findings: list[Finding]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        out[f.kind] = out.get(f.kind, 0) + 1
    return out
