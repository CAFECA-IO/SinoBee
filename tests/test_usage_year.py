"""The year heatmap in Settings > SinoBee.ai account: tokens per day (all models, and the subscription's share) and
the days that reached the 6-hour limit."""
import time

from sinobee.agent import Runtime
from sinobee.types import Usage

from conftest import ScriptedProvider


def test_tokens_per_day_and_the_days_at_the_limit(home, config):
    rt = Runtime(config, provider=ScriptedProvider())
    m = rt.memory
    now = time.time()
    m.record_usage(session_id="s", purpose="agent", provider="sinobee", model="x", input_tokens=1000, output_tokens=200, ts=now)
    m.record_usage(session_id="s", purpose="agent", provider="gemini", model="y", input_tokens=500, output_tokens=0, ts=now)
    m.record_usage(session_id="s", purpose="embedding", provider="ollama", model="e", input_tokens=9999, output_tokens=0, ts=now)
    m.record_usage(session_id="s", purpose="agent", provider="sinobee", model="x", input_tokens=10, output_tokens=0,
                   ts=now - 400 * 86400)                                  # older than a year: left out
    m.mark_quota(40, ts=now)
    m.mark_quota(97, ts=now)
    m.mark_quota(60, ts=now)                                             # the day keeps its highest share
    m.mark_quota(100, ts=now - 3 * 86400)                                 # a limit day without usage recorded
    rows = m.usage_year()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    t = next(r for r in rows if r["day"] == today)
    assert t["tokens"] == 1700 and t["sub_tokens"] == 1200 and t["max6h"] == 97
    assert len(rows) == 2 and rows[0]["tokens"] == 0 and rows[0]["max6h"] == 100

    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    with TestClient(create_app(rt)) as c:
        y = c.get("/api/usage/year").json()
    assert y["total"] == 1700 and y["sub_total"] == 1200 and y["active_days"] == 1 and y["cap_days"] == 2
    assert y["peak"] == {"day": today, "tokens": 1700}


def test_a_subscription_answer_marks_the_day(home, config):
    from sinobee.accounting import scope
    rt = Runtime(config, provider=ScriptedProvider())

    class P:
        provider_name, model = "sinobee", "anthropic/claude-sonnet-5-5"
    with scope("s", "agent") as sc:
        sc.quota = {"6h": 88.5, "168h": 9}
        rt._record(P(), Usage(100, 10))
    assert rt.memory.usage_year()[-1]["max6h"] == 88.5
