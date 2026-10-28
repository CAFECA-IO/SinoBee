"""Runs a milestone plan inside an agent turn (see agent/milestones.py for the rules).

The agent loop calls the hooks:
  begin(query, stats)        after planning: show the plan, wait for the user when needed, start
  panel()                    the <milestones> block of the per-step tail
  before_step(step, stats)   an "interrupt and re-plan" correction; wait for sub-agents the next milestone needs
  after_step(step, stats)    seal a finished milestone (fresh context), apply corrections, collect sub-agents
  on_final(stats)            the model wants to finish: wait for sub-agents / send it back to open milestones
  end(status)                stop what still runs, save
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..types import Message
from . import milestones as M

log = logging.getLogger("sinobee.agent")


class MilestoneRunner:
    def __init__(self, agent, plan: M.Plan, query: str):
        self.agent = agent
        self.plan = plan
        self.query = query
        self.tasks: dict[str, asyncio.Task] = {}          # milestone id -> running sub-agent
        self.ms_start = len(agent.history)                 # first message of the current milestone
        self.step0 = 0                                     # the turn step the current milestone started at
        self.archive: list[Message] = []                   # messages of sealed milestones (out of the context)
        self.interrupt: dict | None = None
        self.sealed = 0

    # ------------------------------------------------------------------ helpers
    @property
    def store(self):
        from ..work.hub import get_hub
        return get_hub(self.agent.rt).store

    @property
    def scope(self) -> str:
        return self.agent.ctx.state["board_scope"]

    def facts(self) -> dict:
        try:
            return M.facts(self.store, self.scope)
        except Exception:  # noqa: BLE001
            return dict(self.plan.data.get("facts") or {})

    def save(self, phase: str = "running") -> None:
        try:
            M.save(self.store, self.scope, self.plan)
        except Exception:  # noqa: BLE001 - the in-memory plan is what the turn uses
            log.exception("could not save the milestone plan")
        card = self.plan.card(self.facts(), phase)
        self.agent._emit("milestones", card)
        rec = getattr(self.agent, "_run_rec", None)
        if rec and rec.rid:
            rec.state["milestones"] = card
            rec._save()

    def control(self, stats: dict, trigger: str, detail: str, **extra) -> None:
        self.agent._control(stats, "milestone", trigger, detail, **extra)

    # ------------------------------------------------------------------ start
    @classmethod
    def resume(cls, agent, query: str) -> "MilestoneRunner | None":
        """An unfinished plan on the blackboard (an interrupted or continued task) carries on in this turn."""
        try:
            from ..work.hub import get_hub
            plan = M.load(get_hub(agent.rt).store, agent.ctx.state["board_scope"])
        except Exception:  # noqa: BLE001
            return None
        if not plan or plan.finished():
            return None
        for m in plan.milestones:
            if m["status"] == "delegated":            # its sub-agent died with the previous turn: run it again
                m["status"] = "todo"
        return cls(agent, plan, query)

    def needs_confirmation(self, stats: dict) -> bool:
        a = self.agent
        if a.depth > 0 or a.platform not in ("web", "cli") or getattr(a.ctx, "ask", None) is None:
            return False
        if self.query.lstrip().startswith(("[自動續跑]", "[續跑]")):
            return False
        return bool(self.plan.data.get("ask_user")) or self.plan.uses_subagents()

    async def begin(self, stats: dict, resumed: bool = False) -> None:
        a = self.agent
        if not resumed:
            M.write_facts(self.store, self.scope, self.plan.data.get("facts") or {}, "plan")
            self._checklist()
        if not resumed and self.needs_confirmation(stats):
            await self._confirm(stats)
        else:
            why = "接續未完成的里程碑" if resumed else "需求明確、不需要子代理：顯示計畫並直接開始"
            self.control(stats, "milestone_started", f"{why}（{self.plan.progress()[1]} 個里程碑）")
        self._start_next(stats)
        self.save("running")
        a._event(f"任務分成 {len(self.plan.milestones)} 個里程碑執行")

    def _checklist(self) -> None:
        """Each milestone's acceptance outcomes join the turn's checklist, tagged with the milestone."""
        from ..tools import checklist_tools
        items = checklist_tools.get(self.agent.ctx)
        for m in self.plan.milestones:
            for text in m["acceptance"]:
                if len(items) >= checklist_tools.MAX_ITEMS:
                    break
                items.append({"id": len(items) + 1, "status": "todo", "evidence": "", "updated": time.time(),
                              "text": f"[{m['id']}] {text}", "check": _file_check(m), "milestone": m["id"]})
        checklist_tools.save(self.agent.ctx)

    async def _confirm(self, stats: dict) -> None:
        """The plan waits for the user: ambiguous request, or sub-agents would run in parallel."""
        a = self.agent
        for rnd in range(3):
            q = self.plan.data.get("ask_user")
            reason = "需求有不確定的地方" if q else "會有子代理並行執行"
            self.save("proposed")
            question = (q + "\n\n" if q else "") + f"要照這個里程碑計畫開始嗎？（{reason}）\n" + self.plan.describe()
            ans = await a.ctx.ask(question[:1500], [
                {"label": "開始", "description": "照這個計畫執行", "recommended": True},
                {"label": "修正計畫", "description": "在下方說明要怎麼改（或回答上面的問題），思維模型會重新規劃"}],
                False, True)
            if ans is None:
                self.plan.data["confirmed"] = "timeout"
                self.control(stats, "milestone_confirmed", f"等待確認逾時，照原計畫開始（{reason}）", decision="timeout")
                return
            text = str(ans.get("text") or "").strip()
            if not text and "修正計畫" not in (ans.get("choices") or []):
                self.plan.data["confirmed"] = "user"
                self.plan.data["ask_user"] = None
                self.control(stats, "milestone_confirmed", f"使用者確認開始（{reason}）", decision="start")
                return
            self.control(stats, "milestone_corrected", f"第 {rnd + 1} 次修正：{text[:200]}", decision="replan")
            self.plan.data["ask_user"] = None
            await self.replan(stats, "使用者在開工前修正計畫", text)
        self.plan.data["confirmed"] = "after_corrections"

    def _start_next(self, stats: dict) -> None:
        """Delegate the independent milestones that are ready; make the next sequential one current."""
        for m in self.plan.parallel_ready():
            self._delegate(m, stats)
        if not self.plan.current():
            nxt = self.plan.next_sequential()
            if nxt and not self.plan.waiting_on(nxt) and self.plan.ready(nxt):
                nxt["status"] = "doing"
                nxt["started"] = time.time()
                self.ms_start = len(self.agent.history)
                self.step0 = stats.get("step", 0)
                if getattr(self.agent, "_progress", None):
                    self.agent._progress.new_milestone(stats.get("step", 0))
                self.agent._event(f"開始里程碑 {nxt['id']}：{nxt['title']}")

    # ------------------------------------------------------------------ sub-agents
    def _delegate(self, m: dict, stats: dict) -> None:
        from ..work.agents import ROLES, run_subagent
        from ..work.hub import get_hub
        a = self.agent
        m["status"] = "delegated"
        m["started"] = time.time()
        facts = self.facts()
        done = [x for x in self.plan.milestones if x["status"] == "done"]
        context = "\n".join(
            [f"This is milestone {m['id']} of a larger task: {self.query[:1500]}",
             "Acceptance: " + "; ".join(m["acceptance"]) if m["acceptance"] else "",
             "Expected outputs: " + "; ".join(m["outputs"]) if m["outputs"] else "",
             ("Shared facts - use these values exactly; if you find a value must differ, write it with "
              "board_write(kind='fact', key=<name>, content=<value>) (it goes to your staging area and the "
              "coordinator decides):\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items())) if facts else
             "Record values other parts must reuse with board_write(kind='fact', key=<name>, content=<value>).",
             ("Finished milestones:\n" + "\n".join(f"- {x['id']} {x['title']}: {x['summary'][:300]}" for x in done))
             if done else ""])
        spec = ROLES.get(m["role"]) or ROLES["general"]
        limit = max(spec["iterations"], 3 * m["estimate_steps"])

        async def go():
            return await run_subagent(a.rt, role=m["role"], task=f"{m['title']}: {m['goal']}", board_scope=self.scope,
                                      parent_session=a.session_id, approve=a.ctx.approve, emit=a._emit,
                                      context=context, hub=get_hub(a.rt), max_iterations=limit,
                                      owner=a.ctx.state.get("run_id") or a.session_id,
                                      title=f"[里程碑 {m['id']}] {m['title']}")
        self.tasks[m["id"]] = asyncio.ensure_future(go())
        self.control(stats, "milestone_delegated", f"{m['id']} {m['title']} 交給子代理（{spec['label']}），與主代理並行")

    async def _collect(self, stats: dict, wait: bool = False) -> bool:
        """Finished sub-agents: their result, their staged facts merged. Returns True when something changed."""
        changed = False
        for mid, task in list(self.tasks.items()):
            if not task.done():
                if not wait:
                    continue
                self.agent._event(f"等待子代理完成里程碑 {mid}")
                try:
                    await task
                except asyncio.CancelledError:
                    if task.cancelled():
                        pass
                    else:
                        raise
                except Exception:  # noqa: BLE001
                    pass
            self.tasks.pop(mid, None)
            m = self.plan.get(mid)
            if not m or task.cancelled():
                continue
            try:
                res = task.result()
            except Exception as e:  # noqa: BLE001
                res = {"status": "error", "result": f"（子代理失敗：{e}）", "session_id": ""}
            m["sub_session"] = res.get("session_id")
            g = getattr(self.agent, "_guard", None)
            if g is not None:                   # the sub-agent's spending counts toward this task's budget
                g.extra_cost += float(res.get("cost_usd") or 0)
            await self._merge_facts(m, res.get("session_id") or "", stats)
            if res.get("status") == "done":
                m["status"] = "done"
                m["summary"] = (res.get("result") or "")[:1500]
                self.control(stats, "milestone_done", f"子代理完成 {mid} {m['title']}")
            else:
                # the coordinator takes it over instead of failing the whole task
                m["status"] = "todo"
                m["parallel"] = False
                m["summary"] = f"子代理未完成（{res.get('status')}）：{(res.get('result') or '')[:400]}"
                self.control(stats, "milestone_taken_over",
                             f"子代理沒有完成 {mid}（{res.get('status')}），改由主代理接手")
            changed = True
        if changed:
            self._start_next(stats)
            self.save()
        return changed

    async def _merge_facts(self, m: dict, sub: str, stats: dict) -> None:
        if not sub:
            return
        add, conflicts = M.merge_plan(self.store, self.scope, sub)
        if add:
            M.write_facts(self.store, self.scope, add, f"merged:{sub}")
        for c in conflicts:
            value, reason = await self._decide_fact(c, m, sub, stats)
            M.write_facts(self.store, self.scope, {c["key"]: value}, "thinking")
            self.store.board_write(self.scope, "issue", f"事實衝突 {c['key']}：共享表「{c['shared']}」vs 子代理 {m['id']}「"
                                   f"{c['proposed']}」→ 採用「{value}」。{reason}", author="thinking")
            self.control(stats, "fact_conflict", f"{c['key']}：共享表「{c['shared'][:80]}」，子代理 {m['id']} 回報「"
                         f"{c['proposed'][:80]}」→ 採用「{value[:80]}」（{reason[:120]}）", decision=value[:60])
        M.mark_merged(self.store, self.scope, sub)

    async def _decide_fact(self, c: dict, m: dict, sub: str, stats: dict) -> tuple[str, str]:
        from . import thinking
        prompt = thinking.FACT_CONFLICT.format(request=self.query[:3000], key=c["key"], shared=c["shared"],
                                               proposed=c["proposed"], who=sub, goal=m["goal"][:300])
        resp = await self.agent._thinking_call("事實裁決", [Message("user", prompt)], 400, stats)
        data = thinking.parse_json(resp.message.text()) if resp else {}
        value = str(data.get("value") or "").strip() or c["shared"]
        return value[:300], str(data.get("reason") or "思維模型沒有回覆，保留共享表的值")[:300]

    # ------------------------------------------------------------------ per step
    def panel(self) -> str:
        return self.plan.panel(self.facts())

    def feedback(self, text: str, mode: str) -> None:
        """A correction from the user while the task runs: at the next milestone boundary, or now."""
        entry = {"text": str(text)[:2000], "mode": "now" if mode == "now" else "boundary", "t": time.time()}
        self.plan.data.setdefault("feedback", []).append(entry)
        if entry["mode"] == "now":
            self.interrupt = entry
        self.save()

    async def before_step(self, step: int, stats: dict) -> None:
        if self.interrupt:
            entry, self.interrupt = self.interrupt, None
            cur = self.plan.current()
            if cur:
                self._seal(cur, "中斷：使用者要求立即重新規劃。這個里程碑到目前為止的工作：" + self._last_words(),
                           [], status="todo")
            self.control(stats, "milestone_interrupted", f"立即中斷並重劃：{entry['text'][:200]}", decision="replan")
            entry["applied"] = True
            await self.replan(stats, "使用者要求立即中斷並重新規劃", entry["text"])
            self._start_next(stats)
            self.save()
        await self._collect(stats)
        cur = self.plan.current()
        if not cur:
            nxt = self.plan.next_sequential()
            if nxt and self.plan.waiting_on(nxt) and not self.plan.parallel_ready():
                await self._collect(stats, wait=True)      # the next milestone needs a sub-agent's result
            self._start_next(stats)

    def tool_done(self, mid: str, summary: str, outputs: list) -> str:
        cur = self.plan.current()
        if not cur or (mid and mid != cur["id"]):
            return f"ERROR: the current milestone is {cur['id'] if cur else '(none)'}; finish that one."
        from ..tools import checklist_tools
        left = [i for i in checklist_tools.open_items(checklist_tools.get(self.agent.ctx))
                if i.get("milestone") == cur["id"]]
        if left:
            return ("ERROR: acceptance items of this milestone are still open - finish and mark them done with "
                    "evidence first: " + "; ".join(f"#{i['id']} {i['text']}" for i in left))
        if not str(summary or "").strip():
            return "ERROR: give a summary: what was done, the results and what later milestones need."
        self.agent.ctx.state["milestone_done"] = (cur["id"], str(summary)[:2000],
                                                  [str(o)[:200] for o in (outputs or [])][:10], "done")
        nxt = self.plan.next_sequential()
        return (f"Milestone {cur['id']} done. After this step the context is cleared; you continue with "
                + (f"{nxt['id']} {nxt['title']}." if nxt and nxt["id"] != cur["id"] else "the final delivery."))

    def _last_words(self) -> str:
        for m in reversed(self.agent.history[self.ms_start:]):
            if m.role == "assistant" and m.text().strip():
                return m.text()[:500]
        return "（尚無文字說明）"

    def _seal(self, m: dict, summary: str, outputs: list, status: str = "done") -> None:
        """Close a milestone and clear its messages from the model context (fresh context for the next one)."""
        a = self.agent
        a._persist()                                          # the transcript keeps every message
        moved = a.history[self.ms_start:]
        self.archive.extend(moved)
        a.history = a.history[:self.ms_start]
        a._persisted = len(a.history)
        m["status"] = status
        m["summary"] = summary[:2000]
        m["result_outputs"] = outputs
        m["finished"] = time.time()
        word = "完成" if status == "done" else "中斷"
        note = (f"[里程碑 {m['id']} {word}] {m['title']}\n摘要：{summary[:2000]}"
                + (f"\n產出：{', '.join(outputs)}" if outputs else "")
                + f"\n（這個里程碑的 {len(moved)} 則訊息已移出上下文；需要細節時讀產出的檔案或黑板。）")
        a.history.append(Message("user", note))
        a._persist()
        self.ms_start = len(a.history)
        self.sealed += 1

    async def after_step(self, step: int, stats: dict) -> None:
        done = self.agent.ctx.state.pop("milestone_done", None)
        if done:
            mid, summary, outputs, st = done
            m = self.plan.get(mid)
            self._seal(m, summary, outputs, status=st)
            self.control(stats, "milestone_done" if st == "done" else "milestone_dropped",
                         f"{mid} {m['title']} {'完成' if st == 'done' else '略過'}（第 {self.step0}–{step} 步）；"
                         f"上下文已清空，帶到下一個里程碑的只有摘要、產出與共享事實")
            pending = [f for f in self.plan.data.get("feedback", []) if f["mode"] == "boundary" and not f.get("applied")]
            if pending:
                for f in pending:
                    f["applied"] = True
                await self.replan(stats, "使用者在執行中修正計畫（在里程碑交界套用）",
                                  "\n".join(f["text"] for f in pending))
            self._start_next(stats)
            self.save()
        elif await self._collect(stats):
            pass

    async def replan(self, stats: dict, why: str, feedback: str = "") -> None:
        from . import thinking
        lines = []
        for m in self.plan.milestones:
            mark = {"done": "[x]", "delegated": "[~]", "doing": "[>]"}.get(m["status"], "[ ]")
            lines.append(f"{mark} {m['id']} {m['title']}: {m['goal']}" + (f" | summary: {m['summary'][:300]}"
                                                                          if m.get("summary") else ""))
        prompt = thinking.REPLAN.format(request=self.query[:4000], plan="\n".join(lines),
                                        facts="\n".join(f"- {k}: {v}" for k, v in self.facts().items()) or "(none)",
                                        why=why, feedback=f"USER'S CORRECTION:\n{feedback}\n" if feedback else "")
        resp = await self.agent._thinking_call("重新規劃", [Message("user", prompt)], 1500, stats)
        data = thinking.parse_json(resp.message.text()) if resp else {}
        if not data.get("milestones"):
            self.control(stats, "milestone_replanned", f"{why}：思維模型沒有給出新計畫，維持原計畫", decision="kept")
            return
        cancel = self.plan.apply_replan(data)
        if (getattr(self.agent, "_fitness", None) or {}).get("weak"):
            from .roles import concretize_milestones
            concretize_milestones([m for m in self.plan.milestones if m["status"] in ("todo", "doing")])
        for mid in cancel:
            t = self.tasks.pop(mid, None)
            if t:
                t.cancel()
        if isinstance(data.get("facts"), dict) and data["facts"]:
            M.write_facts(self.store, self.scope, data["facts"], "replan")
        cur = self.plan.current()
        if cur and cur["id"] not in {m["id"] for m in self.plan.milestones}:
            cur["status"] = "todo"
        self._checklist_sync()
        self.control(stats, "milestone_replanned", f"{why}：{data.get('note') or '計畫已更新'}"
                     + (f"；停止子代理 {', '.join(cancel)}" if cancel else ""), decision="replanned")
        self.save()

    def _checklist_sync(self) -> None:
        """Drop the open acceptance items of milestones the re-plan removed; add the new ones'."""
        from ..tools import checklist_tools
        items = checklist_tools.get(self.agent.ctx)
        live = {m["id"] for m in self.plan.milestones}
        for i in items:
            if i.get("milestone") and i["milestone"] not in live and i["status"] in ("todo", "doing"):
                i["status"] = "dropped"
        have = {i.get("milestone") for i in items}
        for m in self.plan.milestones:
            if m["id"] not in have and m["status"] in ("todo", "doing", "delegated"):
                for text in m["acceptance"]:
                    if len(items) < checklist_tools.MAX_ITEMS:
                        items.append({"id": len(items) + 1, "status": "todo", "evidence": "", "updated": time.time(),
                                      "text": f"[{m['id']}] {text}", "check": _file_check(m), "milestone": m["id"]})
        checklist_tools.save(self.agent.ctx)

    async def on_final(self, stats: dict) -> str | None:
        """The model gave a final answer. Returns a note sending it back, or None to let it finish."""
        if self.tasks:
            await self._collect(stats, wait=True)
            nxt = self.plan.current() or self.plan.next_sequential()
            if nxt:
                return f"[里程碑] 子代理的里程碑已完成，請繼續 {nxt['id']} {nxt['title']}。"
        cur = self.plan.current()
        # the answer itself may close the current milestone when it is the last one open (the integration)
        open_ms = [m for m in self.plan.milestones if m["status"] in ("todo", "doing") and m is not cur]
        if open_ms and stats.get("ms_sendbacks", 0) < 2:
            stats["ms_sendbacks"] = stats.get("ms_sendbacks", 0) + 1
            return ("[里程碑] 還有里程碑沒有完成：" + "、".join(f"{m['id']} {m['title']}" for m in open_ms)
                    + "。完成目前的里程碑並呼叫 milestone(action='done')；若某個里程碑已不需要，說明理由後用 "
                      "milestone(action='drop', id=..., summary=理由)。")
        if open_ms:                   # sent back twice and still open: the turn ends incomplete, the plan stays
            stats["ms_incomplete"] = [f"{m['id']} {m['title']}" for m in open_ms + ([cur] if cur else [])]
            self.save("incomplete")
            return None
        if cur:                       # the answer itself closes the last milestone
            cur["status"] = "done"
            cur["summary"] = cur.get("summary") or "最後的回答"
        self.save("done" if self.plan.finished() else "running")
        return None

    def tool_drop(self, mid: str, reason: str) -> str:
        m = self.plan.get(mid)
        if not m or m["status"] in ("done", "dropped"):
            return f"ERROR: no open milestone {mid}"
        if not str(reason or "").strip():
            return "ERROR: say why the milestone is no longer needed"
        if m["status"] == "delegated" and mid in self.tasks:
            self.tasks.pop(mid).cancel()
        was_current = m["status"] == "doing"
        if not was_current:
            m["status"] = "dropped"
            m["summary"] = f"略過：{reason}"[:600]
        from ..tools import checklist_tools
        for i in checklist_tools.get(self.agent.ctx):
            if i.get("milestone") == mid and i["status"] in ("todo", "doing"):
                i["status"] = "dropped"
        checklist_tools.save(self.agent.ctx)
        if was_current:                # closed after this step like a finished one (its context is cleared too)
            self.agent.ctx.state["milestone_done"] = (mid, f"略過：{reason}"[:600], [], "dropped")
        self.save()
        return f"Milestone {mid} dropped."

    def end(self, status: str) -> None:
        for t in self.tasks.values():
            t.cancel()
        self.tasks.clear()
        self.save("done" if self.plan.finished() else status)


def _file_check(m: dict) -> str:
    """The checklist's "check" for a milestone whose outputs have files (roles.concretize_milestones)."""
    files = m.get("output_files") or []
    return f"write the result with write_file to {' / '.join(files)} and give that path as the evidence" if files else ""
