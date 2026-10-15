"""Schedules: manual, once, every N seconds, daily/weekly at a time, or a 5-field cron expression (local time).

    {"type": "manual"}
    {"type": "once", "at": 1760000000}
    {"type": "interval", "every": 3600}
    {"type": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]}     # days: Monday = 0 (omit for every day)
    {"type": "cron", "expr": "*/15 9-18 * * 1-5"}                    # cron day-of-week: Sunday = 0
"""
from __future__ import annotations

import datetime as dt
import re

MIN_INTERVAL = 60
WEEKDAYS = "一二三四五六日"


class ScheduleError(ValueError):
    pass


def _cron_field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ScheduleError("cron 間隔需大於 0")
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ScheduleError(f"cron 欄位超出範圍：{spec}")
        out.update(range(a, b + 1, step))
    return out


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    fields = expr.split()
    if len(fields) != 5:
        raise ScheduleError("cron 需要 5 個欄位：分 時 日 月 星期")
    try:
        mins = _cron_field(fields[0], 0, 59)
        hours = _cron_field(fields[1], 0, 23)
        dom = _cron_field(fields[2], 1, 31)
        mon = _cron_field(fields[3], 1, 12)
        dow = {d % 7 for d in _cron_field(fields[4], 0, 7)}
    except ValueError as e:
        raise ScheduleError(f"cron 格式錯誤：{e}") from None
    return mins, hours, dom, mon, dow, fields[2] == "*", fields[4] == "*"


def _cron_next(expr: str, after: dt.datetime) -> dt.datetime:
    mins, hours, dom, mon, dow, dom_any, dow_any = parse_cron(expr)
    t = (after + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(366 * 24 * 60):
        if t.month not in mon:
            t = (t.replace(day=1, hour=0, minute=0) + dt.timedelta(days=32)).replace(day=1)
            continue
        cron_dow = (t.weekday() + 1) % 7
        day_ok = (t.day in dom) if dow_any else (cron_dow in dow) if dom_any else (t.day in dom or cron_dow in dow)
        if not day_ok:
            t = (t + dt.timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in hours:
            t = (t + dt.timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute not in mins:
            t += dt.timedelta(minutes=1)
            continue
        return t
    raise ScheduleError("找不到下一次執行時間")


def validate(s: dict | None) -> dict:
    s = dict(s or {"type": "manual"})
    typ = s.get("type") or "manual"
    if typ == "manual":
        return {"type": "manual"}
    if typ == "once":
        at = s.get("at")
        if isinstance(at, str):
            try:
                at = dt.datetime.fromisoformat(at).timestamp()
            except ValueError:
                raise ScheduleError("時間格式需為 YYYY-MM-DDTHH:MM") from None
        if not isinstance(at, (int, float)):
            raise ScheduleError("請指定執行時間")
        return {"type": "once", "at": float(at)}
    if typ == "interval":
        every = int(s.get("every") or 0)
        if every < MIN_INTERVAL:
            raise ScheduleError(f"間隔至少 {MIN_INTERVAL} 秒")
        return {"type": "interval", "every": every}
    if typ in ("daily", "weekly"):
        tm = str(s.get("time") or "09:00")
        if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", tm):
            raise ScheduleError("時間格式需為 HH:MM")
        days = sorted({int(d) for d in (s.get("days") or []) if 0 <= int(d) <= 6})
        return {"type": "daily", "time": tm, **({"days": days} if days and len(days) < 7 else {})}
    if typ == "cron":
        expr = " ".join(str(s.get("expr") or "").split())
        parse_cron(expr)
        return {"type": "cron", "expr": expr}
    raise ScheduleError(f"不支援的排程類型：{typ}")


def next_run(s: dict, after: float, last_run: float | None = None) -> float | None:
    """Next run time (epoch seconds) strictly after `after`, or None when there is none."""
    typ = (s or {}).get("type", "manual")
    base = dt.datetime.fromtimestamp(after)
    if typ == "manual":
        return None
    if typ == "once":
        return s["at"] if (last_run is None) else None
    if typ == "interval":
        every = int(s["every"])
        if last_run is None:
            return after + 5
        nxt = last_run + every
        while nxt <= after:
            nxt += every
        return nxt
    if typ == "daily":
        h, m = (int(x) for x in s["time"].split(":"))
        days = s.get("days")
        cand = base.replace(hour=h, minute=m, second=0, microsecond=0)
        for i in range(8):
            c = cand + dt.timedelta(days=i)
            if c.timestamp() > after and (not days or c.weekday() in days):
                return c.timestamp()
        return None
    if typ == "cron":
        return _cron_next(s["expr"], base).timestamp()
    return None


def upcoming(s: dict, after: float, count: int = 5, last_run: float | None = None) -> list[float]:
    out, t, lr = [], after, last_run
    for _ in range(count):
        n = next_run(s, t, lr)
        if n is None:
            break
        out.append(n)
        t, lr = n, n
    return out


def describe(s: dict) -> str:
    typ = (s or {}).get("type", "manual")
    if typ == "manual":
        return "手動執行"
    if typ == "once":
        return "一次：" + dt.datetime.fromtimestamp(s["at"]).strftime("%Y-%m-%d %H:%M")
    if typ == "interval":
        e = int(s["every"])
        if e % 86400 == 0:
            return f"每 {e // 86400} 天"
        if e % 3600 == 0:
            return f"每 {e // 3600} 小時"
        return f"每 {e // 60} 分鐘"
    if typ == "daily":
        days = s.get("days")
        if not days:
            return f"每天 {s['time']}"
        if days == [0, 1, 2, 3, 4]:
            return f"平日 {s['time']}"
        return "每週" + "、".join(WEEKDAYS[d] for d in days) + f" {s['time']}"
    if typ == "cron":
        return f"cron：{s['expr']}"
    return typ
