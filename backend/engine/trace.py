"""Canonical reasoning-trace builders and urgency/date formatting primitives.

Shared by the deterministic rules engine (`rules.py`), escalation (`escalation.py`),
and all plug-in clinical journeys (`backend/journeys/*`). Kept as a leaf module inside
`backend/engine/` so neither `escalation` nor journeys depend on `rules.py`.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..case.quote import norm

WINDOW = {4: 1, 3: 2, 2: 7, 1: 14}

URGENCY_LABEL = {
    4: "critical: same day; the doctor's mobile is paged if it is unseen after an hour",
    3: "urgent: within 2 days",
    2: "soon: within a week",
    1: "routine: within 2 weeks",
}


def line_with(text: str, pattern: str, flags: int = 0) -> str | None:
    """The first line matching a regex, whitespace collapsed: a verbatim quote once the platform normalises spaces."""
    for ln in (text or "").splitlines():
        if re.search(pattern, ln, flags):
            return norm(ln)[:240] or None
    return None


def result_line(text: str, test: str) -> str | None:
    return line_with(text, r"^\s*" + re.escape(test) + r"(\s{2,}|:)")


def cite(rid: str, quote: str | None = None) -> dict:
    return {"record": rid, "quote": quote} if quote else {"record": rid}


def when(t: datetime) -> str:
    return t.strftime("%a %d %b %H:%M")


def day(t: datetime) -> str:
    return t.strftime("%d %b %Y")


def med_name(quote: str) -> str:
    m = re.match(r"Dispensed:\s*(.+?)\s+x\d+ days", quote or "")
    return m.group(1) if m else (quote or "")[:40]


class Why:
    """Collects reasoning steps for one item."""

    def __init__(self):
        self.steps: list[dict] = []

    def add(self, step: str, text: str, *cites, diff=None, search=None) -> "Why":
        s = {"step": step, "text": text, "cites": [c for c in cites if c]}
        if diff:
            s["diff"] = diff
        if search:
            s["search"] = search
        self.steps.append(s)
        return self
