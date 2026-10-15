"""Fixed workflows as a graph: nodes with dependencies run in parallel when they can, conditions skip branches,
and every node's result is kept (on the run and on the blackboard) so an interrupted run resumes after the last
finished node.

Workflow file (YAML; bundled ones in sinobee/work/workflows, your own in $SINOBEE_HOME/workflows):

    name: research-report
    title: 研究報告
    description: ...
    inputs:
      topic: {type: text, label: 主題, required: true}
    nodes:
      - id: plan
        type: agent              # agent | map | action | approval
        role: researcher         # sub-agent role (see work/agents.py)
        prompt: "…{{inputs.topic}}…"
        output: json             # text (default) | json
      - id: research
        type: map                # one sub-agent per item, in parallel
        over: "{{nodes.plan.output.questions}}"
        role: researcher
        prompt: "研究：{{item}}"
        needs: [plan]
      - id: report
        type: action
        action: report.write
        with: {title: "{{inputs.topic}}", body: "{{nodes.write.output}}"}
        needs: [research]
        when: "nodes.review.output and contains(nodes.review.output, 'pass')"   # optional condition

Templates: {{inputs.x}}, {{nodes.<id>.output[.field]}}, {{item}} inside map, {{job.name}}, {{run.id}}.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import time
from pathlib import Path

import yaml

log = logging.getLogger("sinobee.work")
BUNDLED = Path(__file__).resolve().parent / "workflows"
NODE_TYPES = ("agent", "map", "action", "approval")


class WorkflowError(Exception):
    pass


# ------------------------------------------------------------------ definitions
def load_file(path: Path) -> dict:
    try:
        wf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise WorkflowError(f"{path.name}: {e}") from None
    return validate(wf, source=str(path))


def validate(wf: dict, source: str = "") -> dict:
    if not isinstance(wf, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", str(wf.get("name") or "")):
        raise WorkflowError(f"{source}: name 需為小寫英數與 -")
    nodes = wf.get("nodes") or []
    ids = set()
    for n in nodes:
        nid = str(n.get("id") or "")
        if not re.fullmatch(r"[a-z0-9_]{1,40}", nid) or nid in ids:
            raise WorkflowError(f"{wf['name']}: 節點 id「{nid}」無效或重複")
        if n.get("type") not in NODE_TYPES:
            raise WorkflowError(f"{wf['name']}.{nid}: type 需為 {', '.join(NODE_TYPES)}")
        ids.add(nid)
    for n in nodes:
        for d in n.get("needs") or []:
            if d not in ids:
                raise WorkflowError(f"{wf['name']}.{n['id']}: needs 指到不存在的節點 {d}")
    # cycle check
    seen, stack = set(), set()
    deps = {n["id"]: list(n.get("needs") or []) for n in nodes}

    def visit(x):
        if x in stack:
            raise WorkflowError(f"{wf['name']}: 節點相依形成循環（{x}）")
        if x in seen:
            return
        stack.add(x)
        for d in deps[x]:
            visit(d)
        stack.discard(x)
        seen.add(x)
    for x in deps:
        visit(x)
    wf.setdefault("title", wf["name"])
    wf.setdefault("description", "")
    wf.setdefault("inputs", {})
    wf["source"] = source
    return wf


def catalog(home: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in (BUNDLED, Path(home) / "workflows"):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.y*ml")):
            try:
                wf = load_file(p)
            except WorkflowError as e:
                log.warning("workflow %s skipped: %s", p, e)
                continue
            wf["bundled"] = d == BUNDLED
            out[wf["name"]] = wf
    return out


def levels(wf: dict) -> list[list[str]]:
    """Nodes grouped by depth (for display)."""
    deps = {n["id"]: list(n.get("needs") or []) for n in wf["nodes"]}
    depth: dict[str, int] = {}

    def d(x):
        if x not in depth:
            depth[x] = 0 if not deps[x] else 1 + max(d(y) for y in deps[x])
        return depth[x]
    for x in deps:
        d(x)
    out: list[list[str]] = []
    for x, k in sorted(depth.items(), key=lambda kv: kv[1]):
        while len(out) <= k:
            out.append([])
        out[k].append(x)
    return out


# ------------------------------------------------------------------ expressions and templates
_FUNCS = {
    "contains": lambda a, b: (str(b).lower() in str(a or "").lower()),
    "len": lambda a: len(a or []),
    "lower": lambda a: str(a or "").lower(),
    "json": lambda a: json.dumps(a, ensure_ascii=False, default=str),
}


def evaluate(expr: str, scope: dict):
    """A small, safe expression language: names, .attr / ['key'] / [i], literals, and/or/not, comparisons,
    `in`, and the functions contains(), len(), lower(), json()."""
    tree = ast.parse(expr.strip(), mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in ("true", "True"):
                return True
            if n.id in ("false", "False"):
                return False
            if n.id in ("none", "None", "null"):
                return None
            return scope.get(n.id)
        if isinstance(n, ast.Attribute):
            base = ev(n.value)
            return base.get(n.attr) if isinstance(base, dict) else None
        if isinstance(n, ast.Subscript):
            base, key = ev(n.value), ev(n.slice)
            try:
                return base[key]
            except (KeyError, IndexError, TypeError):
                return None
        if isinstance(n, ast.BoolOp):
            vals = n.values
            if isinstance(n.op, ast.And):
                r = True
                for v in vals:
                    r = ev(v)
                    if not r:
                        return r
                return r
            r = None
            for v in vals:
                r = ev(v)
                if r:
                    return r
            return r
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, right_node in zip(n.ops, n.comparators):
                right = ev(right_node)
                ok = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b,
                      ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
                      ast.In: lambda a, b: a in (b or []), ast.NotIn: lambda a, b: a not in (b or [])}.get(type(op))
                if ok is None:
                    raise WorkflowError(f"不支援的運算：{type(op).__name__}")
                try:
                    if not ok(left, right):
                        return False
                except TypeError:
                    return False
                left = right
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(x) for x in n.elts]
        raise WorkflowError(f"不支援的運算式：{ast.dump(n)[:80]}")
    return ev(tree)


_TPL = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.S)


def render(value, scope: dict):
    if isinstance(value, dict):
        return {k: render(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, scope) for v in value]
    if not isinstance(value, str):
        return value
    m = _TPL.fullmatch(value.strip())
    if m:                               # "{{ expr }}" alone keeps the value's type (list, dict, number...)
        return evaluate(m.group(1), scope)

    def sub(mm):
        v = evaluate(mm.group(1), scope)
        if v is None:
            return ""
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return _TPL.sub(sub, value)


# ------------------------------------------------------------------ execution
class WorkflowRun:
    def __init__(self, hub, run: dict, wf: dict, inputs: dict, job: dict | None = None):
        self.hub, self.run, self.wf, self.inputs, self.job = hub, run, wf, inputs, job
        self.state = run.get("state") or {}
        self.state.setdefault("nodes", {})
        self.state["workflow"] = wf["name"]
        self.state["levels"] = levels(wf)
        self.state["inputs"] = inputs
        for n in wf["nodes"]:
            st = self.state["nodes"].setdefault(n["id"], {"status": "pending"})
            st.setdefault("type", n["type"])
            st.setdefault("title", n.get("title") or n["id"])
            if st["status"] in ("running", "waiting"):     # interrupted: run it again
                st["status"] = "pending"
        self.board_scope = f"run:{run['id']}"

    def scope(self, extra: dict | None = None) -> dict:
        nodes = {k: {"output": v.get("output"), "status": v.get("status")} for k, v in self.state["nodes"].items()}
        return {"inputs": self.inputs, "nodes": nodes, "job": self.job or {}, "run": {"id": self.run["id"]},
                **(extra or {})}

    def save(self, activity: str | None = None) -> None:
        fields = {"state": self.state}
        if activity is not None:
            fields["activity"] = activity
        self.hub.store.update_run(self.run["id"], **fields)
        self.hub.notify("run", {"run_id": self.run["id"]})

    async def execute(self) -> str:
        nodes = {n["id"]: n for n in self.wf["nodes"]}
        while True:
            if self.hub.cancelled(self.run["id"]):
                raise asyncio.CancelledError()
            st = self.state["nodes"]
            pending = [i for i, s in st.items() if s["status"] == "pending"]
            if not pending:
                break
            ready = [i for i in pending if all(st[d]["status"] in ("done", "skipped") for d in nodes[i].get("needs") or [])]
            blocked = [i for i in pending if any(st[d]["status"] == "failed" for d in nodes[i].get("needs") or [])]
            for i in blocked:
                st[i].update(status="skipped", reason="前置節點失敗")
            # a skipped prerequisite skips the node too, unless it says allow_skipped (e.g. an optional revise step)
            skipped = [i for i in ready if not nodes[i].get("allow_skipped")
                       and any(st[d]["status"] == "skipped" for d in nodes[i].get("needs") or [])]
            for i in skipped:
                st[i].update(status="skipped", reason="前置節點略過")
            ready = [i for i in ready if i not in skipped and i not in blocked]
            if skipped and not ready:
                continue
            if not ready:
                if blocked:
                    continue
                raise WorkflowError("流程無法繼續（相依節點未完成）")
            await asyncio.gather(*(self._node(nodes[i]) for i in ready))
            self.save()
        failed = [i for i, s in self.state["nodes"].items() if s["status"] == "failed"]
        if failed:
            raise WorkflowError("節點失敗：" + "、".join(f"{i}（{self.state['nodes'][i].get('error', '')[:120]}）"
                                                       for i in failed))
        last = [n["id"] for n in self.wf["nodes"] if self.state["nodes"][n["id"]]["status"] == "done"]
        summary = self.state["nodes"][last[-1]].get("summary") if last else ""
        return summary or f"流程「{self.wf['title']}」完成"

    async def _node(self, n: dict) -> None:
        st = self.state["nodes"][n["id"]]
        scope = self.scope()
        if n.get("when"):
            try:
                if not evaluate(n["when"], scope):
                    st.update(status="skipped", reason=f"條件不成立：{n['when']}")
                    return
            except WorkflowError as e:
                st.update(status="failed", error=str(e))
                return
        st.update(status="running", started=time.time())
        self.save(f"{n.get('title') or n['id']}")
        try:
            out = await getattr(self, f"_{n['type']}")(n, scope, st)
            st.update(status="done", output=out, finished=time.time())
            if isinstance(out, str):
                st["summary"] = out[:500]
            elif isinstance(out, dict) and out.get("summary"):
                st["summary"] = str(out["summary"])[:500]
            self.hub.store.board_write(self.board_scope, "node_output", out, key=n["id"], author=f"node:{n['id']}")
        except asyncio.CancelledError:
            st.update(status="pending")
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("workflow node %s failed", n["id"])
            st.update(status="failed", error=str(e)[:800], finished=time.time())

    # ---------- node types ----------
    def _prompt(self, n: dict, scope: dict) -> str:
        text = render(n.get("prompt") or "", scope)
        if n.get("output") == "json":
            text += "\n\nReply with ONLY the JSON (no prose before or after)."
        return text

    async def _agent(self, n: dict, scope: dict, st: dict):
        from .agents import extract_json, run_subagent
        res = await run_subagent(self.hub.rt, role=n.get("role") or "general", task=self._prompt(n, scope),
                                 board_scope=self.board_scope, parent_session=self.run.get("session_id"),
                                 approve=self.hub.approver(self.run), emit=self.hub.run_emitter(self.run["id"]),
                                 hub=self.hub, title=f"[{self.wf['title']}] {n.get('title') or n['id']}",
                                 max_iterations=n.get("iterations"), owner=self.run["id"])
        st["sessions"] = [res["session_id"]]
        st["cost_usd"] = res.get("cost_usd", 0)
        if res["status"] not in ("done",):
            raise WorkflowError(f"子代理沒有完成（{res['status']}）：{res['result'][:200]}")
        if n.get("output") == "json":
            data = extract_json(res["result"])
            if data is None:
                raise WorkflowError("子代理沒有回傳 JSON")
            return data
        return res["result"]

    async def _map(self, n: dict, scope: dict, st: dict):
        from .agents import run_many
        items = render(n.get("over"), scope)
        if isinstance(items, dict):
            items = next((v for v in items.values() if isinstance(v, list)), [])
        if not isinstance(items, list) or not items:
            raise WorkflowError(f"map 沒有可處理的項目（over = {n.get('over')}）")
        items = items[: int(n.get("max_items") or 8)]
        tasks = [{"role": n.get("role") or "general", "task": render(n.get("prompt") or "{{item}}", {**scope, "item": it})}
                 for it in items]
        limit = int(self.hub.rt.config.get("work.max_parallel_subagents", 3))
        results = await run_many(self.hub.rt, tasks, board_scope=self.board_scope,
                                 parent_session=self.run.get("session_id"), approve=self.hub.approver(self.run),
                                 emit=self.hub.run_emitter(self.run["id"]), hub=self.hub, limit=limit,
                                 owner=self.run["id"])
        st["sessions"] = [r["session_id"] for r in results]
        st["cost_usd"] = sum(r.get("cost_usd") or 0 for r in results)
        ok = [r for r in results if r["status"] == "done"]
        if not ok:
            raise WorkflowError("所有子代理都沒有完成")
        return [{"item": it, "result": r["result"], "status": r["status"]} for it, r in zip(items, results)]

    async def _action(self, n: dict, scope: dict, st: dict):
        from .actions import ACTIONS, ActionContext
        fn = ACTIONS.get(n.get("action"))
        if fn is None:
            raise WorkflowError(f"未知的動作：{n.get('action')}")
        args = render(n.get("with") or {}, scope)
        ctx = ActionContext(self.hub, self.run, self.job, self.board_scope, st)
        return await fn(ctx, **args)

    async def _approval(self, n: dict, scope: dict, st: dict):
        message = render(n.get("message") or "繼續執行下一步？", scope)
        st["status"] = "waiting"
        self.save("等待你核准")
        ok = await self.hub.approver(self.run)(f"workflow:{self.wf['name']}.{n['id']}", {"message": message},
                                               message)
        st["status"] = "running"
        if not ok:
            raise WorkflowError("未獲核准")
        return {"approved": True, "summary": "已核准"}
