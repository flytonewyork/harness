"""Phase 0 look-back: which follow-ups owed in the past year never closed, by kind.

Reads an export in the format of schema.py (EC's real export, or LoopBench's simulated one), finds every
follow-up the clinic owed, checks whether it closed in time, and lists the ones that did not. It runs once over
past data; nothing is sent to doctors. The rules are the engine's own (lab urgency, monitoring, review windows),
plus the free-text extractor for plans written in notes and letters.

Each flag says what was owed, from which records (opaque keys), by when, and what the data shows:

- **unclosed**: nothing in the data closes it, up to the end of the export;
- **late**: it closed, but after it was due;
- **unknown**: the closure would live outside the clinic's records (an HA appointment is visible only in
  eHealth), so the look-back cannot tell. These are listed, not counted as misses.

Closing evidence: a sign-off, a logged call about it, a matching later result, or a consultation whose note deals
with it (any consultation, if the export has no note text). The unclosed list errs towards follow-ups that
really were dropped.
"""
from __future__ import annotations

import csv
import json
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..engine.agentic import TEST_WORDS
from ..engine.rules import MONITOR, PANEL_TESTS, WINDOW, lab_urgency
from ..extract import Extractor, RulesModel, content_id
from .checks import report_text, simulated_counters, verify_flags
from .schema import TABLES

POLICY = {
    "result_window_days": WINDOW,                 # by engine urgency: 4 critical .. 1 routine
    "order_result_days": 21,                      # a test ordered but never resulted
    "referral_days": {"urgent": 14, "other": 90},
    "fit_days": 180,                              # positive FIT to colonoscopy
    "mammogram_days": 60,                         # abnormal screening mammogram to further imaging
    "monitoring_grace_days": 14,
    "review_grace_days": 14,                      # a planned review is overdue two weeks after its date
    "test_grace": (7, 0.33),                      # committed test: max(7 days, a third of the interval)
}
VISIT_KINDS = {"abnormal_result", "planned_review", "committed_test", "hospital_followup", "fit_positive", "drug_monitoring",
               "mammogram_recall"}
DOC_TEXT_KINDS = ("discharge summary", "A&E summary", "specialist letter", "imaging report", "message")
REVIEW_VISIT = re.compile(r"review|follow|chronic|cdcc|co-care|booked", re.I)   # visits that can be the planned review
ABOUT = {"result": r"\bresult", "order": r"outstanding test", "referral": r"referral|booking|appointment",
         "screen": r"screen|\bFIT\b|colonoscopy", "monitoring": r"monitoring|bloods", "review": r"recall",
         "followup": r"follow-up arranged"}


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


@dataclass
class Flag:
    kind: str
    patient_key: str
    source_keys: list[str]
    started: datetime
    owed_by: datetime
    status: str                        # unclosed | late | unknown
    closed_at: datetime | None = None
    detail: str = ""
    quote: str = ""

    def row(self) -> dict:
        d = asdict(self)
        d["source_keys"] = ";".join(self.source_keys)
        for k in ("started", "owed_by", "closed_at"):
            d[k] = d[k].isoformat(timespec="minutes") if d[k] else ""
        return d


@dataclass
class Export:
    tables: dict[str, list[dict]]
    as_of: datetime
    consults: dict[str, list[datetime]] = field(default_factory=dict)
    contacts: dict[str, list[datetime]] = field(default_factory=dict)
    results: dict[str, list[tuple[datetime, set, str]]] = field(default_factory=dict)   # pid -> (received, tests, group)
    docs: dict[str, list[dict]] = field(default_factory=dict)
    calls: dict[str, list[tuple[datetime, str]]] = field(default_factory=dict)   # pid -> (time, purpose)
    reviews: dict[str, list[datetime]] = field(default_factory=dict)              # review-type consultations
    notes: dict[str, list[tuple[datetime, str]]] = field(default_factory=dict)    # pid -> (time, note text)
    linked_by_lab_no: int = 0


def load(export_dir: str | Path) -> Export:
    d = Path(export_dir)
    tables = {}
    for name in TABLES:
        p = d / f"{name}.csv"
        tables[name] = list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []
    stamps = [v for rows in tables.values() for r in rows for k, v in r.items()
              if v and (k.endswith("datetime") or k == "datetime")]
    ex = Export(tables, max(datetime.fromisoformat(s) for s in stamps) if stamps else datetime.now())
    # results never filed to a patient: link them through the clinic's own request number
    order_pid = {o["lab_no"]: o["patient_key"] for o in tables["lab_orders"] if o["lab_no"] and o["patient_key"]}
    linked = set()
    for r in tables["lab_results"]:
        if not r["patient_key"] and r["lab_no"] in order_pid:
            r["patient_key"] = order_pid[r["lab_no"]]
            linked.add(r["report_key"])
    ex.linked_by_lab_no = len(linked)
    by = defaultdict(list)
    for r in tables["consultations"]:
        by[r["patient_key"]].append((_dt(r["datetime"]), r.get("visit_type", "")))
    ex.consults = {k: sorted(t for t, _ in v) for k, v in by.items()}
    notes = defaultdict(list)
    for r in tables["consultations"]:
        notes[r["patient_key"]].append((_dt(r["datetime"]), (r.get("plan_text") or "").lower()))
    ex.notes = {k: sorted(v) for k, v in notes.items()}
    ex.reviews = {k: sorted(t for t, vt in v if not vt or REVIEW_VISIT.search(vt)) for k, v in by.items()}
    by = defaultdict(list)
    for r in tables["contacts"]:
        by[r["patient_key"]].append((_dt(r["datetime"]), (r.get("purpose") or "").lower()))
    ex.calls = {k: sorted(v) for k, v in by.items()}
    ex.contacts = {k: [t for t, _ in v] for k, v in ex.calls.items()}
    by = defaultdict(list)
    for r in tables["documents"]:
        if r["patient_key"]:
            by[r["patient_key"]].append(r)
    ex.docs = by
    return ex


def first_after(times: list[datetime] | None, t: datetime) -> datetime | None:
    if not times:
        return None
    i = bisect_right(times, t)
    return times[i] if i < len(times) else None


def call_after(ex: Export, pid: str, t: datetime, about: str) -> datetime | None:
    """First logged call or message to the patient after t whose purpose matches `about` (a regex)."""
    rx = re.compile(about, re.I)
    return next((ct for ct, why in ex.calls.get(pid, ()) if ct > t and rx.search(why)), None)


def visit_about(ex: Export, pid: str, t: datetime, rows: dict) -> datetime | None:
    """First consultation after t that dealt with the result. With note text, the note has to mention the result
    or one of the abnormal tests; without any note text in the export, any consultation counts."""
    return visit_mentioning(ex, pid, t, [x["test"].lower() for x in rows.values() if x["flag"] in ("H", "L")] + ["result"])


def visit_mentioning(ex: Export, pid: str, t: datetime, words: list[str]) -> datetime | None:
    for ct, text in ex.notes.get(pid, ()):
        if ct > t and (not text or any(w in text for w in words)):
            return ct
    return None


def doc_date(text: str) -> datetime | None:
    """The event date a hospital summary is about (discharge or attendance), which follow-up is counted from."""
    m = re.search(r"(?:Discharged|Attended):\s*(\d{2})/(\d{2})/(\d{4})", text or "")
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def status_of(closed: datetime | None, owed_by: datetime) -> str | None:
    """None when it closed in time (no flag)."""
    if closed is not None and closed <= owed_by:
        return None
    return "late" if closed is not None else "unclosed"


# --------------------------------------------------------------------------------------------- kinds
def result_flags(ex: Export) -> list[Flag]:
    """Abnormal results: a call, a consultation or a repeat of the same test must follow within the window."""
    groups: dict[tuple, dict] = {}
    for r in ex.tables["lab_results"]:
        if not r["patient_key"]:
            continue                                         # never filed: counted in the summary, cannot be followed
        g = groups.setdefault((r["patient_key"], r["lab_no"] or r["report_key"]),
                              {"keys": [], "rows": {}, "tests": set(), "received": None, "reviewed": None})
        g["tests"].add(r["test"])
        if r["report_key"] not in g["keys"]:
            g["keys"].append(r["report_key"])
        t = _dt(r["received_datetime"])
        g["received"] = t if g["received"] is None or t < g["received"] else g["received"]
        if r.get("reviewed_datetime"):
            g["reviewed"] = _dt(r["reviewed_datetime"])
        v = _f(r["value"])
        if v is not None:
            g["rows"][r["test"]] = {"test": r["test"], "value": v, "flag": r["flag"] or "N",
                                    "ref_low": _f(r["ref_low"]), "ref_high": _f(r["ref_high"])}
    by_pid = defaultdict(list)
    for (pid, gk), g in groups.items():
        by_pid[pid].append((g["received"], g["tests"], gk))
    for pid in by_pid:
        by_pid[pid].sort(key=lambda x: x[0])
    ex.results = by_pid
    out = []
    for (pid, gk), g in groups.items():
        u = lab_urgency(list(g["rows"].values()))
        if u == 0:
            continue
        t = g["received"]
        owed = t + timedelta(days=POLICY["result_window_days"][u])
        cands = [x for x in (call_after(ex, pid, t, ABOUT["result"]), visit_about(ex, pid, t, g["rows"]), g["reviewed"]) if x]
        cands += [rt for rt, tests, gk2 in by_pid[pid] if rt > t and gk2 != gk and tests & g["tests"]][:1]
        closed = min(cands) if cands else None
        st = status_of(closed, owed)
        if st:
            ab = ", ".join(f"{x['test']} {x['value']:g}" for x in g["rows"].values() if x["flag"] in ("H", "L"))
            out.append(Flag("abnormal_result", pid, g["keys"], t, owed, st, closed, f"urgency {u}: {ab}"[:200]))
    return out


def order_flags(ex: Export) -> list[Flag]:
    out = []
    for o in ex.tables["lab_orders"]:
        pid, t = o["patient_key"], _dt(o["datetime"])
        if not pid:
            continue
        got = [rt for rt, _, gk in ex.results.get(pid, ()) if rt > t and (gk == o["lab_no"] if o["lab_no"] else True)]
        got += [x for x in (call_after(ex, pid, t, ABOUT["order"]),) if x]
        closed = min(got) if got else None
        owed = t + timedelta(days=POLICY["order_result_days"])
        st = status_of(closed, owed)
        if st:
            out.append(Flag("test_not_resulted", pid, [o["order_key"]], t, owed, st, closed, o["tests"][:200]))
    return out


def referral_flags(ex: Export) -> list[Flag]:
    out = []
    for r in ex.tables["referrals"]:
        pid, t = r["patient_key"], _dt(r["datetime"])
        if not pid:
            continue
        urgent = r["urgency"] == "urgent"
        owed = t + timedelta(days=POLICY["referral_days"]["urgent" if urgent else "other"])
        replies = [_dt(d["received_datetime"]) for d in ex.docs.get(pid, ())
                   if d["type"] in ("specialist letter", "imaging report") and _dt(d["received_datetime"]) > t]
        if r.get("reply_received_datetime"):
            replies.append(_dt(r["reply_received_datetime"]))
        replies += [x for x in (call_after(ex, pid, t, ABOUT["referral"]),) if x]
        closed = min(replies) if replies else None
        st = status_of(closed, owed)
        if st and r["destination"] == "HA" and closed is None:
            st = "unknown"                                   # HA bookings show only in eHealth
        if st:
            out.append(Flag("referral_reply", pid, [r["referral_key"]], t, owed, st, closed,
                            f"{r['destination']} {r['specialty']} ({r['urgency']})"))
    return out


def fit_flags(ex: Export) -> list[Flag]:
    out = []
    for s in ex.tables["screening"]:
        if "FIT" not in s["programme"] or "POSITIVE" not in s["result"].upper() or not s["patient_key"]:
            continue
        pid, t = s["patient_key"], _dt(s["result_datetime"])
        owed = t + timedelta(days=POLICY["fit_days"])
        letters = [_dt(d["received_datetime"]) for d in ex.docs.get(pid, ())
                   if d["type"] == "specialist letter" and _dt(d["received_datetime"]) > t
                   and (not d.get("text") or re.search(r"colonoscop|endoscop|gastro", d["text"], re.I))]
        # closed by the colonoscopy (its report), not by the call that arranged it
        closed = min(letters) if letters else None
        st = status_of(closed, owed)
        if st:
            out.append(Flag("fit_positive", pid, [s["screen_key"]], t, owed, st, closed, "FIT positive: colonoscopy"))
    return out


def mammogram_flags(ex: Export) -> list[Flag]:
    """An abnormal screening mammogram (BI-RADS 0, 4 or 5) needs further imaging or a breast clinic."""
    out = []
    for pid, docs in ex.docs.items():
        for d in docs:
            if d["type"] != "imaging report" or not re.search(r"BI-RADS\s*(0|4|5)", d.get("text") or ""):
                continue
            t = _dt(d["received_datetime"])
            owed = t + timedelta(days=POLICY["mammogram_days"])
            later = [_dt(x["received_datetime"]) for x in docs if _dt(x["received_datetime"]) > t
                     and (x["type"] == "imaging report" or (x["type"] == "specialist letter"
                          and (not x.get("text") or re.search(r"breast|radiolog|imaging", x["text"], re.I))))]
            closed = min(later) if later else None
            st = status_of(closed, owed)
            if st:
                out.append(Flag("mammogram_recall", pid, [d["doc_key"]], t, owed, st, closed, "abnormal screening mammogram"))
    return out


def monitoring_flags(ex: Export) -> list[Flag]:
    out, seen = [], set()
    for rx in sorted(ex.tables["prescriptions"], key=lambda r: r["datetime"]):
        if rx.get("new_start") != "Y" or not rx["patient_key"]:
            continue
        for name, pat, panel, days in MONITOR:
            if not re.search(pat, rx["drug"], re.I) or (rx["patient_key"], name) in seen:
                continue
            seen.add((rx["patient_key"], name))
            pid, t = rx["patient_key"], _dt(rx["datetime"])
            owed = t + timedelta(days=days + POLICY["monitoring_grace_days"])
            got = [rt for rt, tests, _ in ex.results.get(pid, ()) if rt > t and tests & PANEL_TESTS[panel]]
            got += [x for x in (call_after(ex, pid, t, ABOUT["monitoring"]),) if x]
            closed = min(got) if got else None
            st = status_of(closed, owed)
            if st:
                out.append(Flag("drug_monitoring", pid, [rx["rx_key"]], t, owed, st, closed, f"{panel} after {rx['drug']}"[:200]))
    return out


def tests_for(what: str) -> set:
    return {x for k, v in TEST_WORDS.items() if k in what.lower() for x in v}


def free_text_flags(ex: Export, extractor: Extractor) -> list[Flag]:
    """Plans written in notes and letters: reviews, committed tests, hospital and imaging follow-up."""
    srcs = []
    for c in ex.tables["consultations"]:
        if c["patient_key"] and c.get("plan_text"):
            srcs.append(("consult_note", c["encounter_key"], c["patient_key"], _dt(c["datetime"]), c["plan_text"]))
    kind_of = {"discharge summary": "discharge_summary", "A&E summary": "aed_summary", "specialist letter": "specialist_letter",
               "imaging report": "imaging_report", "message": "message"}
    for pid, docs in ex.docs.items():
        for d in docs:
            if d["type"] in kind_of and d.get("text"):
                srcs.append((kind_of[d["type"]], d["doc_key"], pid, _dt(d["received_datetime"]), d["text"]))
    event = {key: doc_date(text) for kind, key, pid, t, text in srcs if kind in ("discharge_summary", "aed_summary")}
    docs = {}
    for kind, key, pid, t, text in srcs:
        cid = content_id(kind, text)
        docs.setdefault(cid, {"doc_id": cid, "kind": kind, "date": t.date().isoformat(), "text": text})
    ext = extractor.extract(list(docs.values()))
    out = []
    for kind, key, pid, t, text in srcs:
        e = ext.get(content_id(kind, text))
        for c in (e.commitments if e else []):
            if c.conditional or c.owner == "hospital" or c.due_in_days is None:
                continue
            days = c.due_in_days
            t0 = min(t, event.get(key) or t)                  # a hospital summary counts from the discharge
            if c.kind == "review":
                if kind == "consult_note":
                    fk, lo = "planned_review", t + timedelta(days=max(1, days - 14, days * 0.5))
                    owed = t + timedelta(days=days + POLICY["review_grace_days"])
                    cands = [first_after(ex.reviews.get(pid), lo), call_after(ex, pid, t, ABOUT["review"])]
                else:
                    fk, owed = "hospital_followup", t0 + timedelta(days=days + 7)
                    words = ["discharge", "hospital", "a&e", "follow-up", "follow up"] + [w.lower() for w in tests_for(c.what)]
                    cands = [visit_mentioning(ex, pid, t0, words), call_after(ex, pid, t0, ABOUT["followup"])]
                cands = [x for x in cands if x]
                closed = min(cands) if cands else None
            elif c.kind in ("repeat_test", "imaging"):
                fk = "committed_test" if c.kind == "repeat_test" else "imaging_followup"
                g, frac = POLICY["test_grace"]
                owed = t0 + timedelta(days=days + max(g, frac * days))
                if c.kind == "imaging":
                    cands = [_dt(d["received_datetime"]) for d in ex.docs.get(pid, ())
                             if d["type"] == "imaging report" and _dt(d["received_datetime"]) > t + timedelta(days=days * 0.5)]
                else:
                    want = tests_for(c.what)
                    cands = [rt for rt, tests, _ in ex.results.get(pid, ()) if rt > t and (not want or tests & want)]
                    cv = first_after(ex.reviews.get(pid), t + timedelta(days=max(1, days - 7)))
                    cands += [x for x in (cv, call_after(ex, pid, t, ABOUT["followup"])) if x]
                closed = min(cands) if cands else None
            else:
                continue
            st = status_of(closed, owed)
            if st:
                out.append(Flag(fk, pid, [key], t0, owed, st, closed, f"{c.kind}: {c.what}"[:200], c.quote))
    return out


def merge_orders(orders: list[Flag], monitors: list[Flag]) -> list[Flag]:
    """A monitoring panel that was ordered but never done is one follow-up, not two: the order joins the
    monitoring flag it belongs to (same patient, ordered within two days of the start, same panel)."""
    out = list(monitors)
    by_pid = defaultdict(list)
    for m in monitors:
        by_pid[m.patient_key].append(m)
    for o in orders:
        tests = set(o.detail.replace(" ", "").split("/"))
        host = next((m for m in by_pid.get(o.patient_key, ()) if abs((o.started - m.started).total_seconds()) <= 2 * 86400
                     and m.detail.split(" after ")[0] in tests), None)
        if host is not None:
            host.source_keys.append(o.source_keys[0])
        else:
            out.append(o)
    return out


# ------------------------------------------------------------------------------------------------ run
def run(export_dir: str | Path, extractor: Extractor | None = None, index_from: datetime | None = None,
        index_to: datetime | None = None, fee_hkd: tuple[int, int] = (400, 540)) -> dict:
    ex = load(export_dir)
    extractor = extractor or Extractor(RulesModel())
    flags = result_flags(ex) + referral_flags(ex) + fit_flags(ex) + mammogram_flags(ex)
    flags += merge_orders(order_flags(ex), monitoring_flags(ex))
    flags += free_text_flags(ex, extractor)
    flags = [f for f in flags if f.owed_by <= ex.as_of
             and (index_from is None or f.started >= index_from) and (index_to is None or f.started < index_to)]
    dedup, seen = [], set()
    for f in sorted(flags, key=lambda f: (f.started, f.kind)):
        k = (f.kind, f.patient_key, tuple(f.source_keys), f.detail)
        if k not in seen:
            seen.add(k)
            dedup.append(f)
    counts = Counter((f.kind, f.status) for f in dedup)
    kinds = sorted({k for k, _ in counts})
    missed_visits = sum(1 for f in dedup if f.status in ("unclosed", "late") and f.kind in VISIT_KINDS)
    unfiled = len({r["report_key"] for r in ex.tables["lab_results"] if not r["patient_key"]})
    summary = {
        "as_of": ex.as_of.isoformat(timespec="minutes"),
        "flags": len(dedup),
        "by_kind": {k: {s: counts.get((k, s), 0) for s in ("unclosed", "late", "unknown")} for k in kinds},
        "unfiled_lab_reports": unfiled,
        "unfiled_results_linked_by_request_number": ex.linked_by_lab_no,
        "unfiled_documents": sum(1 for r in ex.tables["documents"] if not r["patient_key"]),
        "missed_consultations": missed_visits,
        "missed_consultations_hkd": [missed_visits * fee_hkd[0], missed_visits * fee_hkd[1]],
        "extractor": extractor.model.name,
        "policy": {k: (v if not isinstance(v, dict) else {str(a): b for a, b in v.items()}) for k, v in POLICY.items()},
    }
    observed = verify_flags(ex, dedup)
    summary["counters"] = simulated_counters(dedup, missed_visits)
    summary["platform_checks"] = {"flags": len(dedup), "checks_each": 7,
                                  "with_a_fail": sum(1 for row in observed if not row["passed"])}
    return {"summary": summary, "flags": dedup, "platform_checks": observed}


def write(result: dict, out_dir: str | Path):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = [f.row() for f in result["flags"]]
    with (out / "flags.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["kind"])
        w.writeheader()
        w.writerows(rows)
    (out / "summary.json").write_text(json.dumps(result["summary"], indent=1))
    (out / "platform_checks.json").write_text(json.dumps(result.get("platform_checks", []), indent=1))
    (out / "report.md").write_text(report_text(result["summary"]["counters"]))
