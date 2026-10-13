"""Skill trust levels, policies, provenance lock file and project trust.

Trust is assigned by SinoBee from *where a skill came from* and *whether its content still matches what was
reviewed* - never from anything written inside the skill.

Level      Label (zh-TW)   Typical source                                           Default policy
---------  --------------  -------------------------------------------------------  -----------------------------
builtin    內建            shipped with SinoBee, hash matches the release manifest   listed; scripts as normal tools
verified   官方驗證        sinobee.ai hub, signed + reviewed, verified publisher     listed; scripts as normal tools
local      本機            written or approved by the user                           listed; scripts as normal tools
project    專案            <project>/.sinobee/skills of a project the user trusted   listed; scripts need approval
agent      自動生成        created by SinoBee's learning loop, not yet approved      listed (marked); scripts need approval
community  社群            hub (unreviewed), URL, git                                listed (marked); scripts need approval
untrusted  未信任          project not trusted / changed since trust / modified      hidden; nothing runs
blocked    已封鎖          failed security scan or revoked by the hub                hidden; nothing runs
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

LEVELS = ("builtin", "verified", "local", "project", "agent", "community", "untrusted", "blocked")
LABELS = {"builtin": "內建", "verified": "官方驗證", "local": "本機", "project": "專案", "agent": "自動生成",
          "community": "社群", "untrusted": "未信任", "blocked": "已封鎖"}


@dataclass(frozen=True)
class Policy:
    visible: bool          # appears in the model's catalog / can be activated
    scripts: str           # "allow" (normal tool approval) | "confirm" (always treated as needing approval) | "deny"
    allowed_tools: bool    # honour the skill's `allowed-tools` pre-approvals
    agent_edit: bool       # the agent may patch it in place (otherwise it must fork a local copy)
    caution: bool          # wrap content with an "unverified source" notice


POLICIES = {
    "builtin": Policy(True, "allow", True, False, False),
    "verified": Policy(True, "allow", True, False, False),
    "local": Policy(True, "allow", True, True, False),
    "project": Policy(True, "confirm", False, True, False),
    "agent": Policy(True, "confirm", False, True, True),
    "community": Policy(True, "confirm", False, False, True),
    "untrusted": Policy(False, "deny", False, False, True),
    "blocked": Policy(False, "deny", False, False, True),
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def content_hash(skill_dir: Path) -> str:
    """sha256 over every file (relative path + bytes), stable across machines."""
    h = hashlib.sha256()
    root = Path(skill_dir)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts) or not p.is_file():
            continue
        h.update(str(rel.as_posix()).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def file_manifest(skill_dir: Path) -> dict[str, str]:
    root = Path(skill_dir)
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            out[rel.as_posix()] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class _JsonFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


class LockFile(_JsonFile):
    """Provenance of installed / created skills: ~/.sinobee/skills/.sinobee-lock.json

    {"skills": {"<scope>:<name>": {"trust", "source", "ref", "hash", "installed_at", "publisher", "version",
                                    "signature_ok", "scan": [...] }}, "revoked": ["sha256:..."]}
    """

    def get(self, key: str) -> dict | None:
        return (self.read().get("skills") or {}).get(key)

    def set(self, key: str, entry: dict) -> None:
        with self._lock:
            data = self.read()
            data.setdefault("skills", {})[key] = {**entry, "updated_at": time.time()}
            self.write(data)

    def remove(self, key: str) -> None:
        with self._lock:
            data = self.read()
            (data.get("skills") or {}).pop(key, None)
            self.write(data)

    def revoked(self) -> set[str]:
        return set(self.read().get("revoked") or [])

    def set_revoked(self, hashes: list[str]) -> None:
        with self._lock:
            data = self.read()
            data["revoked"] = sorted(set(hashes))
            self.write(data)


class ProjectTrust(_JsonFile):
    """Projects the user has trusted, stored OUTSIDE the project (a repo cannot trust itself):
    ~/.sinobee/trusted_projects.json  {"<abs root>": {"trusted_at": ts, "skills": {"<name>": "<hash>"}}}
    Skills added or changed after the trust decision become 'untrusted' until re-approved.
    """

    def is_trusted(self, root: Path) -> bool:
        return str(Path(root).resolve()) in self.read()

    def pinned(self, root: Path) -> dict[str, str]:
        return (self.read().get(str(Path(root).resolve())) or {}).get("skills") or {}

    def trust(self, root: Path, skills: dict[str, str]) -> None:
        with self._lock:
            data = self.read()
            data[str(Path(root).resolve())] = {"trusted_at": time.time(), "skills": skills}
            self.write(data)

    def pin(self, root: Path, name: str, h: str) -> None:
        with self._lock:
            data = self.read()
            entry = data.setdefault(str(Path(root).resolve()), {"trusted_at": time.time(), "skills": {}})
            entry.setdefault("skills", {})[name] = h
            self.write(data)

    def untrust(self, root: Path) -> None:
        with self._lock:
            data = self.read()
            data.pop(str(Path(root).resolve()), None)
            self.write(data)
