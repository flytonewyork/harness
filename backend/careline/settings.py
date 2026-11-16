"""Clinic settings and the clock: opening hours, holidays, who owns what, and reply-by times.

Every promise the care line makes about time comes from here (`false_promise` otherwise). Hours follow the
9 Nov decision: the care line keeps the clinic's opening hours (spec 003, HE-01).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT = Path(__file__).with_name("clinic_settings.json")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class Settings:
    raw: dict
    hours: dict = field(init=False)
    holidays: set = field(init=False)

    def __post_init__(self):
        self.hours = {d: [(_t(a), _t(b)) for a, b in self.raw["hours"][d]] for d in DAYS}
        self.holidays = {date.fromisoformat(d) for d in self.raw.get("holidays", [])}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        return cls(json.loads(Path(path or DEFAULT).read_text(encoding="utf-8")))

    def __getitem__(self, k):
        return self.raw[k]

    def now(self) -> datetime:
        """The clinic's wall clock (naive, in the clinic's time zone), whatever the server's own zone is."""
        return datetime.now(ZoneInfo(self.raw["timezone"])).replace(tzinfo=None, second=0, microsecond=0)

    def from_epoch(self, ts: int) -> datetime:
        return datetime.fromtimestamp(ts, ZoneInfo(self.raw["timezone"])).replace(tzinfo=None)

    def name(self, lang: str) -> str:
        return self.raw["name_zh" if lang == "zh" else "name_en"]

    def person(self, pid: str) -> dict:
        return {"id": pid, **self.raw["people"][pid]}

    # ---- hours -------------------------------------------------------------------------------------
    def sessions(self, d: date) -> list[tuple[datetime, datetime]]:
        if d in self.holidays:
            return []
        return [(datetime.combine(d, a), datetime.combine(d, b)) for a, b in self.hours[DAYS[d.weekday()]]]

    def is_open(self, now: datetime) -> bool:
        return any(a <= now < b for a, b in self.sessions(now.date()))

    def next_open(self, now: datetime) -> datetime:
        """The start of the next session, or `now` if the clinic is open."""
        if self.is_open(now):
            return now
        for i in range(0, 15):
            for a, _ in self.sessions(now.date() + timedelta(days=i)):
                if a > now:
                    return a
        raise ValueError("no opening in the next two weeks; check the settings")

    def is_daytime(self, now: datetime) -> bool:
        a, b = (_t(x) for x in self.raw["daytime"])
        return a <= now.time() < b

    def reply_by(self, now: datetime, owner_kind: str) -> datetime:
        """When a person must have replied. Red flags run from now; everything else counts only open minutes,
        so a message at 23:00 is due shortly after opening, never at 03:00 (nobody is on call)."""
        mins = self.raw["reply_minutes"][owner_kind]
        if owner_kind == "named doctor":
            return now + timedelta(minutes=mins)
        t, left = self.next_open(now), timedelta(minutes=mins)
        while True:
            end = next(b for a, b in self.sessions(t.date()) if a <= t < b)
            if t + left <= end:
                return t + left
            left -= end - t
            t = self.next_open(end)

    def hours_text(self, lang: str) -> str:
        zh = {"mon": "星期一", "tue": "星期二", "wed": "星期三", "thu": "星期四", "fri": "星期五", "sat": "星期六", "sun": "星期日"}
        rows = []
        for d in DAYS:
            s = self.raw["hours"][d]
            day = zh[d] if lang == "zh" else d.capitalize()
            rows.append(f"{day} " + (", ".join(f"{a}-{b}" for a, b in s) if s else ("休息" if lang == "zh" else "closed")))
        tail = "公眾假期休息" if lang == "zh" else "Closed on public holidays"
        return "\n".join(rows + [tail])


def fmt_when(t: datetime, now: datetime, lang: str) -> str:
    """A reply-by time as a patient reads it: 'today 15:30', 'tomorrow 09:45', 'Mon 13 Jun 09:15'."""
    hm = t.strftime("%H:%M")
    if t.date() == now.date():
        return f"今日 {hm}" if lang == "zh" else f"today {hm}"
    if t.date() == now.date() + timedelta(days=1):
        return f"聽日 {hm}" if lang == "zh" else f"tomorrow {hm}"
    if lang == "zh":
        return f"{t.month}月{t.day}日 {hm}"
    return t.strftime("%a %d %b ") + hm
