"""Each conversation's own workspace: what the sandbox sees at /workspace.

    ~/.sinobee/workspace/                       the SinoBee workspace (tools on this computer use all of it)
    ~/.sinobee/workspace/conversations/<id>/    a conversation's own /workspace in the sandbox

Before this, every conversation's sandbox mounted the whole workspace, so one task could read, change or delete what
another left there - and a prompt injection in one task could reach them all. Now a conversation sees only its own
folder; to use another conversation's results, add that folder in 對話設定 → 資料夾 like any folder of the computer.

conversation.json "workspace": "own" | "shared". A conversation started before this keeps "shared" (its files are in
the workspace itself, and its next task must still find them): `settle` decides once, at its first task after the
upgrade - "own" for a new conversation, "shared" for one with history. You can switch in 對話設定 → 工作區 (the
sandbox is made again with the other mount). With the tools on this computer (no sandbox) nothing changes.
"""
from __future__ import annotations

from pathlib import Path

MODES = ("own", "shared")


def own_dir(config, conversation: str, make: bool = True) -> Path:
    from .manager import safe
    d = Path(config.workspace) / "conversations" / safe(conversation or "default")
    if make:
        d.mkdir(parents=True, exist_ok=True)
    return d


def mode(config, conversation: str | None) -> str:
    if not conversation:
        return "shared"
    from . import convconf
    v = convconf.load(config, conversation).get("workspace")
    return v if v in MODES else "own"


def settle(config, conversation: str, legacy: bool) -> str:
    """Decide once whether this conversation has its own workspace: a conversation that already has history keeps
    the shared one (its earlier files are there)."""
    from . import convconf
    data = convconf.load(config, conversation)
    if data.get("workspace") in MODES:
        return data["workspace"]
    chosen = "shared" if legacy else "own"
    convconf.update(config, conversation, lambda d: d.__setitem__("workspace", chosen))
    return chosen


def dir_of(config, conversation: str | None) -> Path:
    """This computer's folder that is /workspace for this conversation's sandbox."""
    if mode(config, conversation) == "own":
        return own_dir(config, conversation)
    return Path(config.workspace)


def shown(p: Path | str) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s.startswith(home + "/") or s == home else s
