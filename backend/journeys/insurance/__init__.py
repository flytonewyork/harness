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
from ...extract.reader import Task, verify

FAMILY = "insurance_paperwork"
FAMILIES = {FAMILY: "insurer or corporate-plan paperwork the doctor must complete, check or resubmit by a deadline"}
KINDS = ("insurance_form", "insurer_letter")
DONE_WORDS = ["claim form", "Claim form", "pre-authorisation", "Pre-authorisation", "medical report", "Medical report",
              "Part B", "insurer", "Insurer"]

KIND_LABEL = {"claim_form": "claim form", "pre_auth": "pre-authorisation request", "insurer_query": "request for a medical report",
              "claim_declined": "declined claim", "info_request": "request for further information"}


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


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "twenty-one": 21, "thirty": 30,
             "forty-five": 45, "sixty": 60, "ninety": 90}
ZH_DIGITS = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
DATE = r"(\d{1,2}/\d{1,2}/\d{4}|\d{1,2} [A-Za-z]{3,9},? \d{4}|[A-Za-z]{3,9} \d{1,2},? \d{4}|\d{4}年\d{1,2}月\d{1,2}日)"
NUM = r"(\d+|[A-Za-z-]+(?: \(\d+\))?|[一二兩三四五六七八九十]+)"
ASK = (r"(?i)please (return|provide|complete|send|furnish|forward|fax|reply|submit|let us have)|kindly (complete|return|provide|send)"
       r"|we would be grateful if you could|we request|may (be )?resubmit|is (still )?outstanding|請於|請提供|請填|請將|煩請")
NOT_ASK = r"(?i)should any further information be required|no further information is required|for your (information|records) only"
CONSENT = r"(?i)consent|authori[sz]ation|授權|同意書"
CONSENT_MISSING = r"(?i)not enclosed|not attached|to follow|forwarded separately|will be (sent|forwarded|provided)|missing|未(有)?附|稍後"
CONSENT_GIVEN = r"(?i)enclose|attached|herewith|附上|已附"


def parse_date(s: str) -> datetime | None:
    """A date as printed on a form, or None. A date that does not exist (31/02, month 13: a typo or OCR noise) is
    None, never an exception: one bad form must not stop the engine for the whole clinic."""
    try:
        return _parse_date(s)
    except ValueError:
        return None


def _parse_date(s: str) -> datetime | None:
    s = s.strip().rstrip(".,")
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3,9}),? (\d{4})", s) or re.fullmatch(r"([A-Za-z]{3,9}) (\d{1,2}),? (\d{4})", s)
    if m:
        a, b_, y = m.groups()
        d, mon = (a, b_) if a.isdigit() else (b_, a)
        mo = MONTHS.get(mon[:3].lower())
        return datetime(int(y), mo, int(d)) if mo else None
    return None


def parse_num(s: str) -> int | None:
    s = s.strip()
    m = re.search(r"\((\d+)\)", s)                      # "fourteen (14)"
    if m:
        return int(m.group(1))
    if s.isdigit():
        return int(s)
    if s.lower() in NUM_WORDS:
        return NUM_WORDS[s.lower()]
    if s and all(c in "一二兩三四五六七八九十" for c in s):   # 七, 十, 十四, 二十一
        if "十" not in s:
            return ZH_DIGITS.get(s)
        tens, _, ones = s.partition("十")
        return (ZH_DIGITS.get(tens, 1) if tens else 1) * 10 + (ZH_DIGITS.get(ones, 0) if ones else 0)
    return None


def find_deadline(text: str, t0: datetime) -> tuple[datetime | None, str | None]:
    """The insurer's deadline and the line that states it: a date ('by', 'no later than', 'before', 'on or before')
    or a period ('within N [working] days', in figures, words or Chinese numerals)."""
    m = re.search(r"(?i)\b(?:by|no later than|before|on or before|not later than)\s+" + DATE, text) or re.search(DATE + r"\s*(?:或)?之?前", text)
    if m:
        d = parse_date(m.group(1))
        if d:
            return d, line_with(text, re.escape(m.group(0)))
    for pat, working in ((r"(?i)within " + NUM + r" working days", True), (NUM + r"\s*個?(?:工作天|工作日)內", True),
                         (r"(?i)within " + NUM + r" (?:calendar )?days", False), (NUM + r"\s*(?:日|天)內", False)):
        m = re.search(pat, text)
        if m:
            n = parse_num(m.group(1))
            if n:
                return (working_days_after(t0, n) if working else t0 + timedelta(days=n)), line_with(text, re.escape(m.group(0)))
    return None, None


def classify(title: str, text: str) -> str:
    up, low = title.upper(), text.lower()
    if re.search(r"PRE-?AUTHORI[SZ]ATION|PRE-?APPROVAL|預先批核|預批", up + title):
        return "pre_auth"
    if re.search(r"(?i)declined|unable to admit|not (be )?payable|rejected|拒絕|不獲批", text):
        return "claim_declined"
    if re.search(r"MEDICAL REPORT|醫療報告", up + title) or re.search(r"medical records|consultation notes|copies of", low):
        return "insurer_query"
    if re.search(r"CLAIM FORM|PHYSICIAN|STATEMENT|CERTIFICATE|索償|醫生證明", up + title):
        return "claim_form"
    return "info_request"


def read_identity(text: str) -> dict:
    m_name = (re.search(r"(?:Name|Patient|Insured|Claimant|Life assured|Re)\s*:\s*([A-Z][A-Z'-]+),\s*([A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)*)", text)
              or re.search(r"(?:Name|Patient|Insured|Claimant|Life assured)\s*:\s*([A-Z][A-Z'-]+) ([A-Z][a-z][A-Za-z'-]*(?: [A-Z][a-z][A-Za-z'-]*)*)", text))
    m_dob = re.search(r"(?i)(?:DOB|D\.O\.B\.?|Date of birth)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    m_hk = re.search(r"\b([A-Z]\d{3})XXX\(X\)", text)
    dob = parse_date(m_dob.group(1)) if m_dob else None
    return {"name": f"{m_name.group(1)} {m_name.group(2)}" if m_name else None, "dob": dob.date().isoformat() if dob else None,
            "hkid": m_hk.group(1) if m_hk else None}


def read_insurer_doc(text: str, received: datetime, force: bool = False) -> dict | None:
    """What the insurer asks the doctor to do, and by when, in its own words. None if it asks nothing."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("-- SIMULATED")]
    title = " ".join(lines[1].split()) if len(lines) > 1 else ""
    head = " ".join(lines[0].split()) if lines else "the insurer"
    latin = re.sub(r"^[^A-Za-z]*", "", head) or head          # a bilingual letterhead: keep the English name
    insurer = re.sub(r"\bHk\b", "HK", latin.title()) if latin.isupper() else latin
    body = "\n".join(ln for ln in lines if not re.search(NOT_ASK, ln))
    asks = line_with(body, ASK) or line_with(body, r"(?i)to be completed by|由主診醫生填寫")
    if not asks and not force:
        return None                      # e.g. "Decision: approved ... No further information is required."
    kind = classify(title, text)
    dated = re.search(r"Date:\s*" + DATE, text)
    t0 = (parse_date(dated.group(1)) if dated else None) or received
    due, dq = find_deadline(text, t0)
    if due is not None:
        due = due.replace(hour=17, minute=0)
    m_doc = re.search(r"Attending doctor:\s*(.+)", text)
    m_ref = re.search(r"(?:Our ref|Claim no\.?|Ref)\s*:?\s*([A-Z]+-?\d+)", text)
    auth = line_with(text, CONSENT)
    if auth and re.search(CONSENT_MISSING, auth):
        auth_missing = True
    elif auth and re.search(CONSENT_GIVEN, auth):
        auth_missing = False
    else:
        auth_missing = kind == "insurer_query"      # a records request that never mentions consent: treat as not given
    return {"kind": kind, "insurer": insurer, "dated": min(t0, received), "ref": m_ref.group(1) if m_ref else None,
            "title": title, "title_q": line_with(text, re.escape(lines[1].strip()[:60])) if len(lines) > 1 else None,
            "asks": asks, "due": due, "due_q": dq,
            "sections": [q for q in (line_with(text, r"^Section B1"), line_with(text, r"^Section B\b|^Section B:")) if q],
            "reason_q": line_with(text, r"(?i)^Reason:|unable to admit|declined because"),
            "procedure_q": line_with(text, r"^Procedure requested|申請手術"),
            "lapse_q": line_with(text, r"(?i)will lapse|will not be considered|失效|恕不受理"),
            "auth_q": auth, "auth_missing": auth_missing, "hdr": read_identity(text),
            "doctor": " ".join(m_doc.group(1).split()) if m_doc else None}


KINDS_ENUM = ["claim_form", "pre_auth", "insurer_query", "claim_declined", "info_request", "none"]
TASK = Task(
    name="insurance", version="ins-reader-1",
    instructions=(
        "The document is something an insurer or a corporate medical plan sent a clinic, or a patient brought in. Decide:\n"
        "- asks_doctor: does it ask the clinic's doctor to do anything (complete or sign a form, write a report, supply notes or "
        "information, answer a question, resubmit)? A letter that only informs (paid, settled, approved, withdrawn, received, "
        "under review) asks nothing. asks_quote: the sentence that asks.\n"
        "- kind: claim_form (a form or statement for the doctor to complete), pre_auth (pre-authorisation, pre-approval, "
        "guarantee of payment), insurer_query (medical report, records or notes), claim_declined (declined, rejected, not "
        "admissible, unable to admit, to be resubmitted), info_request (any other question), none.\n"
        "- deadline: the date the insurer wants it by, as YYYY-MM-DD. If it gives a period (within N days or working days), count "
        "it from the date on the letter; working days skip Saturdays and Sundays. deadline_quote: the words that state it.\n"
        "- consent: for a request to release medical information, is the patient's signed consent or authorisation enclosed, "
        "missing or to follow, or not mentioned; not_applicable otherwise. consent_quote: the words, if any.\n"
        "- patient_name (FAMILY Given, as printed), patient_dob (YYYY-MM-DD) and identity_quote (the line that prints them)."),
    schema={"type": "object", "additionalProperties": False,
            "required": ["asks_doctor", "asks_quote", "kind", "deadline", "deadline_quote", "consent", "consent_quote",
                         "patient_name", "patient_dob", "identity_quote"],
            "properties": {"asks_doctor": {"type": "boolean"}, "asks_quote": {"type": ["string", "null"]},
                           "kind": {"type": "string", "enum": KINDS_ENUM},
                           "deadline": {"type": ["string", "null"]}, "deadline_quote": {"type": ["string", "null"]},
                           "consent": {"type": "string", "enum": ["enclosed", "missing", "not_mentioned", "not_applicable"]},
                           "consent_quote": {"type": ["string", "null"]},
                           "patient_name": {"type": ["string", "null"]}, "patient_dob": {"type": ["string", "null"]},
                           "identity_quote": {"type": ["string", "null"]}}},
    quoted={"asks_doctor": "asks_quote", "deadline": "deadline_quote", "patient_name": "identity_quote"})
KEYWORD = "insurance-keyword-v2"


def _iso(s) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def combine(text: str, received: datetime, ans: dict | None, model: str | None) -> dict | None:
    """Keyword reading, widened by a verified model answer. Union only: the model can add an item, an earlier
    deadline, a missing consent or a more specific kind; it can never remove or soften what the keyword reader found."""
    kw = read_insurer_doc(text, received)
    if ans is None:
        if kw is not None:
            kw["read_by"] = KEYWORD + (f" (model {model} had no answer: fell back)" if model else "")
            kw["fell_back"] = KEYWORD if model else None
        return kw
    asks_m = bool(ans.get("asks_doctor")) and ans.get("asks_quote")
    if kw is None and not asks_m:
        return None
    doc = kw or read_insurer_doc(text, received, force=True)
    doc["read_by"] = (KEYWORD + " + " if kw else "") + f"model {model}"
    doc["fell_back"] = None
    if asks_m and not doc["asks"]:
        doc["asks"] = ans["asks_quote"]
    if ans.get("kind") in KINDS_ENUM[:5] and (kw is None or doc["kind"] in ("info_request", "claim_form")) \
            and not (kw and doc["kind"] == "claim_form" and ans["kind"] == "info_request"):
        doc["kind"] = ans["kind"]
    d = _iso(ans.get("deadline"))
    if d is not None and ans.get("deadline_quote"):
        d = d.replace(hour=17, minute=0)
        if doc["due"] is None or d < doc["due"]:
            doc["due"], doc["due_q"] = d, ans["deadline_quote"]
    if ans.get("consent") == "missing" or (doc["kind"] == "insurer_query" and ans.get("consent") == "not_mentioned"):
        doc["auth_missing"] = True
        if ans.get("consent_quote") and norm_in(ans["consent_quote"], text):
            doc["auth_q"] = ans["consent_quote"]
    hdr = doc["hdr"]
    if ans.get("identity_quote"):
        if not hdr.get("name") and ans.get("patient_name"):
            hdr["name"] = " ".join(str(ans["patient_name"]).replace(",", " ").split())
        if not hdr.get("dob") and _iso(ans.get("patient_dob")):
            hdr["dob"] = _iso(ans["patient_dob"]).date().isoformat()
        doc["id_q"] = ans["identity_quote"]
    return doc


def norm_in(q: str, text: str) -> bool:
    return " ".join(q.split()) in " ".join(text.split())


class InsuranceJourney:
    """Cooperative mixin: class Suite(InsuranceJourney, ..., AgenticHarness). Calls super() first everywhere."""

    def reset(self, api):
        super().reset(api)
        if getattr(self, "case", None) is None:
            from backend.case.wire import ensure_case
            ensure_case(self, reset=not getattr(self, "_case_ready", False))
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
        reader = getattr(self, "reader", None)                  # the shared model reader, off unless the host sets it
        ans, dropped = verify(TASK, r["text"], reader.read(TASK, r["text"])) if reader is not None else (None, [])
        self.ins_stats["model_fields_dropped"] += len(dropped)
        doc = combine(r["text"], t, ans, reader.name if reader is not None else None)
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
        """The latest referral in the 45-day window. The case projection wins when it has one."""
        case = getattr(self, "case", None)
        if case is not None:
            from backend.case.wire import referral_rows
            rows = [x for x in referral_rows(case, pid, t, open_only=True)
                    if t - timedelta(days=45) <= x["t"] <= t]
            if rows:
                return rows[-1]
        refs = [x for x in getattr(self, "referrals", ()) if x["pid"] == pid and t - timedelta(days=45) <= x["t"] <= t]
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
        w.add("source", f"{label.capitalize()} from {insurer}, received by {self.chan_label(r['channel'])} on {when(t)}. "
              f"Read by {doc.get('read_by', KEYWORD)}.",
              cite(r["id"], doc["title_q"]))
        w.add("identity", how, *self.id_cites(r["id"], r["text"]), *([cite(r["id"], doc["id_q"])] if doc.get("id_q") else []))
        finding = [cite(r["id"], doc["asks"])]
        if doc["due_q"] and doc["due_q"] != doc["asks"]:
            finding.append(cite(r["id"], doc["due_q"]))
        for q in (doc["reason_q"], doc["procedure_q"], *doc["sections"][:1], doc["lapse_q"]):
            if q:
                finding.append(cite(r["id"], q))
        due_txt = f"by {day(due)}" if due else "with no return date on the document (taken as 14 days)"
        asked = {"claim_form": "to complete and sign the doctor's part of its claim form",
                 "pre_auth": "to complete the medical section of its pre-authorisation request",
                 "insurer_query": "for a medical report", "claim_declined": "to complete the missing section so the claim can be resubmitted",
                 "info_request": "for further information on a claim"}[kind]
        w.add("finding", f"The insurer asks the doctor {asked}, {due_txt}.", *finding)
        evidence = [r["id"]]
        ctx = []
        if ref:      # cited, not evidence: the referral is the core engine's loop, and the doctor's action here is on the form
            q = line_with(ref["text"], r"^REFERRAL LETTER")
            ctx.append(f"It is for the {'URGENT ' if ref['urgent'] else ''}referral written on {day(ref['t'])}.")
            w.add("context", " ".join(ctx), cite(ref["rid"], q), cite(ref["rid"], line_with(ref["text"], r"^Reason:")))
        if doc["auth_missing"] and not doc["auth_q"]:
            w.add("context", "The request does not mention the patient's signed authorisation: nothing can be released until it is on file.")
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
                "claim_declined": "Complete the missing section and resubmit",
                "info_request": "Reply to the insurer with what it asks for"}[kind]
        w.add("request", f"{what}, then close this item. Opened for {self.nm(owner)} at urgency {urgency} ({URGENCY_LABEL[urgency]}); "
              f"due {when(item_due)}. Only a doctor can close it.")
        reason = {"claim_form": f"Insurer claim form to complete for {insurer} {due_txt}",
                  "pre_auth": f"Pre-authorisation for {'an urgent' if ref and ref['urgent'] else 'a'} referral: return {due_txt}"
                              + ("; the urgent referral must not wait for it" if ref and ref["urgent"] else ""),
                  "insurer_query": f"{insurer} asks for a medical report {due_txt}"
                                   + ("; no signed authorisation from the patient is enclosed" if doc["auth_missing"] else ""),
                  "claim_declined": f"Claim declined by {insurer} (doctor's section incomplete): resubmit {due_txt}",
                  "info_request": f"{insurer} asks for further information on a claim {due_txt}"}[kind]
        iid = api.open_item(pid, owner, FAMILY, reason, evidence, urgency, item_due, reasoning=w.steps)
        self.ins_open[iid] = {"due": due, "pid": pid, "label": label}
        self.ins_stats["items"] += 1

    def ins_watch(self, api):
        """Two working days before the insurer's deadline, raise anything still open to urgent."""
        today = api.now.date()                            # once a day, on the first tick whatever the tick spacing
        if not self.ins_open or getattr(self, "ins_watched", None) == today:
            return
        self.ins_watched = today
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
