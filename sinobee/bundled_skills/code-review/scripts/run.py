#!/usr/bin/env python3
"""Code review helpers (git only, no other dependency).

    run.py scope <repo> [base]     what changed: base commit, commits, files with +/- counts, hunks (trimmed),
                                   test / lint commands found in the repo
    run.py secrets <repo> [base]   added lines that look like keys, tokens, passwords or private keys

base defaults to the merge base with origin/main, main, origin/master or master; with no commits ahead it
reviews the uncommitted working-tree changes. JSON out; exit 0 ok, 1 secrets found, 2 usage error.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SECRET = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("OpenAI / Anthropic key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("password / secret assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
    ("connection string with password", re.compile(r"\b[a-z+]+://[^/\s:@]+:[^@\s]{4,}@")),
]
PLACEHOLDER = re.compile(r"(?i)example|dummy|changeme|placeholder|your[_-]|xxx|\*\*\*|<[^>]+>|test|fake|\$\{")


def out(d, code=0):
    print(json.dumps(d, ensure_ascii=False, indent=1))
    sys.exit(code)


def git(repo: Path, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def base_of(repo: Path, base: str | None) -> str | None:
    if base:
        return git(repo, "merge-base", base, "HEAD").strip() or base
    for cand in ("origin/main", "main", "origin/master", "master"):
        r = subprocess.run(["git", "-C", str(repo), "merge-base", cand, "HEAD"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            mb = r.stdout.strip()
            if mb != git(repo, "rev-parse", "HEAD").strip():
                return mb
    return None


def diff_args(repo: Path, base: str | None) -> list[str]:
    b = base_of(repo, base)
    return [f"{b}...HEAD"] if b else ["HEAD"]          # nothing ahead: working tree vs HEAD


def commands(repo: Path) -> list[str]:
    cmds = []
    if (repo / "pyproject.toml").exists() or (repo / "pytest.ini").exists() or (repo / "tests").is_dir():
        cmds.append("python -m pytest -q")
    if (repo / "pyproject.toml").exists() and "ruff" in (repo / "pyproject.toml").read_text(errors="ignore"):
        cmds.append("ruff check .")
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
            cmds += [f"npm run {k}" for k in ("test", "lint", "typecheck", "build") if k in scripts]
        except ValueError:
            pass
    if (repo / "go.mod").exists():
        cmds += ["go test ./...", "go vet ./..."]
    if (repo / "Cargo.toml").exists():
        cmds += ["cargo test", "cargo clippy"]
    if (repo / "Makefile").exists() and re.search(r"^test:", (repo / "Makefile").read_text(errors="ignore"), re.M):
        cmds.append("make test")
    return cmds


def scope(repo: Path, base: str | None):
    rng = diff_args(repo, base)
    files = []
    for line in git(repo, "diff", "--numstat", *rng).splitlines():
        a, d, f = line.split("\t", 2)
        files.append({"file": f, "added": int(a) if a.isdigit() else a, "deleted": int(d) if d.isdigit() else d})
    commits = git(repo, "log", "--oneline", "--no-decorate", f"{rng[0].split('...')[0]}..HEAD").splitlines() \
        if "..." in rng[0] else []
    patch = git(repo, "diff", "-U3", *rng)
    if len(patch) > 60000:
        patch = patch[:60000] + "\n… (diff truncated: read the files in full)"
    out({"status": "ok", "range": rng[0] if "..." in rng[0] else "working tree vs HEAD", "commits": commits[:50],
         "files": files, "tests_and_linters": commands(repo), "diff": patch,
         "next": "read each changed file in full and search for callers of every changed function"})


def secrets(repo: Path, base: str | None):
    rng = diff_args(repo, base)
    found, current = [], None
    line_no = 0
    for line in git(repo, "diff", "-U0", *rng).splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            for kind, rx in SECRET:
                m = rx.search(line)
                if m and not PLACEHOLDER.search(m.group(0)):
                    shown = m.group(0)
                    found.append({"file": current, "line": line_no, "kind": kind,
                                  "match": shown[:6] + "…" + shown[-3:] if len(shown) > 12 else "…"})
                    break
            line_no += 1
    out({"status": "fix" if found else "ok", "problems": [f"{f['file']}:{f['line']} {f['kind']} ({f['match']})"
                                                          for f in found]}, 1 if found else 0)


def main(a):
    if len(a) < 2 or a[0] not in ("scope", "secrets"):
        out({"status": "error", "error": __doc__}, 2)
    repo = Path(a[1]).resolve()
    try:
        git(repo, "rev-parse", "--git-dir")
    except Exception as e:  # noqa: BLE001
        out({"status": "error", "error": f"{repo} is not a git repository: {e}"}, 2)
    try:
        (scope if a[0] == "scope" else secrets)(repo, a[2] if len(a) > 2 else None)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        out({"status": "error", "error": f"{type(e).__name__}: {e}"}, 2)


if __name__ == "__main__":
    main(sys.argv[1:])
