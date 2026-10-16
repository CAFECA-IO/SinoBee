"""Usage accounting context: which session / purpose a model call belongs to.

The agent sets a fresh UsageScope per turn; background reflection and compression set their own purpose.
Every model call made through a FallbackChain is recorded by Runtime (see agent/loop.py).
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class UsageScope:
    session_id: str | None = None
    purpose: str = "agent"          # agent | reflection | compression | test | embedding
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    unpriced: bool = False
    quota_6h_pct: float = 0.0                 # SinoBee.ai subscription: share of the 6-hour limit this scope used
    quota: dict | None = None                 # latest {"6h": used %, "168h": used %} reported by sinobee.ai
    quota_events: list = field(default_factory=list)  # quota stops / fallbacks to show in the GUI

    def as_dict(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens, "cost_usd": round(self.cost_usd, 6),
                "unpriced": self.unpriced,
                **({"quota_6h_pct": round(self.quota_6h_pct, 2), "quota": self.quota} if self.quota else {})}

    def drain_quota_events(self) -> list:
        out, self.quota_events = self.quota_events, []
        return out


current_scope: contextvars.ContextVar[UsageScope] = contextvars.ContextVar("sinobee_usage_scope",
                                                                           default=UsageScope())


@contextmanager
def scope(session_id: str | None, purpose: str):
    s = UsageScope(session_id, purpose)
    token = current_scope.set(s)
    try:
        yield s
    finally:
        current_scope.reset(token)
