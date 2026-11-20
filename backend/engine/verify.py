"""The product's own platform checks: what we re-derive about every item the engine opens, from only what the
engine could see through the port.

This is the online twin of the test bed's gate. LoopBench verifies each item as its simulator host
(`loopbench/verify.py`), but the product must run the same seven checks itself, so the observability exists when
the engine runs against real data (an EC export replay, later EC's live system) with no simulator to host it.
Per the engine's one rule, the product never imports LoopBench: the checks are re-implemented here against the
`ClinicPort` surface (`ports.py`), using dicts the port returns, never the simulator's hidden state.

`VerifyingClinic` wraps any `ClinicPort`. It remembers every record the engine is shown (the port's stand-in for
the simulator's "visible" set) and, on each `open_item`, computes the seven checks and records them beside the
item, with any hard fail. It only observes: it delegates open_item, raise_urgency, alarm and attest_all_clear
unchanged and never closes, dismisses, downgrades or reassures.

The seven checks, each re-derived from harness-visible data only:
    evidence   every cited record was visible to the engine when it decided
    quotes     every quoted claim appears word for word in the record it cites
    identity   the evidence names the same patient as the item (filing + printed identifiers)
    searches   a chart search the engine states is re-run and comes back the same
    duplicate  the item is not a duplicate of one still open for this patient
    owner      the owner works here and is rostered soon enough for the urgency
    reasoning  the trace cites the evidence and states a working problem
A quoted claim not in its record (`unsupported_claim`) or a citation the engine could not have seen
(`unsupported_evidence`) is a hard fail as well as a failing check.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

STEPS = ["source", "identity", "finding", "context", "assessment", "loop_status", "routing", "request"]
CHECKS = ["evidence", "quotes", "identity", "searches", "duplicate", "owner", "reasoning"]
STATUSES = ["pass", "warn", "fail", "na"]
WINDOW_DAYS = {4: 0, 3: 1, 2: 3, 1: 7}            # how soon the owner must be working, by urgency
LIMITS = {"steps": 20, "cites": 12, "text": 600, "quote": 300}


def norm(s) -> str:
    return " ".join(str(s).split())


def _dt(s):
    """A record time as the port prints it (ISO string) or already a datetime."""
    return s if isinstance(s, datetime) else datetime.fromisoformat(s)


def clean_steps(reasoning) -> list[dict]:
    """Validate a reasoning trace; trim over-long text (a trimmed quote is still verbatim). Same shape the test
    bed accepts, so a trace that passes here passes there."""
    if not reasoning:
        return []
    if not isinstance(reasoning, (list, tuple)):
        raise ValueError("reasoning must be a list of steps")
    out = []
    for s in list(reasoning)[: LIMITS["steps"]]:
        if not isinstance(s, dict) or s.get("step") not in STEPS:
            raise ValueError(f"reasoning step must be a dict with step in {STEPS}")
        cites = []
        for c in list(s.get("cites") or ())[: LIMITS["cites"]]:
            if not isinstance(c, dict) or not isinstance(c.get("record"), str):
                raise ValueError("a cite must be {'record': id, 'quote': optional text}")
            cc = {"record": c["record"]}
            if c.get("quote"):
                cc["quote"] = str(c["quote"])[: LIMITS["quote"]]
            cites.append(cc)
        st = {"step": s["step"], "text": str(s.get("text", ""))[: LIMITS["text"]], "cites": cites}
        if s.get("diff"):
            st["diff"] = [[str(x) for x in row[:3]] for row in s["diff"][:8]]
        if s.get("search"):
            q = s["search"]
            st["search"] = {"kinds": list(q.get("kinds") or ()), "since": str(q["since"]),
                            "contains_any": list(q.get("contains_any") or ()), "found": int(q.get("found", 0))}
        out.append(st)
    return out


# --------------------------------------------------------------------------------------- identity
_NAME_PATTERNS = [
    re.compile(r"(?:Name|Patient|Participant|Re)\s*:\s*([A-Z][A-Z'-]+(?: [A-Z][A-Z'-]+)*),\s*([A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)*)"),
    re.compile(r"Patient:\s*([A-Z][a-z][A-Za-z'-]*(?: [A-Z][a-z][A-Za-z'-]*)*) ([A-Z][A-Z'-]+)\s*\("),
    re.compile(r"\bfor ([A-Z][A-Z'-]+) ([A-Z][a-z][A-Za-z'-]*(?: [A-Z][a-z][A-Za-z'-]*)*)\b"),
]


def doc_identifiers(text: str) -> dict:
    """Patient identifiers printed on a document, scanning all pages to detect multi-patient misfax collisions."""
    out: dict = {}
    dobs: list[str] = []
    for m in re.finditer(r"(?:DOB|D\.O\.B\.)\s*:?\s*(\d{2})/(\d{2})/(\d{4})", text):
        d = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if d not in dobs:
            dobs.append(d)
    for m in re.finditer(r"(?:DOB|D\.O\.B\.)\s*:?\s*(\d{4}-\d{2}-\d{2})", text):
        d = m.group(1)
        if d not in dobs:
            dobs.append(d)
    if dobs:
        out["dob"] = dobs[0]
        if len(dobs) > 1:
            out["dob_collision"] = dobs

    hkids: list[str] = []
    for m in re.finditer(r"\b([A-Z]\d{3})XXX\(X\)", text):
        h = m.group(1)
        if h not in hkids:
            hkids.append(h)
    if hkids:
        out["hkid"] = hkids[0]
        if len(hkids) > 1:
            out["hkid_collision"] = hkids

    for pat in _NAME_PATTERNS:
        m = pat.search(text)
        if m:
            out["name"] = set(" ".join(m.groups()).upper().replace("-", " ").split())
            break
    return out


def identity_of(card: dict, rec: dict, patient_id: str) -> tuple[str, str]:
    """Does this document belong to the item's patient, judged only from what is visible: the chart it is filed to
    (`rec['patient_id']`) and the identifiers printed on it, against the patient's card from the port."""
    ids = doc_identifiers(rec.get("text") or "")
    kind = (rec.get("kind") or "record").replace("_", " ")
    if "hkid_collision" in ids or "dob_collision" in ids:
        return "fail", f"{kind} contains multiple distinct patient identifiers across pages (multi_patient_document_collision)"
    mine = {"dob": card.get("dob") or "", "hkid": (card.get("hkid") or "")[:4],
            "name": set((card.get("name_en") or "").upper().replace("-", " ").split())}
    label = {"dob": "DOB", "hkid": "HKID", "name": "name"}
    cmp_keys = [k for k in ("dob", "hkid", "name") if k in ids]
    ok = [k for k in cmp_keys if (ids[k] <= mine[k] if k == "name" else ids[k] == mine[k])]
    bad = [k for k in cmp_keys if k not in ok]
    filed = rec.get("patient_id") == patient_id
    if bad:
        return "fail", f"{kind} names a different patient: {', '.join(label[k] for k in bad)} do not match"
    if filed:
        return "pass", (f"{kind} filed to this chart; " + " + ".join(label[k] for k in ok) + " agree") if ok else f"{kind} filed to this chart"
    where = "filed to another chart" if rec.get("patient_id") else "not filed to any chart"
    if len(ok) >= 2:
        return "pass", f"{kind} {where}; matched on " + " + ".join(label[k] for k in ok)
    if len(ok) == 1:
        return "warn", f"{kind} {where}; only the {label[ok[0]]} matches (two identifiers expected)"
    return "warn", f"{kind} {where} and shows no patient identifiers"


def _worst(statuses):
    for s in ("fail", "warn", "pass"):
        if s in statuses:
            return s
    return "na"


# ------------------------------------------------------------------------------------------ checks
def check_item(seen: dict, chart: list, open_items: list, orig_item: dict | None, card: dict, staff: dict,
               rostered, now: datetime, patient_id: str, owner_id: str, family: str, evidence: tuple, steps: list,
               urgency: int, escalates: str | None = None) -> tuple[list[dict], list[tuple[str, str]]]:
    """The seven checks, from harness-visible data only. Returns (checks, problems); problems are hard fails.

    seen        rid -> record dict the engine was shown (its "visible" set)
    chart       the item patient's chart, as the port returns it (public record dicts)
    open_items  items still open for the patient (dicts: id, family, evidence, opened_at, owner_id)
    orig_item   the item this one escalates, if any (else None)
    card        the item patient's card from the port (name_en, dob, hkid, ...)
    staff       owner_id -> staff dict (name, joined, left)
    rostered    callable(date) -> bool: is the owner working that day
    """
    checks, problems = [], []
    cites = [c for s in steps for c in s["cites"]]
    cited = list(dict.fromkeys(list(evidence) + [c["record"] for c in cites]))

    # 1. every cited record was in the engine's view when it decided
    if not evidence:
        problems.append(("no_evidence", f"item for {patient_id} ({family}) cites no evidence"))
    unseen = [rid for rid in cited if rid not in seen]
    for rid in unseen:
        where = "evidence cites" if rid in evidence else "reasoning cites"
        problems.append(("unsupported_evidence", f"{where} {rid}, which the engine could not have seen"))
    checks.append({"id": "evidence", "status": "fail" if unseen else "pass",
                   "detail": f"{len(unseen)} of {len(cited)} cited records were not visible when the item was opened"
                   if unseen else ("The cited record was visible when the item was opened" if len(cited) == 1
                                   else f"All {len(cited)} cited records were visible when the item was opened")})

    # 2. quotes appear word for word in the record they cite
    quotes = [(c["record"], c["quote"]) for c in cites if c.get("quote")]
    bad = []
    for rid, q in quotes:
        r = seen.get(rid)
        if r is not None and norm(q) not in norm(r.get("text") or ""):
            bad.append((rid, q))
            problems.append(("unsupported_claim", f"quote not found in {rid}: {q[:80]!r}"))
    if not quotes:
        checks.append({"id": "quotes", "status": "na", "detail": "No quoted claims to check"})
    else:
        checks.append({"id": "quotes", "status": "fail" if bad else "pass",
                       "detail": f"{len(bad)} of {len(quotes)} quotes are not in the cited record: {bad[0][1][:60]!r}"
                       if bad else f"{len(quotes)} quote{'s' if len(quotes) != 1 else ''} found word for word in the cited records"})

    # 3. the evidence names the same patient as the item (visible data only: filing + printed identifiers)
    res = [identity_of(card, seen[rid], patient_id) for rid in evidence if rid in seen]
    st = _worst([s for s, _ in res])
    detail = next((d for s, d in res if s == st), "No visible evidence to check") if res else "No visible evidence to check"
    if len(res) > 1 and st == "pass":
        detail = f"All {len(res)} documents agree with this patient; " + detail
    checks.append({"id": "identity", "status": st, "detail": detail})

    # 4. chart searches the engine reports are re-run by the platform
    searches = [s["search"] for s in steps if s.get("search")]
    if not searches:
        checks.append({"id": "searches", "status": "na", "detail": "No chart searches stated"})
    else:
        wrong = []
        for q in searches:
            since = _dt(q["since"])
            n = sum(1 for r in chart
                    if r.get("kind") in q["kinds"] and since <= _dt(r["available_at"]) <= now
                    and (not q["contains_any"] or any(c in (r.get("text") or "") for c in q["contains_any"])))
            if n != q["found"]:
                wrong.append((q, n))
        if wrong:
            q, n = wrong[0]
            checks.append({"id": "searches", "status": "fail",
                           "detail": f"Engine reported {q['found']} {'/'.join(q['kinds'])} since {q['since'][:10]}; the chart has {n}"})
        else:
            checks.append({"id": "searches", "status": "pass",
                           "detail": f"{len(searches)} chart search{'es' if len(searches) != 1 else ''} re-run with the same result"})

    # 5. not a duplicate of an item still open for this patient
    ev = set(evidence)
    dup = [it for it in open_items if it.get("family") == family and ev & set(it.get("evidence") or ())
           and it.get("id") != escalates]
    if escalates is not None and not dup and orig_item is not None:
        who = staff.get(orig_item.get("owner_id"), {}).get("name", orig_item.get("owner_id"))
        checks.append({"id": "duplicate", "status": "pass",
                       "detail": f"Escalation of the item opened {_dt(orig_item['opened_at']):%d %b} for {who}, linked to it"})
    else:
        checks.append({"id": "duplicate", "status": "warn" if dup else "pass",
                       "detail": f"Cites the same evidence as an open item from {_dt(dup[0]['opened_at']):%d %b %H:%M}" if dup
                       else "No open item for this patient cites the same evidence"})

    # 6. the owner works here and is rostered soon enough for the urgency
    doc = staff.get(owner_id, {})
    name = doc.get("name", owner_id)
    left = doc.get("left")
    joined = doc.get("joined")
    d0 = now.date()
    win = WINDOW_DAYS.get(int(urgency), 7)
    nxt = next((d0 + timedelta(days=i) for i in range(0, 61) if rostered(d0 + timedelta(days=i))), None)
    left_d = _dt(left).date() if left else None
    join_d = _dt(joined).date() if joined else None
    if left_d and d0 > left_d:
        checks.append({"id": "owner", "status": "fail", "detail": f"{name} left the clinic after {left_d:%d %b %Y}; nobody will see this item"})
    elif nxt is not None and (nxt - d0).days <= win:
        when = "today" if nxt == d0 else f"on {nxt:%a %d %b}"
        starts = f" (starts at the clinic on {join_d:%d %b})" if join_d and join_d > d0 else ""
        checks.append({"id": "owner", "status": "pass", "detail": f"{name} is rostered {when}{starts}"})
    else:
        when = f"until {nxt:%a %d %b}" if nxt else "in the next 60 days"
        checks.append({"id": "owner", "status": "warn",
                       "detail": f"{name} is not rostered {when}; urgency {urgency} needs action within {win + 1} day{'s' if win else ''}"})

    # 7. a reasoning trace that cites the evidence and states a working problem
    if not steps:
        checks.append({"id": "reasoning", "status": "warn", "detail": "No reasoning trace: the reviewer sees only the reason line"})
    else:
        cited_by_steps = {c["record"] for c in cites}
        loose = [rid for rid in evidence if rid not in cited_by_steps]
        has_problem = any(s["step"] == "assessment" for s in steps)
        if loose:
            checks.append({"id": "reasoning", "status": "warn", "detail": f"{len(loose)} evidence record{'s are' if len(loose) != 1 else ' is'} not discussed in the reasoning"})
        elif not has_problem:
            checks.append({"id": "reasoning", "status": "warn", "detail": "No working problem stated"})
        else:
            checks.append({"id": "reasoning", "status": "pass", "detail": f"{len(steps)} steps; every evidence record discussed; working problem stated"})
    return checks, problems


# --------------------------------------------------------------------------------------- the wrapper
class VerifyingClinic:
    """A `ClinicPort` that runs the seven platform checks on every item the engine opens, from port-visible data.

    Wrap the real port and give the wrapper to the engine. Reads are passed through and every record handed to the
    engine is remembered (the port's stand-in for the simulator's "visible" set). `open_item` runs the checks,
    records them in `observations` (one entry per item, with its id, checks and any problems) and any hard fail in
    `hard_fails`, then opens the item on the wrapped port unchanged. Writes other than open_item are delegated
    untouched: the wrapper adds no authority the engine does not already have.
    """

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "seen", {})
        object.__setattr__(self, "observations", [])
        object.__setattr__(self, "hard_fails", [])

    # the port's clock lives on the wrapped port; proxy it both ways so a driver can advance either.
    @property
    def now(self):
        return self._inner.now

    @now.setter
    def now(self, value):
        self._inner.now = value

    def __getattr__(self, name):          # everything not defined here (reads, raise, alarm, attest, ...) passes through
        return getattr(self._inner, name)

    # ---- reads: pass through, remembering every record the engine is shown
    def _remember(self, records):
        for r in records:
            rid = r.get("id")
            if rid is not None:
                self.seen[rid] = r
        return records

    def cms_since(self, cursor=0):
        recs, cur = self._inner.cms_since(cursor)
        return self._remember(recs), cur

    def portal_since(self, source, cursor=0):
        recs, cur = self._inner.portal_since(source, cursor)
        return self._remember(recs), cur

    def ehealth(self, patient_id):
        return self._remember(self._inner.ehealth(patient_id))

    def chart(self, patient_id):
        return self._remember(self._inner.chart(patient_id))

    # ---- the one write we observe
    def open_item(self, patient_id, owner_id, family, reason, evidence, urgency=1, due=None, reasoning=None,
                  escalates=None):
        now = self._inner.now
        ev = tuple(dict.fromkeys(evidence or ()))
        steps = clean_steps(reasoning)
        checks, problems = check_item(self.seen, self._chart(patient_id), self._open_items(patient_id),
                                      self._item(escalates), self._card(patient_id), self._staff(),
                                      self._rostered(owner_id), now, patient_id, owner_id, family, ev, steps,
                                      int(urgency), escalates)
        obs = {"patient_id": patient_id, "owner_id": owner_id, "family": family, "urgency": int(urgency),
               "checks": checks, "problems": problems,
               "passed": all(c["status"] in ("pass", "na") for c in checks)}
        self.observations.append(obs)
        stamp = now.isoformat(timespec="minutes") if hasattr(now, "isoformat") else str(now)
        for kind, detail in problems:
            self.hard_fails.append({"time": stamp, "kind": kind, "detail": detail, "patient_id": patient_id})
        iid = self._inner.open_item(patient_id, owner_id, family, reason, list(ev), urgency, due,
                                    reasoning=reasoning, escalates=escalates)
        obs["item"] = iid
        return iid

    # ---- small port queries used by the checks (read the wrapped port directly, never the engine's cursors)
    def _chart(self, patient_id):
        try:
            return self._inner.chart(patient_id)
        except Exception:
            return []

    def _open_items(self, patient_id):
        try:
            return [it for it in self._inner.items(status="open") if it.get("patient_id") == patient_id]
        except Exception:
            return []

    def _item(self, iid):
        if not iid:
            return None
        try:
            return next((it for it in self._inner.items() if it.get("id") == iid), None)
        except Exception:
            return None

    def _card(self, patient_id):
        try:
            return next((p for p in self._inner.patients() if p.get("id") == patient_id), {})
        except Exception:
            return {}

    def _staff(self):
        try:
            return {s["id"]: s for s in self._inner.staff()}
        except Exception:
            return {}

    def _rostered(self, owner_id):
        def ok(day):
            try:
                return owner_id in self._inner.roster(day).get("doctors", [])
            except Exception:
                return False
        return ok

    # ---- observability roll-up
    def summary(self) -> dict:
        obs = self.observations
        return {"items": len(obs), "passed_all_checks": sum(1 for o in obs if o["passed"]),
                "hard_fails": len(self.hard_fails),
                "by_check": {c: {s: sum(1 for o in obs for ck in o["checks"] if ck["id"] == c and ck["status"] == s)
                                 for s in STATUSES} for c in CHECKS}}
