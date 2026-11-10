"""Each doctor's hours: when they read the line, when clinic-started messages may go out, and reply-by times.

It reuses spec 004's clock (`backend.careline.settings`), so the line keeps the 9 Nov rule: patients can write at any
hour, clinical messages wait for the doctor's hours, and nothing is promised at 3 am. Nobody is on call unless the
doctor chooses to be.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.careline.settings import DAYS, Settings, fmt_when

from .config import HK_HOLIDAYS

DEFAULT_HOURS = {d: [["09:00", "18:00"]] for d in DAYS[:5]} | {"sat": [["09:00", "13:00"]], "sun": []}
REPLY_MINUTES = {"question": 240, "repeat": 240, "result": 480, "plan": 120, "named doctor": 0}


def clock(doctor: dict) -> Settings:
    raw = {
        "timezone": doctor.get("timezone", "Asia/Hong_Kong"),
        "hours": doctor.get("hours") or DEFAULT_HOURS,
        "holidays": sorted(set(HK_HOLIDAYS) | set(doctor.get("holidays", []))),
        "daytime": doctor.get("daytime") or ["09:00", "20:00"],
        "reply_minutes": {**REPLY_MINUTES, **doctor.get("reply_minutes", {})},
    }
    return Settings(raw)


def now_hk(tz: str = "Asia/Hong_Kong") -> datetime:
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None, second=0, microsecond=0)


def reply_by(doctor: dict, now: datetime, kind: str) -> datetime:
    s = clock(doctor)
    if kind not in s.raw["reply_minutes"]:
        kind = "question"
    return s.reply_by(now, kind)


def is_open(doctor: dict, now: datetime) -> bool:
    return clock(doctor).is_open(now)


def is_daytime(doctor: dict, now: datetime) -> bool:
    return clock(doctor).is_daytime(now)


def next_daytime(doctor: dict, now: datetime) -> datetime:
    """The next moment a clinic-started message may go out (HE-06)."""
    s = clock(doctor)
    if s.is_daytime(now):
        return now
    a, _ = s.raw["daytime"]
    h, m = (int(x) for x in a.split(":"))
    t = now.replace(hour=h, minute=m)
    return t if t > now else t + timedelta(days=1)


def hours_text(doctor: dict, lang: str) -> str:
    """'Mon–Fri 09:00–18:00, Sat 09:00–13:00' or its Chinese."""
    h = doctor.get("hours") or DEFAULT_HOURS
    zh = {"mon": "一", "tue": "二", "wed": "三", "thu": "四", "fri": "五", "sat": "六", "sun": "日"}
    groups: list[tuple[list[str], str]] = []
    for d in DAYS:
        s = ", ".join(f"{a}–{b}" for a, b in h.get(d, []))
        if not s:
            continue
        if groups and groups[-1][1] == s and DAYS.index(groups[-1][0][-1]) == DAYS.index(d) - 1:
            groups[-1][0].append(d)
        else:
            groups.append(([d], s))
    parts = []
    for days, s in groups:
        if lang == "zh":
            name = f"星期{zh[days[0]]}" + (f"至{zh[days[-1]]}" if len(days) > 1 else "")
        else:
            name = days[0].capitalize() + (f"–{days[-1].capitalize()}" if len(days) > 1 else "")
        parts.append(f"{name} {s}")
    return ("，" if lang == "zh" else ", ").join(parts)


__all__ = ["clock", "now_hk", "reply_by", "is_open", "is_daytime", "next_daytime", "hours_text", "fmt_when"]
