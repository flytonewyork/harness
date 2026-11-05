"""Day-7 and day-14 escalation for items nobody has actioned.

The engine may only open items and raise urgency, so escalation uses exactly those two moves:

- **Day 7:** raise the item one step (to at least 2, at most 3). It rises up the owner's list.
- **Day 14:** if the owner is rostered today or tomorrow, an item that was opened urgent (3) is raised to
  critical (4), which pages their mobile; an item opened routine is raised to urgent (3) and never pages, even
  though day 7 already lifted it. If the owner is away (leave, not rostered, or gone), the same item is opened
  for the doctor on duty today, the named backup, linked to the original with `escalates`, so the platform knows
  it is not a duplicate.

Days are calendar days since the item was opened, checked at the morning run.

Escalation copies are not escalated again. Each escalation is written into the item's reasoning trace, so the
doctor sees why an item jumped up their list.
"""
from __future__ import annotations

from datetime import datetime, timedelta

DAY_RAISE, DAY_ESCALATE = 7, 14


class Escalation:
    def __init__(self, raise_after_days: int = DAY_RAISE, escalate_after_days: int = DAY_ESCALATE):
        self.raise_after = raise_after_days
        self.escalate_after = escalate_after_days
        self.reset()

    def reset(self):
        self.stage: dict[str, int] = {}          # item id -> 0 none, 1 raised at day 7, 2 escalated at day 14
        self.base: dict[str, int] = {}           # item id -> urgency when first seen (before any raise)
        self.copies: set[str] = set()            # escalation items we opened ourselves
        self.log: list[tuple] = []               # (time, item id, action, detail) for reports and the API

    def tick(self, engine, api):
        now = api.now
        for it in api.items(status="open"):
            iid = it["id"]
            if iid in self.copies:
                continue
            age = (now.date() - datetime.fromisoformat(it["opened_at"]).date()).days
            self.base.setdefault(iid, it["urgency"])
            stage = self.stage.get(iid, 0)
            if stage == 0 and age >= self.raise_after:
                new = min(3, max(2, it["urgency"] + 1))
                if new > it["urgency"]:
                    api.raise_urgency(iid, new, reason=f"Unactioned for {age} days: raised from {it['urgency']} to {new}.")
                    self.log.append((now, iid, "raised", f"day {age}: urgency {it['urgency']} -> {new}"))
                self.stage[iid] = 1
                stage = 1
            if stage == 1 and age >= self.escalate_after:
                self.stage[iid] = 2
                self.escalate(engine, api, it, age)

    def escalate(self, engine, api, it: dict, age: int):
        now, owner = api.now, it["owner_id"]
        soon = [now.date() + timedelta(days=i) for i in (0, 1)]
        if any(owner in engine.roster(api, d)["doctors"] for d in soon):
            if self.base.get(it["id"], it["urgency"]) >= 3:
                api.raise_urgency(it["id"], 4, reason=f"Unactioned for {age} days: raised to critical, which pages {engine.nm(owner)}'s mobile.")
                self.log.append((now, it["id"], "paged", f"day {age}: urgency -> 4, owner {owner}"))
            elif it["urgency"] < 3:
                api.raise_urgency(it["id"], 3, reason=f"Unactioned for {age} days: raised to urgent.")
                self.log.append((now, it["id"], "raised", f"day {age}: urgency -> 3, owner {owner}"))
            return
        on_duty = engine.roster(api, now.date())["doctors"]
        if not on_duty:                               # clinic closed today: try again tomorrow
            self.stage[it["id"]] = 1
            return
        backup = on_duty[0]
        from .rules import URGENCY_LABEL, Why, cite, when   # local import: rules imports this module
        w = Why()
        w.add("source", f"Escalation of the item opened on {when(datetime.fromisoformat(it['opened_at']))} for {engine.nm(owner)}: "
                        f"“{it['reason']}”.", *[cite(e) for e in it["evidence"]])
        w.add("loop_status", f"Nobody has actioned it for {age} days.")
        w.add("assessment", f"Working problem: an open {it['family'].replace('_', ' ')} loop with its owner away. "
                            "Plan: the doctor on duty reviews it today.")
        u = max(3, it["urgency"])
        w.add("routing", f"{engine.nm(owner)} is not rostered today or tomorrow. {engine.nm(backup)} is on duty today (backup).")
        w.add("request", f"Escalation opened for {engine.nm(backup)} at urgency {u} ({URGENCY_LABEL[u]}); due {when(now + timedelta(days=1))}.")
        cid = api.open_item(it["patient_id"], backup, it["family"], f"Escalated (day {age}): {it['reason']}"[:200],
                            it["evidence"], u, now + timedelta(days=1), reasoning=w.steps, escalates=it["id"])
        self.copies.add(cid)
        self.log.append((now, it["id"], "escalated", f"day {age}: copied to backup {backup} as {cid}"))
