"""Evidence: pictures the system itself captured, kept so you can check what the agent claims (sandbox plan V3).

Every screenshot a tool takes (screenshot, the browser's screenshot, playtest shots, frame series and clips) is
saved here by SinoBee, not by the agent, and gets an id - E1, E2, ... per conversation. In the sandbox a checklist
item about what is seen on screen is accepted as done only when its evidence cites such an id (checklist_tools),
so "I verified it" has to point at a picture you can open.

    ~/.sinobee/evidence/<conversation>/manifest.json   [{"id", "t", "turn", "kind", "label", "files", "source",
                                                          "expect", "ok", "observed", "problem", "playtest"}]
    ~/.sinobee/evidence/<conversation>/E7.png          (frames: E8-1.jpg, E8-2.jpg ...; clips: E9.mp4)

Privacy (V5): nothing is captured while you have taken over the sandbox desktop, and before a browser page is
captured its password fields and any text containing one of your stored secrets are blurred (MASK_JS).
Retention: conversations whose evidence is older than evidence.keep_days (30) go, then the oldest until the total
is under evidence.max_mb (1000).
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

ID_RE = re.compile(r"\bE(\d+)\b")
_lock = threading.Lock()

# blur password fields and text holding a secret, on the page about to be captured
MASK_JS = r"""
(secrets) => {
  let n = 0;
  for (const el of document.querySelectorAll('input[type=password]')) { el.style.filter = 'blur(6px)'; n++; }
  if (secrets && secrets.length) {
    const walk = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    const hits = [];
    while (walk.nextNode()) {
      const t = walk.currentNode.nodeValue || '';
      if (secrets.some(s => s && t.includes(s))) hits.push(walk.currentNode.parentElement);
    }
    for (const el of hits) if (el) { el.style.filter = 'blur(8px)'; n++; }
    for (const el of document.querySelectorAll('input,textarea')) {
      if (secrets.some(s => s && String(el.value || '').includes(s))) { el.style.filter = 'blur(8px)'; n++; }
    }
  }
  return n;
}
"""


def secret_snippets(config) -> list[str]:
    """Stored secrets (API keys, passwords) long enough to recognise, for MASK_JS."""
    try:
        values = config.secrets.all_values()
    except Exception:  # noqa: BLE001
        return []
    return sorted({v for v in values if isinstance(v, str) and len(v) >= 8})


class Store:
    def __init__(self, config, conversation: str):
        self.config = config
        self.conversation = re.sub(r"[^a-zA-Z0-9_.-]", "-", conversation or "default")[:64]
        self.root = Path(config.home) / "evidence"
        self.dir = self.root / self.conversation
        self.manifest_path = self.dir / "manifest.json"

    # ----------------------------------------------------------------- reading
    def entries(self) -> list[dict]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def get(self, eid: str) -> dict | None:
        return next((e for e in self.entries() if e.get("id") == eid), None)

    def ids(self) -> set[str]:
        return {e["id"] for e in self.entries()}

    def for_turn(self, turn) -> list[dict]:
        return [e for e in self.entries() if e.get("turn") == turn]

    def file(self, name: str) -> Path | None:
        p = (self.dir / name).resolve()
        if p.parent != self.dir.resolve() or not p.is_file():
            return None
        return p

    # ----------------------------------------------------------------- writing
    def add(self, kind: str, label: str, blobs: list[tuple[bytes, str]], *, turn=None, source: str = "",
            **extra) -> dict:
        """Save one piece of evidence (one picture, or a series of frames, or a clip) and return its entry.
        blobs: [(bytes, extension such as "png" / "jpg" / "mp4")]"""
        with _lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            entries = self.entries()
            n = max([int(m.group(1)) for e in entries for m in [ID_RE.match(e.get("id", ""))] if m] + [0]) + 1
            eid = f"E{n}"
            files = []
            for i, (data, ext) in enumerate(blobs, 1):
                name = f"{eid}.{ext}" if len(blobs) == 1 else f"{eid}-{i}.{ext}"
                (self.dir / name).write_bytes(data)
                files.append(name)
            entry = {"id": eid, "t": round(time.time(), 3), "turn": turn, "kind": kind, "label": label[:200],
                     "files": files, "source": source}
            entry.update({k: v for k, v in extra.items() if v is not None})
            entries.append(entry)
            self.manifest_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        prune(self.config, keep=self.conversation)
        return entry

    def update(self, eid: str, **fields) -> None:
        with _lock:
            entries = self.entries()
            for e in entries:
                if e.get("id") == eid:
                    e.update({k: v for k, v in fields.items() if v is not None})
            self.manifest_path.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")


def store_of(ctx) -> Store:
    st = ctx.state.get("evidence_store") if isinstance(getattr(ctx, "state", None), dict) else None
    if st is None or st.config is not ctx.config:
        state = ctx.state if isinstance(getattr(ctx, "state", None), dict) else {}
        # a sub-agent's pictures belong to its conversation (they can be cited and reviewed there)
        st = Store(ctx.config, state.get("sandbox_conversation") or getattr(ctx, "session_id", "") or "default")
        if isinstance(getattr(ctx, "state", None), dict):
            ctx.state["evidence_store"] = st
    return st


def capture(ctx, png: bytes, label: str, source: str, kind: str = "screenshot", ext: str = "png",
            **extra) -> dict | None:
    """Keep a picture a tool just took. None while the user has the sandbox desktop (nothing is recorded then)."""
    if taken(ctx):
        return None
    try:
        entry = store_of(ctx).add(kind, label, [(png, ext)], turn=ctx.state.get("turn_id"), source=source, **extra)
    except OSError:
        return None
    if getattr(ctx, "emit", None):
        try:
            ctx.emit("evidence", {"items": [entry]})
        except Exception:  # noqa: BLE001
            pass
    return entry


def taken(ctx) -> bool:
    ex = ctx.state.get("executor") if isinstance(getattr(ctx, "state", None), dict) else None
    return bool(ex is not None and getattr(ex, "kind", "") == "sandbox"
                and getattr(ex, "conversation", None) in getattr(ex.manager, "takeover", {}))


def prune(config, keep: str | None = None) -> None:
    root = Path(config.home) / "evidence"
    if not root.is_dir():
        return
    keep_days = float(config.get("evidence.keep_days", 30) or 30)
    max_mb = float(config.get("evidence.max_mb", 1000) or 1000)
    now = time.time()
    dirs = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        files = [f for f in d.iterdir() if f.is_file()]
        newest = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
        size = sum(f.stat().st_size for f in files)
        dirs.append([newest, size, d])
    for newest, size, d in list(dirs):
        if d.name != keep and now - newest > keep_days * 86400:
            shutil.rmtree(d, ignore_errors=True)
            dirs.remove([newest, size, d])
    total = sum(s for _, s, _ in dirs)
    for newest, size, d in sorted(dirs):
        if total <= max_mb * 1024 * 1024:
            break
        if d.name == keep:
            continue
        shutil.rmtree(d, ignore_errors=True)
        total -= size
