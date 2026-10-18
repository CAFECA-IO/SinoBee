"""Post-task reflection: turn experience into memories and Agent Skills.

Adaptive skill generation
  Passive (user-initiated)
    - the user says "remember how to do this / make it a skill"      -> skill_request signal
    - `/learn <url | path | description>`                            -> handled by the agent itself (agent/loop.py)
    - Web UI "learn a skill from this conversation" button            -> learn_from_session()
  Active (agent-initiated)
    - gap:        a non-trivial task (many tool calls / recovered errors) with no matching skill
    - repetition: the same kind of task appears in >= 2 past episodes  -> strongly prefer creating a skill
    - failure:    a used skill failed or the user corrected the agent  -> patch (or fork + patch if read-only)
  Lifecycle: skills created here are trust=agent, status=experimental (candidate); they are promoted to active
  after `promote_after` successful uses or user approval, and flagged NEEDS REVISION when they keep failing.
  skills.auto_create: auto (create directly) | suggest (stage for user approval in the Web UI) | off.

Signals gate the reflection (no fixed-interval nudges); the auxiliary model reads a compact digest and returns JSON
that is applied through the same validated APIs the agent uses.
"""
from __future__ import annotations

import json
import logging
import re

from ..types import Message

log = logging.getLogger("sinobee.learning")

def _reflect_tier(mem: dict) -> str:
    """Reflection may write short, long or core memories, never permanent ones. Task facts default to short."""
    tier = mem.get("tier")
    if tier in ("short", "long", "core"):
        return tier
    stable = mem.get("kind") in ("preference", "profile", "lesson") and float(mem.get("importance", 0.5)) >= 0.8
    return "long" if stable else "short"


REFLECT_PROMPT = """You are the learning module of an autonomous computer-use agent. Review the task transcript and
decide what is worth keeping. Be selective: most turns need few or no memories and no new skill.

Return ONLY a JSON object (omit keys or leave them empty when nothing applies):
{
  "memories": [ {"content": "...", "kind": "preference|profile|fact|environment|lesson",
                 "tier": "short|long|core", "importance": 0.0-1.0, "tags": ["..."]} ],
  "memory_updates": [ {"id": "mxxxx", "content": "corrected statement"} ],
  "skill": {"action": "none|create|patch|fork_patch", "name": "lowercase-hyphen-name", "scope": "global|project",
            "reason": "why (gap / repetition / failure / user request)",
            "content": "full SKILL.md (create)",
            "old_string": "exact text copied from the skill (patch)", "new_string": "replacement (patch)"},
  "skill_feedback": [ {"name": "...", "success": true, "note": "what worked or failed"} ],
  "episode_summary": "2-4 sentences: what the user wanted, what was done, outcome"
}

Memory rules: durable facts about the user, preferences and corrections (importance >= 0.8; tier core only if it
applies to nearly every future task), environment facts (paths, accounts, versions), lessons from failures. One
self-contained statement each. Choose the tier by how long it stays true:
- short: facts about THIS task or project that may matter in the next days (file locations, decisions, status);
  they expire automatically unless they are used again.
- long: stable facts, preferences, profile and lessons that stay true for months.
- core: only standing rules that apply to nearly every task (keep it tiny).
Never create permanent memories yourself; only the user decides that. Never store secrets, passwords, tokens or one-off details. Contradicted memories go
in memory_updates.

Skill rules (Agent Skills specification, agentskills.io):
- create only if the procedure is non-trivial AND reusable AND no existing skill covers it. Create when the user
  asked to remember/learn it, or when SIMILAR PAST TASKS >= 2, or when it took many steps / error recovery.
- patch an existing editable skill when a step was wrong or missing; for read-only skills (trust builtin, verified,
  community) use "fork_patch" (a copy named <name>-custom is made, then patched).
- name: lowercase a-z, 0-9, single hyphens, <= 64 chars, describes the capability.
- content format - ONLY these top-level frontmatter fields: name, description, license, compatibility,
  allowed-tools, metadata. metadata values are strings. No flow style ([a, b]). Example:
  ---
  name: einvoice-download-tw
  description: Downloads Taiwan e-invoices from the MOF portal as CSV. Use when the user asks for 電子發票, invoice
    exports or tax filing data.
  metadata:
    version: "1.0.0"
    sinobee-tags: invoice, tax, browser
    sinobee-triggers: 電子發票, 發票, e-invoice
    sinobee-requires-tools: browser
  ---
  # When to Use
  # Procedure        (numbered, concrete: URLs, selectors, commands; one default, not a menu)
  # Gotchas          (what failed and the fix)
  # Verification
  Ground every step in the transcript (what actually worked, the user's corrections). Write general rules, not an
  incident log. Keep it under ~150 lines.
- scope "project" only when the procedure is specific to the current project/repository.
- skill_feedback: judge every skill listed under SKILLS USED.
"""


def digest(turn: list[Message], limit: int = 24000) -> str:
    lines = []
    for m in turn:
        if m.role == "user":
            lines.append(f"USER: {m.text()[-3000:]}")
        elif m.role == "assistant":
            if m.text():
                lines.append(f"AGENT: {m.text()[:1500]}")
            for t in m.tool_calls:
                lines.append(f"  CALL {t.name} {json.dumps(t.arguments, ensure_ascii=False)[:400]}")
        elif m.role == "tool":
            text = m.text()
            tag = "ERROR" if text.startswith("ERROR") else "result"
            lines.append(f"  {tag} ({m.name}): {text[:600]}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit // 3] + "\n...[middle omitted]...\n" + text[-2 * limit // 3:]
    return text


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e != -1:
            text = text[s:e + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


async def reflect(rt, session_id: str, turn: list[Message], stats: dict, unrated_skills: list[str],
                  emit=None, force_skill: bool = False, extra: str = "") -> dict:
    from ..accounting import scope
    with scope(session_id, "reflection") as sc:
        report = await _reflect(rt, session_id, turn, stats, unrated_skills, force_skill, extra)
        report["usage"] = sc.as_dict()
    if emit:
        emit("learned", report)
    return report


async def learn_from_session(rt, session_id: str, emit=None) -> dict:
    """Passive generation from the Web UI: distil a whole past session into a skill."""
    turn = rt.memory.load_messages(session_id)
    if not turn:
        return {"errors": ["session not found or empty"]}
    stats = {"skill_request": True, "tool_calls": sum(len(m.tool_calls) for m in turn)}
    return await reflect(rt, session_id, turn, stats, [], emit=emit, force_skill=True)


def _mode(rt) -> str:
    v = rt.config.get("skills.auto_create", "auto")
    if v is True:
        return "auto"
    if v is False:
        return "off"
    return str(v).lower()


async def _reflect(rt, session_id: str, turn: list[Message], stats: dict, unrated_skills: list[str],
                   force_skill: bool, extra: str = "") -> dict:
    report: dict = {"memories": [], "updates": [], "skill": None, "feedback": [], "errors": [], "signals": {}}
    mode = _mode(rt)
    try:
        user_text = " ".join(m.text() for m in turn if m.role == "user")[-2000:]
        core = rt.memory.render_core() or "(empty)"
        related = await rt.memory.asearch(user_text, k=8, tiers=("short", "long"), touch=False)
        related_txt = "\n".join(f"[{m.id}] {m.content}" for m in related if m.kind != "episode") or "(none)"
        # repetition detection: similar past episodes (other sessions)
        episodes = [m for m in rt.memory.search(user_text, k=8, tiers=("long",), kinds=["episode"], touch=False,
                                                min_score=0.35) if m.source != session_id]
        report["signals"]["similar_past_tasks"] = len(episodes)
        skill_hits = rt.skills.search(user_text, 5)
        report["signals"]["skill_gap"] = not skill_hits or skill_hits[0][1] < 1.0
        skills_txt = "\n".join(f"{rt.skills._line(s)} [trust={s.trust}, editable={s.policy.agent_edit}]"
                               for s, _ in skill_hits) or "(none)"
        used_txt = ""
        for name in unrated_skills:
            try:
                used_txt += f"\n--- {name} ---\n" + rt.skills.view(name, record=False)[:6000]
            except Exception:
                pass
        signals = [k for k in ("correction", "recovered", "skill_request", "skill_gap") if stats.get(k)]
        if stats.get("final_empty"):
            instructions_outcome = ("OUTCOME: the turn FAILED because the model returned no answer / errored. That is not "
                                    "evidence about the skills: leave skill_feedback empty and do not create or patch "
                                    "skills from this transcript.")
        else:
            instructions_outcome = ""
        if stats.get("tool_calls"):
            signals.append(f"tool_calls={stats['tool_calls']}")
        if episodes:
            signals.append(f"repetition={len(episodes)}")
        instructions = ""
        if mode == "off":
            instructions = "Skill creation is DISABLED: set skill.action to none (patching/feedback still allowed)."
        elif force_skill or stats.get("skill_request"):
            instructions = ("The USER ASKED to turn this into a skill: create one (or patch the overlapping skill) "
                            "unless the transcript contains no reusable procedure.")
        if stats.get("created_this_turn"):
            instructions += f" The agent already created {stats['created_this_turn']} this turn: do not duplicate."
        prompt = (f"LEARNING SIGNALS: {', '.join(signals) or 'none'}\n{instructions}\n{instructions_outcome}\n{extra}\n\n"
                  f"PROJECT: {rt.skills.project_dir} (trusted: "
                  f"{bool(rt.skills.project_dir) and rt.skills.projects.is_trusted(rt.skills.project_dir)})\n\n"
                  f"CORE MEMORY:\n{core}\n\nRELATED LONG-TERM MEMORIES:\n{related_txt}\n\n"
                  f"SIMILAR PAST TASKS ({len(episodes)}):\n" + "\n".join(f"- {e.content[:300]}" for e in episodes[:5])
                  + f"\n\nEXISTING RELATED SKILLS:\n{skills_txt}\n\nSKILLS USED (need feedback):{used_txt or ' (none)'}"
                  f"\n\nTRANSCRIPT:\n{digest(turn)}")
        resp = await rt.aux.chat([Message("system", REFLECT_PROMPT), Message("user", prompt)],
                                 temperature=0.2, max_tokens=4000)
        data = parse_json(resp.message.text())
    except Exception as e:
        log.exception("reflection failed")
        report["errors"].append(str(e))
        return report

    for mem in (data.get("memories") or [])[:6]:
        try:
            if rt.config.get("memory.write_approval"):
                rt.memory.stage("memory_save", mem)
                continue
            res = await rt.memory.asave(mem["content"], kind=mem.get("kind", "fact"), tier=_reflect_tier(mem),
                                        tags=mem.get("tags"), importance=float(mem.get("importance", 0.5)),
                                        source=session_id)
            report["memories"].append(res)
        except Exception as e:
            report["errors"].append(f"memory: {e}")
    for upd in (data.get("memory_updates") or [])[:4]:
        try:
            cur = rt.memory.get(upd["id"])
            if cur and cur.pinned:      # permanent memories change only on the user's instruction
                report["errors"].append(f"memory: {upd['id']} is permanent, not changed by reflection")
                continue
            report["updates"].append(await rt.memory.aupdate(upd["id"], upd.get("content")))
        except Exception as e:
            report["errors"].append(f"update: {e}")

    report["skill"] = _apply_skill(rt, data.get("skill") or {}, mode, report)
    for fb in (data.get("skill_feedback") or []):
        try:
            if fb.get("name") in rt.skills.skills:
                report["feedback"].append({"name": fb["name"],
                                           **rt.skills.feedback(fb["name"], bool(fb.get("success")), fb.get("note", ""))})
        except Exception as e:
            report["errors"].append(f"feedback: {e}")
    summary = (data.get("episode_summary") or "").strip()
    if summary:
        prev = next((s.get("summary") for s in rt.memory.sessions(500) if s["id"] == session_id), None)
        rt.memory.set_summary(session_id, (prev + "\n" if prev else "") + summary)
        try:
            await rt.memory.asave(summary, kind="episode", tier="long", importance=0.3, source=session_id,
                                  tags=["episode"])
        except Exception:
            pass
    report["consolidation"] = rt.memory.consolidate(
        archive_after_days=float(rt.config.get("memory.archive_after_days", 90)))
    log.info("reflection: %s", {k: len(v) if isinstance(v, list) else v for k, v in report.items()})
    return report


def _apply_skill(rt, sk: dict, mode: str, report: dict) -> dict | None:
    action = (sk.get("action") or "none").lower()
    name = (sk.get("name") or "").strip()
    if action == "none" or not name:
        return None
    mgr = rt.skills
    scope = sk.get("scope") if sk.get("scope") in ("global", "project") else "global"
    try:
        if action == "create":
            if mode == "off":
                return None
            if scope == "project" and not (mgr.project_dir and mgr.projects.is_trusted(mgr.project_dir)):
                scope = "global"
            res = mgr.create(name, sk.get("content") or "", scope=scope, actor="agent")
            return {**res, "reason": sk.get("reason", "")}
        if action in ("patch", "fork_patch"):
            target = mgr.get(name)
            if action == "fork_patch" or not target.policy.agent_edit:
                fork_name = f"{name}-custom"
                if fork_name not in mgr.skills:
                    mgr.fork(name, fork_name, actor="agent")
                name = fork_name
            res = mgr.patch(name, sk.get("old_string") or "", sk.get("new_string") or "", actor="agent")
            return {**res, "reason": sk.get("reason", "")}
    except Exception as e:
        report["errors"].append(f"skill: {e}")
    return None


FEEDBACK_TAGS = {"wrong": "答案不正確", "incomplete": "沒有完成", "slow": "太慢", "expensive": "太貴",
                 "format": "格式不好", "misunderstood": "誤解需求", "unsafe": "做了不該做的事", "great": "很好"}


async def learn_from_feedback(rt, feedback_id: int, turn: dict, rating: int, tags: list[str], comment: str,
                              emit=None, rate_skills: bool = True) -> dict:
    """The user's explicit rating is ground truth: update skill outcomes, then let the reflector turn the
    feedback into lessons (memories), skill fixes or - for praised multi-step work - a new skill."""
    report: dict = {"feedback_id": feedback_id, "skills_rated": []}
    positive = rating > 0
    tag_txt = ", ".join(FEEDBACK_TAGS.get(t, t) for t in tags)
    note = f"user {'👍' if positive else '👎'}" + (f" [{tag_txt}]" if tag_txt else "") + (f" {comment}" if comment else "")
    for name in (turn.get("skills") or []) if rate_skills else []:
        if name in rt.skills.skills:
            try:
                rt.skills.feedback(name, positive, note)
                report["skills_rated"].append(name)
            except Exception as e:
                report.setdefault("errors", []).append(str(e))
    if rt.config.get("learning.enabled", True) and (not positive or comment or turn.get("tool_calls", 0) >= 3):
        messages = rt.memory.turn_messages(turn)
        extra = (f"USER FEEDBACK ON THIS TURN (ground truth): {'POSITIVE 👍' if positive else 'NEGATIVE 👎'}"
                 f"{'; reasons: ' + tag_txt if tag_txt else ''}{'; comment: ' + comment if comment else ''}.\n")
        if positive:
            extra += ("The user liked the result. If the procedure was multi-step and reusable and no existing skill "
                      "covers it, create a skill from it. Save any preference the comment reveals. "
                      "Leave skill_feedback empty (already recorded).")
        else:
            extra += ("The user was NOT satisfied. Save the lesson as a memory (kind=lesson or preference, importance "
                      ">= 0.8, tier core when it is a general preference about how to answer, e.g. format, language, "
                      "level of detail). If a skill that was used caused the problem, patch the wrong step "
                      "(fork_patch for read-only skills). Never create a new skill from a disliked result. Leave "
                      "skill_feedback empty (already recorded).")
        stats = {"skill_request": False, "tool_calls": turn.get("tool_calls", 0), "status": "done"}
        rep = await reflect(rt, turn["session_id"], messages, stats, [], emit=None, extra=extra)
        report.update({k: rep.get(k) for k in ("memories", "updates", "skill", "errors", "usage")})
        if not positive:
            # a bug the user reported once becomes a check that runs on every later task (and a regression eval)
            try:
                from ..agent.rules import propose_from_feedback
                prop = await propose_from_feedback(rt, turn, messages, rating, comment, tags)
            except Exception as e:  # noqa: BLE001
                prop = {"rule_rejected": f"proposal failed: {e}"}
            if prop.get("rule"):
                report["rule"] = prop["rule"]
                if emit:
                    emit("rule_added", {"rule": prop["rule"], "session_id": turn["session_id"]})
            if prop.get("eval"):
                report["eval"] = prop["eval"]["id"]
    rt.memory.mark_feedback_learned(feedback_id, report)
    if emit:
        emit("feedback_learned", {**report, "session_id": turn["session_id"], "turn_id": turn["id"]})
    return report
