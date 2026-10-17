"""ask_user: when the task needs a decision or information only the user has, ask with suggested options."""
from __future__ import annotations

from .registry import ToolContext, tool

MAX_OPTIONS = 4


def _clean(options) -> list[dict]:
    out = []
    for o in options or []:
        if isinstance(o, str):
            o = {"label": o}
        if not isinstance(o, dict) or not str(o.get("label") or "").strip():
            continue
        out.append({"label": str(o["label"]).strip()[:80], "description": str(o.get("description") or "").strip()[:240],
                    "recommended": bool(o.get("recommended"))})
    if sum(o["recommended"] for o in out) > 1:          # one suggestion at most
        first = next(i for i, o in enumerate(out) if o["recommended"])
        for i, o in enumerate(out):
            o["recommended"] = i == first
    return out[:MAX_OPTIONS]


@tool("ask_user", "core",
      "Ask the user to decide or fill in something only they know, offering 2-4 concrete options as buttons "
      "(put the one you recommend first and mark it recommended). The user can also type their own answer. "
      "The task waits for the reply. Use it instead of asking in plain text; do not ask when a sensible default "
      "exists or the answer is in the conversation, files or memory.",
      {"properties": {
          "question": {"type": "string", "description": "one short question in the user's language"},
          "options": {"type": "array", "minItems": 2, "maxItems": MAX_OPTIONS, "items": {"type": "object", "properties": {
              "label": {"type": "string", "description": "the choice, 1-6 words"},
              "description": {"type": "string", "description": "what happens with this choice, the trade-off"},
              "recommended": {"type": "boolean"}}, "required": ["label"]}},
          "multi": {"type": "boolean", "description": "the user may pick several options"},
          "allow_text": {"type": "boolean", "description": "offer a box for their own answer (default true)"}},
       "required": ["question", "options"]})
async def ask_user(ctx: ToolContext, question: str, options: list | None = None, multi: bool = False,
                   allow_text: bool = True):
    opts = _clean(options)
    if len(opts) < 2:
        return "ERROR: give 2-4 options (label, description, one recommended)."
    ask = getattr(ctx, "ask", None)
    if ask is None:
        rec = next((o["label"] for o in opts if o["recommended"]), opts[0]["label"])
        return (f"No one can answer here (unattended run). Proceed with the recommended option ({rec}) and "
                "state that assumption in your answer.")
    ans = await ask(str(question).strip()[:300], opts, bool(multi), allow_text is not False)
    if not ans:
        rec = next((o["label"] for o in opts if o["recommended"]), opts[0]["label"])
        return (f"The user did not answer in time. Proceed with the recommended option ({rec}), say so, and let "
                "them change it later.")
    picked = [c for c in ans.get("choices") or [] if c]
    text = str(ans.get("text") or "").strip()
    parts = []
    if picked:
        parts.append("The user chose: " + "; ".join(picked))
    if text:
        parts.append(("Their note: " if picked else "The user answered: ") + text)
    return ". ".join(parts) or "The user dismissed the question: decide yourself and explain the choice."
