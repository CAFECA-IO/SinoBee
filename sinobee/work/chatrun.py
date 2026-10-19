"""Every conversation turn is also a run in the work store (kind "chat"), next to background jobs and workflows.

The record keeps what the work tab needs for past / now / next and for resuming after an interruption:
the request, each tool step (ok / failed, duration), files produced, messages the user typed while it ran,
the final status and summary, and the cost.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("sinobee.work")

STATUS = {"done": "done", "incomplete": "incomplete", "error": "failed", "failed": "failed", "stopped": "cancelled",
          "blocked": "blocked"}
MAX_STEPS = 300


def _brief(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    for k in ("command", "path", "url", "query", "name", "args", "action"):
        if args.get(k):
            v = args[k]
            extra = f" {args['args']}" if k == "name" and args.get("args") else ""
            return (str(v) + extra)[:160]
    return ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])[:160]


class ChatRun:
    def __init__(self, agent, request: str, title: str):
        self.rid = None
        self.hub = None
        if agent.platform not in ("web", "cli") or not agent.config.get("work.track_chat", True):
            return
        try:
            from .hub import get_hub
            self.hub = get_hub(agent.rt)
            run = self.hub.store.create_run(job_id=None, name=title or "對話", kind="chat", trigger="chat",
                                            state={"input": request[:4000], "steps": [], "artifacts": [],
                                                   "steers": [], "model": agent.rt.provider.label})
            self.rid = run["id"]
            self.state = run["state"]
            self.hub.store.update_run(self.rid, status="running", started_at=time.time(),
                                      session_id=agent.session_id, activity="思考中")
            self.hub.notify("run", {"run_id": self.rid})
        except Exception:  # noqa: BLE001 - bookkeeping must never break a conversation
            log.exception("could not record the chat run")
            self.rid = None

    def _save(self, **fields) -> None:
        if not self.rid:
            return
        try:
            self.hub.store.update_run(self.rid, state=self.state, **fields)
            self.hub.notify("run", {"run_id": self.rid})
        except Exception:  # noqa: BLE001
            log.exception("could not update the chat run")

    def tool_started(self, name: str) -> None:
        if self.rid:
            try:
                self.hub.store.update_run(self.rid, activity=f"執行工具：{name}")
            except Exception:  # noqa: BLE001
                pass

    def step(self, name: str, args: dict, ok: bool, seconds: float, error: str = "") -> None:
        if not self.rid:
            return
        steps = self.state.setdefault("steps", [])
        steps.append({"n": len(steps) + 1, "tool": name, "brief": _brief(name, args), "ok": ok,
                      "s": seconds, "t": time.time(), **({"error": error[:300]} if error else {})})
        del steps[:-MAX_STEPS]
        self._save(activity="思考中")

    def files(self, files: list[dict]) -> None:
        if not self.rid or not files:
            return
        self.state.setdefault("artifacts", []).extend(files)
        for f in files:
            try:
                self.hub.store.board_write(f"run:{self.rid}", "artifact", {"path": f["path"], "title": f["name"]},
                                           key=f["path"], author="chat")
            except Exception:  # noqa: BLE001
                pass
        self._save()

    def steer(self, text: str) -> None:
        if self.rid:
            self.state.setdefault("steers", []).append({"text": text[:2000], "t": time.time()})
            self._save()

    def event(self, text: str) -> None:
        """Notable moments (verification result, model escalation) shown in the run detail."""
        if self.rid:
            self.state.setdefault("events", []).append({"text": text[:500], "t": time.time()})
            self._save()

    def control(self, entry: dict) -> None:
        """A decision about the execution itself (consult, stop, health check) with its exact trigger."""
        if self.rid:
            ctl = self.state.setdefault("control", [])
            ctl.append({k: entry.get(k) for k in ("t", "event", "trigger", "label", "step", "detail", "decision",
                                                   "thresholds", "evidence")})
            del ctl[:-100]
            self._save()

    def progress(self, trace: list[dict]) -> None:
        """The newest per-step progress verdicts (saved with the next step)."""
        if self.rid:
            self.state["progress"] = [{k: t.get(k) for k in ("step", "progress", "why", "kind", "no_progress")}
                                      for t in trace[-60:]]

    def finish(self, status: str, text: str, error: dict | None, cost: float, turn_id=None) -> None:
        if not self.rid:
            return
        self.state["turn_id"] = turn_id
        if status == "stopped":
            self.state["user_stopped"] = True       # stopped by the user: never resumed automatically
        self._save(status=STATUS.get(status, status), finished_at=time.time(), activity="",
                   summary=(text or "")[:1000], error=((error or {}).get("summary") or "")[:500],
                   cost_usd=round(cost or 0, 6))
