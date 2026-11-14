"""A critical lab posted outside the three daily ticks pages the named doctor.

The engine's scheduled runs are 08:30, 13:30 and 19:30. A critical result can arrive at any hour. This wake
runs only then. It lets the rules engine open the item at urgency 4 for the doctor named on the report,
which is what pages that doctor. It writes no patient message. A result that lands on a tick is left for
that tick. A result that is not critical is left for the next tick.
"""
from __future__ import annotations

from datetime import datetime, time

TICKS = (time(8, 30), time(13, 30), time(19, 30))


def _clock(now: datetime) -> time:
    return time(now.hour, now.minute)


def wake_critical_lab(harness, api, record: dict) -> dict:
    """Raise only. Returns whether an item was opened for the named doctor."""
    if _clock(api.now) in TICKS:
        return {"raised": False, "reason": "on a scheduled tick", "patient_message": False}
    before = set(harness.raised)
    staged = len(harness.pending)
    harness.on_cms(api, record)
    critical = [p for p in harness.pending if p.get("urgency", 0) >= 4 and p["key"] not in harness.raised]
    if not critical:
        harness.pending = harness.pending[:staged]
        return {"raised": False, "reason": "not critical", "patient_message": False}
    harness.check_pending(api)
    opened = [k for k in harness.raised if k not in before]
    return {"raised": bool(opened), "reason": "raised for the named doctor" if opened else "not raised",
            "patient_message": False}
