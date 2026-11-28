"""Journey: inpatient and outpatient follow-on.

After a hospital stay or a specialist visit the family doctor is asked to do things, and the asks arrive in
places nobody watches: a discharge summary in eHealth or in the patient's hand, a faxed specialist letter, an
HA appointment booked months too late, a patient's reply to the care line. This journey reads them, opens one
item per document for the doctor with the asks quoted word for word, and keeps watching until the follow-on has
happened. It only opens items and raises urgency; only a doctor closes an item.

What it adds to the core engine:

  discharge and A&E summaries   reads the follow-up, pending-at-discharge and discharge-medicine lines; knows
                                when the hospital keeps the follow-up itself ("No need for family doctor follow
                                up"); checks the patient the summary names, not just the chart it was filed to
  admission watch               a phone memo or care-line message saying the patient was admitted, or an urgent
                                HA referral, puts the patient on a daily eHealth pull for 21 days (when the
                                clinic system has eHealth access), so the summary or booking is seen in a day
                                rather than at the next 14-day sweep
  specialist letters            reads the letter's asks (repeat tests, chase a biopsy, monitor a medicine) and
                                stays quiet when the patient is discharged back with nothing to do
  HA SOPC bookings              an urgent referral booked as routine, or months out, is raised as urgent
  care-line replies             red flags (breathless, chest pain, fainting, bleeding...) at urgency 4 (pages), quoted
                                in the patient's own words; "no better" at 3; a missed or too-distant specialist
                                appointment re-opens the referral; "much better" still opens at 1 for the doctor to close
  second attempts               core v1.1 re-opens an actioned item when the clinic could not reach the patient,
                                and escalates items nobody actions (day 7 raise, day 14 page or backup)

Cooperative mixin (see /journeys conventions): every hook calls super() first; core's naive discharge and
message rules are skipped only for records this journey `claims`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ...engine.ports import FAMILIES as PORT_FAMILIES
from ...engine.trace import URGENCY_LABEL, Why, cite, day, line_with, when

NAME = "followon"
SUMMARY = ("After a hospital stay or a specialist visit: discharge summaries, specialist letters, HA SOPC bookings and "
           "care-line replies become one item per document, asks quoted word for word.")
FAMILIES = {
    "followup_recommended": "a hospital or specialist asks the family doctor to follow up (subtypes inpatient, "
                            "outpatient, red_flag, not_better)",
    "test_never_resulted": "a result still pending at discharge or a biopsy to chase",
    "drug_monitoring": "monitoring a medicine started in hospital (e.g. INR after new warfarin)",
    "referral": "an HA specialist booking that does not match the referral's urgency, or a missed appointment",
}
MED_FAMILY = "med_change" if "med_change" in PORT_FAMILIES else "followup_recommended"

WATCH_DAYS = 21
URGENT_SOPC_MAX_DAYS = 56          # an urgent HA referral booked later than 8 weeks is a mismatch

# ------------------------------------------------------------------------------------- reading text
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "a": 1}


def _days(n: str, unit: str) -> int:
    k = int(n) if n.isdigit() else NUM.get(n.lower(), 1)
    return k * (7 if unit.lower().startswith("week") else 30 if unit.lower().startswith("month") else 1)


def clause(text: str, m: re.Match) -> str:
    """The clause around a match, verbatim (split on sentence and clause punctuation, English or Chinese)."""
    a, b = m.start(), m.end()
    while a > 0 and text[a - 1] not in "。，,.!！？?;；\n(":
        a -= 1
    while b < len(text) and text[b] not in "。，,.!！？?;；\n)":
        b += 1
    return " ".join(text[a:b].split()).strip()


def sentence(text: str, m: re.Match) -> str:
    """The sentence around a match, verbatim, without a leading 'Follow-up:'-style label."""
    a, b = m.start(), m.end()
    while a > 0 and text[a - 1] not in ".;\n":
        a -= 1
    while b < len(text) and text[b] not in ".;\n":
        b += 1
    s = " ".join(text[a:b].split()).strip()
    return re.sub(r"^(Follow-up|Disposal|Pending at discharge|Medications on discharge): ", "", s)


GP_NEGATION = re.compile(r"no need for (?:the )?family doctor|no action (?:is )?(?:required|needed) from (?:the )?family doctor|"
                         r"family doctor follow[- ]?up (?:is )?not (?:required|needed)", re.I)
GP_WHO = re.compile(r"family (?:doctor|physician)|\bGP\b|your (?:own )?doctor|referring doctor|private doctor", re.I)   # not GOPC: that is HA's own clinic
ASK_VERB = re.compile(r"\b(?:please|kindly|arrange|follow(?:ed)? up|review|repeat|check|see|monitor|titrate|adjust|recheck|chase)\b", re.I)
DO_VERB = re.compile(r"\b(?:repeat|check|recheck|review|monitor|titrate|adjust|remove|stitches off|wound)\b", re.I)
REVIEW_WORD = re.compile(r"follow(?:ed)? up|review|\bsee\b", re.I)
CONDITIONAL = re.compile(r"\bif (?:symptoms?|needed|required|necessary|worse|worsen|recur|persist|not better|no improvement|not improving|unwell)|"
                         r"\bprn\b|as needed|should (?:symptoms|it) recur", re.I)
HOSPITAL_KEPT = re.compile(r"\bSOPC\b|\bGOPC\b|appointment given|as booked|pacemaker clinic|clinic in \d+", re.I)
PENDING = re.compile(r"\bpending\b|awaited|to follow\b|to be followed up|not yet available|outstanding", re.I)
INR = re.compile(r"check INR in (\d+|one|two|three|four|six|a)\s+(day|week)s?", re.I)
MED_CHANGE = re.compile(r"\((?:NEW|new|dose increased|dose reduced)\)|\bSTOPPED\b|\bstopped\b|\bincreased to\b|\breduced to\b|\bStarted\b|\bstarted on\b", re.I)
ANTICOAG = re.compile(r"warfarin|apixaban|rivaroxaban|dabigatran|edoxaban|enoxaparin", re.I)

LETTER_ASK = re.compile(r"(?:^|(?<=[.;\n] ))(?:please|kindly|I would be grateful if you could|would you)\b[^.;\n]+", re.I | re.M)
LETTER_DISCHARGED = re.compile(r"discharged back to your care|no further follow[- ]?up (?:is )?(?:needed|required)|no action (?:needed|required)", re.I)
CHASE = re.compile(r"\bchase\b|biopsy|histology|result(?:s)? (?:is |are )?(?:pending|awaited)", re.I)
INTERVAL = re.compile(r"\b(?:in|within|after)\s+(\d+|one|two|three|four|six|a)\s+(day|week|month)s?", re.I)

# care-line replies (Cantonese and English); a term preceded by a negation word does not count
RED_FLAGS = [r"好喘", r"氣促", r"喘", r"透唔到氣", r"呼吸困難", r"胸口痛", r"胸痛", r"心口痛", r"坐住瞓", r"腳腫", r"暈低", r"暈倒", r"昏",
             r"吐血", r"便血", r"屙血", r"黑色大便", r"高燒", r"發高燒", r"神志不清", r"breathless", r"short of breath",
             r"chest pain", r"sleep(?:ing)? sitting up", r"swollen", r"faint", r"passed out", r"vomit(?:ing|ed)? blood",
             r"blood in", r"black stool", r"high fever", r"confus"]
NOT_BETTER = [r"冇好到", r"未好", r"差咗", r"都係咁", r"唔見好", r"冇改善", r"仲有", r"not getting better", r"no better",
              r"not better", r"worse", r"about the same"]
FINE = [r"好返", r"好多", r"冇問題", r"冇事", r"much better", r"feeling fine", r"all good"]
APPT = r"專科|覆診|appointment|SOPC|clinic"
MISSED = [r"唔記得", r"錯過", r"冇去到", r"冇去", r"取消", r"冇收到新", r"missed", r"forgot", r"cancel", r"haven'?t heard"]
TOO_LONG = [r"太耐", r"太遲", r"後年", r"明年", r"too long", r"so late", r"year after next"]
ADMITTED = re.compile(r"\badmitted to\b|入咗醫院|入院|住院", re.I)
NEG = re.compile(r"(?:冇|沒有|沒|唔|無|no |not |without |never )\S{0,2}$", re.I)


def _dmy(m: re.Match, hour: int = 0) -> datetime | None:
    """dd/mm/yyyy from a match (groups 1-3), or None for a date that does not exist (a typo or OCR noise): one bad
    document must not stop the engine for the whole clinic."""
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), hour)
    except ValueError:
        return None


def _hit(text: str, pats) -> re.Match | None:
    for p in pats:
        for m in re.finditer(p, text, re.I):
            if not NEG.search(text[max(0, m.start() - 4):m.start()]):
                return m
    return None


def classify_reply(text: str) -> tuple[str, re.Match | None]:
    """One of red_flag, appt_missed, appt_too_long, not_better, fine, unclear."""
    if (m := _hit(text, RED_FLAGS)):
        return "red_flag", m
    if re.search(APPT, text, re.I):
        if (m := _hit(text, MISSED)):
            return "appt_missed", m
        if (m := _hit(text, TOO_LONG)):
            return "appt_too_long", m
    if (m := _hit(text, NOT_BETTER)):
        return "not_better", m
    if (m := _hit(text, FINE)):
        return "fine", m
    return "unclear", None


def read_discharge(text: str) -> dict:
    """The family doctor's asks in an HA discharge or A&E summary, each as a verbatim quote."""
    out = {"asks": [], "review_days": None, "pending": None, "inr_days": None, "med_changes": [], "negated": None,
           "anticoag": False, "soonest_days": None}
    fu = line_with(text, r"^(Follow-up|Disposal):") or ""
    pend = line_with(text, r"^Pending at discharge:")
    meds = line_with(text, r"^Medications on discharge:") or ""
    if (m := GP_NEGATION.search(fu)):
        out["negated"] = clause(fu, m)
    # every sentence of the follow-up line: an ask names the family doctor (or follows one that does) and asks for
    # something; a conditional ("if symptoms recur") or a hospital-kept appointment is not an ask
    body = fu.split(":", 1)[1] if ":" in fu else fu
    gp_line = bool(GP_WHO.search(body))
    for m in re.finditer(r"[^.;]+", body):
        sen = " ".join(m.group(0).split())
        if not sen or CONDITIONAL.search(sen) or GP_NEGATION.search(sen):
            continue
        if HOSPITAL_KEPT.search(sen) and not GP_WHO.search(sen):
            continue
        if (GP_WHO.search(sen) and ASK_VERB.search(sen)) or (gp_line and DO_VERB.search(sen) and INTERVAL.search(sen)):
            out["asks"].append(sen)
    days = [_days(m.group(1), m.group(2)) for a in out["asks"] for m in INTERVAL.finditer(a)]
    rv = next((a for a in out["asks"] if REVIEW_WORD.search(a) and INTERVAL.search(a)), None)
    if rv:
        m = INTERVAL.search(rv)
        out["review_days"] = _days(m.group(1), m.group(2))
    out["soonest_days"] = min(days) if days else None
    # the floor: never quieter than the core rule, except where the hospital keeps the follow-up or makes it conditional
    if not out["asks"] and not out["negated"] and not CONDITIONAL.search(body) and \
            re.search(r"family doctor", text, re.I) and re.search(r"follow up", text, re.I):
        out["asks"].append(" ".join(body.split()))
        out["fell_back"] = "core discharge rule"
    if (m := INR.search(fu)):
        out["inr_days"] = _days(m.group(1), m.group(2))
    if pend and PENDING.search(pend):
        out["pending"] = pend.split(":", 1)[1].strip()
    for part in re.split(r";\s*", meds.split(":", 1)[1] if ":" in meds else ""):
        part = part.strip().rstrip(".")
        if part and MED_CHANGE.search(part):
            out["med_changes"].append(part)
            out["anticoag"] |= bool(ANTICOAG.search(part))
    return out


def read_letter(text: str) -> dict:
    body = text.split("\n\n", 3)[-1] if "\n\n" in text else text
    body = body.split("Yours sincerely")[0]
    asks = [" ".join(m.group(0).split()) for m in LETTER_ASK.finditer(body)]
    asks = [a for a in asks if not re.match(r"(?i)thank", a)]
    med = [clause(body, m) for m in re.finditer(r"[A-Z][a-z]+ (?:increased|reduced|started|stopped)[^.;]*|Started [^.;]+|Stopped [^.;]+", body)]
    chase = [a for a in asks if CHASE.search(a)]
    days = [_days(m.group(1), m.group(2)) for a in asks for m in INTERVAL.finditer(a)]
    return {"asks": asks, "med_changes": med, "chase": chase, "discharged_back": bool(LETTER_DISCHARGED.search(body)),
            "soonest_days": min(days) if days else None}


def header_identity(text: str) -> dict:
    out = {}
    m = re.search(r"(?:Patient|Re|Name):\s*([A-Z][A-Z'-]+),\s*([A-Z][a-z]+(?: [A-Z][a-z]+)*)", text)
    if m:
        out["name"] = f"{m.group(1)} {m.group(2)}"
        out["line"] = line_with(text, r"^(Patient|Re|Name):")
    m = re.search(r"\b([A-Z]\d{3})XXX\(X\)", text)
    if m:
        out["hkid"] = m.group(1)
    m = re.search(r"DOB:?\s*(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        out["dob"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return out


class FollowonJourney:
    """Mixin: put before the engine class, e.g. class H(FollowonJourney, AgenticHarness)."""
    journey = "followon"

    def reset(self, api):
        super().reset(api)
        self._case_fresh = False
        self.fo_watch: dict[str, datetime] = {}            # pid -> watch until (daily eHealth pull)
        self.fo_seen: set[str] = set()                    # eHealth record ids already read by this journey
        self._use_case()
        self.fo_docs: dict[str, list[dict]] = {}          # pid -> discharge summaries / letters read (for replies)
        self.fo_items: dict[str, str] = {}                # record id -> item id opened by this journey
        self.fo_stats = {"documents": 0, "items": 0, "quiet": 0, "watch_pulls": 0}

    def _use_case(self):
        """Shared log from rules v1.1, or a private log cleared on each reset. Later calls do not clear it."""
        from backend.case.wire import ensure_case
        if getattr(self, "_case_ready", False) and getattr(self, "case", None) is not None:
            return self.case
        if getattr(self, "case", None) is not None and getattr(self, "_case_fresh", False):
            return self.case
        case = ensure_case(self, reset=True)
        self._case_fresh = True
        return case

    def _fo_refs(self, pid, as_of=None):
        case = getattr(self, "case", None)
        if case is None:
            return []
        from backend.case.wire import referral_rows
        return referral_rows(case, pid, as_of, ha_only=True)

    def _note_discharge(self, r, pid, t, med_lines):
        from backend.case.wire import note_medicine_lines, note_stay
        log = getattr(self, "case", None) or self._use_case()
        if r.get("kind") in ("discharge_summary", "aed_summary"):
            note_stay(log, r, pid, t)
        note_medicine_lines(log, r, pid, t, med_lines or [])

    # --------------------------------------------------------------------------------- routing
    def bid(self, api, r: dict) -> float:
        """Inbound care-line messages from a patient in this journey's care: 3 within 30 days of a discharge or
        specialist letter this journey read, or about a specialist appointment after an HA referral; else 0
        (the followup journey is the fallback, so no message goes unread)."""
        if r.get("kind") != "message" or r.get("channel") != "careline" or r["data"].get("direction") == "out" or not r.get("patient_id"):
            return 0
        pid, t = r["patient_id"], datetime.fromisoformat(r["available_at"])
        recent = [x for x in self.fo_docs.get(pid, ()) if timedelta(0) <= t - x["t"] <= timedelta(days=30)]
        if recent:
            return 3
        if self._fo_refs(pid, t) and re.search(APPT, r["text"], re.I):
            return 3
        return 0

    def claims(self, api, r) -> bool:
        mine = r.get("kind") in ("discharge_summary", "aed_summary", "specialist_letter")
        return mine or super().claims(api, r)

    # --------------------------------------------------------------------------------- reading
    def on_cms(self, api, r: dict):
        super().on_cms(api, r)
        k, t = r["kind"], datetime.fromisoformat(r["available_at"])
        if k in ("discharge_summary", "aed_summary"):
            self.fo_discharge(api, r, t)
        elif k == "specialist_letter":
            self.fo_letter(api, r, t)
        elif k == "message" and r["channel"] == "careline" and r["patient_id"] and r["data"].get("direction") != "out":
            if self.won(api, r, FollowonJourney) if hasattr(self, "won") else FollowonJourney.bid(self, api, r) > 0:
                self.fo_reply(api, r, t)
        elif k == "phone_memo" and r["patient_id"] and ADMITTED.search(r["text"]):
            self.fo_watch[r["patient_id"]] = t + timedelta(days=WATCH_DAYS)
        elif k == "referral_letter" and r["patient_id"] and re.search(r"Hospital Authority|SOPC|\bHA\b", r["text"] + str(r["data"])):
            from backend.case.wire import note_referral
            note_referral(self._use_case(), r, r["patient_id"], t, ha=True)
            if r["data"].get("urgency") == "urgent" or "URGENT" in r["text"]:
                self.fo_watch[r["patient_id"]] = t + timedelta(days=WATCH_DAYS)
        elif k == "appointment" and r["patient_id"]:
            self.closure_ev[r["patient_id"]].append((t, "appointment"))
            self.fo_appointment(api, r["patient_id"], r, t)

    def on_ehealth(self, api, pid: str, r: dict):
        """A record read from eHealth (by core's sweep or this journey's watch)."""
        super().on_ehealth(api, pid, r)
        if r["id"] in self.fo_seen:
            return
        self.fo_seen.add(r["id"])
        self.ehealth_seen.add(r["id"])                    # the engine has read it: core's sweep need not read it again
        t = datetime.fromisoformat(r["available_at"])
        if r["kind"] in ("discharge_summary", "aed_summary") and t > api.now - timedelta(days=21):
            if not any(c > t for c in self.consults.get(pid, ())):
                self.fo_discharge(api, r, t, pid=pid)
        elif r["kind"] == "appointment":
            self.fo_appointment(api, pid, r, t)

    def tick(self, api):
        super().tick(api)
        now = api.now
        if now.hour == 8:
            self.fo_pull_watched(api)

    def fo_pull_watched(self, api):
        for pid, until in list(self.fo_watch.items()):
            if api.now > until:
                del self.fo_watch[pid]
                continue
            if not self.pts.get(pid, {}).get("ehealth_consent"):
                del self.fo_watch[pid]
                continue
            try:
                recs = api.ehealth(pid)
            except PermissionError:
                self.fo_watch.clear()                     # no programmatic eHealth on this system
                return
            except self._down:
                return                                    # core's sweep raises the eHealth alarm
            self.fo_stats["watch_pulls"] += 1
            for r in recs:
                self.on_ehealth(api, pid, r)

    # ----------------------------------------------------------------------------- identity
    def fo_identify(self, api, r: dict) -> tuple[str | None, str, list[dict]]:
        hdr = header_identity(r["text"])
        cites = [cite(r["id"], hdr.get("line"))] if hdr.get("line") else [cite(r["id"])]
        filed = r.get("patient_id")
        if filed and filed in self.pts:
            p = self.pts[filed]
            ok_name = not hdr.get("name") or set(hdr["name"].upper().split()) <= set(p["name_en"].upper().split())
            ok_id = (not hdr.get("hkid") or p["hkid"].startswith(hdr["hkid"])) and (not hdr.get("dob") or p["dob"] == hdr["dob"])
            if ok_name and ok_id:
                which = " and ".join(x for x in ("name", "HKID" if hdr.get("hkid") else "", "date of birth" if hdr.get("dob") else "") if x)
                return filed, f"Filed to {p['name_en']}'s chart; the document's {which} agree with the chart.", cites
        if hdr.get("name") and (hdr.get("hkid") or hdr.get("dob")):
            m = api.find_patients(name=hdr["name"], dob=hdr.get("dob"), hkid=hdr.get("hkid"))
            if len(m) == 1:
                where = (f"Filed to {self.pname(filed)}'s chart, but the document names someone else" if filed else "Not filed to any chart")
                return m[0]["id"], (f"{where}. Exactly one patient in the clinic index matches the document's name and "
                                    f"{'HKID' if hdr.get('hkid') else 'date of birth'}: {m[0]['name_en']}. The item is opened for that patient; "
                                    f"the document should be moved to the right chart."), cites
        return None, "", cites

    # ----------------------------------------------------------------------------- documents
    def fo_discharge(self, api, r: dict, t: datetime, pid: str | None = None):
        self.fo_stats["documents"] += 1
        if pid is None:
            pid, how, idc = self.fo_identify(api, r)
            if pid is None:
                api.alarm("cms", f"{r['kind'].replace('_', ' ')} {r['id']} names a patient this clinic cannot match")
                return
        else:
            how = f"Read from eHealth for {self.pname(pid)}; the summary prints the patient's name and HKID."
            idc = [cite(r["id"], line_with(r["text"], r"^Patient:"))]
        d = read_discharge(r["text"])
        text = r["text"]
        self.fo_docs.setdefault(pid, []).append({"t": t, "rid": r["id"], "kind": r["kind"],
                                                 "quote": line_with(text, r"^(Principal diagnosis|Diagnosis):")})
        self._note_discharge(r, pid, t, d["med_changes"])
        gp_asks = d["asks"] or d["pending"] or d["inr_days"]
        if not gp_asks:
            self.fo_stats["quiet"] += 1
            return
        kind = "discharge summary" if r["kind"] == "discharge_summary" else "A&E attendance summary"
        dis = t
        m = re.search(r"Discharged:\s*(\d{2})/(\d{2})/(\d{4})", text)
        if m:
            dis = _dmy(m, 12) or t
        w = Why()
        w.add("source", f"Hospital Authority {kind} arrived via {self.chan_label(r['channel'])} on {when(t)}.",
              cite(r["id"], line_with(text, r"^HOSPITAL AUTHORITY")))
        w.add("identity", how, *idc)
        fcites, parts = [], []
        for a in d["asks"]:
            fcites.append(cite(r["id"], a))
        if d["asks"]:
            parts.append("The hospital asks the family doctor: " + " / ".join(f"“{a}”" for a in d["asks"]))
        if d["pending"]:
            pq = d["pending"].split(";")[0].strip()
            fcites.append(cite(r["id"], pq))
            parts.append(f"A result was still pending at discharge: “{pq}”")
        if d.get("fell_back"):
            parts.append("This reader found no specific ask, so it fell back to the core discharge rule (the summary mentions the family doctor and follow-up)")
        if d["negated"]:
            parts.append(f"The summary also says “{d['negated']}”, but it lists asks for the family doctor, so it is raised")
        w.add("finding", ". ".join(parts) + ".", *fcites)
        if d["med_changes"]:
            w.add("context", "Medicines changed in hospital: " + "; ".join(f"“{x}”" for x in d["med_changes"][:4]) + ".",
                  *[cite(r["id"], x) for x in d["med_changes"][:4]])
        dx = line_with(text, r"^(Principal diagnosis|Diagnosis):")
        dxt = dx.split(":", 1)[1].strip() if dx else "the hospital episode"
        plan = []
        if d["review_days"]:
            plan.append(f"book the review by {day(dis + timedelta(days=d['review_days']))}")
        if d["inr_days"]:
            plan.append(f"INR by {day(dis + timedelta(days=d['inr_days']))}")
        if d["pending"]:
            plan.append("get the pending result from the hospital (eHealth or the ward) and act on it")
        if d["med_changes"]:
            plan.append("reconcile the medicine list with the discharge changes")
        if not plan:
            plan.append("do what the hospital asks")
        w.add("assessment", f"Working problem: follow-on after {dxt}. Plan: " + "; ".join(plan) + ".", cite(r["id"], dx))
        n, q = self.chart_search(api, pid, ["consult_note"], dis)
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart since the discharge on {day(dis)}.", search=q)
        soon = min(x for x in (d["review_days"], d["soonest_days"], d["inr_days"], 7 if d["pending"] else None, 14) if x)
        urgency = 2 if (soon <= 10 or d["pending"] or d["anticoag"] or d["inr_days"]) else 1
        due_days = max(1, min((dis + timedelta(days=soon) - api.now).days, 7 if urgency == 2 else 14))
        fam = (MED_FAMILY if d["med_changes"] else "followup_recommended") if (d["asks"] or d["inr_days"]) else "test_never_resulted"
        what = ("pending result" if d["pending"] and not d["asks"] else "review" if d["review_days"] else "asks")
        reason = f"After discharge ({dxt[:60]}): {what} for the family doctor" + (", result pending at discharge" if d["pending"] and d["asks"] else "") \
            + ("; medicines changed" if d["med_changes"] else "")
        # keyed on the text, as core does, so the same summary arriving twice (eHealth and a paper copy) is one item
        self.fo_raise(api, ("dc", pid, text[:120]), pid, fam, reason, [r["id"]], urgency, due_days, w, r)

    def fo_letter(self, api, r: dict, t: datetime):
        self.fo_stats["documents"] += 1
        pid, how, idc = self.fo_identify(api, r)
        if pid is None:
            return
        d = read_letter(r["text"])
        self.fo_docs.setdefault(pid, []).append({"t": t, "rid": r["id"], "kind": r["kind"], "quote": r["text"].split("\n", 1)[0]})
        self._note_discharge(r, pid, t, d["med_changes"])
        if not d["asks"]:
            self.fo_stats["quiet"] += 1
            return
        text = r["text"]
        spec = text.split("\n", 1)[0]
        w = Why()
        w.add("source", f"Letter from {spec} arrived via {self.chan_label(r['channel'])} on {when(t)}.", cite(r["id"], spec))
        w.add("identity", how, *idc)
        w.add("finding", "The specialist asks the family doctor: " + " / ".join(f"“{a}”" for a in d["asks"][:4]) + ".",
              *[cite(r["id"], a) for a in d["asks"][:4]])
        if d["med_changes"]:
            w.add("context", "Medicines changed by the specialist: " + "; ".join(f"“{x}”" for x in d["med_changes"][:3]) + ".",
                  *[cite(r["id"], x) for x in d["med_changes"][:3]])
        plan = []
        if d["chase"]:
            plan.append("chase the outstanding result with the specialist if it has not arrived")
        if d["soonest_days"]:
            plan.append(f"book what the letter asks for by about {day(t + timedelta(days=d['soonest_days']))}")
        if d["med_changes"]:
            plan.append("update the medicine list")
        w.add("assessment", "Working problem: specialist follow-on for the family doctor. Plan: " + ("; ".join(plan) or "do what the letter asks") + ".")
        n, q = self.chart_search(api, pid, ["consult_note", "action_note"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} or action note{'s' if n != 1 else ''} in the chart since the letter.", search=q)
        urgency = 2 if (d["chase"] or (d["soonest_days"] and d["soonest_days"] <= 14)) else 1
        fam = MED_FAMILY if d["med_changes"] else ("test_never_resulted" if d["chase"] and len(d["asks"]) == len(d["chase"]) else "followup_recommended")
        thing = next((w_ for w_ in ("biopsy", "histology") if any(w_ in a.lower() for a in d["chase"])), "result")
        reason = "Specialist letter asks the family doctor to act" + (f": {thing} result to chase" if d["chase"] else "") + ("; medicines changed" if d["med_changes"] else "")
        self.fo_raise(api, ("dc", "letter", r["id"]), pid, fam, reason, [r["id"]], urgency, 7 if urgency == 2 else 14, w, r)

    def fo_appointment(self, api, pid: str, r: dict, t: datetime):
        m = re.search(r"Appointment (\d{2})/(\d{2})/(\d{4})", r["text"])
        tri = re.search(r"triaged ([a-z -]+?)[.,]", r["text"])
        appt = _dmy(m) if m else None
        if appt is None:
            return
        for ref in self._fo_refs(pid, t):
            if not ref["urgent"] or ref["t"] > t:
                continue
            gap = (appt - ref["t"]).days
            if gap <= URGENT_SOPC_MAX_DAYS and not (tri and "routine" in tri.group(1)):
                continue
            w = Why()
            w.add("source", f"HA appointment {'in eHealth' if r['channel'] == 'ehealth' else 'slip in the chart'} seen on {when(api.now)}.",
                  cite(r["id"], " ".join(r["text"].split())[:200]))
            w.add("identity", f"{'Read from eHealth for' if r['channel'] == 'ehealth' else 'Filed to the chart of'} {self.pname(pid)}.", cite(r["id"]))
            w.add("finding", f"The referral on {day(ref['t'])} was marked urgent, but the booking is "
                             f"{'triaged ' + tri.group(1) + ' and ' if tri else ''}{gap // 7} weeks after the referral (appointment {appt:%d %b %Y}).",
                  cite(ref["rid"], line_with(ref["text"], r"^REFERRAL LETTER")), cite(ref["rid"], line_with(ref["text"], r"^Reason:")),
                  cite(r["id"], tri.group(0).rstrip(".,") if tri else None))
            w.add("assessment", "Working problem: an urgent referral booked as non-urgent; the condition the doctor suspected may wait more than a year. "
                                "Plan: phone the SOPC to ask for re-triage with the red-flag features, send an updated letter, or offer a private specialist now; tell the patient.")
            n, q = self.chart_search(api, pid, ["referral_letter", "action_note"], ref["t"] + timedelta(minutes=10))
            w.add("loop_status", f"{'No' if n == 0 else n} further referral letter{'s' if n != 1 else ''} or action note{'s' if n != 1 else ''} in the chart since the referral.", search=q)
            self.fo_raise(api, ("ref", "tri", ref["rid"]), pid, "referral", "Urgent referral: HA appointment triaged routine / too late",
                          [r["id"], ref["rid"]], 3, 2, w, r, hint=ref["owner"], links=("ref", ref["rid"]))

    def fo_reply(self, api, r: dict, t: datetime):
        pid, text = r["patient_id"], r["text"]
        kind, m = classify_reply(text)
        q = " ".join(text.split("\n")[0].split())[:300]       # the patient's whole message (first language), verbatim
        if m is not None and m.group(0) not in q:              # what was read sits on a later line: quote that line
            a = text.rfind("\n", 0, m.start()) + 1
            b = text.find("\n", m.end())
            ln = text[a:b if b >= 0 else len(text)]
            k = m.start() - a
            q = " ".join(ln[max(0, k - 150):k + 150].split())
        if ADMITTED.search(text):
            self.fo_watch[pid] = t + timedelta(days=WATCH_DAYS)
        w = Why()
        w.add("source", f"The patient replied on the care line at {when(t)}.", cite(r["id"], q))
        w.add("identity", f"Received in {self.pname(pid)}'s care-line thread (the number registered for this patient).", cite(r["id"]))
        refs = [x for x in self._fo_refs(pid, t) if x["t"] < t]
        docs = [x for x in self.fo_docs.get(pid, ()) if timedelta(0) <= t - x["t"] <= timedelta(days=30)]
        if docs:
            doc = docs[-1]
            w.add("context", f"{'Discharge summary' if doc['kind'] == 'discharge_summary' else 'A&E summary' if doc['kind'] == 'aed_summary' else 'Specialist letter'} "
                             f"read on {day(doc['t'])}: {doc['quote'] or 'see the document'}.", cite(doc["rid"], doc["quote"]))
        ev = [r["id"]]
        hint, fam = None, "followup_recommended"
        if kind == "red_flag":
            w.add("finding", f"Red-flag symptom in the patient's own words: “{q}”.", cite(r["id"], q))
            w.add("assessment", "Working problem: possible deterioration after a recent episode of care. Plan: the doctor calls the patient today; "
                                "if the symptoms are confirmed, same-day review or A&E. The care line does not reassure or advise.")
            u, due, reason = 4, 1, "Care line: red-flag reply after discharge"
        elif kind == "not_better":
            w.add("finding", f"The patient says they are not improving: “{q}”.", cite(r["id"], q))
            w.add("assessment", "Working problem: not improving as expected. Plan: the doctor decides whether to call, bring the review forward or reassess.")
            u, due, reason = 3, 2, "Care line: not improving after discharge"
        elif kind in ("appt_missed", "appt_too_long"):
            fam = "referral"
            if refs:
                ref = refs[-1]
                ev.append(ref["rid"])
                hint = ref["owner"]
                w.add("context", f"{'Urgent referral' if ref['urgent'] else 'Referral'} written here on {day(ref['t'])}.",
                      cite(ref["rid"], line_with(ref["text"], r"^Reason:")))
            if kind == "appt_missed":
                w.add("finding", f"The patient says they missed their specialist appointment: “{q}”.", cite(r["id"], q))
                w.add("assessment", "Working problem: specialist referral loop re-opened by a missed appointment. Plan: help the patient rebook "
                                    "with the SOPC (or re-refer) and check nothing has changed clinically.")
                u, due, reason = 2, 5, "Care line: specialist appointment missed"
            else:
                urgent = any(x["urgent"] for x in refs)
                w.add("finding", f"The patient reports a specialist appointment far in the future: “{q}”.", cite(r["id"], q))
                w.add("assessment", "Working problem: the specialist booking may not match the referral's urgency. Plan: check the booking, ask the SOPC "
                                    "to re-triage or offer a private specialist; tell the patient.")
                u, due, reason = (3 if urgent else 2), 2, "Care line: specialist appointment too far away"
            key = ("ref", "tri", refs[-1]["rid"]) if (kind == "appt_too_long" and refs) else ("ref", "cl", r["id"])
            if key in self.raised:                         # the eHealth booking already raised it: raise, do not duplicate
                iid = self.raised[key]
                cur = next((x for x in api.items(status="open") if x["id"] == iid), None)
                if cur is not None and cur["urgency"] < max(u, 3):        # raise only: escalation may already have paged it
                    api.raise_urgency(iid, max(u, 3), reason=f"The patient also wrote on the care line: “{q}”")
                return
            n, qs = self.chart_search(api, pid, ["consult_note", "action_note"], t - timedelta(days=30))
            w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} or action note{'s' if n != 1 else ''} in the chart in the last 30 days.", search=qs)
            self.fo_raise(api, key, pid, fam, reason, ev, u, due, w, r, hint=hint, links=("ref", refs[-1]["rid"]) if refs else None)
            return
        else:                                              # better, or not classified: still the doctor's to close
            w.add("finding", ("The patient reports improvement and names no warning symptom" if kind == "fine" else
                              "The reply names no warning symptom") + f": “{q}”. Only a doctor decides the patient is fine.", cite(r["id"], q))
            w.add("assessment", "Working problem: post-discharge check-in reply. Plan: the doctor reads it, confirms nothing is outstanding "
                                "from the discharge plan, and closes it.")
            u, due, reason = 1, 7, ("Care line: better after discharge; doctor to confirm and close" if kind == "fine"
                                    else "Care line: reply after discharge for the doctor to read")
        n, qs = self.chart_search(api, pid, ["consult_note"], t - timedelta(days=14))
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart in the last 14 days.", search=qs)
        self.fo_raise(api, ("dc", "cl", r["id"]), pid, fam, reason, ev, u, due, w, r, hint=hint)

    # ------------------------------------------------------------------------------ the loop
    def fo_raise(self, api, key, pid, family, reason, evidence, urgency, due_days, why, r, hint=None, links=None):
        """Open one item. If core already has an open item on the same evidence and family (e.g. its 'no booking'
        referral watch), the new item is linked to it with `escalates`, so it is not a duplicate."""
        if key in self.raised:
            return
        prior = self.raised.get(links) if links else None
        if prior and any(x["id"] == prior for x in api.items(status="open")):
            owner, route_txt = self.route(api, hint, pid, urgency)
            why.add("routing", route_txt + f" Linked to the open item {prior} on the same referral, which this one supersedes.")
            why.add("request", f"Opened for {self.nm(owner)} at urgency {urgency} ({URGENCY_LABEL[urgency]}); due {when(api.now + timedelta(days=due_days))}.")
            self.raised[key] = api.open_item(pid, owner, family, reason[:200], list(evidence), urgency, api.now + timedelta(days=due_days),
                                             reasoning=why.steps, escalates=prior)
        else:
            self.raise_item(api, key, pid, family, reason[:200], evidence, urgency, hint=hint, days=due_days, why=why)
        if key in self.raised:
            self.fo_items[r["id"]] = self.raised[key]
            self.fo_stats["items"] += 1



JOURNEY = FollowonJourney
