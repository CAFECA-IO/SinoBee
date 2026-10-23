"""Folders of this computer the sandbox may see (sandbox plan E1).

Folders are given to one conversation at a time - by you (the 「📁 資料夾」 button of the conversation) or when
SinoBee asks (request_host_access) - and kept in ~/.sinobee/sandbox/<conversation>/grants.json. They appear at
/host/<name>. Settings has one switch, `sandbox.review_writes` (寫入前審核, on by default): a folder given for writing
is then a review copy; off, it is written directly. The switch decides when a folder is given; a folder keeps its mode.

Each folder has a mode:

    review  (default) the sandbox works on a copy (an APFS clone on macOS, so it costs almost nothing); its changes
            reach your folder only after you review them (staging.py)
    ro      mounted read-only
    rw      mounted read/write: changes land directly (for a project you trust, e.g. a git repository)

Some places are never offered, whatever is asked: the home folder itself (or anything containing it), the root of the
disk and system folders, credential folders (.ssh, .aws, .gnupg, …), keychains and SinoBee's own data (its workspace is
already /workspace). A folder holding more than `sandbox.review_max_files` (50,000) files is too big to copy: it can be
read-only or read/write only.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .paths import MODES

PRIVATE_DIRS = (".ssh", ".aws", ".gnupg", ".gpg", ".kube", ".docker", ".config/gcloud", ".azure", ".netrc",
                ".password-store", "Library/Keychains", "Library/Cookies", "Library/Application Support/1Password",
                ".config/op", ".git-credentials", ".npmrc", ".pypirc")
SYSTEM_DIRS = ("/", "/System", "/Library", "/Applications", "/bin", "/sbin", "/usr", "/etc", "/var", "/private",
               "/opt", "/dev", "/proc", "/sys", "/boot", "/root", "/Volumes", "/Users", "/home", "/tmp")
NAME_RX = re.compile(r"[^\w.\-㐀-鿿豈-﫿]+")


def home() -> Path:
    return Path.home().resolve()


def refuse(config, path: str | os.PathLike) -> str | None:
    """Why this folder can never be given to the sandbox (None when it can)."""
    try:
        p = Path(os.fspath(path)).expanduser().resolve()
    except (OSError, RuntimeError):
        return "路徑無效"
    h = home()
    if str(p) in SYSTEM_DIRS or p == h or p in h.parents:
        return "不能授權整個家目錄、磁碟根目錄或系統資料夾，請選裡面的某個資料夾"
    for d in PRIVATE_DIRS:
        q = h / d
        if p == q or q in p.parents:
            return f"{d} 存放帳號與金鑰，不能給沙盒"
        if p in q.parents:
            return f"這個資料夾包含 {d}（帳號與金鑰），請選更裡面的資料夾"
    sb = Path(config.home).expanduser().resolve()
    if p == sb or sb in p.parents or p in sb.parents:
        return "SinoBee 自己的資料不能授權（工作區已經是 /workspace）"
    if not p.exists():
        return "找不到這個資料夾"
    if not p.is_dir():
        return "這不是資料夾（要用單一檔案時請直接附上檔案）"
    return None


def count_files(path: Path, limit: int) -> int:
    """Files under path, counting stops past limit."""
    n = 0
    stack = [Path(path)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    else:
                        n += 1
                        if n > limit:
                            return n
        except OSError:
            continue
    return n


def review_max(config) -> int:
    return int(config.get("sandbox.review_max_files", 50_000) or 50_000)


def entry(path: str | os.PathLike, mode: str = "review", name: str | None = None) -> dict:
    p = Path(os.fspath(path)).expanduser().resolve()
    nm = NAME_RX.sub("_", name or p.name).strip("._") or "folder"
    return {"path": str(p), "name": nm[:60], "mode": mode if mode in MODES else "review"}


# ----------------------------------------------------------------- a conversation's own grants
def conv_dir(config, conversation: str) -> Path:
    from .manager import safe
    return Path(config.home) / "sandbox" / safe(conversation or "default")


def grants(config, conversation: str) -> list[dict]:
    try:
        data = json.loads((conv_dir(config, conversation) / "grants.json").read_text(encoding="utf-8"))
        return [g for g in data if isinstance(g, dict) and g.get("path")]
    except (OSError, ValueError):
        return []


def add_grant(config, conversation: str, item: dict) -> None:
    cur = [g for g in grants(config, conversation) if g["path"] != item["path"]] + [item]
    d = conv_dir(config, conversation)
    d.mkdir(parents=True, exist_ok=True)
    (d / "grants.json").write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")


def review_writes(config) -> bool:
    return config.get("sandbox.review_writes", True) is not False


def write_mode(config, asked: str = "write") -> str:
    """The mode a folder given now gets: read-only when asked, else review or direct writing as Settings say."""
    if asked == "ro":
        return "ro"
    return "review" if review_writes(config) else "rw"


def remove_grant(config, conversation: str, path: str) -> dict | None:
    """Take a folder away from the conversation; its review copy and baseline go too. Returns the removed grant."""
    import shutil
    target = str(Path(path).expanduser().resolve())
    cur = grants(config, conversation)
    gone = next((g for g in cur if g["path"] == target), None)
    if gone is None:
        return None
    d = conv_dir(config, conversation)
    name = next((f["name"] for f in folders_for(config, conversation) if f["path"] == target), None)
    (d / "grants.json").write_text(json.dumps([g for g in cur if g["path"] != target], ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    if name:
        shutil.rmtree(d / "host" / name, ignore_errors=True)
        (d / "base" / f"{name}.json").unlink(missing_ok=True)
    return gone


def folders_for(config, conversation: str | None) -> list[dict]:
    """What this conversation's sandbox sees: the folders given to it."""
    out: dict[str, dict] = {}
    for g in grants(config, conversation) if conversation else []:
        out[g["path"]] = entry(g["path"], g.get("mode") or "review", g.get("name"))
    # unique names, as sandbox_map makes them
    used: set[str] = set()
    for e in out.values():
        base, k = e["name"], 2
        while e["name"] in used:
            e["name"], k = f"{base}-{k}", k + 1
        used.add(e["name"])
    return list(out.values())
