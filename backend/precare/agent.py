"""The precare agent: one conversation per booked appointment, before the visit.

    agent = PrecareAgent(port)
    agent.start(appt_id)                 # sends the invite on the number on file for the appointment
    agent.handle(appt_id, msg_id)        # each patient reply
    agent.tick(appt_id)                  # clock: one reminder after 24 h, and the brief is on file 2 h before the visit

Flow: name, date of birth (both must match the booking), consent, reason for the visit, how long (new problems
only), medicines, allergies, anything else, then the clinic's preparation instructions and an acknowledgement.
The doctor gets one `previsit_brief` item per appointment, whatever happened (complete, declined, stopped, no
reply, identity not confirmed, escalated), so no intake is silently dropped.

Every inbound message is screened first (`safety.screen`): a red flag or crisis gets a fixed reply sending the
patient to 999 / A&E, an urgency-4 item for the appointment's doctor and an alarm, and the questions stop. A
question only a doctor can answer (medicines, results, "is this serious") is never answered: it opens a
`previsit_question` item due before the visit. A glucose-lowering medicine before a fasting test, or a blood
thinner before a procedure, does the same. Every claim in an item's reasoning quotes the patient word for word.

The agent reads replies with fixed rules. A model can be plugged in later to read free text, but the safety
screens, the texts the patient receives and the four write moves stay as they are.
"""
from __future__ import annotations

import re
import unicodedata
from itertools import permutations
from datetime import date, datetime, timedelta

from . import safety
from .texts import LABEL, TEXT_VERSION, compose, values_for

AGENT_VERSION = "precare-agent-v4"   # v1: name + DOB with filler words failed; v2: "冇問題" consent, an added English
#                                       given name; v3: "23rd Sept 1964", safety-netting in the prep message
DATA_STEPS = ("reason", "duration", "meds", "allergies", "anything_else")
TERMINAL = ("declined", "stopped", "identity_failed", "done", "escalated", "closed")
FASTING_TESTS = {"fasting_glucose", "lipids", "fasting_lipids", "glucose_tolerance"}
QUOTE_MAX = 300
REMIND_AFTER = timedelta(hours=24)
BRIEF_BEFORE = timedelta(hours=2)
YES = r"^\s*(yes|y|ok|okay|sure|agree|i agree|同意|好|好呀|係|可以|得)\s*[.!。！]?\s*$"
NO = r"^\s*(no|n|nope|don'?t|disagree|唔同意|不同意|唔要|唔好|不要|唔使)\s*[.!。！]?\s*$"
NONE = r"^\s*(none|no|nil|nothing|n/?a|no allergies|no medicines?|冇|沒有|無|冇呀|冇喎|冇食藥|冇敏感)\s*[.!。！]?\s*$"
FILLER = {"my", "name", "is", "i", "am", "im", "this", "mr", "mrs", "ms", "miss", "dr", "born", "on", "dob", "date", "of",
          "birth", "and", "the", "hi", "hello", "here", "it", "its", "s",
          "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def is_yes(text: str) -> bool:
    """A yes to the consent question: starts with a yes word ("Yes please", "ok 👍", "sure, go ahead", "同意呀",
    "好呀冇問題") and carries no negation. Anything unclear is not a yes; asked twice, it counts as a no."""
    if re.fullmatch(r"\s*(冇問題|無問題|没问题|no problem|no worries)\s*[呀啊喎囉la]*\W*", unicodedata.normalize("NFKC", text).lower()):
        return True
    t = unicodedata.normalize("NFKC", text).lower().replace("冇問題", "").replace("no problem", "")
    t = re.sub(r"[^\w\s\u4e00-\u9fff']", " ", t).strip()
    if re.search(r"\b(no|not|don'?t|never|nope)\b|唔|不|冇|無|別", t):
        return False
    # Whole tokens only. A bare prefix made "yesterday", "oklahoma", "好痛" and "係咪有病" into consent.
    if re.match(r"^(?:yes|yeah|yep|y|ok|okay|sure|agree|fine|go ahead|of course|i agree)\b", t):
        return True
    if re.match(r"^(?:同意|可以|好呀|好|係|得)(?:$|\s|[呀啊喎囉])", t):
        return True
    return bool(re.fullmatch(YES, text, re.I))


def has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def parse_dob(text: str) -> date | None:
    """Day first (Hong Kong): 23/09/1964, 23-9-1964, 23.09.1964, 1964-09-23, 1964年9月23日, 23 Sep 1964."""
    t = unicodedata.normalize("NFKC", text)
    for pat, order in ((r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", "ymd"), (r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", "ymd"),
                       (r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", "dmy")):
        m = re.search(pat, t)
        if m:
            a, b, c = map(int, m.groups())
            y, mo, d = (a, b, c) if order == "ymd" else (c, b, a)
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b", t, re.I)
    if m and m.group(2).lower() in MONTHS:
        d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
    else:
        m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", t, re.I)       # "Sept 23, 1964"
        if not (m and m.group(1).lower() in MONTHS):
            return None
        d, mo, y = int(m.group(2)), MONTHS[m.group(1).lower()], int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _tokens(name: str) -> list[str]:
    t = unicodedata.normalize("NFKC", name).lower()
    t = re.sub(r"\d{1,4}[-/.年月日]\S*", " ", t)                       # drop a date typed on the same line
    return [w for w in re.split(r"[^a-z一-鿿]+", t) if w and w not in FILLER]


def name_matches(text: str, patient: dict) -> bool:
    """Exact match on the English name (any order, hyphens ignored) or the Chinese name. No fuzzy matching:
    a near miss asks again rather than risk the wrong patient."""
    if patient.get("name_zh") and patient["name_zh"] in unicodedata.normalize("NFKC", text).replace(" ", ""):
        return True
    want = sorted(w for w in re.split(r"[^a-z]+", patient["name_en"].lower().replace("-", "")) if w)
    got = [w for w in _tokens(text.replace("-", "")) if not has_cjk(w)]
    if want and sorted(got) == want:
        return True
    joined = "".join(got)                                   # "Taiman Chan", "ChanTaiMan": same name, spaced differently
    if joined and any(joined == "".join(p) for p in permutations(want)):
        return True
    extra = [w for w in got if w not in want]               # "Peter Chan Tai Man": the booking's name plus one English
    return len(want) >= 2 and len(extra) == 1 and sorted(w for w in got if w in want) == want   # given name (DOB must match too)


def quote(text: str) -> str:
    """A verbatim excerpt: the message itself, cut at QUOTE_MAX characters (a prefix is still word for word)."""
    return text.strip()[:QUOTE_MAX]


class PrecareAgent:
    version = AGENT_VERSION

    def __init__(self, port, screen=None, case=None):
        from backend.case import CaseLog
        from .screens import RulesScreen
        self.port = port
        self.screen = screen or RulesScreen()      # rules + shared reader: screens.with_reader(reader)
        self._own_case = case is None
        self.case = case if case is not None else CaseLog()
        self.state: dict[str, dict] = {}
        self.blocked: list[dict] = []          # outbound messages the guard stopped (should stay empty)
        self.log: list[tuple] = []             # (time, appt, action, detail)

    def reset(self):
        """Clear the conversation. A private case starts empty; a shared case is left to its owner."""
        from backend.case import CaseLog
        self.state.clear()
        self.blocked.clear()
        self.log.clear()
        if self._own_case:
            self.case = CaseLog()

    # ---- helpers ----------------------------------------------------------------------------------------------
    def _st(self, appt_id: str) -> dict:
        if appt_id not in self.state:
            a = self.port.appointment(appt_id)
            self.state[appt_id] = {"appt": a, "patient": self.port.patient(a["patient_id"]),
                                   "doctor": self.port.doctor(a["doctor_id"]), "step": "new", "lang": a.get("lang", "en"),
                                   "attempts": 0, "name_ok": False, "verified_at": None, "consent": None, "answers": {},
                                   "questions": [], "flags": [], "brief": None, "prep": [], "ack": None,
                                   "invited_at": None, "reminded": False, "last_in": None, "consent_asks": 0}
        return self.state[appt_id]

    def _send(self, appt_id: str, st: dict, ids: str | list[str], slot: str | None = None) -> str | None:
        """Compose from catalogue templates and check before sending; the port only ever gets template ids."""
        ids = [ids] if isinstance(ids, str) else list(ids)
        if st.get("stopped_at") and ids != ["fixed.stopped"] and not any(i in ("fixed.red_flag", "fixed.crisis") for i in ids):
            return None                                   # opted out: only the emergency replies still go
        body = compose(ids, st["lang"], values_for(st["appt"], st["doctor"], st["lang"]))
        fails = safety.check_outbound(body, LABEL[st["lang"]])
        if fails:
            self.blocked.append({"appt": appt_id, "templates": ids, "fails": fails})
            self.port.alarm("precare", f"outbound message blocked for {appt_id}: {', '.join(fails)}")
            return None
        return self.port.send(appt_id, ids, st["lang"], slot)

    def _ask(self, appt_id: str, st: dict, step: str):
        st["step"] = step
        self._send(appt_id, st, f"ask.{step}", slot=step)

    def _next_data_step(self, st: dict, after: str | None) -> str:
        order = [s for s in DATA_STEPS if s != "duration" or st["appt"].get("visit_type") == "new_problem"]
        if after is None:
            return order[0]
        i = order.index(after)
        return order[i + 1] if i + 1 < len(order) else "prep"

    def _cite(self, st: dict, slot: str) -> dict | None:
        a = st["answers"].get(slot)
        return {"record": a[0], "quote": quote(a[1])} if a else None

    def _due(self, st: dict) -> datetime:
        start = datetime.fromisoformat(st["appt"]["start"])
        due = start - BRIEF_BEFORE
        return due if due > self.port.now else start

    # ---- the conversation ---------------------------------------------------------------------------------------
    def start(self, appt_id: str):
        st = self._st(appt_id)
        if st["step"] != "new":
            return
        st["invited_at"] = self.port.now
        st["step"] = "name"
        self._send(appt_id, st, "ask.invite", slot="name")
        self.log.append((self.port.now, appt_id, "invite", ""))

    def handle(self, appt_id: str, msg_id: str):
        st = self._st(appt_id)
        msg = next(m for m in self.port.messages(appt_id) if m["id"] == msg_id)
        text = msg.get("text") or ""
        st["last_in"] = self.port.now
        if st["step"] in ("name", "new") and st["attempts"] == 0 and not st["name_ok"]:
            st["lang"] = "zh" if has_cjk(text) else "en"

        # 1. safety screen first, at every step
        verdict = self.screen.classify(text)
        if verdict["kind"]:
            return self._escalate(appt_id, st, msg, verdict["kind"], verdict)
        # 2. opt-out
        if safety.is_stop(text):
            return self._stop(appt_id, st)
        step = st["step"]
        if step == "escalated":
            return self._send(appt_id, st, "fixed.after_alarm")
        if step in TERMINAL:
            if step == "stopped" or st.get("stopped_at"):
                return None                               # opted out: no further message and no new item
            if st["consent"] and safety.clinical_question(text):
                return self._question(appt_id, st, msg, "question after the intake")
            return self._send(appt_id, st, "fixed.closed")
        if step in ("name", "dob"):
            return self._identity(appt_id, st, text)
        if step == "consent":
            return self._consent(appt_id, st, text)
        if step in DATA_STEPS:
            return self._answer(appt_id, st, msg, step)
        if step == "prep_ack":
            st["ack"] = msg_id
            if safety.clinical_question(text):
                self._question(appt_id, st, msg, "question at the preparation step")
            st["step"] = "done"
            self._send(appt_id, st, "fixed.done")
            return self._brief(appt_id, st, "complete")

    def _identity(self, appt_id, st, text):
        p = st["patient"]
        dob = parse_dob(text)
        if st["step"] == "name":
            st["name_ok"] = name_matches(text, p)
            if dob is None:
                return self._ask(appt_id, st, "dob")
        ok = st["name_ok"] and dob is not None and dob.isoformat() == p["dob"]
        if ok:
            st["verified_at"] = self.port.now
            st["step"] = "consent"
            st["consent_asks"] = 1
            return self._send(appt_id, st, "ask.consent", slot="consent")
        st["attempts"] += 1
        st["name_ok"] = False
        if st["attempts"] >= 2:
            st["step"] = "identity_failed"
            self._send(appt_id, st, "fixed.identity_failed")
            return self._brief(appt_id, st, "identity_failed")
        st["step"] = "name"
        self._send(appt_id, st, "ask.retry", slot="name")

    def _consent(self, appt_id, st, text):
        if is_yes(text):
            st["consent"] = {"at": self.port.now.isoformat(), "text_version": TEXT_VERSION}
            return self._ask(appt_id, st, self._next_data_step(st, None))
        if re.search(NO, text, re.I) or st["consent_asks"] >= 2:
            st["step"] = "declined"
            self._send(appt_id, st, "fixed.declined")
            return self._brief(appt_id, st, "declined")
        st["consent_asks"] += 1
        self._send(appt_id, st, "ask.consent", slot="consent")

    def _answer(self, appt_id, st, msg, step):
        text = msg.get("text") or ""
        st["answers"][step] = (msg["id"], text)
        if safety.clinical_question(text):
            self._question(appt_id, st, msg, "asked during the pre-visit questions")
        if step in ("meds", "anything_else", "reason"):
            self._medicine_checks(appt_id, st, msg)
        if step in ("meds", "allergies", "reason") and st.get("consent"):
            self._note_answer(st, msg, step)
        nxt = self._next_data_step(st, step)
        if nxt == "prep":
            return self._prep(appt_id, st)
        self._ask(appt_id, st, nxt)

    def _note_answer(self, st, msg, step):
        from backend.case.wire import note_patient
        note_patient(self.case, patient_id=st["patient"]["id"], record_id=msg["id"],
                     text=msg.get("text") or "", at=self.port.now, step=step)

    def _chart_medicines(self, st) -> str:
        """Active regimens and allergies already on the shared case, other than this patient's own replies."""
        view = self.case.project(st["patient"]["id"], self.port.now)
        lines = [m.quote for m in view.medications if m.provenance != "patient"]
        lines += [a.quote for a in view.allergies if a.provenance != "patient"]
        return "\n".join(lines)

    def _medicine_checks(self, appt_id, st, msg):
        a, text = st["appt"], msg.get("text") or ""
        chart = self._chart_medicines(st)
        if chart:
            text = text + "\n" + chart
        if set(a.get("tests") or ()) & FASTING_TESTS:
            hit = safety.mentions(safety.GLUCOSE_LOWERING, text)
            if hit:
                self._question(appt_id, st, msg, f"takes a glucose-lowering medicine ({hit}) and has a fasting blood test: "
                               "the doctor needs to say what to do with it on the morning of the test", notify=False)
        if a.get("procedure"):
            hit = safety.mentions(safety.ANTITHROMBOTIC, text)
            if hit:
                self._question(appt_id, st, msg, f"takes a blood thinner or antiplatelet ({hit}) and is booked for "
                               f"{a['procedure']}: the doctor needs to say whether to continue it", notify=False)

    def prep_keys(self, appt: dict) -> list[str]:
        keys = []
        if set(appt.get("tests") or ()) & FASTING_TESTS:
            keys.append("fasting")
        keys += ["medicines_unchanged", "bring_meds"]
        if appt.get("visit_type") in ("new_problem", "chronic_review", "procedure"):
            keys.append("bring_reports")
        if appt.get("procedure"):
            keys.append("procedure")
        keys += ["arrive_early", "safety_net"]
        return keys

    def _prep(self, appt_id, st):
        st["prep"] = self.prep_keys(st["appt"])
        ids = ["prep.head"] + [f"prep.{k}" for k in st["prep"]]
        if any(q["medicine"] for q in st["questions"]):
            ids.append("fixed.question_routed")
        st["step"] = "prep_ack"
        self._send(appt_id, st, ids + ["ask.prep_ack"], slot="prep_ack")

    def _question(self, appt_id, st, msg, why: str, notify: bool = True):
        if any(q["msg"] == msg["id"] and q["why"] == why for q in st["questions"]):
            return
        a, start = st["appt"], datetime.fromisoformat(st["appt"]["start"])
        urgency = 3 if start - self.port.now < timedelta(hours=24) else 2
        text = msg.get("text") or ""
        trace = [
            {"step": "source", "text": f"Patient's message on the pre-visit line for appointment {a['id']}.",
             "cites": [{"record": msg["id"], "quote": quote(text)}]},
            {"step": "identity", "text": "Name and date of birth matched the booking before consent was given."},
            {"step": "finding", "text": f"Needs the doctor: {why}.", "cites": [{"record": msg["id"], "quote": quote(text)}]},
            {"step": "assessment", "text": "The line has not answered or advised. It cannot advise on medicines, results or symptoms."},
            {"step": "routing", "text": "The doctor for this appointment."},
            {"step": "request", "text": "Reply to the patient before the visit. Only a doctor closes this item."},
        ]
        iid = self.port.open_item(st["patient"]["id"], a["doctor_id"], "previsit_question", f"Pre-visit question: {quote(text)[:120]}",
                                  [msg["id"]], urgency=urgency, due=self._due(st), reasoning=trace)
        st["questions"].append({"item": iid, "msg": msg["id"], "why": why, "medicine": not notify})
        self.log.append((self.port.now, appt_id, "question", iid))
        if notify:
            self._send(appt_id, st, "fixed.question_routed")

    def _escalate(self, appt_id, st, msg, kind, verdict=None):
        lang, a = st["lang"], st["appt"]
        self._send(appt_id, st, f"fixed.{kind}")
        already = [f for f in st["flags"] if f["kind"] == kind]
        text = msg.get("text") or ""
        verified = st["verified_at"] is not None
        trace = [
            {"step": "source", "text": f"Patient's message on the pre-visit line for appointment {a['id']} ({a['start']}).",
             "cites": [{"record": msg["id"], "quote": quote(text)}]},
            {"step": "identity", "text": "Name and date of birth matched the booking." if verified else
             "Not yet confirmed: the message came from the number on file for this appointment."},
            {"step": "finding", "text": ("Possible crisis or self-harm" if kind == "crisis" else "Possible urgent symptom") +
             f", flagged by {(verdict or {}).get('by', 'the fixed red-flag rules')}.",
             "cites": [{"record": msg["id"], "quote": quote(text)}] +
                      ([{"record": msg["id"], "quote": verdict["evidence"]}] if (verdict or {}).get("evidence") else [])},
            {"step": "assessment", "text": "Keyword screen, not a diagnosis. It over-triggers on purpose. The patient was told to call 999 or go to A&E; nothing reassuring was said."},
            {"step": "loop_status", "text": "Open. The pre-visit questions have stopped."},
            {"step": "routing", "text": "The doctor for this appointment, and the clinic alarm."},
            {"step": "request", "text": "Call the patient now. Only a doctor closes this item."},
        ]
        if not already:
            iid = self.port.open_item(st["patient"]["id"], a["doctor_id"], "previsit_red_flag", f"Urgent before visit: {quote(text)[:120]}",
                                      [msg["id"]], urgency=4, due=self.port.now, reasoning=trace, escalates=st["brief"])
            st["flags"].append({"kind": kind, "item": iid, "msg": msg["id"]})
        self.port.alarm("precare", f"{kind} reported before appointment {a['id']} ({st['patient']['id']}); "
                                   f"patient told to call 999 / go to A&E")
        self.log.append((self.port.now, appt_id, kind, msg["id"]))
        if st["step"] not in TERMINAL:
            st["step"] = "escalated"

    def _stop(self, appt_id, st):
        was = st["step"]
        st["step"] = "stopped"
        self._send(appt_id, st, "fixed.stopped")
        st["stopped_at"] = self.port.now
        if not st["brief"] and was not in ("declined", "identity_failed"):
            self._brief(appt_id, st, "stopped")

    def tick(self, appt_id: str):
        st = self._st(appt_id)
        now, start = self.port.now, datetime.fromisoformat(st["appt"]["start"])
        if st["step"] in TERMINAL and st["brief"]:
            return
        if st["invited_at"] and not st["reminded"] and st["last_in"] is None and now - st["invited_at"] >= REMIND_AFTER and now < start - BRIEF_BEFORE:
            st["reminded"] = True
            self._send(appt_id, st, "fixed.reminder", slot=st["step"] if st["step"] not in TERMINAL else None)
        if not st["brief"] and now >= start - BRIEF_BEFORE:
            status = "escalated" if st["flags"] else ("no_reply" if st["last_in"] is None else "incomplete")
            self._brief(appt_id, st, status)

    # ---- the doctor's brief ---------------------------------------------------------------------------------------
    STATUS_TEXT = {
        "complete": "Pre-visit questions completed.",
        "declined": "Patient declined the pre-visit questions. No answers were kept.",
        "stopped": "Patient replied STOP. Only answers given with consent before that are shown.",
        "identity_failed": "Name and date of birth did not match the booking twice. Nothing the sender wrote was kept. The front desk may want to call.",
        "no_reply": "No reply to the pre-visit questions or the reminder.",
        "incomplete": "Pre-visit questions started but not finished before the visit.",
        "escalated": "An urgent symptom was reported before the visit; see the linked urgent item. Questions stopped there.",
    }

    def _brief(self, appt_id: str, st: dict, status: str):
        if st["brief"]:
            return st["brief"]
        a, doc = st["appt"], st["doctor"]
        cites = {s: self._cite(st, s) for s in DATA_STEPS} if st["consent"] else {}
        trace = [{"step": "source", "text": f"Pre-visit line for appointment {a['id']}: {a.get('visit_type', 'visit')} with "
                                            f"{doc['name_en']} on {a['start']}. {self.STATUS_TEXT[status]}"}]
        if st["verified_at"]:
            ident = f"Name and date of birth matched the booking at {st['verified_at']:%d %b %H:%M}."
            if st["consent"]:
                ident += f" Consent to keep answers and share them with the doctor given at {st['consent']['at'][:16]} ({TEXT_VERSION})."
            trace.append({"step": "identity", "text": ident})
        else:
            trace.append({"step": "identity", "text": "Identity not confirmed on this line."})
        labels = {"reason": "Reason for the visit, in the patient's words", "duration": "How long, in the patient's words",
                  "meds": "Medicines the patient reports", "allergies": "Medicine allergies the patient reports",
                  "anything_else": "Anything else the patient wants the doctor to know"}
        for s in DATA_STEPS:
            c = cites.get(s)
            if c:
                trace.append({"step": "finding" if s in ("reason", "duration") else "context", "text": labels[s] + ".", "cites": [c]})
        flags = ", ".join(f"{f['kind']} (item {f['item']})" for f in st["flags"])
        trace.append({"step": "assessment", "text": "No diagnosis is made on this line. " +
                      (f"Red-flag rules matched: {flags}." if flags else "The fixed red-flag rules matched nothing in these messages; that is a keyword screen, not a clinical assessment.") +
                      (f" {len(st['questions'])} question(s) for the doctor opened as separate items." if st["questions"] else "")})
        if st["prep"]:
            trace.append({"step": "loop_status", "text": f"Preparation sent ({', '.join(st['prep'])}); " +
                          ("acknowledged." if st["ack"] else "not acknowledged.")})
        trace.append({"step": "routing", "text": f"{doc['name_en']}, the doctor for this appointment."})
        trace.append({"step": "request", "text": "Read before the visit. Only a doctor closes this brief."})
        reason = cites.get("reason")
        title = f"Pre-visit brief ({status}): " + (reason["quote"][:100] if reason else self.STATUS_TEXT[status])
        urgency = 4 if st["flags"] else 2 if st["questions"] else 1
        evidence = [c["record"] for c in cites.values() if c]
        iid = self.port.open_item(st["patient"]["id"], a["doctor_id"], "previsit_brief", title, evidence, urgency=urgency,
                                  due=datetime.fromisoformat(a["start"]), reasoning=trace,
                                  escalates=st["flags"][0]["item"] if st["flags"] else None)
        st["brief"] = iid
        self.log.append((self.port.now, appt_id, "brief", status))
        return iid
