"""Reviewing a git repository by git itself (staging.py, via="git"): the size of the project does not matter.

The copy is made by clone.py like any review copy - the working tree as it is, with uncommitted and untracked files,
and the repository (.git cloned in one piece) - so git works in the sandbox as in your folder. The baseline is a
commit of that working tree, made in the copy only (your repository is not touched):

    base/<name>.base-index     the index of the baseline (started from your own index, so only files whose size or
                               time differ are read)
    base/<name>.review-index   the index the review keeps of the copy's working tree, refreshed on each look
    refs/sinobee/base          (in the copy) the baseline commit, kept from git gc

Changes are `git diff --cached <baseline>` of the review index: added / modified / deleted, from git's own stat cache,
so a check costs what changed, not what exists. Files git ignores (build output, .env, …) are not reviewed and never
written back; node_modules and the other clone.DEPS folders are left out as everywhere. A conflict is a change whose
original no longer hashes to the baseline's blob. Commits and branches the sandbox makes stay in the copy: what is
reviewed is the working tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from . import clone

ENV = {"GIT_AUTHOR_NAME": "SinoBee", "GIT_AUTHOR_EMAIL": "sinobee@localhost", "GIT_COMMITTER_NAME": "SinoBee",
       "GIT_COMMITTER_EMAIL": "sinobee@localhost", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
CFG = ["-c", "core.checkStat=minimal", "-c", "core.trustctime=false", "-c", "core.quotePath=false",
       "-c", "core.fsmonitor=false", "-c", "advice.addIgnoredFile=false", "-c", "core.autocrlf=false"]
EXCLUDES = [f":(exclude,glob)**/{d}/**" for d in sorted(clone.DEPS)]
MAX_DIFFS = 200


@lru_cache(maxsize=1)
def git_ok() -> bool:
    """git runs here (on a Mac without the command line tools /usr/bin/git only asks to install them)."""
    if not shutil.which("git"):
        return False
    try:
        return subprocess.run(["git", "--version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_repo(path) -> bool:
    """A folder that is the top of a git repository (its own .git folder)."""
    return (Path(path) / ".git").is_dir() and git_ok()


def _git(repo: Path, *args: str, index: Path | None = None, input: str | None = None, check: bool = True) -> str:
    env = {**os.environ, **ENV}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    r = subprocess.run(["git", *CFG, "-C", str(repo), *args], capture_output=True, input=input, env=env,
                       timeout=900, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        from .staging import StagingError
        raise StagingError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _snapshot(repo: Path, index: Path) -> str:
    """The working tree into `index` (what changed since is re-read only); returns its tree."""
    _git(repo, "add", "-A", "--", ".", *EXCLUDES, index=index)
    return _git(repo, "write-tree", index=index).strip()


def _commit(repo: Path, tree: str, parent: str | None, message: str, ref: str = "refs/sinobee/base") -> str:
    sha = _git(repo, "commit-tree", tree, "-m", message, *(["-p", parent] if parent else [])).strip()
    _git(repo, "update-ref", ref, sha)
    return sha


def _idx(stem: Path, kind: str) -> Path:
    return Path(f"{stem}.{kind}-index")


def make(src: Path, dst: Path, stem: Path) -> dict:
    """The copy of repository src at dst and its baseline commit; returns what base/<name>.json keeps."""
    clone.clone_tree(src, dst)
    for k, v in (("core.checkStat", "minimal"), ("core.trustctime", "false")):   # the sandbox's git: stat as here
        _git(dst, "config", k, v, check=False)
    bidx = _idx(stem, "base")
    bidx.parent.mkdir(parents=True, exist_ok=True)
    if (dst / ".git" / "index").is_file():
        shutil.copyfile(dst / ".git" / "index", bidx)
    else:
        bidx.unlink(missing_ok=True)
    tree = _snapshot(dst, bidx)
    head = _git(dst, "rev-parse", "-q", "--verify", "HEAD^{commit}", check=False).strip() or None
    branch = _git(dst, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    base = _commit(dst, tree, head, f"SinoBee 基準：{src} 在 {time.strftime('%Y-%m-%d %H:%M')} 的樣子")
    shutil.copyfile(bidx, _idx(stem, "review"))
    return {"via": "git", "base": base, "head": head, "branch": branch}


def _blobs(repo: Path, commit: str, paths: list[str]) -> dict[str, tuple[str, str]]:
    """{path: (mode, blob)} of these paths in commit."""
    out: dict[str, tuple[str, str]] = {}
    for i in range(0, len(paths), 500):
        for line in _git(repo, "ls-tree", "-z", commit, "--", *paths[i:i + 500]).split("\0"):
            if "\t" in line:
                meta, path = line.split("\t", 1)
                mode, _kind, sha = meta.split()
                out[path] = (mode, sha)
    return out


def _hash(repo: Path, rel: str, f: Path) -> str:
    """The blob your file would be in the repository (its attributes - line endings, LFS - applied by path)."""
    return _git(repo, "hash-object", "--path", rel, str(f)).strip()


def changes(copy: Path, orig_root: Path, meta: dict, stem: Path, name: str, with_diff: bool,
            max_lines: int = 400) -> list[dict]:
    ridx = _idx(stem, "review")
    if not ridx.is_file() and _idx(stem, "base").is_file():
        shutil.copyfile(_idx(stem, "base"), ridx)
    _snapshot(copy, ridx)
    raw = _git(copy, "diff", "--cached", "--name-status", "-z", "--no-renames", meta["base"], index=ridx).split("\0")
    pairs = [(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2) if raw[i]]
    kinds = {"A": "added", "D": "deleted"}
    items = [(kinds.get(st[0], "modified"), rel) for st, rel in pairs]
    base = _blobs(copy, meta["base"], [rel for k, rel in items if k != "added"])
    present = [rel for k, rel in items if k != "added" and (orig_root / rel).is_file()]
    orig_hash = {rel: _hash(copy, rel, orig_root / rel) for rel in present}
    out = []
    for n, (kind, rel) in enumerate(items):
        orig, p = orig_root / rel, copy / rel
        if kind == "added":
            conflict = os.path.lexists(orig)
        else:
            conflict = orig_hash.get(rel) != base.get(rel, ("", ""))[1]
        item = {"folder": name, "path": rel, "kind": kind, "conflict": conflict,
                "size": p.stat().st_size if p.is_file() else None, "original": str(orig)}
        if with_diff and n < MAX_DIFFS:
            d = _git(copy, "diff", "--cached", "--no-color", "--no-ext-diff", "-U3", meta["base"], "--", rel,
                     index=ridx, check=False)
            if "\nBinary files " in d or d.startswith("Binary files "):
                item["binary"] = True
            else:
                lines = d.splitlines(keepends=True)
                body = next((i for i, line in enumerate(lines) if line.startswith("--- ")), 0)
                lines = [f"--- 原本/{rel}\n", f"+++ 修改後/{rel}\n"] + [x for x in lines[body:] if not x.startswith(("--- ", "+++ "))]
                if len(lines) > max_lines:
                    lines = lines[:max_lines] + [f"…（差異太長，只顯示前 {max_lines} 行）\n"]
                item["binary"] = False
                item["diff"] = "".join(lines)
        out.append(item)
    return out


def rebase(copy: Path, meta: dict, stem: Path, now: dict[str, Path | None]) -> None:
    """A new baseline commit where these paths have the given files' content (None: removed)."""
    if not now:
        return
    tmp = _idx(stem, "tmp")
    tmp.unlink(missing_ok=True)
    _git(copy, "read-tree", meta["base"], index=tmp)
    zero = "0" * len(meta["base"])
    lines = []
    for rel, f in now.items():
        if f is None or not f.is_file():
            lines.append(f"0 {zero}\t{rel}")
        else:
            sha = _git(copy, "hash-object", "-w", "--path", rel, str(f)).strip()
            lines.append(f"{'100755' if os.access(f, os.X_OK) else '100644'} {sha}\t{rel}")
    _git(copy, "update-index", "--index-info", index=tmp, input="\n".join(lines) + "\n")
    tree = _git(copy, "write-tree", index=tmp).strip()
    tmp.unlink(missing_ok=True)
    meta["base"] = _commit(copy, tree, meta["base"], "SinoBee 基準更新（套用／捨棄／復原）")


BRANCH = __import__("re").compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/\-]{1,120}(?<![./])$")


def _orig_git(repo: Path, *args: str, index: Path | None = None, input: str | None = None,
              check: bool = True) -> subprocess.CompletedProcess:
    """git in YOUR repository, with your own identity (author of the branch commit) - never its working tree or index."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    r = subprocess.run(["git", "-c", "core.quotePath=false", "-c", "core.autocrlf=false", "-C", str(repo), *args],
                       capture_output=True, input=input, env=env, timeout=900, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode:
        from .staging import StagingError
        raise StagingError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def to_branch(copy: Path, orig_root: Path, meta: dict, stem: Path, items: list[dict], branch: str,
              message: str) -> dict:
    """The reviewed changes as a commit on a branch of YOUR repository (stage 3): its parent is the branch's tip if
    the branch exists, else the commit your folder was on when the copy was made. Only objects and the branch ref are
    written: your working tree, your index and the branch you have checked out stay as they are."""
    from .staging import StagingError
    if not BRANCH.match(branch) or branch.endswith(".lock"):
        raise StagingError(f"分支名稱不合法：{branch}")
    if not items:
        raise StagingError("沒有要交付的變更")
    ref = f"refs/heads/{branch}"
    cur = _orig_git(orig_root, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    if cur == ref:
        raise StagingError(f"{branch} 是你目前所在的分支：請換一個名稱（交付到分支不會改動你正在用的分支）")
    old = _orig_git(orig_root, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False).stdout.strip()
    parent = old or meta.get("head")
    tmp = _idx(stem, "branch")
    tmp.unlink(missing_ok=True)
    try:
        if parent:
            _orig_git(orig_root, "read-tree", parent, index=tmp)
        zero = "0" * 40
        lines = []
        for c in items:
            rel, f = c["path"], copy / c["path"]
            if c["kind"] == "deleted" or not f.is_file():
                lines.append(f"0 {zero}\t{rel}")
            else:
                sha = _orig_git(orig_root, "hash-object", "-w", "--path", rel, str(f)).stdout.strip()
                lines.append(f"{'100755' if os.access(f, os.X_OK) else '100644'} {sha}\t{rel}")
        _orig_git(orig_root, "update-index", "--add", "--remove", "--index-info", index=tmp,
                  input="\n".join(lines) + "\n")
        tree = _orig_git(orig_root, "write-tree", index=tmp).stdout.strip()
    finally:
        tmp.unlink(missing_ok=True)
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    r = _orig_git(orig_root, *args, check=False)
    if r.returncode:                      # no identity configured for this repository: SinoBee's
        r = subprocess.run(["git", "-C", str(orig_root), *args], capture_output=True, text=True,
                           env={**os.environ, **ENV}, timeout=120)
        if r.returncode:
            raise StagingError(f"git commit-tree: {(r.stderr or r.stdout).strip()[:300]}")
    commit = r.stdout.strip()
    _orig_git(orig_root, "update-ref", "-m", "SinoBee: 交付審核的變更", ref, commit, old or zero)
    return {"branch": branch, "commit": commit, "parent": parent, "created": not old,
            "files": [c["path"] for c in items]}


def sync(copy: Path, orig_root: Path, meta: dict, stem: Path) -> int:
    """At the start of a task: what you changed in your folder since the copy was made comes into the copy - files the
    sandbox has not changed (those stay conflicts for the review). Your working tree is read through the copy's own
    repository (objects go there, not into yours). Returns how many files moved."""
    oidx = _idx(stem, "orig")
    if not oidx.is_file() and _idx(stem, "base").is_file():
        shutil.copyfile(_idx(stem, "base"), oidx)
    _git(copy, f"--work-tree={orig_root}", "add", "-A", "--", ".", *EXCLUDES, index=oidx)
    theirs = _git(copy, "write-tree", index=oidx).strip()
    raw = _git(copy, "diff-tree", "-r", "-z", "--name-status", "--no-renames", meta["base"], theirs).split("\0")
    yours = [(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2) if raw[i]]
    if not yours:
        return 0
    ridx = _idx(stem, "review")
    if not ridx.is_file() and _idx(stem, "base").is_file():
        shutil.copyfile(_idx(stem, "base"), ridx)
    _snapshot(copy, ridx)
    mine = _git(copy, "diff", "--cached", "--name-only", "-z", "--no-renames", meta["base"], index=ridx).split("\0")
    touched = {x for x in mine if x}
    now: dict[str, Path | None] = {}
    for st, rel in yours:
        if rel in touched:
            continue                                  # both changed it: the review shows a conflict
        src, dst = orig_root / rel, copy / rel
        try:
            if st.startswith("D") or not src.is_file():
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                now[rel] = None
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists() or dst.is_symlink():
                    dst.unlink()
                clone.clone_file(src, dst)
                s = src.stat()
                os.utime(dst, ns=(s.st_atime_ns, s.st_mtime_ns))
                now[rel] = dst
        except OSError:
            continue
    rebase(copy, meta, stem, now)
    return len(now)
