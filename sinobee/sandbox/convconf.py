"""One settings file per conversation: what this conversation's sandbox may use, and where it differs from Settings.

    ~/.sinobee/sandbox/<conversation>/conversation.json
    {
      "version": 1, "conversation": "<id>", "created": <t>, "used": <t>,
      "folders":   [{"path", "name", "mode": review|ro|rw}],           # 📁 or request_host_access
      "network":   {"domains": ["github.com"], "all": false},           # allowed for this conversation, kept
      "overrides": {"review_writes": true|false, "image": "standard"|"office"}   # absent = as in Settings
    }

The other things kept beside it are the review copies (host/), their baselines (base/), what applies replaced
(applied/) and the pending count (pending.json) - see staging.py. A conversation from before this file had only
grants.json: it is read once and turned into conversation.json.

What is *not* here: domains allowed "only for this task" and "all network this task" stay in memory and end with the
task (manager.grants / open_all), and the sandbox's home folder is the Docker volume sinobee-home-<conversation>.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

VERSION = 1
IMAGES = ("standard", "office")
_lock = threading.Lock()


def conv_dir(config, conversation: str) -> Path:
    from .manager import safe
    return Path(config.home) / "sandbox" / safe(conversation or "default")


def path_of(config, conversation: str) -> Path:
    return conv_dir(config, conversation) / "conversation.json"


def _blank(conversation: str) -> dict:
    now = time.time()
    return {"version": VERSION, "conversation": conversation, "created": now, "used": now,
            "folders": [], "network": {"domains": [], "all": False}, "overrides": {}}


def load(config, conversation: str) -> dict:
    """The conversation's settings (a blank set when it has none). Never raises for a missing or broken file."""
    p = path_of(config, conversation)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        data = _blank(conversation)
        old = conv_dir(config, conversation) / "grants.json"          # before conversation.json
        try:
            grants = json.loads(old.read_text(encoding="utf-8"))
            data["folders"] = [g for g in grants if isinstance(g, dict) and g.get("path")]
            with _lock:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                old.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    base = _blank(conversation)
    for k, v in base.items():
        data.setdefault(k, v)
    if not isinstance(data.get("network"), dict):
        data["network"] = base["network"]
    data["network"].setdefault("domains", [])
    data["network"].setdefault("all", False)
    if not isinstance(data.get("overrides"), dict):
        data["overrides"] = {}
    return data


def save(config, conversation: str, data: dict) -> dict:
    data["version"], data["conversation"] = VERSION, conversation
    p = path_of(config, conversation)
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return data


def update(config, conversation: str, fn) -> dict:
    """Read, change with fn(data), write - one at a time."""
    data = load(config, conversation)
    fn(data)
    return save(config, conversation, data)


def touch(config, conversation: str) -> None:
    """The conversation was used now (cleanup keeps what is used). Only for conversations that have settings."""
    if path_of(config, conversation).exists() or (conv_dir(config, conversation) / "grants.json").exists():
        update(config, conversation, lambda d: d.__setitem__("used", time.time()))


def mtime(config, conversation: str):
    try:
        return path_of(config, conversation).stat().st_mtime_ns
    except OSError:
        return None


# ----------------------------------------------------------------- what Settings say, unless this conversation differs
def review_writes(config, conversation: str | None = None) -> bool:
    if conversation:
        v = load(config, conversation)["overrides"].get("review_writes")
        if isinstance(v, bool):
            return v
    return config.get("sandbox.review_writes", True) is not False


def image(config, conversation: str | None = None) -> str:
    if conversation:
        v = load(config, conversation)["overrides"].get("image")
        if v in IMAGES:
            return v
    v = config.get("sandbox.image", "standard")
    return v if v in IMAGES else "standard"


def domains(config, conversation: str) -> tuple[list[str], bool]:
    net = load(config, conversation)["network"]
    return [d for d in net.get("domains") or [] if isinstance(d, str)], bool(net.get("all"))


# ----------------------------------------------------------------- tidying up
def _drop_overlays(conversation: str) -> None:
    """A conversation tidied away: its overlay views go too (nothing waits for review there)."""
    try:
        from . import overlay
        overlay.remove(conversation)
    except Exception:  # noqa: BLE001 - Docker may not be running
        pass


def sweep_files(config, known=None, now: float | None = None) -> dict:
    """Keep ~/.sinobee/sandbox from growing for ever:

    - a folder of a conversation that never started (a draft of the 📁 button) and is a day old goes;
    - a conversation unused for sandbox.keep_days (30) loses its review copies, baselines, apply backups and
      checkpoint copies, unless
      changes are waiting for review (then everything stays); its settings file stays, and the copies are made again
      from the folders the next time it is used;
    - backups of applies older than sandbox.keep_days go (undo for them is gone).
    known(conversation) -> bool: whether the conversation still exists (None = do not judge)."""
    import shutil

    from . import staging
    now = now or time.time()
    keep = float(config.get("sandbox.keep_days", 30) or 30) * 86400
    root = Path(config.home) / "sandbox"
    out = {"drafts": [], "tidied": [], "kept_pending": [], "backups": 0}
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir():
            continue
        try:
            data = json.loads((d / "conversation.json").read_text(encoding="utf-8"))
            conv = str(data.get("conversation") or d.name)
            used = float(data.get("used") or 0) or d.stat().st_mtime
        except (OSError, ValueError):
            conv, used = d.name, d.stat().st_mtime
        try:
            waiting = len(staging.pending(config, conv))
        except Exception:  # noqa: BLE001
            waiting = 0
        if waiting:
            out["kept_pending"].append(conv)
            continue
        if known is not None and now - used > 86400 and not known(conv):
            _drop_overlays(conv)
            shutil.rmtree(d, ignore_errors=True)
            # a draft's own workspace holds only what was attached before a first message that never came
            shutil.rmtree(Path(config.workspace) / "conversations" / d.name, ignore_errors=True)
            out["drafts"].append(conv)
            continue
        if now - used > keep:
            _drop_overlays(conv)
            gone = False
            for sub in ("host", "base", "applied", "checkpoints"):
                if (d / sub).exists():
                    shutil.rmtree(d / sub, ignore_errors=True)
                    gone = True
            (d / "pending.json").unlink(missing_ok=True)
            if gone:
                out["tidied"].append(conv)
            continue
        applied = d / "applied"
        for b in applied.iterdir() if applied.is_dir() else []:
            try:
                t = json.loads((b / "record.json").read_text(encoding="utf-8")).get("t") or b.stat().st_mtime
            except (OSError, ValueError):
                t = b.stat().st_mtime
            if now - float(t) > keep:
                shutil.rmtree(b, ignore_errors=True)
                out["backups"] += 1
    # own workspaces of drafts that only ever got attachments (no folder, no message)
    wroot = Path(config.workspace) / "conversations"
    for w in sorted(wroot.iterdir()) if known is not None and wroot.is_dir() else []:
        try:
            if (w.is_dir() and now - w.stat().st_mtime > 86400 and not (root / w.name).exists()
                    and {e.name for e in w.iterdir()} <= {"uploads"} and not known(w.name)):
                shutil.rmtree(w, ignore_errors=True)
                out["drafts"].append(w.name)
        except OSError:
            continue
    return out
