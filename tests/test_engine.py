"""Engine tests that need no simulator: the port contract and the escalation ladder."""
from datetime import date, datetime, timedelta
from pathlib import Path

from backend.engine import Escalation, RulesHarness


class FakePort:
    """Just enough of a clinic for escalation: one doctor away, one on duty."""
    ServiceUnavailable = ConnectionError

    def __init__(self, now, owner_rostered: bool):
        self.now = now
        self.owner_rostered = owner_rostered
        self._items = {}
        self.raised, self.opened = [], []

    def roster(self, d):
        docs = ["dr_backup"] + (["dr_owner"] if self.owner_rostered else [])
        return {"open": True, "closed_reason": "", "doctors": docs}

    def items(self, status=None):
        return [dict(v) for v in self._items.values() if status is None or v["status"] == status]

    def raise_urgency(self, iid, u, reason=""):
        assert u >= self._items[iid]["urgency"], "engine must never lower urgency"
        self._items[iid]["urgency"] = u
        self.raised.append((iid, u, reason))

    def open_item(self, pid, owner, family, reason, evidence, urgency=1, due=None, reasoning=None, escalates=None):
        iid = f"Q{len(self._items)}"
        self._items[iid] = {"id": iid, "patient_id": pid, "owner_id": owner, "family": family, "reason": reason,
                            "evidence": list(evidence), "urgency": urgency, "status": "open",
                            "opened_at": self.now.isoformat(), "escalates": escalates}
        self.opened.append((iid, owner, escalates, reasoning))
        return iid


class FakeEngine:
    def roster(self, api, d):
        return api.roster(d)

    def nm(self, doc):
        return doc


def _run(owner_rostered: bool, urgency: int):
    t0 = datetime(2027, 3, 1, 8, 30)
    port = FakePort(t0, owner_rostered)
    first = port.open_item("p1", "dr_owner", "planned_review", "Planned review not attended", ["R1"], urgency)
    esc = Escalation()
    for d in range(0, 16):
        port.now = t0 + timedelta(days=d)
        esc.tick(FakeEngine(), port)
    return port, first, esc


def test_day7_raises_and_day14_pages_urgent_items_when_owner_present():
    port, first, esc = _run(True, 3)
    assert [u for i, u, _ in port.raised if i == first] == [4]          # 3 is already >= the day-7 floor
    port, first, esc = _run(True, 1)
    assert [u for i, u, _ in port.raised if i == first] == [2, 3]       # low: raised twice, never paged
    assert not port.opened[1:]
    port, first, esc = _run(True, 2)
    assert [u for i, u, _ in port.raised if i == first] == [3]          # routine: day 7 lifts it; day 14 never pages
    assert not port.opened[1:]


def test_day14_copies_to_backup_when_owner_away():
    port, first, esc = _run(False, 2)
    copies = [o for o in port.opened if o[2] == first]
    assert len(copies) == 1 and copies[0][1] == "dr_backup"
    steps = copies[0][3]
    assert {s["step"] for s in steps} >= {"source", "assessment", "routing", "request"}
    assert any(e == "R1" for s in steps for c in s["cites"] for e in [c["record"]])   # reasoning discusses the evidence


def test_engine_never_imports_loopbench():
    import re
    for p in Path(__file__).resolve().parents[1].joinpath("backend").rglob("*.py"):
        assert not re.search(r"^\s*(from|import)\s+loopbench", p.read_text(), re.M), f"{p} imports the test bed"


def test_rules_versions():
    assert RulesHarness().name == "rules" and RulesHarness(version="1.1").name == "rules-v1.1"
