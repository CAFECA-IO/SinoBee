"""The turn's timeline: what the AI asked itself and answered, what it did and what came of it (proposal R, 2026-10-22).

A large task used to fill the conversation with separate cards (plan, consults, budget checks, checks, interim notes,
tool rows). Here the same events are folded into one ordered list that the web UI shows inside a single bubble -
the process expanded while the task runs and collapsed under the result when it is done - and that is saved with the
run, so a reopened conversation shows exactly what was shown live.

Items (each has id, kind, phase, t):
  phase     a milestone: title, status
  think     a question and its answer, with who answered (思維模型 / 執行模型 / 系統檢查 / 審查模型 / 你)
  work      consecutive tool calls: title, result, tools [{id, name, brief, ok}], files, status running|ok|err
  check     a check and its outcome (成果檢查, 視覺檢查, 驗收審查, 預算檢查點 ...), ok true|false|None
  steer     an instruction the user added while the task ran

The execution model writes "問題：…" at the start of a piece of work and "答：…" once it knows (agent/prompt.py);
without them a piece of work is shown with its tools' result only. A turn is shown in full ("mode": "full") when it
was planned, runs milestones or uses more than SMALL_STEPS tools; smaller turns keep the simple presentation.
"""
from __future__ import annotations

import json
import re
import time

SMALL_STEPS = 5
WORK_SPLIT = 5           # a piece of work without its own question is split after this many tools
MAX_ITEMS = 400
MAX_TOOLS_PER_WORK = 40
Q_RE = re.compile(r"^\s*(?:\*\*)?(?:問題|Question)\s*[:：]\s*(?:\*\*)?\s*(.+)$", re.I)
A_RE = re.compile(r"^\s*(?:\*\*)?(?:答|答案|Answer)\s*[:：]\s*(?:\*\*)?\s*(.+)$", re.I)
QUIET_TOOLS = {"task_checklist", "milestone", "ask_user", "working_memory"}
VERB = {"shell": ("執行", "個指令"), "read_file": ("讀取", "個檔案"), "write_file": ("寫入", "個檔案"),
        "edit_file": ("修改", "個檔案"), "list_dir": ("查看", "個資料夾"), "search_files": ("搜尋檔案", "次"),
        "web_search": ("搜尋網路", "次"), "web_fetch": ("讀取", "個網頁"), "browser": ("操作瀏覽器", "次"),
        "screenshot": ("截圖", "次"), "mouse": ("操作滑鼠", "次"), "keyboard": ("輸入", "次"),
        "skill_view": ("載入技能", "次"), "skill_run": ("執行技能", "次"), "delegate": ("派出子代理", "次")}
# a consult's trigger as the question the thinking model was asked
CONSULT_Q = {
    "production_gap": "驗收清單還沒完成，連續多步沒有新產出，接下來怎麼做？",
    "no_progress": "連續多步沒有進度，接下來怎麼做？",
    "exploration_stall": "一直在探索、沒有產出，該怎麼收斂？",
    "repeated_error": "同一個錯誤一再出現，要換什麼做法？",
    "consecutive_errors": "工具連續失敗，要換什麼做法？",
    "repeated_failed_call": "一再重複已失敗的操作，要換什麼做法？",
    "loop_repeat": "同一個操作重複且沒有進度，問題出在哪？",
    "loop_alternating": "兩組操作來回重複，問題出在哪？",
    "file_oscillation": "檔案反覆改回先前的版本，該採用哪一版？",
    "health_steps": "步數已經很多、驗收還沒通過，要不要調整做法？",
    "health_compaction": "上下文已經壓縮，目前真正的問題是什麼？",
    "budget_no_output": "預算已用八成仍沒有產出，接下來怎麼做？",
    "long_wait": "等待太久，要繼續等還是換做法？",
}
CHECK_STATUS = re.compile(r"完成檢查|成果檢查|驗收清單|驗收審查|獨立審查|視覺檢查")
CHECK_OUTCOME = re.compile(r"通過|未通過|無法執行|失敗|需要修正|發現|未完成|略過")   # a check's result, not its start


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _first_sentence(text: str, n: int = 80) -> str:
    text = re.sub(r"[`*_#>]+", "", text or "").strip()
    m = re.match(r"(.+?[。！？!?]|.+?(?:\. |$))", text.split("\n", 1)[0])
    return _clip((m.group(1) if m else text).rstrip("：:"), n)


def _brief(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    for k in ("command", "path", "url", "query", "name", "action"):
        if args.get(k):
            return _clip(args[k], 120)
    return _clip(", ".join(f"{k}={v}" for k, v in list(args.items())[:2]), 120)


def _result_line(preview: str) -> str:
    """The line of a tool's output that says how it went (the shell header is skipped; JSON is shown compact)."""
    text = str(preview or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("[exit"):
        lines = lines[1:] or lines
    body = "\n".join(lines)
    if body[:1] in "{[":
        try:
            return _clip(json.dumps(json.loads(body), ensure_ascii=False, separators=(", ", ": ")), 160)
        except ValueError:
            pass
    for ln in lines:                         # a bare brace or bracket says nothing
        if len(ln) > 2:
            return _clip(ln, 140)
    return _clip(lines[0] if lines else "", 140)


SECTION = re.compile(r"^\s*(?:\*\*)?[（(]([1-3])[)）]\s*(.*)$")


def advice_gist(advice: str) -> str:
    """A consult's answer in a line: what is going wrong, then the first next step (CONSULT asks for (1) (2) (3))."""
    found: dict[str, str] = {}
    sec = None
    for raw in str(advice or "").splitlines():
        ln = raw.strip().strip("*").strip()
        if not ln:
            continue
        m = SECTION.match(ln)
        if m:
            sec = m.group(1)
            head, _, after = m.group(2).partition("：") if "：" in m.group(2) else m.group(2).partition(":")
            ln = after.strip() if after.strip() else ("" if len(head) <= 16 else head)
            if not ln:
                continue
        ln = re.sub(r"^[-•*\d.)\s]+", "", ln)
        if sec in ("1", "2") and sec not in found and len(ln) > 4:
            found[sec] = ln.rstrip("。.；;")
    if not found:
        return _first_sentence(advice, 300)
    return _clip("；".join(x for x in (found.get("1"), ("下一步：" + found["2"]) if found.get("2") else None) if x), 300)


def work_title(counts: dict) -> str:
    parts = []
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        v = VERB.get(name)
        parts.append(f"{v[0]} {n} {v[1]}" if v else f"{name} ×{n}")
    return "、".join(parts[:3]) or "處理中"


class Timeline:
    def __init__(self, emit=None, workspace: str | None = None):
        self.emit = emit
        self.workspace = (str(workspace).rstrip("/") + "/") if workspace else None
        self.items: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.phase: str | None = None
        self.work: dict | None = None            # the open piece of work
        self.question: dict | None = None        # the execution model's open question
        self.consult: dict | None = None         # the consult waiting for its answer
        self.tools = 0
        self.planned = False
        self.milestones = False
        self.checklist = (0, 0)
        self.answer_drafts = 0
        self._n = 0

    # ------------------------------------------------------------------ state
    @property
    def mode(self) -> str:
        return "full" if (self.planned or self.milestones or self.tools > SMALL_STEPS) else "compact"

    def meta(self) -> dict:
        return {"mode": self.mode, "tools": self.tools, "checklist": list(self.checklist),
                "counts": {k: sum(1 for i in self.items if i["kind"] == k) for k in ("think", "work", "check")}}

    def snapshot(self) -> dict:
        return {"items": self.items[-MAX_ITEMS:], **self.meta()}

    def _new(self, kind: str, **fields) -> dict:
        self._n += 1
        it = {"id": f"i{self._n}", "kind": kind, "phase": self.phase, "t": round(time.time(), 2), **fields}
        self.items.append(it)
        self.by_id[it["id"]] = it
        self._push(it)
        return it

    def _push(self, it: dict) -> None:
        if self.emit:
            try:
                self.emit("timeline", {"item": it, **self.meta()})
            except Exception:  # noqa: BLE001 - presentation must never break a task
                pass

    def _close_work(self) -> None:
        w, self.work = self.work, None
        if w and w["status"] == "running":
            w["status"] = "err" if w["tools"] and not any(t.get("ok") for t in w["tools"]) else "ok"
            self._push(w)

    def _answer(self, item: dict | None, text: str, source: str | None = None) -> None:
        if item and not item.get("a") and text:
            item["a"] = _clip(text, 300)
            if source:
                item["source"] = source
            self._push(item)

    # ------------------------------------------------------------------ events
    def feed(self, kind: str, d: dict) -> None:
        try:
            fn = getattr(self, "_on_" + kind, None)
            if fn:
                fn(d or {})
        except Exception:  # noqa: BLE001
            pass

    def _on_plan(self, d: dict) -> None:
        self.planned = True
        a = d.get("understanding") or ""
        if d.get("approach"):
            a += ("；" if a else "") + str(d["approach"])
        steps = [str(s) for s in d.get("steps") or []]
        if steps:
            a += ("。步驟：" if a else "") + "；".join(steps[:6])
        self._new("think", q="這個任務要怎麼做？", a=_clip(a, 400), source="思維模型")

    def _on_milestones(self, d: dict) -> None:
        self.milestones = True
        for m in d.get("milestones") or []:
            pid = f"ms-{m.get('id')}"
            it = self.by_id.get(pid)
            st = m.get("status") or "todo"
            if it is None and st in ("doing", "delegated", "done", "failed"):
                self._close_work()
                it = {"id": pid, "kind": "phase", "phase": pid, "t": round(time.time(), 2),
                      "title": f"{m.get('id')} {m.get('title') or ''}".strip(), "status": st}
                self.items.append(it)
                self.by_id[pid] = it
                self._push(it)
            elif it is not None and it.get("status") != st:
                it["status"] = st
                if m.get("summary"):
                    it["summary"] = _clip(m["summary"], 200)
                self._push(it)
            if st == "doing" and self.phase != pid:
                self._close_work()
                self.phase = pid

    def _on_checklist(self, d: dict) -> None:
        items = d.get("items") or []
        self.checklist = (sum(1 for i in items if i.get("status") == "done"), len(items))

    def _on_assistant(self, d: dict) -> None:
        text = str(d.get("text") or "")
        qs, ans, rest = [], None, []
        for ln in text.splitlines():
            if (m := Q_RE.match(ln)):
                qs.append(m.group(1).strip())
            elif (m := A_RE.match(ln)) and ans is None:
                ans = m.group(1).strip()
            else:
                rest.append(ln)
        rest_text = "\n".join(rest).strip()
        if self.question and ans:           # without a 答 line the question is shown with its work's result
            self._answer(self.question, ans)
        if d.get("final"):
            self._close_work()
            self.question = None
            self.answer_drafts += 1
            return
        note = _first_sentence(rest_text) if rest_text else ""
        w = self.work
        if w is not None and not qs and len(w["tools"]) < WORK_SPLIT:
            if note:                           # the same piece of work goes on: its notes are kept as its steps
                w.setdefault("notes", []).append(note)
                if w.get("auto"):
                    w["title"], w["auto"] = note, False
                self._push(w)
            return
        self._close_work()
        if qs:
            self.question = self._new("think", q=_clip(qs[0], 200), a="", source="執行模型")
        elif self.question and self.question.get("a"):
            self.question = None
        self.work = self._new("work", title=note, auto=not note, result="", tools=[], counts={}, files=[],
                              status="running", notes=[])

    def _on_tool_call(self, d: dict) -> None:
        name = d.get("name") or ""
        if name == "ask_user":
            a = d.get("arguments") or {}
            self._close_work()
            self._new("think", q=_clip(a.get("question") or "", 200), a="", source="你", ask=d.get("id"))
            return
        self.tools += 1
        if self.work is None:
            self.work = self._new("work", title="", auto=True, result="", tools=[], counts={}, files=[],
                                  status="running")
        w = self.work
        if len(w["tools"]) < MAX_TOOLS_PER_WORK:
            w["tools"].append({"id": d.get("id"), "name": name, "brief": _brief(name, d.get("arguments") or {})})
        if name not in QUIET_TOOLS:
            w["counts"][name] = w["counts"].get(name, 0) + 1
        if w.get("auto"):
            w["title"] = work_title(w["counts"]) if w["counts"] else \
                {"task_checklist": "更新驗收清單", "milestone": "更新里程碑"}.get(name, "處理中")
        self._push(w)

    def _find_tool(self, cid) -> tuple[dict | None, dict | None]:
        for it in reversed(self.items[-60:]):
            if it["kind"] == "work":
                for t in it["tools"]:
                    if t.get("id") == cid:
                        return it, t
            elif it["kind"] == "think" and it.get("ask") == cid:
                return it, None
        return None, None

    def _on_tool_result(self, d: dict) -> None:
        it, t = self._find_tool(d.get("id"))
        if it is None:
            return
        if t is None:              # the user's answer to ask_user
            ans = str(d.get("preview") or "")
            chose = re.search(r"The user chose: (.+?)(?:\. Their note: (.+))?$", ans, re.S)
            said = re.search(r"The user answered: (.+)$", ans, re.S)
            if chose:
                out = chose.group(1) + (f"（{chose.group(2)}）" if chose.group(2) else "")
            elif said:
                out = said.group(1)
            elif "did not answer" in ans or "No one can answer" in ans:
                out = "沒有回覆，採用建議的選項"
            else:
                out = "由 SinoBee 自行決定"
            self._answer(it, out.strip())
            return
        t["ok"] = not d.get("error")
        if d.get("image"):
            t["image"] = True
        if t["name"] not in QUIET_TOOLS:
            line = _result_line(d.get("preview") or "")
            if self.workspace:
                line = line.replace(self.workspace, "")
            it["result"] = ("失敗：" + line) if d.get("error") else line
        self._push(it)

    def _on_artifact(self, d: dict) -> None:
        it, _ = self._find_tool(d.get("id"))
        if it is not None and it["kind"] == "work":
            for f in d.get("files") or []:
                if f.get("path") not in [x.get("path") for x in it["files"]]:
                    it["files"].append({"path": f.get("path"), "name": f.get("name"), "kind": f.get("kind")})
            self._push(it)

    def _on_control(self, e: dict) -> None:
        ev, trig = e.get("event"), e.get("trigger") or ""
        detail = str(e.get("detail") or "")
        if ev == "consult_requested":
            self._close_work()
            q = CONSULT_Q.get(trig) or f"{e.get('label') or trig}，接下來怎麼做？"
            self.consult = self._new("think", q=q, a="", source="思維模型", trigger=trig)
        elif ev == "consult":
            ans = advice_gist(e["advice"]) if e.get("advice") else re.sub(r"^思維模型\s+\S+[:：]\s*", "", detail)
            target = self.consult if self.consult and not self.consult.get("a") else None
            if target is None:
                target = self._new("think", q=CONSULT_Q.get(trig) or f"{e.get('label') or trig}，接下來怎麼做？", a="",
                                   source="思維模型", trigger=trig)
            self._answer(target, ans, "思維模型")
            self.consult = None
        elif ev == "self_check":
            self._close_work()
            self._new("think", q="思維模型的諮詢次數已用完：目前進度如何、做法要不要調整？",
                      a="交由執行模型自我檢查並寫下接下來 3 步", source="系統檢查")
        elif ev in ("budget", "stop", "delivery", "health_check"):
            self._close_work()
            bad = e.get("decision") in ("at_risk", "stop", "failed") or ev == "stop"
            self._new("check", title=e.get("label") or trig, result=_clip(detail, 240),
                      ok=False if bad else (None if ev == "delivery" else True), source="系統檢查")
        elif ev == "milestone" and trig in ("milestone_replanned", "milestone_interrupted", "milestone_taken_over",
                                              "fact_conflict"):
            self._close_work()
            self._new("think", q=f"{e.get('label')}：為什麼？", a=_clip(detail, 300), source="思維模型")

    def _on_qa(self, d: dict) -> None:
        issues = d.get("issues") or []
        self._close_work()
        self._new("check", title="成果檢查", ok=not issues, source="系統檢查",
                  result=f"發現 {len(issues)} 個問題：" + "；".join(_clip(f"{i.get('name')}：{i.get('problem')}", 80)
                                                         for i in issues[:3]) if issues else "通過")

    def _on_visual(self, d: dict) -> None:
        issues = d.get("issues") or []
        self._close_work()
        self._new("check", title=f"視覺檢查（{d.get('shots', 0)} 張畫面）", ok=not issues, source="視覺模型",
                  result=("；".join(_clip(i.get("problem"), 90) for i in issues[:3])) if issues else "沒有阻擋交付的問題")

    def _on_review(self, d: dict) -> None:
        self._close_work()
        ok = d.get("verdict") != "revise"
        probs = d.get("problems") or []
        self._new("check", title="驗收審查", ok=ok, source="審查模型",
                  result=_clip(d.get("summary") or "", 160) + ("" if ok or not probs else
                                                                "；" + "；".join(_clip(p.get("what"), 60) for p in probs[:3])))

    def _on_status(self, d: dict) -> None:
        msg = re.sub(r"^[⚠︎ⓘ✓✕\s]+", "", str(d.get("message") or ""))
        if d.get("control") or not CHECK_STATUS.search(msg) or not CHECK_OUTCOME.search(msg):
            return
        self._close_work()
        title, _, rest = msg.partition("：")
        skipped = re.search(r"無法執行|略過", msg)
        ok = None if skipped else not re.search(r"未通過|失敗|需要修正|發現|未完成", msg)
        self._new("check", title=_clip(title, 40), result=_clip(rest, 200), ok=ok, source="系統檢查")

    def _on_steer_applied(self, d: dict) -> None:
        self._close_work()
        self._new("steer", text=_clip(d.get("text") or "", 300), source="你")

    def finish(self) -> None:
        self._close_work()


def meta_of(items: list[dict]) -> dict:
    """The meta of a saved timeline whose turn is still running (only its items are saved while it runs)."""
    tools = sum(len(i.get("tools") or []) for i in items if i.get("kind") == "work")
    planned = any(i.get("kind") == "think" and i.get("q") == "這個任務要怎麼做？" for i in items)
    full = planned or any(i.get("kind") == "phase" for i in items) or tools > SMALL_STEPS
    return {"mode": "full" if full else "compact", "tools": tools, "checklist": [0, 0],
            "counts": {k: sum(1 for i in items if i.get("kind") == k) for k in ("think", "work", "check")}}
