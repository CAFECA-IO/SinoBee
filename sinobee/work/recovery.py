"""Recovery of interrupted work: SinoBee checks every minute for tasks that stopped without finishing - the server
was restarted or crashed in the middle of a turn, or a turn's process died - and resumes them by itself.

  - a chat run still marked running whose conversation is no longer running is marked interrupted;
  - an interrupted conversation turn (recent, the newest one in its conversation, started in the web interface) is
    looked at by the AUXILIARY model - cheap background work - which reads the request, the steps done and the
    checklist and decides whether it needs resuming (it does not when the answer was already given or the turn
    was waiting for the user) and writes the brief for the resumed turn: what is done, what is left, what to do
    next. The resumed turn itself runs on the execution model, in the same conversation;
  - an interrupted scheduled task starts again once.
Each conversation is resumed at most work.auto_resume_max times a day, so a task that keeps crashing the process
does not loop. Settings: work.auto_resume, work.auto_resume_max, work.auto_resume_hours.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("sinobee.recovery")
INTERVAL = 60
BRIEF = """A task the user gave an AI agent was interrupted in the middle (the program restarted). Decide whether it
must be resumed, and if so write the brief the agent will resume from.

USER REQUEST:
{request}

STEPS ALREADY DONE (newest last):
{steps}

ACCEPTANCE CHECKLIST:
{checklist}

LAST THING THE AGENT SAID:
{last}

Resume unless the task is clearly finished (the final answer was already given), or the agent was waiting for the
user's answer or approval, or the user asked it to stop.
Reply with JSON only, texts in the user's language:
{{"resume": true|false, "reason": "<one sentence>", "done": ["<finished part>"], "remaining": ["<what is left>"],
  "next": "<the first thing to do now>"}}"""


def _json(text: str) -> dict:
    s, e = (text or "").find("{"), (text or "").rfind("}")
    if s == -1 or e <= s:
        return {}
    try:
        data = json.loads(text[s:e + 1])
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


class Recovery:
    def __init__(self, hub):
        self.hub = hub
        self.last = time.time() - INTERVAL + 20          # first check 20 s after start-up
        self.busy = False

    @property
    def store(self):
        return self.hub.store

    def due(self, now: float | None = None) -> bool:
        return not self.busy and (now or time.time()) - self.last >= INTERVAL

    # ------------------------------------------------------------------ helpers
    def _platform(self, sid: str) -> str:
        try:
            mem = self.hub.rt.memory
            with mem._lock:
                r = mem.db.execute("SELECT platform FROM sessions WHERE id=?", (sid,)).fetchone()
            return (r["platform"] if r else "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def _alive(self, sid: str) -> bool:
        fn = getattr(self.hub, "chat_alive", None)
        return bool(fn and fn(sid))

    def _budget(self, sid: str) -> int:
        """Auto-resumes of this conversation in the last 24 h."""
        mem = self.hub.rt.memory
        try:
            hist = json.loads(mem.meta_get(f"autoresume:{sid}") or "[]")
        except (ValueError, TypeError):
            hist = []
        return len([t for t in hist if time.time() - t < 86400])

    def _count(self, sid: str) -> None:
        mem = self.hub.rt.memory
        try:
            hist = json.loads(mem.meta_get(f"autoresume:{sid}") or "[]")
        except (ValueError, TypeError):
            hist = []
        mem.meta_set(f"autoresume:{sid}", json.dumps([t for t in hist if time.time() - t < 86400] + [time.time()]))

    def _mark(self, run: dict, **info) -> None:
        state = dict(run.get("state") or {})
        state["auto_resume"] = {"at": time.time(), **info}
        self.store.update_run(run["id"], state=state)
        self.hub.notify("run", {"run_id": run["id"]})

    def _checklist(self, sid: str) -> str:
        out = []
        try:
            rows = self.store.board_read(f"session:{sid}", kinds=["plan"], key="checklist", limit=1)
            items = json.loads(rows[-1]["content"]) if rows else []
            out = [f"[{i.get('status')}] {i.get('text')}" for i in items]
        except Exception:  # noqa: BLE001
            pass
        try:                         # a task that runs as milestones resumes at its first unfinished milestone
            from ..agent import milestones as M
            plan = M.load(self.store, f"session:{sid}")
            if plan and not plan.finished():
                out.append("MILESTONES (the resumed turn continues at the first one not done):")
                out += [f"[{m['status']}] {m['id']} {m['title']}" + (f" - {m['summary'][:200]}" if m.get("summary") else "")
                        for m in plan.milestones]
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(out) or "(none)"

    def _last_answer(self, sid: str) -> str:
        try:
            msgs = self.hub.rt.memory.load_messages(sid)
            for m in reversed(msgs[-30:]):
                if m.role == "assistant" and m.text().strip():
                    return m.text()[:1500]
        except Exception:  # noqa: BLE001
            pass
        return "(nothing)"

    async def _brief(self, run: dict) -> dict:
        """The auxiliary model's decision and resume brief (a plain resume when it cannot answer)."""
        from ..types import Message
        st = run.get("state") or {}
        steps = "\n".join(f"- {s.get('tool')} {s.get('brief', '')} -> {'ok' if s.get('ok', True) else 'FAILED'}"
                          for s in (st.get("steps") or [])[-40:]) or "(none)"
        prompt = BRIEF.format(request=(st.get("input") or run.get("name") or "")[:3000], steps=steps[:6000],
                              checklist=self._checklist(run["session_id"])[:2500],
                              last=self._last_answer(run["session_id"]))
        try:
            resp = await self.hub.rt.aux.chat([Message("user", prompt)], None, temperature=0, max_tokens=600)
            data = _json(resp.message.text())
        except Exception as e:  # noqa: BLE001
            log.info("resume brief failed: %s", e)
            data = {}
        if "resume" not in data:
            data = {"resume": True, "reason": "無法判斷進度，直接從中斷處續跑", "done": [], "remaining": [], "next": ""}
        return data

    def eligible(self, run: dict, newest_at: float | None = None) -> tuple[bool, str]:
        """Whether the check would resume this interrupted conversation turn, and why not (for the chat card)."""
        cfg = self.hub.rt.config
        st = run.get("state") or {}
        if st.get("user_stopped"):
            return False, "你已停止這個任務，不會自動續跑"
        if st.get("auto_resume"):
            ar = st["auto_resume"]
            return False, "已續跑" if ar.get("resumed") else (ar.get("reason") or "不會自動續跑")
        if not cfg.get("work.auto_resume", True):
            return False, "自動續跑已關閉"
        if time.time() - (run.get("created_at") or 0) > float(cfg.get("work.auto_resume_hours", 24)) * 3600:
            return False, f"中斷超過 {cfg.get('work.auto_resume_hours', 24)} 小時，不會自動續跑"
        if newest_at and (run.get("created_at") or 0) < newest_at:
            return False, "之後已有新的對話，不會自動續跑"
        sid = run.get("session_id") or ""
        if self._platform(sid) != "web":
            return False, "這段對話不是在網頁介面開始的"
        if self._budget(sid) >= int(cfg.get("work.auto_resume_max", 2)):
            return False, "今天已自動續跑過，請手動繼續"
        return True, ""

    # ------------------------------------------------------------------ the check
    async def check(self) -> list[dict]:
        cfg = self.hub.rt.config
        self.last = time.time()
        if not cfg.get("work.auto_resume", True) or self.busy:
            return []
        self.busy = True
        try:
            return await self._check(cfg)
        finally:
            self.busy = False

    async def _check(self, cfg) -> list[dict]:
        from .store import ACTIVE
        done: list[dict] = []
        window = float(cfg.get("work.auto_resume_hours", 24)) * 3600
        max_n = int(cfg.get("work.auto_resume_max", 2))
        now = time.time()
        runs = self.store.runs(limit=200)
        # 1. a conversation turn marked running although nothing runs it any more (the turn's task died)
        for r in runs:
            if r["kind"] == "chat" and r["status"] in ACTIVE and r.get("session_id") and \
                    now - (r.get("started_at") or r.get("created_at") or now) > 60 and \
                    self._platform(r["session_id"]) == "web" and not self._alive(r["session_id"]) and \
                    self.hub.chat_resumer is not None:
                self.store.update_run(r["id"], status="interrupted", activity="", error="執行中斷（沒有回應）")
                r["status"] = "interrupted"
                self.hub.notify("run", {"run_id": r["id"]})
        newest: dict[str, float] = {}
        for r in runs:
            if r["kind"] == "chat" and r.get("session_id"):
                newest[r["session_id"]] = max(newest.get(r["session_id"], 0), r.get("created_at") or 0)
        for r in runs:
            st = r.get("state") or {}
            if r["status"] != "interrupted" or st.get("auto_resume") or st.get("user_stopped"):
                continue
            if now - (r.get("created_at") or 0) > window:
                continue
            if r["kind"] == "task" and r.get("job_id"):
                self._mark(r, resumed=True, reason="排程工作中斷，重新執行一次")
                try:
                    self.hub.run_job(r["job_id"], trigger="retry")
                    done.append({"run_id": r["id"], "kind": "task"})
                except Exception as e:  # noqa: BLE001
                    log.info("could not restart task run %s: %s", r["id"], e)
                continue
            if r["kind"] != "chat" or not r.get("session_id") or self.hub.chat_resumer is None:
                continue
            sid = r["session_id"]
            if self._platform(sid) != "web" or self._alive(sid):
                continue
            if (r.get("created_at") or 0) < newest.get(sid, 0):
                self._mark(r, resumed=False, reason="之後已有新的對話，不再續跑")
                continue
            if self._budget(sid) >= max_n:
                self._mark(r, resumed=False, reason=f"今天已自動續跑 {max_n} 次，請手動繼續")
                continue
            brief = await self._brief(r)
            if not brief.get("resume"):
                self._mark(r, resumed=False, reason=str(brief.get("reason") or "不需要續跑")[:300])
                self.hub.notify("auto_resume", {"run_id": r["id"], "session_id": sid, "resumed": False,
                                                "reason": brief.get("reason", "")})
                continue
            self._mark(r, resumed=True, reason=str(brief.get("reason") or "")[:300], brief=brief)
            self._count(sid)
            self.hub.resume(r["id"], brief=brief, auto=True)
            self.hub.notify("auto_resume", {"run_id": r["id"], "session_id": sid, "resumed": True,
                                            "reason": brief.get("reason", ""), "next": brief.get("next", "")})
            done.append({"run_id": r["id"], "kind": "chat", "session_id": sid})
        return done
