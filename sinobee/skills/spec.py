"""Agent Skills specification (https://agentskills.io/specification) support.

- parse()      lenient reader: accepts skills written for other clients (Hermes, Claude Code, legacy SinoBee),
               falls back on common YAML mistakes, records diagnostics instead of failing.
- validate()   strict checks, identical in spirit to the reference validator `skills-ref validate`.
- normalize()  converts any accepted frontmatter into a spec-compliant one: unknown top-level fields are moved
               into `metadata` (string -> string) under `sinobee-*` keys.
- render()     writes SKILL.md text from (frontmatter, body).

SinoBee extension keys (all inside `metadata`, values are strings):
  version, author                 common keys used across the ecosystem
  sinobee-tags                    comma-separated tags
  sinobee-triggers                comma-separated keywords (any language) that hint when to use the skill
  sinobee-requires-tools          comma-separated SinoBee tool names; skill is hidden when one is unavailable
  sinobee-platforms               comma-separated: linux, macos, windows
  sinobee-category                free text
Trust, provenance and usage statistics are NEVER read from the skill itself (a skill could lie about them);
they live in SinoBee's lock file / database.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import yaml

ALLOWED_FIELDS = ("name", "description", "license", "compatibility", "metadata", "allowed-tools")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
RECOMMENDED_MAX_LINES = 500
RECOMMENDED_MAX_TOKENS = 5000
SPEC_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")      # strict ASCII form used for skills SinoBee writes
SKILL_FILENAMES = ("SKILL.md", "skill.md")

# legacy / foreign top-level keys -> SinoBee metadata keys
LEGACY_MAP = {
    "version": "version", "author": "author", "tags": "sinobee-tags", "triggers": "sinobee-triggers",
    "requires_tools": "sinobee-requires-tools", "requires-tools": "sinobee-requires-tools",
    "platforms": "sinobee-platforms", "category": "sinobee-category",
}


class SpecError(Exception):
    pass


@dataclass
class Parsed:
    frontmatter: dict
    body: str
    diagnostics: list[str] = field(default_factory=list)


def _stringify(v) -> str:
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, dict):
        return ", ".join(f"{k}={_stringify(x)}" for k, x in v.items())
    return str(v)


def _split(text: str) -> tuple[str, str]:
    m = re.match(r"^﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", text, re.S)
    if not m:
        raise SpecError("SKILL.md must start with YAML frontmatter delimited by '---'")
    return m.group(1), m.group(2).strip()


def _quote_colon_values(raw: str) -> str:
    """Fallback for the most common invalid YAML: unquoted scalar values containing ': '."""
    out = []
    for line in raw.splitlines():
        m = re.match(r"^(\s*[A-Za-z0-9_-]+):\s+(.*:\s.*)$", line)
        if m and not m.group(2).lstrip().startswith(("'", '"', "|", ">", "[", "{")):
            val = m.group(2).replace("\\", "\\\\").replace('"', '\\"')
            line = f'{m.group(1)}: "{val}"'
        out.append(line)
    return "\n".join(out)


def strict_yaml_issues(raw: str) -> list[str]:
    """Constructs that strict YAML parsers (e.g. the reference validator's strictyaml) reject."""
    issues = []
    for line in raw.splitlines():
        m = re.match(r"^\s*[A-Za-z0-9_-]+:\s*([\[{])", line)
        if m:
            issues.append(f"flow-style YAML ({m.group(1)}...) is rejected by strict parsers; use block style or a string")
            break
    return issues


def parse(text: str) -> Parsed:
    raw, body = _split(text)
    diags: list[str] = strict_yaml_issues(raw)
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        try:
            fm = yaml.safe_load(_quote_colon_values(raw)) or {}
            diags.append("frontmatter was invalid YAML (unquoted ':'); parsed with fallback quoting")
        except yaml.YAMLError as e:
            raise SpecError(f"invalid YAML frontmatter: {e}")
    if not isinstance(fm, dict):
        raise SpecError("frontmatter must be a YAML mapping")
    return Parsed(fm, body, diags)


def validate(fm: dict, dir_name: str | None = None, *, strict_ascii: bool = False) -> list[str]:
    """Return a list of spec violations (empty = valid). Mirrors `skills-ref validate`."""
    errors: list[str] = []
    extra = set(fm) - set(ALLOWED_FIELDS)
    if extra:
        errors.append(f"Unexpected fields in frontmatter: {', '.join(sorted(extra))}. "
                      f"Only {sorted(ALLOWED_FIELDS)} are allowed.")
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Field 'name' must be a non-empty string")
    else:
        name = unicodedata.normalize("NFKC", name.strip())
        if len(name) > MAX_NAME:
            errors.append(f"Skill name '{name}' exceeds {MAX_NAME} character limit")
        if name != name.lower():
            errors.append(f"Skill name '{name}' must be lowercase")
        if name.startswith("-") or name.endswith("-"):
            errors.append("Skill name cannot start or end with a hyphen")
        if "--" in name:
            errors.append("Skill name cannot contain consecutive hyphens")
        if not all(c.isalnum() or c == "-" for c in name):
            errors.append(f"Skill name '{name}' contains invalid characters (letters, digits, hyphens only)")
        if strict_ascii and not SPEC_NAME_RE.match(name):
            errors.append(f"Skill name '{name}' must use a-z, 0-9 and single hyphens")
        if dir_name is not None and unicodedata.normalize("NFKC", dir_name) != name:
            errors.append(f"Directory name '{dir_name}' must match skill name '{name}'")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("Field 'description' must be a non-empty string")
    elif len(desc) > MAX_DESCRIPTION:
        errors.append(f"Description exceeds {MAX_DESCRIPTION} character limit ({len(desc)} chars)")
    if "compatibility" in fm:
        c = fm["compatibility"]
        if not isinstance(c, str) or not c.strip():
            errors.append("Field 'compatibility' must be a non-empty string")
        elif len(c) > MAX_COMPATIBILITY:
            errors.append(f"Compatibility exceeds {MAX_COMPATIBILITY} character limit")
    if "metadata" in fm:
        md = fm["metadata"]
        if not isinstance(md, dict):
            errors.append("Field 'metadata' must be a mapping of string keys to string values")
        else:
            bad = [k for k, v in md.items() if not isinstance(v, str)]
            if bad:
                errors.append(f"metadata values must be strings (quote them): {', '.join(map(str, bad))}")
    if "allowed-tools" in fm and not isinstance(fm["allowed-tools"], str):
        errors.append("Field 'allowed-tools' must be a space-separated string")
    if "license" in fm and not isinstance(fm["license"], str):
        errors.append("Field 'license' must be a string")
    return errors


def normalize(fm: dict) -> tuple[dict, list[str]]:
    """Return a spec-compliant copy of fm plus notes about what was changed."""
    notes: list[str] = []
    out: dict = {}
    meta: dict[str, str] = {}
    src_meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    for k, v in src_meta.items():
        if isinstance(v, dict):  # nested client namespaces, e.g. metadata.hermes.tags
            for k2, v2 in v.items():
                key = LEGACY_MAP.get(k2, f"{k}-{k2}")
                meta.setdefault(key, _stringify(v2))
            notes.append(f"flattened nested metadata '{k}'")
        else:
            meta[str(k)] = _stringify(v)
    for k, v in fm.items():
        if k in ("name", "description", "license", "compatibility"):
            if v is not None and v != "":
                out[k] = str(v).strip() if k != "description" else re.sub(r"\s+", " ", str(v)).strip()
        elif k == "allowed-tools":
            out[k] = " ".join(v) if isinstance(v, (list, tuple)) else str(v)
        elif k == "allowed_tools":
            out["allowed-tools"] = " ".join(v) if isinstance(v, (list, tuple)) else str(v)
            notes.append("renamed allowed_tools -> allowed-tools")
        elif k == "metadata":
            continue
        else:
            key = LEGACY_MAP.get(k, f"sinobee-{k}".replace("_", "-"))
            meta.setdefault(key, _stringify(v))
            notes.append(f"moved top-level '{k}' into metadata['{key}']")
    if meta:
        out["metadata"] = meta
    if "description" in out and len(out["description"]) > MAX_DESCRIPTION:
        out["description"] = out["description"][:MAX_DESCRIPTION - 1] + "…"
        notes.append("truncated description to 1024 characters")
    order = {k: i for i, k in enumerate(ALLOWED_FIELDS)}
    return dict(sorted(out.items(), key=lambda kv: order.get(kv[0], 99))), notes


def render(fm: dict, body: str) -> str:
    text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1000, default_flow_style=False).strip()
    return f"---\n{text}\n---\n\n{body.strip()}\n"


def quality_warnings(fm: dict, body: str) -> list[str]:
    """Best-practice hints (agentskills.io/skill-creation/best-practices); never blocking."""
    warns = []
    lines = body.count("\n") + 1
    if lines > RECOMMENDED_MAX_LINES:
        warns.append(f"SKILL.md body has {lines} lines; keep it under {RECOMMENDED_MAX_LINES} and move details "
                     "into references/")
    if len(body) / 3.5 > RECOMMENDED_MAX_TOKENS:
        warns.append("SKILL.md body is over ~5000 tokens; split into references/ loaded on demand")
    desc = fm.get("description") or ""
    if len(desc) < 40 or not re.search(r"\b(use|when)\b|使用|時", desc, re.I):
        warns.append("description should say what the skill does AND when to use it, with trigger keywords")
    if len(re.findall(r"#\d{2,}|PR ?\d+|issue ?\d+", body)) > 5:
        warns.append("reads like an incident log; prefer generalizable procedures")
    if re.search(r"\]\((?!https?:)[^)]*/[^)]*/[^)]*\)", body):
        warns.append("keep file references one level deep from SKILL.md")
    return warns


def meta_list(fm: dict, key: str) -> list[str]:
    v = (fm.get("metadata") or {}).get(key, "")
    return [x.strip() for x in str(v).split(",") if x.strip()]


def suggest_name(text: str) -> str:
    """Make a spec-compliant name from free text."""
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")
    name = re.sub(r"-{2,}", "-", name)[:MAX_NAME].strip("-")
    return name or "skill"
