"""The care line agent: one patient message in, the replies out, and every step on record.

Order of work for every inbound message (spec 003, "rows that apply to every message"):
1. Red flags and crisis messages get the fixed reply and an alert to the named doctor, before anything else (HE-03/04).
2. "Stop" ends routine messages at once (AC-04).
3. Join: consent, then name and date of birth; linked only when both match exactly one chart (AC-01).
4. A request for anything from the chart on an unverified number asks a check question first (AC-02).
5. The menu option or the classified intent runs that feature's fixed steps. Anything clinical goes to the doctor,
   word for word, with an owner and a reply-by time (HE-02, HE-05, NV-01..04).
6. Every reply carries the sender label (AC-05) and passes the outbound checks before it is sent; a failed check
   blocks the send and opens a hand-off instead.

The agent sends only texts from `texts.py`. It opens hand-offs and drafts; it never closes, dismisses, approves,
downgrades or reassures. Only a person does that, through the store's person side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import safety
from .clinic import CarePort, parse_dob
from .intents import RulesIntent
from .settings import Settings, fmt_when
from .store import CareStore
from .texts import MENU, TEXT_VERSION, fixed, say

VERIFY_DAYS = 30
CHART = {"results", "plan", "repeat", "receipts"}          # requests that read the chart need a verified number
PLAN_LINE = re.compile(r"^(P:|Plan|Repeat|Refer|Return|Review|Follow|FU\b|RV\b|Next|Told pt|Advised|Recheck|Blood x|Continue|Stop|Start)", re.I)


@dataclass
class Inbound:
    wa: str                       # the sender's WhatsApp number (wa_id)
    text: str = ""
    reply_id: str | None = None   # id of a tapped button or list row
    kind: str = "text"            # text | interactive | image | document | other
    at: datetime | None = None
    msg_id: str | None = None


@dataclass
class Outbound:
    to: str
    body: str
    type: str = "text"            # text | buttons | list
    buttons: list = field(default_factory=list)   # [{"id","title"}] max 3, titles ≤ 20 chars
    rows: list = field(default_factory=list)      # [{"id","title","description"}] max 10, titles ≤ 24 chars
    button: str = ""
    feature: str = ""
    approved: bool = False
    fails: list = field(default_factory=list)

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if v not in ("", [], None) or k in ("approved",)}


def lang_of(text: str, default: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return "zh"
    if re.search(r"[A-Za-z]{3,}", text or ""):
        return "en"
    return default


class CareAgent:
    def __init__(self, clinic: CarePort, settings: Settings, store: CareStore, intents=None, mode: str = "sim",
                 testers: set[str] | None = None):
        self.clinic, self.settings, self.store = clinic, settings, store
        self.intents = intents or RulesIntent()
        self.mode, self.testers = mode, testers or set()

    # ---- helpers ---------------------------------------------------------------------------------------------
    def doctor(self) -> str:
        return self.settings.person(self.settings["named_doctor"])["name"]

    def label(self, lang: str) -> str:
        return say("label", lang, clinic=self.settings.name(lang))

    def _out(self, c, body, feature, **kw) -> Outbound:
        return Outbound(to=c["wa"], body=f"{self.label(c['lang'])}\n{body}", feature=feature, **kw)

    def _first(self, c) -> str:
        if not c.get("patient_id"):
            return ""
        p = self.clinic.patient(c["patient_id"])
        if c["lang"] == "zh" and p.get("name_zh"):
            return p["name_zh"]
        return p["name_en"].split(" ", 1)[-1]

    def _handoff(self, c, now, kind, feature, quote, note="", key="handoff_open") -> Outbound:
        h = self.store.open_handoff(now, c["wa"], kind, feature, quote, c.get("patient_id"), note)
        owner = self.settings.person(h["owner"])["name"]
        if key == "handoff_open" and not self.settings.is_open(now):
            key = "handoff_closed"
        when = fmt_when(datetime.fromisoformat(h["reply_by"]), now, c["lang"])
        return self._out(c, say(key, c["lang"], owner=owner, when=when), feature)

    def menu(self, c) -> Outbound:
        zh = c["lang"] == "zh"
        rows = [{"id": f"menu:{k}", "title": (z if zh else e)[:24], "description": (e if zh else z)[:72]} for k, z, e in MENU]
        return self._out(c, say("menu_body", c["lang"]), "menu", type="list", rows=rows, button=say("menu_button", c["lang"]))

    # ---- entry point -----------------------------------------------------------------------------------------
    def handle(self, m: Inbound) -> list[Outbound]:
        now = m.at or self.settings.now()
        with self.store.lock:
            outs = self._handle(m, now)
            outs = [self._guard(self.store.conversations.get(m.wa) or {"wa": m.wa, "lang": "en", "consent": None}, o, now) for o in outs]
            self.store.tick(now)
            self.store.save()
        return outs

    def _record_in(self, c, m, now, redact=False):
        c["messages"].append({"dir": "in", "at": now.isoformat(timespec="seconds"), "kind": m.kind,
                              "text": "[not kept: tester mode]" if redact else m.text, "reply_id": m.reply_id, "msg_id": m.msg_id})

    def _handle(self, m: Inbound, now: datetime) -> list[Outbound]:
        text = (m.text or "").strip()
        known = m.wa in self.store.conversations
        c = self.store.conversation(m.wa)
        if not m.reply_id:
            c["lang"] = lang_of(text, c["lang"])

        # tester mode: strangers get one fixed notice and nothing they wrote is kept
        if self.mode == "tester" and m.wa not in self.testers and not m.wa.startswith("sim-"):
            flag = safety.red_flag(text)
            self.store.forget(m.wa)
            self.store.log(now, m.wa, "stranger", red_flag=flag)
            c = {"wa": m.wa, "lang": lang_of(text, "en"), "consent": None}
            if flag:
                return [self._out(c, fixed(flag, c["lang"], doctor=self.doctor()), "HE-03" if flag == "red_flag" else "HE-04")]
            return [self._out(c, say("demo_only", c["lang"]), "demo")]

        self._record_in(c, m, now)
        self.store.log(now, m.wa, "in", type=m.kind, reply_id=m.reply_id)

        # 1. red flags and crisis, whatever the state
        flag = safety.red_flag(text)
        if flag:
            feature = "HE-03" if flag == "red_flag" else "HE-04"
            self.store.open_handoff(now, m.wa, "named doctor", feature, text, c.get("patient_id"), note=f"{flag}: fixed reply sent")
            self.store.log(now, m.wa, "alert", flag=flag, to=self.settings["named_doctor"])
            return [self._out(c, fixed(flag, c["lang"], doctor=self.doctor()), feature)]

        # 2. stop
        if safety.is_stop(text):
            c.update(stopped=True, invites=False)
            self.store.open_handoff(now, m.wa, "staff", "AC-04", text, c.get("patient_id"), note="patient opted out")
            return [self._out(c, say("stopped", c["lang"]), "AC-04")]
        if c["stopped"]:
            if re.fullmatch(r"(join|加入)", text, re.I):
                c.update(stopped=False, state="awaiting_consent" if not c["consent"] else "linked")
                return [self._welcome(c)] if not c["consent"] else [self.menu(c)]
            return [self._handoff(c, now, "staff", "AC-04", text, note="wrote after opting out", key="handoff_staff")]

        # 3. join
        st = c["state"]
        if st == "new" or (not known):
            c["state"] = "awaiting_consent"
            return [self._welcome(c)]
        if st == "awaiting_consent":
            return self._consent(c, m, text, now)
        if st == "awaiting_name":
            c["pending"]["name"] = text[:80]
            c["state"] = "awaiting_dob"
            return [self._out(c, say("ask_dob", c["lang"]), "AC-01")]
        if st == "awaiting_dob":
            return self._dob(c, text, now)
        if st == "awaiting_check":
            return self._check(c, text, now)
        if st == "awaiting_document":
            c["state"] = "linked"
            return [self._filed(c, m, now, "AH-01")]

        # 4-5. a joined (or waiting-to-be-matched) patient
        return self._route(c, m, text, now)

    # ---- AC-01 join ----------------------------------------------------------------------------------------------
    def _welcome(self, c) -> Outbound:
        lang = c["lang"]
        return self._out(c, say("welcome", lang, clinic=self.settings.name(lang), doctor=self.doctor()), "AC-01", type="buttons",
                         buttons=[{"id": "consent:yes", "title": say("btn_join", lang)}, {"id": "consent:no", "title": say("btn_no", lang)}])

    def _consent(self, c, m, text, now):
        yes = m.reply_id == "consent:yes" or re.fullmatch(r"(yes|y|ok|okay|join|好|好呀|加入|得)\W*", text, re.I)
        no = m.reply_id == "consent:no" or re.fullmatch(r"(no|n|no thanks|唔使|唔要|唔好)\W*", text, re.I)
        if yes:
            c["consent"] = {"at": now.isoformat(timespec="seconds"), "wording": say("consent_wording", c["lang"], clinic=self.settings.name(c["lang"])),
                            "text_version": TEXT_VERSION}
            c["state"] = "awaiting_name"
            self.store.log(now, c["wa"], "consent", scope="care")
            return [self._out(c, say("ask_name", c["lang"]), "AC-01")]
        if no:
            lang = c["lang"]
            self.store.forget(c["wa"])
            self.store.log(now, c["wa"], "declined")
            return [Outbound(to=c["wa"], body=f"{self.label(lang)}\n{say('declined', lang, phone=self.settings['phone'])}", feature="AC-01")]
        return [self._welcome(c)]

    def _dob(self, c, text, now):
        dob = parse_dob(text)
        if not dob:
            c["pending"]["tries"] = c["pending"].get("tries", 0) + 1
            if c["pending"]["tries"] < 3:
                return [self._out(c, say("bad_dob", c["lang"]), "AC-01")]
            c["state"] = "unmatched"
            return [self._handoff(c, now, "staff", "AC-01", f"name: {c['pending'].get('name')}; date of birth: {text}", note="could not read the date", key="no_match")]
        matches = self.clinic.find_patients(c["pending"].get("name", ""), dob)
        if len(matches) != 1:
            c["state"] = "unmatched"
            self.store.log(now, c["wa"], "link_refused", matches=len(matches))
            return [self._handoff(c, now, "staff", "AC-01", f"name: {c['pending'].get('name')}; date of birth: {text}",
                                  note=f"{len(matches)} charts match name and date of birth", key="no_match")]
        p = matches[0]
        c.update(patient_id=p["id"], state="linked", verified_at=now.isoformat(timespec="seconds"), pending={})
        self.store.log(now, c["wa"], "linked", patient_id=p["id"], on="name+dob")
        self._draft_plan(c, now)
        lang = c["lang"]
        return [self._out(c, say("linked", lang, first=self._first(c)), "AC-01"),
                self._out(c, say("ask_invites", lang), "AC-04", type="buttons",
                          buttons=[{"id": "invites:yes", "title": say("btn_invites_yes", lang)}, {"id": "invites:no", "title": say("btn_invites_no", lang)}]),
                self.menu(c)]

    # ---- AC-02 identity ------------------------------------------------------------------------------------------
    def _verified(self, c, now) -> bool:
        v = c.get("verified_at")
        return bool(v) and now - datetime.fromisoformat(v) < timedelta(days=VERIFY_DAYS)

    def _check(self, c, text, now):
        intent = c["pending"].pop("after_check", None)
        c["state"] = "linked"
        dob = parse_dob(text)
        if dob and c.get("patient_id") and self.clinic.patient(c["patient_id"])["dob"] == dob:
            c["verified_at"] = now.isoformat(timespec="seconds")
            self.store.log(now, c["wa"], "check_passed")
            return self._do(c, intent, c["pending"].pop("after_text", ""), None, now)
        self.store.log(now, c["wa"], "check_failed")
        return [self._handoff(c, now, "staff", "AC-02", c["pending"].pop("after_text", ""), note="identity check failed", key="check_failed")]

    # ---- routing -------------------------------------------------------------------------------------------------
    def _intent(self, m, text) -> tuple[str, str]:
        if m.reply_id and m.reply_id.startswith("menu:"):
            return m.reply_id.split(":", 1)[1], "menu"
        label = RulesIntent().classify(text)
        who = "rules"
        if label in ("other",) and not safety.clinical_question(text) and not safety.sick_leave(text) and hasattr(self.intents, "classify_named"):
            label, who = self.intents.classify_named(text)
        if safety.sick_leave(text):
            return "sick_leave", who
        if safety.clinical_question(text) and label not in ("book", "cancel", "info", "fees", "staff", "repeat"):
            return "clinical", "safety"
        return label, who

    def _route(self, c, m, text, now):
        rid = m.reply_id or ""
        if rid.startswith("invites:"):
            c["invites"] = rid == "invites:yes"
            self.store.log(now, c["wa"], "consent", scope="invitations", value=c["invites"])
            state = ("on" if c["invites"] else "off") if c["lang"] == "en" else ("開" if c["invites"] else "關")
            return [self._out(c, say("invites_saved", c["lang"], state=state), "AC-04")]
        if rid.startswith("slot:"):
            return self._book_slot(c, rid[5:], now)
        if rid.startswith("med:"):
            meds = self.clinic.medicines(c["patient_id"])
            i = int(rid[4:])
            if i < len(meds):
                return [self._repeat_request(c, meds[i], now)]
            c["state"] = "awaiting_med"
            return [self._out(c, say("repeat_ask", c["lang"]), "MD-01")]
        if rid.startswith("checkin:"):
            return self._checkin_answer(c, rid[8:], text, now)
        if c["state"] == "awaiting_med" and text:
            c["state"] = "linked"
            return [self._repeat_request(c, text, now)]
        if m.kind in ("image", "document"):
            return [self._filed(c, m, now, "AV-04")]

        intent, who = self._intent(m, text)
        c["messages"][-1].update(intent=intent, by=who)
        self.store.log(now, c["wa"], "intent", intent=intent, by=who)
        if c["state"] == "unmatched" and intent in CHART | {"tests", "hospital"}:
            return [self._handoff(c, now, "staff", "AC-02", text, note="number not linked to a chart yet", key="handoff_staff")]
        if intent in CHART and not self._verified(c, now):
            c["state"] = "awaiting_check"
            c["pending"].update(after_check=intent, after_text=text)
            return [self._out(c, say("check_dob", c["lang"]), "AC-02")]
        return self._do(c, intent, text, m, now)

    def _do(self, c, intent, text, m, now) -> list[Outbound]:
        lang = c["lang"]
        s = self.settings
        if intent in ("greeting", None):
            return [self.menu(c)]
        if intent == "info":                                                                     # FD-03
            return [self._out(c, say("info", lang, clinic=s.name(lang), address=s["address_zh" if lang == "zh" else "address_en"],
                                      phone=s["phone"], hours=s.hours_text(lang)), "FD-03")]
        if intent == "fees":                                                                     # FD-05
            rows = "\n".join(f"• {f[lang]}" + (f": HK${f['hkd']}" if f["hkd"] else "") for f in s["fees"])
            return [self._out(c, say("fees", lang, clinic=s.name(lang), rows=rows), "FD-05")]
        if intent == "book":                                                                     # FD-01
            return [self._offer_slots(c, now)]
        if intent == "cancel":
            b = self.clinic.cancel(c["patient_id"]) if c.get("patient_id") else None
            if not b:
                return [self._handoff(c, now, "staff", "FD-01", text, key="handoff_staff")]
            return [self._out(c, say("cancelled", lang, when=fmt_when(datetime.fromisoformat(b["when"]), now, lang)), "FD-01")]
        if intent == "staff":                                                                    # FD-08
            return [self._handoff(c, now, "staff", "FD-08", text, key="handoff_staff")]
        if intent == "results":                                                                  # RT-08
            return [self._results(c, now)]
        if intent == "tests":                                                                    # RT-01, RT-02
            return self._tests(c, text, now)
        if intent == "repeat":                                                                   # MD-01
            if text and m is not None and m.kind == "text" and not self.clinic.medicines(c["patient_id"]):
                return [self._repeat_request(c, text, now)]                                     # the request, word for word
            return [self._repeat_menu(c, text, now)]
        if intent == "plan":                                                                     # AV-01
            return [self._plan(c, now)]
        if intent == "hospital":                                                                 # AH-01
            c["state"] = "awaiting_document"
            return [self._out(c, say("ask_document", lang, doctor=self.doctor()), "AH-01")]
        if intent == "receipts":                                                                 # FD-06
            v = self.clinic.last_visit(c["patient_id"])
            if not v:
                return [self._handoff(c, now, "staff", "FD-06", text, key="no_receipt")]
            return [self._out(c, say("receipt", lang, date=v["date"]), "FD-06")]
        if intent == "sick_leave":                                                               # NV-05
            return [self._out(c, say("refuse_sick_leave", lang), "NV-05"), self.menu(c)]
        if intent == "clinical":                                                                 # NV-01..04, MD-02, AV-03, HE-02
            return [self._handoff(c, now, "doctor", "AV-03", text, key="refuse_clinical")]
        return [self._handoff(c, now, "staff", "FD-08", text, key="handoff_staff")]              # anything else: a person

    # ---- FD-01 booking ---------------------------------------------------------------------------------------------
    def _offer_slots(self, c, now):
        slots = self.clinic.free_slots(now)
        if not slots:
            return self._handoff(c, now, "staff", "FD-01", "asked to book; no free slot", key="no_slots")
        rows = [{"id": "slot:" + t.isoformat(timespec="minutes"), "title": fmt_when(t, now, c["lang"])[:24], "description": self.doctor()} for t in slots]
        return self._out(c, say("slots", c["lang"], doctor=self.doctor()), "FD-01", type="list", rows=rows,
                         button="揀時間" if c["lang"] == "zh" else "Choose a time")

    def _book_slot(self, c, iso, now):
        t = datetime.fromisoformat(iso)
        free = {x.isoformat(timespec="minutes") for x in self.clinic.free_slots(now)}
        if iso not in free:       # never promise a time the book doesn't have (false_promise)
            return [self._offer_slots(c, now)]
        self.clinic.book(c["patient_id"], t)
        self.store.log(now, c["wa"], "booked", when=iso)
        return [self._out(c, say("booked", c["lang"], when=fmt_when(t, now, c["lang"]), doctor=self.doctor()), "FD-01")]

    # ---- RT-08 results ---------------------------------------------------------------------------------------------
    def _results(self, c, now):
        lang = c["lang"]
        rows = self.clinic.results(c["patient_id"])
        if not rows:
            return self._out(c, say("results_none", lang) + "\n" + say("results_tail", lang), "RT-08")
        key = {"back_unreviewed": "results_line_pending_review", "back_reviewed": "results_line_reviewed", "waiting": "results_line_waiting"}
        lines = [say(key[r["status"]], lang, date=r["date"], doctor=self.doctor()) for r in rows]
        return self._out(c, "\n".join(lines) + "\n\n" + say("results_tail", lang), "RT-08")

    # ---- RT-01 test preparation --------------------------------------------------------------------------------------
    def _tests(self, c, text, now):
        v = self.clinic.last_visit(c["patient_id"]) if c.get("patient_id") else None
        prep = self.settings["test_prep_approved"]["fasting_blood"]
        if v and re.search(r"\b(FBS|fasting|lipid|HbA1c|blood x|repeat K|RFT)\b", v["text"], re.I):
            return [self._out(c, say("test_prep", c["lang"], doctor=self.doctor(), text=prep[c["lang"]]), "RT-01", approved=True),
                    self._handoff(c, now, "staff", "RT-02", text or "tests and scans", note="book the test with the lab", key="handoff_staff")]
        return [self._handoff(c, now, "doctor", "RT-01", text or "tests and scans", note="no approved preparation for this test")]

    # ---- MD-01 repeat prescription -----------------------------------------------------------------------------------
    def _repeat_menu(self, c, text, now):
        meds = self.clinic.medicines(c["patient_id"])
        if not meds:
            c["state"] = "awaiting_med"
            return self._out(c, say("repeat_ask", c["lang"]), "MD-01")
        rows = [{"id": f"med:{i}", "title": m[:24], "description": m[24:96]} for i, m in enumerate(meds[:9])]
        rows.append({"id": f"med:{len(meds[:9])}", "title": say("repeat_other", c["lang"]), "description": ""})
        return self._out(c, say("repeat_pick", c["lang"]), "MD-01", type="list", rows=rows, button="揀藥" if c["lang"] == "zh" else "Choose")

    def _repeat_request(self, c, med, now):
        h = self.store.open_handoff(now, c["wa"], "doctor", "MD-01", med, c.get("patient_id"), note="repeat prescription request")
        when = fmt_when(datetime.fromisoformat(h["reply_by"]), now, c["lang"])
        return self._out(c, say("repeat_sent", c["lang"], med=med, owner=self.settings.person(h["owner"])["name"], when=when,
                                phone=self.settings["phone"]), "MD-01")

    # ---- AV-01 the plan in writing -------------------------------------------------------------------------------------
    def _draft_plan(self, c, now):
        v = self.clinic.last_visit(c["patient_id"])
        if not v:
            return None
        lines = [ln.strip() for ln in v["text"].splitlines() if PLAN_LINE.match(ln.strip())]
        if not lines:
            return None
        text = "\n".join(f"• {re.sub(r'^P:\s*', '', ln)}" for ln in lines)   # the doctor's words, verbatim
        return self.store.add_draft(now, c["wa"], c["patient_id"], "AV-01", text, source=v["id"])

    def _plan(self, c, now):
        lang = c["lang"]
        v = self.clinic.last_visit(c["patient_id"])
        d = self._draft_plan(c, now)
        if not v or not d:
            return self._out(c, say("plan_none", lang), "AV-01")
        if d["status"] != "approved":
            if not any(h["feature"] == "AV-01" and h["patient_id"] == c["patient_id"] for h in self.store.open_handoff_for(c["wa"])):
                self.store.open_handoff(now, c["wa"], "doctor", "AV-01", "patient asked for the written plan", c["patient_id"], note=f"draft {d['id']} waits for approval")
            h = next(h for h in self.store.open_handoff_for(c["wa"]) if h["feature"] == "AV-01")
            return self._out(c, say("plan_pending", lang, doctor=self.doctor(), when=fmt_when(datetime.fromisoformat(h["reply_by"]), now, lang)), "AV-01")
        body = say("plan_header", lang, doctor=self.doctor(), date=v["date"]) + "\n" + d["approved_text"] + "\n\n" + say("plan_tail", lang)
        d["sent"].append(now.isoformat(timespec="minutes"))
        return self._out(c, body, "AV-01", approved=True)

    # ---- AV-02 check-in (the clinic starts it) ---------------------------------------------------------------------------
    def start_checkin(self, wa: str, now: datetime | None = None, days: int = 3) -> list[Outbound]:
        now = now or self.settings.now()
        with self.store.lock:
            c = self.store.conversations.get(wa)
            if not c or not c.get("consent") or c.get("stopped") or not c.get("patient_id"):
                self.store.log(now, wa, "checkin_skipped", why="no consent, stopped or not linked")
                return []
            if not self.settings.is_daytime(now):          # HE-06: clinic-started messages go out in the daytime
                self.store.log(now, wa, "checkin_deferred", why="outside daytime")
                return []
            lang = c["lang"]
            v = self.clinic.last_visit(c["patient_id"])
            o = self._out(c, say("checkin", lang, first=self._first(c), days=days, doctor=v["doctor"] if v else self.doctor()), "AV-02",
                          type="buttons", buttons=[{"id": "checkin:better", "title": say("btn_better", lang)},
                                                   {"id": "checkin:same", "title": say("btn_same", lang)},
                                                   {"id": "checkin:worse", "title": say("btn_worse", lang)}])
            o = self._guard(c, o, now)
            self.store.save()
            return [o]

    def _checkin_answer(self, c, ans, text, now):
        self.store.log(now, c["wa"], "checkin_answer", answer=ans)
        if ans == "worse":
            h = self.store.open_handoff(now, c["wa"], "doctor", "AV-02", text or "Worse", c.get("patient_id"), note="check-in: worse")
            return [self._out(c, say("checkin_worse", c["lang"], owner=self.settings.person(h["owner"])["name"],
                                     when=fmt_when(datetime.fromisoformat(h["reply_by"]), now, c["lang"])), "AV-02")]
        return [self._out(c, say("checkin_better" if ans == "better" else "checkin_same", c["lang"]), "AV-02")]

    # ---- AH-01 / AV-04 documents ------------------------------------------------------------------------------------------
    def _filed(self, c, m, now, feature):
        quote = m.text or f"[{m.kind} received, id {m.msg_id}]"
        h = self.store.open_handoff(now, c["wa"], "doctor", feature, quote, c.get("patient_id"), note="document for the doctor to file and read")
        return self._out(c, say("document_filed", c["lang"], owner=self.settings.person(h["owner"])["name"],
                                when=fmt_when(datetime.fromisoformat(h["reply_by"]), now, c["lang"])), h["feature"])

    # ---- outbound checks --------------------------------------------------------------------------------------------------
    def _guard(self, c, o: Outbound, now) -> Outbound:
        full = o.body + "\n" + "\n".join(b["title"] for b in o.buttons) + "\n" + "\n".join(r["title"] for r in o.rows)
        consent = bool(c.get("consent")) or o.feature in ("AC-01", "HE-03", "HE-04", "demo", "AC-04")
        fails = safety.check_outbound(full, label=self.label(c.get("lang", "en")), approved=o.approved, consent=consent)
        if o.approved and o.feature == "AV-01":
            d = next((d for d in self.store.drafts.values() if d["patient_id"] == c.get("patient_id") and d["status"] == "approved"), None)
            if not d or d["approved_text"] not in o.body:
                fails.append("unapproved_send")
        if not fails:
            c.setdefault("messages", []).append({"dir": "out", "at": now.isoformat(timespec="seconds"), "feature": o.feature,
                                                 "text": o.body, "type": o.type, "text_version": TEXT_VERSION})
            self.store.log(now, o.to, "out", feature=o.feature, type=o.type)
            return o
        self.store.log(now, o.to, "blocked", feature=o.feature, fails=fails)
        h = self.store.open_handoff(now, o.to, "doctor", o.feature or "?", "[blocked reply] " + o.body[:300], c.get("patient_id"),
                                    note=f"outbound check failed: {', '.join(fails)}")
        lang = c.get("lang", "en")
        safe = Outbound(to=o.to, body=f"{self.label(lang)}\n" + say("handoff_staff", lang, when=fmt_when(datetime.fromisoformat(h["reply_by"]), now, lang)),
                        feature=o.feature, fails=fails)
        c.setdefault("messages", []).append({"dir": "out", "at": now.isoformat(timespec="seconds"), "feature": o.feature, "text": safe.body,
                                             "blocked": fails})
        return safe
