"""Run the product's seven platform checks on every look-back flag.

The look-back is a batch over past records, so there is no live port. Each flag is checked as the item it
would be: the source rows are the visible records, the quote has to be in the row it cites, and the seven
checks are the same function the engine runs on an opened item (`backend.engine.verify.check_item`).
"""
from __future__ import annotations

from ..engine.verify import CHECKS, check_item, norm

MINUTES_PER_ITEM = 8


def source_text(ex, key: str) -> str:
    """The export text a flag's quote has to be found in. Nothing is added that the row did not hold."""
    chunks: list[str] = []
    for r in ex.tables["lab_results"]:
        if r.get("report_key") == key:
            chunks.append(" ".join(p for p in (r.get("test"), r.get("value"), r.get("flag")) if p))
    pairs = (
        ("lab_orders", "order_key", ("tests",)),
        ("referrals", "referral_key", ("destination", "specialty", "urgency")),
        ("prescriptions", "rx_key", ("drug",)),
        ("consultations", "encounter_key", ("plan_text", "visit_type")),
        ("documents", "doc_key", ("text", "type")),
        ("screening", "screen_key", ("programme", "result")),
        ("contacts", "contact_key", ("purpose",)),
    )
    for table, field, cols in pairs:
        for r in ex.tables[table]:
            if r.get(field) == key:
                chunks.append(" ".join(p for p in (r.get(c) for c in cols) if p))
    return "\n".join(chunks)


def _owner(ex) -> tuple[str, dict, bool]:
    keys = [r["doctor_key"] for r in ex.tables["doctors"] if r.get("doctor_key")]
    if not keys:
        keys = [r["registered_doctor_key"] for r in ex.tables["patients"] if r.get("registered_doctor_key")]
    if not keys:
        return "unassigned", {"unassigned": {"id": "unassigned", "name": "Unassigned", "joined": None, "left": None}}, False
    owner = keys[0]
    return owner, {owner: {"id": owner, "name": owner, "joined": None, "left": None}}, True


def verify_flags(ex, flags: list) -> list[dict]:
    """One observation per flag. Every observation carries all seven check ids."""
    owner, staff, works = _owner(ex)
    rostered = lambda _day: works
    now = ex.as_of
    out = []
    for flag in flags:
        seen = {}
        for key in flag.source_keys:
            seen[key] = {"id": key, "patient_id": flag.patient_key, "kind": flag.kind,
                         "text": source_text(ex, key), "available_at": flag.started.isoformat()}
        cites = []
        placed = False
        for key in flag.source_keys:
            text = seen[key]["text"]
            if flag.quote and norm(flag.quote) in norm(text):
                cites.append({"record": key, "quote": flag.quote})
                placed = True
            else:
                cites.append({"record": key})
        if flag.quote and not placed and cites:
            cites[0] = {"record": flag.source_keys[0], "quote": flag.quote}
        steps = []
        if flag.source_keys:
            steps.append({"step": "assessment", "text": "Working problem: " + (flag.detail or flag.kind),
                          "cites": cites})
        evidence = tuple(flag.source_keys)
        card = {"id": flag.patient_key, "name_en": "", "dob": "", "hkid": ""}
        chart = [seen[k] for k in flag.source_keys]
        checks, problems = check_item(seen, chart, [], None, card, staff, rostered, now, flag.patient_key,
                                      owner, flag.kind, evidence, steps, 2, None)
        ids = [c["id"] for c in checks]
        if ids != CHECKS:
            raise RuntimeError(f"look-back flag {flag.kind} ran {ids}, not the seven checks")
        out.append({"kind": flag.kind, "patient_key": flag.patient_key, "source_keys": list(flag.source_keys),
                    "checks": checks, "problems": [{"kind": k, "detail": d} for k, d in problems],
                    "passed": all(c["status"] in ("pass", "na", "warn") for c in checks)})
    return out


def simulated_counters(flags: list, missed_visits: int) -> dict:
    """The four figures the look-back will count. Every one is simulated. Hours back is an assumption."""
    loops = sum(1 for f in flags if f.status == "unclosed")
    closes = sum(1 for f in flags if f.status == "late")
    opened = loops + closes
    hours = round(opened * MINUTES_PER_ITEM / 60, 1)
    assumption = (f"Assumption, not a measured time study: {MINUTES_PER_ITEM} minutes per opened item.")
    return {
        "simulated": True,
        "loops_left_open": {"value": loops, "simulated": True},
        "doctor_closes": {"value": closes, "simulated": True},
        "hours_back": {"value": hours, "simulated": True, "minutes_per_item": MINUTES_PER_ITEM,
                       "items_opened": opened, "assumption": assumption},
        "recovered_visits": {"value": missed_visits, "simulated": True},
    }


def report_text(counters: dict) -> str:
    c = counters
    lines = [
        "# Look-back",
        "",
        "Simulated. Not an EC clinic. These are the four counters this look-back counted.",
        "",
        f"- Loops left open: {c['loops_left_open']['value']}. Simulated.",
        f"- Doctor closes: {c['doctor_closes']['value']}. Simulated.",
        f"- Hours back: {c['hours_back']['value']}. Simulated. {c['hours_back']['assumption']}",
        f"- Recovered visits: {c['recovered_visits']['value']}. Simulated.",
        "",
    ]
    return "\n".join(lines)
