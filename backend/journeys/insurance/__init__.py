"""The insurance journey: get insurer and corporate-plan paperwork in front of the right doctor before it lapses.

After a visit, insurers send a clinic claim forms (attending physician's statements), pre-authorisation requests
for referrals and procedures, requests for medical reports, and decisions on claims. In a private clinic these sit
in a tray or a fax pile until the patient chases them; the claim lapses, the procedure is not pre-approved, or the
patient pays. This journey reads each one as it lands and, when the doctor has something to do, opens one item:

    perceive  every insurance form or insurer letter that reaches the clinic system (paper, fax, scanned)
    identify  the patient from the chart it is filed to, only if its name and date of birth agree; otherwise the
              clinic index by name + date of birth (+ HKID), and only on exactly one match
    decide    what the insurer asks for and by when (its own words), whether it needs the doctor at all
              (a paid claim does not), and whether the clinic has already done it (a chart search the platform
              re-runs); routine paperwork waits one engine run so a same-desk completion is not raised
    act       open one item with the insurer's request and deadline quoted word for word; carry the clinical
              urgency of a linked referral, not the paperwork's; flag a records request that has no signed
              authorisation
    watch     raise the item to urgent two working days before the insurer's deadline if it is still open

It can only open items and raise them. It never fills in, signs, sends or releases anything, never contacts the
insurer or the patient, and never says whether something is covered. Only a doctor closes the item.

Reading is template- and keyword-based (no model call), which makes it immune to instructions written into a
form, but it will miss layouts it has not seen; a model reader belongs behind backend/extract/models.py.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from ...engine.rules import URGENCY_LABEL, Why, cite, day, line_with, when

FAMILY = "insurance_paperwork"
FAMILIES = {FAMILY: "insurer or corporate-plan paperwork the doctor must complete, check or resubmit by a deadline"}
KINDS = ("insurance_form", "insurer_letter")
DONE_WORDS = ["claim form", "Claim form", "pre-authorisation", "Pre-authorisation", "medical report", "Medical report",
              "Part B", "insurer", "Insurer"]

KIND_LABEL = {"claim_form": "claim form", "pre_auth": "pre-authorisation request", "insurer_query": "request for a medical report",
              "claim_declined": "declined claim"}


def _dmy(s: str) -> datetime:
    d, m, y = s.split("/")
    return datetime(int(y), int(m), int(d))


def working_days_after(t: datetime, n: int) -> datetime:
    d = t
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def working_days_between(a: datetime, b: datetime) -> int:
    n, d = 0, a.date()
    while d < b.date():
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def read_insurer_doc(text: str, received: datetime) -> dict | None:
    """What the insurer asks the doctor to do, and by when, in its own words. None if it asks nothing."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    title = " ".join(lines[1].split()) if len(lines) > 1 else ""
    head = " ".join(lines[0].split()) if lines else "the insurer"
    latin = re.sub(r"^[^A-Za-z]*", "", head) or head          # a bilingual letterhead: keep the English name
    insurer = re.sub(r"\bHk\b", "HK", latin.title()) if latin.isupper() else latin
    up = title.upper()
    if "PRE-AUTHORISATION" in up or "預先批核" in title:
        kind = "pre_auth"
    elif "MEDICAL REPORT" in up:
        kind = "insurer_query"
    elif "CLAIM FORM" in up or "PHYSICIAN" in up or re.search("索償|醫生證明", title):
        kind = "claim_form"
    elif re.search(r"Decision:\s*declined", text, re.I):
        kind = "claim_declined"
    else:
        kind = None
    asks = (line_with(text, r"(?i)please (return|provide|complete)|may be resubmitted|請於.*(交回|提供)")
            or line_with(text, r"(?i)to be completed by|由主診醫生填寫"))
    if kind is None or not asks:
        return None                      # e.g. "Decision: approved ... No further information is required."
    dated = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", text)
    t0 = _dmy(dated.group(1)) if dated else received
    m_by = re.search(r"\bby (\d{2}/\d{2}/\d{4})", text)
    m_wd = re.search(r"within (\d+) working days|(\d+)\s*個工作天內", text)
    m_d = re.search(r"within (\d+) days|(\d+)\s*日內", text)
    if m_by:
        due, dq = _dmy(m_by.group(1)), line_with(text, re.escape(m_by.group(0)))
    elif m_wd:
        due, dq = working_days_after(t0, int(m_wd.group(1) or m_wd.group(2))), line_with(text, re.escape(m_wd.group(0)))
    elif m_d:
        due, dq = t0 + timedelta(days=int(m_d.group(1) or m_d.group(2))), line_with(text, re.escape(m_d.group(0)))
    else:
        due, dq = None, None
    if due is not None:
        due = due.replace(hour=17, minute=0)
    m_name = re.search(r"Name:\s*([A-Z][A-Z'-]+),\s*([A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)*)", text)
    m_dob = re.search(r"DOB\s*(\d{2}/\d{2}/\d{4})", text)
    m_hk = re.search(r"\b([A-Z]\d{3})XXX\(X\)", text)
    m_doc = re.search(r"Attending doctor:\s*(.+)", text)
    m_ref = re.search(r"Our ref:\s*([A-Z]+-\d+)", text)
    auth = line_with(text, r"(?i)^Authori[sz]ation signed")
    return {"kind": kind, "insurer": insurer, "dated": min(t0, received), "ref": m_ref.group(1) if m_ref else None, "title": title, "title_q": line_with(text, re.escape(lines[1].strip()[:60])) if len(lines) > 1 else None,
            "asks": asks, "due": due, "due_q": dq,
            "sections": [q for q in (line_with(text, r"^Section B1"), line_with(text, r"^Section B\b|^Section B:")) if q],
            "reason_q": line_with(text, r"^Reason:"), "procedure_q": line_with(text, r"^Procedure requested"),
            "lapse_q": line_with(text, r"(?i)will lapse|will not be considered"),
            "auth_q": auth, "auth_missing": bool(auth and re.search(r"not enclosed|missing|to follow", auth, re.I)),
            "hdr": {"name": f"{m_name.group(1)} {m_name.group(2)}" if m_name else None,
                    "dob": _dmy(m_dob.group(1)).date().isoformat() if m_dob else None,
                    "hkid": m_hk.group(1) if m_hk else None},
            "doctor": " ".join(m_doc.group(1).split()) if m_doc else None}


class InsuranceJourney:
    """Cooperative mixin: class Suite(InsuranceJourney, ..., AgenticHarness). Calls super() first everywhere."""

    def reset(self, api):
        super().reset(api)
        self.ins_waiting: list[tuple[dict, datetime]] = []      # (record, first seen) held one run for a completion note
        self.ins_open: dict[str, dict] = {}                     # item id -> {"due", "pid", "label"}
        self.ins_seen: set[str] = set()
        self.ins_refs: set[tuple] = set()                      # (patient, insurer, insurer's ref) already raised
        self.ins_stats = Counter()

    def on_cms(self, api, r: dict):
        super().on_cms(api, r)
        if r["kind"] in KINDS and r["id"] not in self.ins_seen:
            self.ins_seen.add(r["id"])
            self.ins_waiting.append((r, api.now))
            self.ins_stats["documents"] += 1

    def tick(self, api):
        super().tick(api)
        keep = []
        for r, seen in self.ins_waiting:
            if not self.ins_consider(api, r, seen):
                keep.append((r, seen))
        self.ins_waiting = keep
        self.ins_watch(api)

    # ------------------------------------------------------------------------------ decide and act
    def ins_consider(self, api, r: dict, seen: datetime) -> bool:
        """True once the record is dealt with (item opened or nothing to do); False to look again next run."""
        t = datetime.fromisoformat(r["available_at"])
        doc = read_insurer_doc(r["text"], t)
        if doc is None:
            self.ins_stats["no_action_needed"] += 1
            return True
        pid, how = self.identify(api, r, doc["hdr"])
        if not pid:
            self.ins_stats["unmatched"] += 1        # stays out of every chart; the platform's unfiled list shows it
            return True
        key = (pid, doc["insurer"], doc["ref"]) if doc["ref"] else None
        if key and key in self.ins_refs:
            self.ins_stats["resent_copy"] += 1      # the same request again (a re-sent fax): one item is enough
            return True
        short = doc["due"] is not None and working_days_between(api.now, doc["due"]) <= 3
        if api.now == seen and not short:
            return False                             # routine: give the desk one run to record a completion
        since = min(t - timedelta(minutes=1), doc["dated"])    # a desk note can be typed before the paper is scanned
        n, search = self.chart_search(api, pid, ["action_note"], since, DONE_WORDS)
        if n:
            self.ins_stats["already_done"] += 1
            return True
        self.ins_open_item(api, r, doc, pid, how, t, search)
        if key:
            self.ins_refs.add(key)
        return True

    def ins_linked_referral(self, pid: str, t: datetime) -> dict | None:
        refs = [x for x in self.referrals if x["pid"] == pid and t - timedelta(days=45) <= x["t"] <= t]
        return refs[-1] if refs else None

    def ins_open_item(self, api, r, doc, pid, how, t, search):
        kind, insurer, due = doc["kind"], doc["insurer"], doc["due"]
        label = KIND_LABEL[kind]
        ref = self.ins_linked_referral(pid, t) if kind == "pre_auth" else None
        days_left = (due - api.now).days if due else None
        if ref and ref["urgent"]:
            urgency = 3
        elif kind == "insurer_query" and doc["auth_missing"]:
            urgency = 2
        elif due is not None and days_left <= 7:
            urgency = 2
        else:
            urgency = 1
        hint = self.doc_id(doc["doctor"]) or (ref["owner"] if ref else None)
        owner, route_txt = self.route(api, hint, pid, urgency)
        item_due = due if due is not None else api.now + timedelta(days=14)
        if item_due < api.now + timedelta(hours=4):
            item_due = api.now + timedelta(hours=4)
        w = Why()
        w.add("source", f"{label.capitalize()} from {insurer}, received by {self.chan_label(r['channel'])} on {when(t)}.",
              cite(r["id"], doc["title_q"]))
        w.add("identity", how, *self.id_cites(r["id"], r["text"]))
        finding = [cite(r["id"], doc["asks"])]
        if doc["due_q"] and doc["due_q"] != doc["asks"]:
            finding.append(cite(r["id"], doc["due_q"]))
        for q in (doc["reason_q"], doc["procedure_q"], *doc["sections"][:1], doc["lapse_q"]):
            if q:
                finding.append(cite(r["id"], q))
        due_txt = f"by {day(due)}" if due else "with no return date on the document (taken as 14 days)"
        asked = {"claim_form": "to complete and sign the doctor's part of its claim form",
                 "pre_auth": "to complete the medical section of its pre-authorisation request",
                 "insurer_query": "for a medical report", "claim_declined": "to complete the missing section so the claim can be resubmitted"}[kind]
        w.add("finding", f"The insurer asks the doctor {asked}, {due_txt}.", *finding)
        evidence = [r["id"]]
        ctx = []
        if ref:      # cited, not evidence: the referral is the core engine's loop, and the doctor's action here is on the form
            q = line_with(ref["text"], r"^REFERRAL LETTER")
            ctx.append(f"It is for the {'URGENT ' if ref['urgent'] else ''}referral written on {day(ref['t'])}.")
            w.add("context", " ".join(ctx), cite(ref["rid"], q), cite(ref["rid"], line_with(ref["text"], r"^Reason:")))
        if doc["auth_q"]:
            w.add("context", "The patient's signed authorisation is NOT with the request: nothing can be released until it is on file."
                  if doc["auth_missing"] else "The insurer says the patient's signed authorisation is enclosed.", cite(r["id"], doc["auth_q"]))
        w.add("context", f"Chart searched for a note that this was already done, since the date on the letter: {search['found']} found.", search=search)
        if ref and ref["urgent"]:
            problem = (f"Insurer paperwork for an urgent referral. The referral is urgent on clinical grounds: book it now and do not hold "
                       f"it for the insurer's pre-approval; the form only decides who pays.")
        elif kind == "insurer_query" and doc["auth_missing"]:
            problem = "A request to release medical information without the patient's signed authorisation: ask the insurer or the patient for it first."
        elif kind == "claim_declined":
            problem = "A claim declined for an incomplete doctor's section; it can be resubmitted within the insurer's window."
        else:
            problem = f"Outstanding insurer paperwork: the {label} needs the doctor before the insurer's deadline."
        w.add("assessment", problem + " The agent has not filled in, signed, sent or released anything, and has not told the "
              "patient whether anything is covered.")
        w.add("loop_status", f"Open. Due {day(item_due)}" + (f" ({days_left} days)." if days_left is not None else "."))
        w.add("routing", route_txt)
        what = {"claim_form": "Complete and sign the doctor's part of the form",
                "pre_auth": "Complete the medical section of the pre-authorisation",
                "insurer_query": "Check the authorisation, then decide what to release" if doc["auth_missing"] else "Write the medical report",
                "claim_declined": "Complete the missing section and resubmit"}[kind]
        w.add("request", f"{what}, then close this item. Opened for {self.nm(owner)} at urgency {urgency} ({URGENCY_LABEL[urgency]}); "
              f"due {when(item_due)}. Only a doctor can close it.")
        reason = {"claim_form": f"Insurer claim form to complete for {insurer} {due_txt}",
                  "pre_auth": f"Pre-authorisation for {'an urgent' if ref and ref['urgent'] else 'a'} referral: return {due_txt}"
                              + ("; the urgent referral must not wait for it" if ref and ref["urgent"] else ""),
                  "insurer_query": f"{insurer} asks for a medical report {due_txt}"
                                   + ("; no signed authorisation from the patient is enclosed" if doc["auth_missing"] else ""),
                  "claim_declined": f"Claim declined by {insurer} (doctor's section incomplete): resubmit {due_txt}"}[kind]
        iid = api.open_item(pid, owner, FAMILY, reason, evidence, urgency, item_due, reasoning=w.steps)
        self.ins_open[iid] = {"due": due, "pid": pid, "label": label}
        self.ins_stats["items"] += 1

    def ins_watch(self, api):
        """Two working days before the insurer's deadline, raise anything still open to urgent."""
        if api.now.hour != 8 or not self.ins_open:
            return
        status = {it["id"]: it for it in api.items("open")}
        for iid, x in list(self.ins_open.items()):
            it = status.get(iid)
            if it is None:
                self.ins_open.pop(iid)                    # a doctor has dealt with it
                continue
            if x["due"] is not None and it["urgency"] < 3 and working_days_between(api.now, x["due"]) <= 2:
                api.raise_urgency(iid, 3, f"The insurer's deadline for the {x['label']} is {day(x['due'])} and it is still open.")
                self.ins_stats["raised"] += 1



JOURNEY = InsuranceJourney
NAME = "insurance"
SUMMARY = ("Insurer and corporate-plan paperwork (claim forms, pre-authorisations, medical-report requests, declined "
           "claims) put in front of the right doctor before the insurer's deadline, with the request quoted word for word")
