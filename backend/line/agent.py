"""The line: one inbound WhatsApp message in, everything it causes out, all on record.

Who is writing decides the side:
- a registered doctor's number → the doctor's console (`_doctor`);
- anyone else → a patient (`_patient`).

Order for every patient message (spec 003's rows that apply to every message, kept from spec 004):
1. Red flags and crisis messages get the fixed reply and an alert to the doctor, whatever the state.
2. STOP ends messages at once.
3. Join: the doctor's code from the QR, consent, name, date of birth. Nothing is kept from someone who says no.
4. Buttons, media, then the message's intent. A clinical question is never answered by the line: it gets a fixed
   acknowledgement with a reply-by time in the doctor's hours, and goes to the doctor with a draft.

Only the doctor approves clinical words (the plan, a drafted reply) or writes their own. The line opens hand-offs and
drafts; it never closes, dismisses, downgrades or reassures. Every outbound message passes `_guard` before sending.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from backend.careline import safety
from backend.careline.clinic import parse_dob

from . import drafting, hours
from .hours import fmt_when
from .store import Store, new_id
from .templates import lang_code
from .texts import TEXT_VERSION, dsay, fixed, say
from .transport import Out

CODE = re.compile(r"\[([A-Z]{2}\d{2})(?:\.(\d))?\]")
WINDOW = timedelta(hours=24)
THANKS = re.compile(r"^\W*(thanks?( you)?|thx|ty|多謝|唔該|thank u|ok thanks?|👍|🙏)\W*$", re.I)
GREETING = re.compile(r"^\W*(hi|hello|hey|menu|help|你好|哈囉|選單)\W*$", re.I)
WANT_PLAN = re.compile(r"\b(my plan|the plan|plan again|resend)\b|計劃", re.I)
WANT_REPEAT = re.compile(r"\b(repeat|refill|more (of )?(my )?(medicine|meds|tablets|pills))\b|覆配|配藥|攞藥|拎藥", re.I)
WANT_RESULT = re.compile(r"\b(result|report|blood test results?)\b|報告", re.I)
DOC_CMD = re.compile(r"^\s*(help|queue|web|qr|resume|pause)(?:\s+(\d+))?\s*$", re.I)
MODE_MINUTES = 30


def lang_of(text: str, default: str) -> str:
    if re.search(r"[一-鿿]", text or ""):
        return "zh"
    if re.search(r"[A-Za-z]{3,}", text or ""):
        return "en"
    return default


def iso(t: datetime) -> str:
    return t.isoformat(timespec="minutes")


class Line:
    def __init__(self, cfg, store: Store, transport, model, stripe=None, media=None):
        self.cfg, self.store, self.tx, self.model, self.stripe, self.media = cfg, store, transport, model, stripe, media

    # ---- people ---------------------------------------------------------------------------------------------------
    def doctor_by_wa(self, wa: str) -> dict | None:
        return self.store.one("doctor", wa=wa)

    def doctor(self, did: str) -> dict:
        return self.store.get("doctor", did)

    def dname(self, d: dict, lang: str) -> str:
        return d.get("name_zh") if lang == "zh" and d.get("name_zh") else d["name_en"]

    def dshort(self, d: dict, lang: str) -> str:
        return d.get("short_zh") if lang == "zh" and d.get("short_zh") else d.get("short_en", d["name_en"])

    def clinic(self, d: dict, cid: str | None) -> dict:
        cs = d.get("clinics") or [{"id": "c1", "name_en": "the clinic", "name_zh": "診所", "address_en": "", "address_zh": "", "phone": ""}]
        return next((c for c in cs if c["id"] == cid), cs[0])

    def first(self, p: dict) -> str:
        n = (p.get("name") or "").strip()
        if re.search(r"[一-鿿]", n):
            return n[-2:] if len(n) >= 3 else n
        parts = n.split()
        if len(parts) >= 2 and parts[0].isupper():
            return " ".join(parts[1:])
        return parts[0] if parts else ""

    def pname(self, p: dict) -> str:
        return p.get("name") or "a patient"

    def wa_doc(self, wa: str) -> dict:
        return self.store.get("wa", wa) or {"wa": wa, "state": "new", "lang": "en", "outbox": []}

    # ---- entry -------------------------------------------------------------------------------------------------------
    def handle(self, m: dict, now: datetime | None = None) -> list[str]:
        """One inbound message (from `parse_line_webhook` or the simulator). Returns the ids of what was sent."""
        now = m.get("at") or now or hours.now_hk()
        with self.store.lock:
            self._inbound = m["wa"]                                # they just wrote: their window is open
            try:
                return self._handle(m, now)
            finally:
                self._inbound = None

    _inbound: str | None = None

    def _handle(self, m: dict, now: datetime) -> list[str]:
        if m.get("msg_id"):
            if self.store.get("seen", m["msg_id"]):
                return []                                      # Meta retries: handle each message once
            self.store.put("seen", m["msg_id"], {"at": iso(now)})
        w = self.wa_doc(m["wa"])
        w["last_in"] = iso(now)
        w.pop("waiting_sent", None)
        outbox, w["outbox"] = w.get("outbox", []), []
        self.store.put("wa", m["wa"], w)
        sent = self._flush(outbox, now)
        d = self.doctor_by_wa(m["wa"])
        if d:
            outs = self._doctor(d, m, now)
        elif self.cfg.mode == "staging" and m["wa"] not in self.cfg.testers:
            outs = self._stranger(m, now)
        else:
            outs = self._patient(m, now)
        return sent + self.deliver(outs, now)

    def _stranger(self, m, now):
        lang = lang_of(m.get("text", ""), "en")
        self.store.delete("wa", m["wa"])
        flag = safety.red_flag(m.get("text") or "")
        from backend.careline.texts import say as say4
        body = (fixed(flag, lang, doctor="the care team") if flag else say4("demo_only", lang))
        return [Out(to=m["wa"], body="tmrw care line (automated, test)\n" + body, feature="demo", kind="console")]

    # ---- delivery: the 24-hour window, the outbox, the guard ---------------------------------------------------------
    def deliver(self, outs: list[Out], now: datetime) -> list[str]:
        ids = []
        for o in outs:
            if o is None:
                continue
            o = self._guard(o, now)
            if o is None:
                continue
            w = self.wa_doc(o.to)
            last = datetime.fromisoformat(w["last_in"]) if w.get("last_in") else None
            open_ = o.to == self._inbound or (last is not None and now - last < WINDOW)
            if not open_ and o.type != "template":
                if o.template:
                    o = Out(to=o.to, type="template", template=o.template, feature=o.feature, kind=o.kind, ctx=o.ctx, body=o.body)
                else:
                    w.setdefault("outbox", []).append(o.__dict__)
                    if not w.get("waiting_sent"):
                        w["waiting_sent"] = iso(now)
                        self.store.put("wa", o.to, w)
                        ids += self._send(self._waiting(o, w), now)
                    else:
                        self.store.put("wa", o.to, w)
                    continue
            ids += self._send(o, now)
        return ids

    def _waiting(self, o: Out, w: dict) -> Out:
        d = self.doctor_by_wa(o.to)
        if d:
            return Out(to=o.to, type="template", feature="console", kind="console",
                       template={"name": "line_doctor_new", "lang": "en", "params": [str(len(w.get("outbox", [])))], "payloads": ["dq"]})
        p = self.store.get("patient", w.get("current") or "") or {}
        doc = self.doctor(p["doctor_id"]) if p.get("doctor_id") else None
        lang = w.get("lang", "en")
        return Out(to=o.to, type="template", feature="waiting", kind="fixed",
                   template={"name": "line_waiting", "lang": lang_code(lang, "line_waiting"),
                             "params": [self.dname(doc, lang) if doc else "Your doctor"], "payloads": ["read"]})

    def _flush(self, outbox: list[dict], now) -> list[str]:
        ids = []
        for od in outbox:
            ids += self._send(Out(**od), now)
        return ids

    def _send(self, o: Out, now) -> list[str]:
        mid = self.tx.send(o)
        w = self.store.get("wa", o.to) or {}
        pid = (o.ctx or {}).get("patient_id") or (w.get("current") if not self.doctor_by_wa(o.to) else None)
        mdoc = {"id": mid, "wa": o.to, "dir": "out", "at": iso(now), "feature": o.feature, "kind": o.kind, "type": o.type,
                "text": o.body if o.type != "template" else f"[template {o.template['name']}] " + " | ".join(map(str, o.template.get("params", []))),
                "patient_id": pid, "text_version": TEXT_VERSION}
        self.store.put("msg", mid, mdoc)
        if o.ctx:
            self.store.put("ctx", mid, {"id": mid, **o.ctx})
        self.store.event(now, "out", feature=o.feature, kind=o.kind, type=o.type, msg=mid)
        return [mid]

    def _label(self, d: dict, lang: str, kind: str) -> str:
        key = {"fixed": "label", "approved": "label_approved", "doctor": "label_doctor"}[kind]
        return say(key, lang, doctor=self.dname(d, lang))

    def _guard(self, o: Out, now) -> Out | None:
        """The message-side hard fails, checked on every patient message before it is sent (spec 003)."""
        if o.kind == "console" or o.type == "template":
            return o
        w = self.wa_doc(o.to)
        p = self.store.get("patient", w.get("current") or "") if w.get("current") else None
        d = self.doctor(p["doctor_id"]) if p else (self.doctor(w["doctor_id"]) if w.get("doctor_id") else None)
        lang = (p or w).get("lang", "en")
        if not d:
            return o
        label = self._label(d, lang, o.kind)
        full = o.body + "\n" + "\n".join(b["title"] for b in o.buttons) + "\n" + "\n".join(r["title"] for r in o.rows)
        consent = (bool(p and p.get("consent")) and (p or {}).get("state") != "stopped") or o.feature in ("AC-01", "HE-03", "HE-04", "AC-04")
        if o.kind == "doctor":
            fails = [] if full.startswith(label) else ["impersonation"]
            fails += [] if consent else ["no_consent"]
        else:
            fails = safety.check_outbound(full, label=label, approved=o.kind == "approved", sensitive_ok=o.kind == "approved",
                                          consent=consent)
        if o.kind == "approved":
            ok = o.ctx and o.ctx.get("approved_hash") and o.ctx["approved_hash"] == hashlib.sha256(o.body.split("\n", 1)[1].encode()).hexdigest()
            if not ok:
                fails.append("unapproved_send")
        if not fails:
            return o
        self.store.event(now, "blocked", feature=o.feature, fails=",".join(sorted(set(fails))))
        if p and d:
            self.deliver([self._console(d, f"Blocked a message to {self.pname(p)} ({', '.join(sorted(set(fails)))}). Nothing was sent; "
                                           "please reply to them yourself.", ctx={"patient_id": p["id"]})], now)
        return None

    # ---- message builders ------------------------------------------------------------------------------------------------
    def _out(self, p_or_w: dict, d: dict, body: str, feature: str, kind: str = "fixed", **kw) -> Out:
        lang = p_or_w.get("lang", "en")
        return Out(to=p_or_w["wa"], body=f"{self._label(d, lang, kind)}\n{body}", feature=feature, kind=kind, **kw)

    def _console(self, d: dict, body: str, ctx: dict | None = None, **kw) -> Out:
        return Out(to=d["wa"], body=f"{dsay('prefix')} {body}", feature="console", kind="console", ctx=ctx, **kw)

    def menu(self, p, d) -> Out:
        lang = p["lang"]
        rows = [{"id": "menu:plan", "title": say("m_plan", lang), "description": ""},
                {"id": "menu:ask", "title": say("m_ask", lang, short=self.dshort(d, lang))[:24], "description": say("m_ask_d", lang)},
                {"id": "menu:repeat", "title": say("m_repeat", lang), "description": say("m_repeat_d", lang)},
                {"id": "menu:result", "title": say("m_result", lang), "description": say("m_result_d", lang)}]
        return self._out(p, d, say("menu_body", lang), "menu", type="list", rows=rows, button=say("menu_button", lang))

    def _record_in(self, m, now, patient_id=None, doctor_id=None, keep_text=True):
        mid = m.get("msg_id") or new_id("IN")
        self.store.put("msg", mid, {"id": mid, "wa": m["wa"], "dir": "in", "at": iso(now), "kind": m.get("kind", "text"),
                                    "text": m.get("text", "") if keep_text else "", "reply_id": m.get("reply_id"),
                                    "media_ref": m.get("media_ref"), "patient_id": patient_id, "doctor_id": doctor_id})

    # ---- hand-offs: the line opens, only a doctor ends -----------------------------------------------------------------
    def open_handoff(self, now, p, d, kind, quote, **extra) -> dict:
        hid = new_id("H")
        rb = hours.reply_by(d, now, "named doctor" if kind == "red_flag" else kind)
        h = {"id": hid, "patient_id": p["id"], "doctor_id": d["id"], "kind": kind, "quote": quote, "opened_at": iso(now),
             "reply_by": iso(rb), "status": "open", "history": [], **extra}
        self.store.put("handoff", hid, h)
        self.store.event(now, "handoff_opened", handoff=hid, patient=p["id"], doctor=d["id"], kind=kind)
        return h

    def close_handoff(self, now, hid: str, by_doctor: str, resolution: str):
        h = self.store.get("handoff", hid)
        if not h or h["doctor_id"] != by_doctor:
            raise PermissionError("only the patient's doctor ends a hand-off")
        h.update(status="closed", closed_at=iso(now), resolution=resolution)
        self.store.put("handoff", hid, h)
        self.store.event(now, "handoff_closed", handoff=hid, doctor=by_doctor, resolution=resolution)

    def _when(self, d, h, now, lang):
        return fmt_when(datetime.fromisoformat(h["reply_by"]), now, lang)

    def _ack(self, p, d, h, now) -> Out:
        lang = p["lang"]
        if d.get("paused_until") and now.date().isoformat() <= d["paused_until"]:
            return self._out(p, d, say("ack_paused", lang, doctor=self.dname(d, lang), until=d["paused_until"]), "HE-02")
        key = "ack_open" if hours.is_open(d, now) else "ack_closed"
        return self._out(p, d, say(key, lang, doctor=self.dname(d, lang), when=self._when(d, h, now, lang)), "HE-02")

    # ======================================================================================================================
    # patients
    # ======================================================================================================================
    def _patient(self, m, now) -> list[Out]:
        text = (m.get("text") or "").strip()
        w = self.wa_doc(m["wa"])
        if not m.get("reply_id") and text:
            w["lang"] = lang_of(text, w.get("lang", "en"))
        p = self.store.get("patient", w["current"]) if w.get("current") else None
        code = CODE.search(text)
        if code:                                                # a QR scan: join (or switch to) that doctor
            d = self.store.one("doctor", code=code.group(1))
            if d:
                existing = self.store.one("patient", wa=m["wa"], doctor_id=d["id"])
                if existing and existing.get("state") == "joined":
                    w["current"] = existing["id"]
                    self.store.put("wa", m["wa"], w)
                    existing["lang"] = w["lang"]
                    self.store.put("patient", existing["id"], existing)
                    return [self.menu(existing, d)]
                w.update(state="awaiting_consent", doctor_id=d["id"], clinic_id=f"c{code.group(2) or 1}", current=None)
                self.store.put("wa", m["wa"], w)
                return [self._welcome(w, d)]
        # 1. red flags, whatever the state (before consent too: safety first, nothing else kept)
        flag = safety.red_flag(text)
        d = self.doctor(p["doctor_id"]) if p else (self.doctor(w["doctor_id"]) if w.get("doctor_id") else None)
        if flag:
            return self._red_flag(m, now, w, p, d, flag, text)
        if not d:
            self.store.put("wa", m["wa"], w)
            return [Out(to=m["wa"], body=say("no_code", w["lang"]), feature="no_code", kind="console")]
        # 3. joining
        if not p:
            return self._join(m, now, w, d, text)
        p["lang"] = w["lang"]
        self.store.put("patient", p["id"], p)
        self.store.put("wa", m["wa"], w)
        # 2. stop
        if safety.is_stop(text):
            p["state"] = "stopped"
            self.store.put("patient", p["id"], p)
            self._record_in(m, now, p["id"], d["id"])
            self.store.event(now, "stopped", patient=p["id"])
            return [self._out(p, d, say("stopped", p["lang"], doctor=self.dname(d, p["lang"])), "AC-04"),
                    self._console(d, f"{self.pname(p)} stopped messages from the line.", ctx={"patient_id": p["id"]})]
        if p.get("state") == "stopped":
            if re.fullmatch(r"\W*(join|加入)\W*", text, re.I):
                p["state"] = "joined"
                self.store.put("patient", p["id"], p)
                return [self.menu(p, d)]
            return []
        if m.get("media_id") and self.media:
            data, mime = self.tx.download(m["media_id"])
            m["media_ref"] = self.media.put(data, mime or m.get("mime") or "application/octet-stream")
        self._record_in(m, now, p["id"], d["id"])
        self.store.event(now, "in", patient=p["id"], kind=m.get("kind", "text"))
        return self._route(m, now, p, d, text)

    def _welcome(self, w, d) -> Out:
        lang = w["lang"]
        body = say("welcome", lang, doctor=self.dname(d, lang), hours=hours.hours_text(d, lang), privacy=f"{self.cfg.base_url}/privacy")
        return Out(to=w["wa"], body=f"{self._label(d, lang, 'fixed')}\n{body}", feature="AC-01", type="buttons",
                   buttons=[{"id": "consent:yes", "title": say("btn_join", lang)}, {"id": "consent:no", "title": say("btn_no", lang)}])

    def _join(self, m, now, w, d, text) -> list[Out]:
        lang, st = w["lang"], w.get("state")
        rid = m.get("reply_id") or ""
        if st == "awaiting_consent":
            yes = rid == "consent:yes" or re.fullmatch(r"\W*(yes|y|ok|okay|join|好|好呀|加入|得)\W*", text, re.I)
            no = rid == "consent:no" or re.fullmatch(r"\W*(no|n|not now|no thanks|唔使|唔要|暫時唔使)\W*", text, re.I)
            if yes:
                w.update(state="awaiting_name", consent={"at": iso(now), "text_version": TEXT_VERSION,
                                                         "wording": say("consent_wording", lang, doctor=self.dname(d, lang))})
                self.store.put("wa", m["wa"], w)
                return [Out(to=m["wa"], body=f"{self._label(d, lang, 'fixed')}\n{say('ask_name', lang, doctor=self.dname(d, lang))}", feature="AC-01")]
            if no:
                self.store.delete("wa", m["wa"])
                self.store.event(now, "declined", doctor=d["id"])
                return [Out(to=m["wa"], body=f"{self._label(d, lang, 'fixed')}\n{say('declined', lang)}", feature="AC-01")]
            self.store.put("wa", m["wa"], w)
            return [self._welcome(w, d)]
        if st == "awaiting_name" and text:
            w.update(state="awaiting_dob", name=text[:80])
            self.store.put("wa", m["wa"], w)
            return [Out(to=m["wa"], body=f"{self._label(d, lang, 'fixed')}\n{say('ask_dob', lang)}", feature="AC-01")]
        if st == "awaiting_dob":
            dob = parse_dob(text)
            if not dob:
                w["tries"] = w.get("tries", 0) + 1
                self.store.put("wa", m["wa"], w)
                return [Out(to=m["wa"], body=f"{self._label(d, lang, 'fixed')}\n{say('bad_dob', lang)}", feature="AC-01")]
            p = {"id": new_id("P"), "wa": m["wa"], "doctor_id": d["id"], "clinic_id": w.get("clinic_id", "c1"),
                 "name": w.get("name", ""), "dob": dob, "lang": lang, "consent": w["consent"], "state": "joined",
                 "confirmed": False, "joined_at": iso(now), "plan_id": None}
            self.store.put("patient", p["id"], p)
            w = {"wa": m["wa"], "state": "joined", "lang": lang, "current": p["id"], "last_in": w.get("last_in"), "outbox": []}
            self.store.put("wa", m["wa"], w)
            self.store.event(now, "joined", patient=p["id"], doctor=d["id"])
            c = self.clinic(d, p["clinic_id"])
            sess = self.store.get("dsession", d["id"]) or {"id": d["id"]}
            sess.update(focus=p["id"], focus_at=iso(now), chosen=False)
            self.store.put("dsession", d["id"], sess)
            where = f" at {c['name_en']}" if d.get("clinics") and len(d["clinics"]) > 1 else ""
            return [self._out(p, d, say("joined", lang, first=self.first(p), doctor=self.dname(d, lang), hours=hours.hours_text(d, lang)), "AC-01"),
                    self._console(d, dsay("joined", name=self.pname(p), year=dob[:4], where=where), ctx={"patient_id": p["id"]},
                                  type="buttons", buttons=[{"id": f"conf:no:{p['id']}", "title": "Not my patient"}])]
        self.store.put("wa", m["wa"], w)
        return [self._welcome(w, d)]

    def _red_flag(self, m, now, w, p, d, flag, text) -> list[Out]:
        lang = (p or w).get("lang", "en")
        feature = "HE-03" if flag == "red_flag" else "HE-04"
        name = self.dname(d, lang) if d else ("你嘅醫生" if lang == "zh" else "your doctor")
        label = self._label(d, lang, "fixed") if d else "tmrw care line (automated)"
        outs = [Out(to=m["wa"], body=f"{label}\n{fixed(flag, lang, doctor=name)}", feature=feature,
                    kind="fixed" if d else "console")]
        if d:
            if p:
                self._record_in(m, now, p["id"], d["id"])
                h = self.open_handoff(now, p, d, "red_flag", text, flag=flag)
                ctx = {"patient_id": p["id"], "handoff_id": h["id"]}
                who = self.pname(p)
            else:
                ctx, who = None, (w.get("name") or "Someone joining your line")
            self.store.event(now, "alert", doctor=d["id"], flag=flag)
            outs.append(self._console(d, dsay("red_flag", name=who, quote=text[:500], phone="+" + m["wa"]), ctx=ctx))
        return outs

    def _route(self, m, now, p, d, text) -> list[Out]:
        lang = p["lang"]
        rid = m.get("reply_id") or ""
        if rid in ("read", "dq"):
            return []                                            # the outbox was flushed on arrival
        if rid.startswith("menu:"):
            k = rid[5:]
            if k == "plan":
                return [self._resend_plan(p, d)]
            if k == "ask":
                return [self._out(p, d, say("ask_prompt", lang, doctor=self.dname(d, lang)), "HE-02")]
            if k == "repeat":
                return [self._repeat_menu(p, d)]
            if k == "result":
                return [self._out(p, d, say("result_ask", lang), "RV-01")]
        if rid.startswith("checkin:"):
            return self._checkin_answer(p, d, rid[8:], now)
        if rid.startswith("med:"):
            return self._repeat_request(p, d, rid[4:], now)
        if rid.startswith("rev:"):
            return self._review_answer(p, d, rid, now)
        if rid.startswith("pickup:"):
            return self._pickup(p, d, rid, now)
        if m.get("kind") in ("image", "document"):
            return self._result_received(p, d, m, now)
        if m.get("kind") == "audio":
            h = self.open_handoff(now, p, d, "question", "[voice note]", media_ref=m.get("media_ref"))
            return [self._ack(p, d, h, now), self._question_to_doctor(p, d, h, now)]
        if THANKS.match(text):
            return [self._out(p, d, say("thanks", lang), "thanks")]
        if GREETING.match(text):
            return [self.menu(p, d)]
        if WANT_PLAN.search(text) and len(text) < 40:
            return [self._resend_plan(p, d)]
        if WANT_REPEAT.search(text) and not safety.clinical_question(text):
            return [self._repeat_menu(p, d)]
        if WANT_RESULT.search(text) and len(text) < 40 and not safety.clinical_question(text):
            return [self._out(p, d, say("result_ask", lang), "RV-01")]
        if not text:
            return [self.menu(p, d)]
        h = self.open_handoff(now, p, d, "question", text)                       # anything else goes to the doctor
        return [self._ack(p, d, h, now), self._question_to_doctor(p, d, h, now)]

    def _question_to_doctor(self, p, d, h, now) -> Out:
        flag = "" if p.get("confirmed") or p.get("plan_id") else " (not yet confirmed as your patient)"
        body = dsay("question", name=self.pname(p), flag=flag, quote=h["quote"][:700], when=self._when(d, h, now, "en"))
        plan = self.store.get("plan", p["plan_id"]) if p.get("plan_id") else None
        buttons = [{"id": f"q:write:{h['id']}", "title": dsay("btn_write")}, {"id": f"q:call:{h['id']}", "title": dsay("btn_call")}]
        if plan and h["quote"] != "[voice note]":
            try:
                r = self.model.draft_reply(question=h["quote"], plan_text=plan["text"], doctor=d, lang=p["lang"])
                h["draft"] = r.get("draft", "")
                self.store.put("handoff", h["id"], h)
                body += "\n\n" + dsay("draft_reply", draft=h["draft"])
                buttons.insert(0, {"id": f"q:send:{h['id']}", "title": dsay("btn_send_draft")})
            except Exception as e:  # noqa: BLE001  the doctor still gets the question
                self.store.event(now, "model_failed", job="draft_reply", why=type(e).__name__)
        return self._console(d, body, ctx={"patient_id": p["id"], "handoff_id": h["id"]}, type="buttons", buttons=buttons)

    # ---- the plan --------------------------------------------------------------------------------------------------------
    def _resend_plan(self, p, d) -> Out:
        plan = self.store.get("plan", p["plan_id"]) if p.get("plan_id") else None
        if not plan:
            return self._out(p, d, say("plan_none", p["lang"], doctor=self.dname(d, p["lang"])), "AV-01")
        return Out(to=p["wa"], body=f"{self._label(d, p['lang'], 'approved')}\n{plan['text']}", feature="AV-01", kind="approved",
                   ctx={"patient_id": p["id"], "plan_id": plan["id"], "approved_hash": plan["approved_hash"]})

    # ---- check-ins -------------------------------------------------------------------------------------------------------
    def _checkin_answer(self, p, d, ans, now) -> list[Out]:
        lang, item = p["lang"], self.store.one("item", patient_id=p["id"], kind="checkin", status="sent")
        days = item["days"] if item else 3
        self.store.event(now, "checkin_answer", patient=p["id"], answer=ans)
        if ans == "worse":
            h = self.open_handoff(now, p, d, "question", "Check-in: Worse", checkin=True)
            return [self._out(p, d, say("checkin_worse", lang, doctor=self.dname(d, lang), when=self._when(d, h, now, lang)), "AV-02"),
                    self._console(d, dsay("checkin_worse", name=self.pname(p), days=days, quote="", when=self._when(d, h, now, "en")),
                                  ctx={"patient_id": p["id"], "handoff_id": h["id"]}, type="buttons",
                                  buttons=[{"id": f"q:write:{h['id']}", "title": dsay("btn_write")}, {"id": f"q:call:{h['id']}", "title": dsay("btn_call")}])]
        return [self._out(p, d, say("checkin_better" if ans == "better" else "checkin_same", lang, doctor=self.dname(d, lang)), "AV-02")]

    # ---- repeats (paid) --------------------------------------------------------------------------------------------------
    def _meds(self, p) -> list[str]:
        plan = self.store.get("plan", p["plan_id"]) if p.get("plan_id") else None
        return drafting.medicines(plan["items"]) if plan else []

    def _repeat_menu(self, p, d) -> Out:
        meds, lang = self._meds(p), p["lang"]
        if not meds:
            return self._out(p, d, say("repeat_none", lang, doctor=self.dname(d, lang)), "MD-01")
        rows = [{"id": f"med:{i}", "title": x[:24], "description": x[24:72]} for i, x in enumerate(meds[:10])]
        return self._out(p, d, say("repeat_pick", lang), "MD-01", type="list", rows=rows, button="揀藥" if lang == "zh" else "Choose")

    def _repeat_request(self, p, d, i, now) -> list[Out]:
        meds = self._meds(p)
        if not i.isdigit() or int(i) >= len(meds):
            return [self._repeat_menu(p, d)]
        med, lang = meds[int(i)], p["lang"]
        h = self.open_handoff(now, p, d, "repeat", med, med=med)
        fee = int((d.get("fees") or {}).get("repeat", 0))
        return [self._out(p, d, say("repeat_sent", lang, med=med, doctor=self.dname(d, lang), when=self._when(d, h, now, lang)), "MD-01"),
                self._console(d, dsay("repeat", name=self.pname(p), med=med, fee=fee, service_fee=self.cfg.service_fee_hkd),
                              ctx={"patient_id": p["id"], "handoff_id": h["id"]}, type="buttons",
                              buttons=[{"id": f"rp:ok:{h['id']}", "title": dsay("btn_approve")},
                                       {"id": f"rp:visit:{h['id']}", "title": dsay("btn_visit")},
                                       {"id": f"rp:no:{h['id']}", "title": dsay("btn_decline")}])]

    def repeat_decision(self, d, hid, what, now) -> list[Out]:
        """The doctor's decision on a repeat request: approve (then the patient pays), needs a visit, or decline."""
        h = self.store.get("handoff", hid)
        if not h or h["doctor_id"] != d["id"] or h["status"] != "open":
            return [self._console(d, "That request is already dealt with.")]
        p = self.store.get("patient", h["patient_id"])
        lang, c = p["lang"], self.clinic(d, p.get("clinic_id"))
        cname, phone = c.get("name_zh" if lang == "zh" else "name_en") or c["name_en"], c.get("phone", "")
        if what == "visit":
            self.close_handoff(now, hid, d["id"], "needs a visit")
            return [self._out(p, d, say("repeat_visit", lang, doctor=self.dname(d, lang), med=h["med"], clinic=cname, phone=phone), "MD-01"),
                    self._console(d, dsay("repeat_done", name=self.pname(p), what="was asked to book a visit"))]
        if what == "no":
            self.close_handoff(now, hid, d["id"], "declined")
            return [self._out(p, d, say("repeat_declined", lang, doctor=self.dname(d, lang), med=h["med"], clinic=cname, phone=phone), "MD-01"),
                    self._console(d, dsay("repeat_done", name=self.pname(p), what="was told you can't repeat it here"))]
        fee = int((d.get("fees") or {}).get("repeat", 0))
        h["approved_at"] = iso(now)
        self.store.put("handoff", hid, h)
        pay = self._payment(p, d, "repeat", fee, hid, now, title=f"{d['name_en']}: repeat prescription ({h['med']})")
        if pay and pay.get("url"):
            f = say("fees_line", lang, doctor=self.dname(d, lang), **{k: pay[k] for k in ("doctor_fee", "service_fee", "total")})
            body = say("repeat_approved", lang, doctor=self.dname(d, lang), med=h["med"], fees=f, url=pay["url"], clinic=cname)
            o = self._out(p, d, body, "MD-01", template={"name": "line_payment", "lang": lang_code(lang, "line_payment"),
                                                         "params": [self.dname(d, lang), f"{h['med']}: {f}", pay["url"]]})
            return [o, self._console(d, dsay("repeat_done", name=self.pname(p), what=f"was sent a payment link (HK${pay['total']})"))]
        self.close_handoff(now, hid, d["id"], "approved")
        return [self._out(p, d, say("repeat_free", lang, doctor=self.dname(d, lang), med=h["med"], clinic=cname), "MD-01"),
                self._pickup_menu(p, d, h["med"], None, now), self._console(d, dsay("repeat_done", name=self.pname(p), what="was approved (no charge)"))]

    def _pickup_menu(self, p, d, med, pay_id, now) -> Out:
        lang, c = p["lang"], self.clinic(d, p.get("clinic_id"))
        s = hours.clock({**d, "hours": c.get("hours") or d.get("hours")})
        rows, t = [], now
        for i in range(0, 14):
            day = (now + timedelta(days=i)).date()
            for a, b in s.sessions(day):
                if b > now + timedelta(hours=2) and len(rows) < 6:
                    label = fmt_when(a, now, lang)
                    rows.append({"id": f"pickup:{pay_id or 'free'}:{a.isoformat(timespec='minutes')}:{b.strftime('%H:%M')}",
                                 "title": (label + f"–{b.strftime('%H:%M')}")[:24], "description": ""})
        cname = c.get("name_zh" if lang == "zh" else "name_en") or c["name_en"]
        p.setdefault("pickup_med", {})[pay_id or "free"] = med
        self.store.put("patient", p["id"], p)
        return self._out(p, d, say("pickup_pick", lang, med=med, clinic=cname), "MD-01", type="list", rows=rows,
                         button="揀時間" if lang == "zh" else "Choose a time")

    def _pickup(self, p, d, rid, now) -> list[Out]:
        # pickup:<payment id or 'free'>:<YYYY-MM-DDTHH:MM>:<HH:MM>
        try:
            pay_id, rest = rid[len("pickup:"):].split(":", 1)
            a, end = datetime.fromisoformat(rest[:16]), rest[17:]
        except ValueError:
            return []
        when = fmt_when(a, now, p["lang"]) + "–" + end
        med = (p.get("pickup_med") or {}).get(pay_id, "")
        lang, c = p["lang"], self.clinic(d, p.get("clinic_id"))
        cname = c.get("name_zh" if lang == "zh" else "name_en") or c["name_en"]
        addr = c.get("address_zh" if lang == "zh" else "address_en") or ""
        pay = self.store.get("payment", pay_id) if pay_id and pay_id != "free" else None
        self.store.event(now, "pickup_set", patient=p["id"], payment=pay_id)
        return [self._out(p, d, say("pickup_set", lang, med=med, clinic=cname, address=addr, when=when), "MD-01"),
                self._console(d, dsay("pickup", name=self.pname(p), med=med, clinic=c["name_en"], when=fmt_when(a, now, "en"),
                                      fee=pay["total"] if pay else 0), ctx={"patient_id": p["id"]})]

    # ---- result reviews (paid) ---------------------------------------------------------------------------------------------
    def _result_received(self, p, d, m, now) -> list[Out]:
        lang = p["lang"]
        fee = int((d.get("fees") or {}).get("result", 0))
        p.setdefault("reports", []).append({"ref": m.get("media_ref"), "at": iso(now), "mime": m.get("mime")})
        self.store.put("patient", p["id"], p)
        if not (self.cfg.payments and d.get("stripe_account") and fee > 0):
            h = self.open_handoff(now, p, d, "result", m.get("text") or "[report]", media_ref=m.get("media_ref"))
            return [self._ack(p, d, h, now), self._result_to_doctor(p, d, h, now, paid=None)]
        f = say("fees_line", lang, doctor=self.dname(d, lang), doctor_fee=fee, service_fee=self.cfg.service_fee_hkd, total=fee + self.cfg.service_fee_hkd)
        ref = m.get("media_ref") or ""
        return [self._out(p, d, say("result_offer", lang, doctor=self.dname(d, lang), fees=f), "RV-01", type="buttons",
                          buttons=[{"id": f"rev:yes:{ref}", "title": say("btn_review", lang)}, {"id": f"rev:no:{ref}", "title": say("btn_not_now", lang)}])]

    def _review_answer(self, p, d, rid, now) -> list[Out]:
        _, ans, ref = (rid.split(":", 2) + ["", ""])[:3]
        lang = p["lang"]
        if ans != "yes":
            return [self._out(p, d, say("result_kept", lang, doctor=self.dname(d, lang)), "RV-01")]
        fee = int((d.get("fees") or {}).get("result", 0))
        pay = self._payment(p, d, "result", fee, None, now, title=f"{d['name_en']}: result review", media_ref=ref)
        if not pay or not pay.get("url"):
            h = self.open_handoff(now, p, d, "result", "[report]", media_ref=ref)
            return [self._ack(p, d, h, now), self._result_to_doctor(p, d, h, now, paid=None)]
        return [self._out(p, d, say("result_pay", lang, doctor=self.dname(d, lang), url=pay["url"]), "RV-01",
                          template={"name": "line_payment", "lang": lang_code(lang, "line_payment"),
                                    "params": [self.dname(d, lang), say("m_result", lang), pay["url"]]})]

    def _result_to_doctor(self, p, d, h, now, paid) -> Out:
        url = f"{self.cfg.base_url}/doctor#patient={p['id']}"
        return self._console(d, dsay("result", name=self.pname(p), url=url), ctx={"patient_id": p["id"], "handoff_id": h["id"]},
                             type="buttons", buttons=[{"id": f"q:write:{h['id']}", "title": dsay("btn_write")},
                                                      {"id": f"q:call:{h['id']}", "title": dsay("btn_call")}])

    # ---- payments ---------------------------------------------------------------------------------------------------------
    def _payment(self, p, d, kind, doctor_fee, hid, now, title, media_ref=None) -> dict | None:
        if not (self.cfg.payments and self.stripe and d.get("stripe_account") and doctor_fee > 0):
            return None
        pid = new_id("PAY")
        f = {"doctor_fee": doctor_fee, "service_fee": self.cfg.service_fee_hkd, "total": doctor_fee + self.cfg.service_fee_hkd}
        s = self.stripe.checkout(account=d["stripe_account"], doctor_fee=doctor_fee, service_fee=self.cfg.service_fee_hkd, title=title,
                                 payment_id=pid, success_url=f"{self.cfg.base_url}/paid?p={pid}", cancel_url=f"{self.cfg.base_url}/paid?p={pid}&cancel=1")
        pay = {"id": pid, "patient_id": p["id"], "doctor_id": d["id"], "kind": kind, "handoff_id": hid, "media_ref": media_ref,
               **f, "currency": "HKD", "status": "pending", "session_id": s["id"], "url": s["url"], "created_at": iso(now)}
        self.store.put("payment", pid, pay)
        self.store.event(now, "payment_created", payment=pid, kind=kind, total=f["total"])
        return pay

    def paid(self, payment_id: str, now: datetime | None = None) -> list[str]:
        """Stripe says a checkout completed. Idempotent."""
        now = now or hours.now_hk()
        with self.store.lock:
            pay = self.store.get("payment", payment_id)
            if not pay or pay["status"] == "paid":
                return []
            pay.update(status="paid", paid_at=iso(now))
            self.store.put("payment", payment_id, pay)
            self.store.event(now, "paid", payment=payment_id, kind=pay["kind"], total=pay["total"])
            p, d = self.store.get("patient", pay["patient_id"]), self.doctor(pay["doctor_id"])
            outs = [self._console(d, dsay("paid", name=self.pname(p), total=pay["total"], what="repeat" if pay["kind"] == "repeat" else "result review"),
                                  ctx={"patient_id": p["id"]})]
            if pay["kind"] == "repeat":
                h = self.store.get("handoff", pay["handoff_id"])
                if h and h["status"] == "open":
                    self.close_handoff(now, h["id"], d["id"], "approved and paid")
                outs.insert(0, self._pickup_menu(p, d, h["med"] if h else "", payment_id, now))
            else:
                h = self.open_handoff(now, p, d, "result", "[report: paid review]", media_ref=pay.get("media_ref"), payment_id=payment_id)
                outs = [self._out(p, d, say("result_paid", p["lang"], doctor=self.dname(d, p["lang"]), when=self._when(d, h, now, p["lang"])), "RV-01"),
                        self._result_to_doctor(p, d, h, now, paid=pay)]
            return self.deliver(outs, now)

    # ======================================================================================================================
    # the doctor's console
    # ======================================================================================================================
    def _session(self, d) -> dict:
        return self.store.get("dsession", d["id"]) or {"id": d["id"]}

    def _doctor(self, d, m, now) -> list[Out]:
        text = (m.get("text") or "").strip()
        rid = m.get("reply_id") or ""
        sess = self._session(d)
        if not d.get("welcomed"):                              # first contact: the welcome, then carry on with the message
            d["welcomed"] = iso(now)
            self.store.put("doctor", d["id"], d)
            return [self._doctor_welcome(d, now)] + ([] if text.lower() in ("", "hi", "hello", "start") and not m.get("media_id")
                                                     else self._doctor(d, m, now))
        ctx = self.store.get("ctx", m["context_id"]) if m.get("context_id") else None
        if m.get("media_id") and self.media:
            data, mime = self.tx.download(m["media_id"])
            m["media_ref"] = self.media.put(data, mime or m.get("mime") or "application/octet-stream")
            m["mime"] = mime or m.get("mime")
        self._record_in(m, now, (ctx or {}).get("patient_id"), d["id"])
        # buttons
        if rid:
            return self._doctor_button(d, sess, rid, now)
        # commands
        cmd = DOC_CMD.match(text) if m.get("kind") == "text" else None
        if cmd:
            return self._command(d, cmd.group(1).lower(), cmd.group(2), now)
        # an edit or a reply the doctor is writing; it lapses after 30 minutes so a later message can't go astray
        mode = sess.get("mode")
        if mode and (not sess.get("mode_at") or now - datetime.fromisoformat(sess["mode_at"]) > timedelta(minutes=MODE_MINUTES)):
            for k in ("mode", "mode_at", "patient_id", "handoff_id", "plan_id"):
                sess.pop(k, None)
            self.store.put("dsession", d["id"], sess)
            mode = None
        if mode == "edit" and m.get("kind") == "text" and text:
            return self._plan_edited(d, sess, text, now)
        if mode == "write" and m.get("kind") == "text" and text:
            return self._doctor_writes(d, sess, text, now)
        # what is this about? the swiped message, else the patient who just joined
        target = ctx or {}
        if not target:                                          # no swipe-reply: only if exactly one patient is waiting for a plan
            recent = [p for p in self.store.find("patient", doctor_id=d["id"]) if not p.get("plan_id") and not p.get("not_mine")
                      and p.get("joined_at") and now - datetime.fromisoformat(p["joined_at"]) < timedelta(hours=3)]
            chosen = sess.get("focus") if sess.get("focus_at") and now - datetime.fromisoformat(sess["focus_at"]) < timedelta(minutes=10) and sess.get("chosen") else None
            if chosen:
                target = {"patient_id": chosen}
            elif len(recent) == 1:
                target = {"patient_id": recent[0]["id"]}
        if mode == "write" and m.get("kind") == "audio":
            target = {"patient_id": sess.get("patient_id"), "handoff_id": sess.get("handoff_id")}
        if m.get("kind") not in ("audio", "text"):
            return [self._console(d, dsay("not_doctor_cmd"))]
        if not target.get("patient_id"):
            sess.update(pending={"kind": m.get("kind"), "text": text, "media_ref": m.get("media_ref"), "mime": m.get("mime")})
            self.store.put("dsession", d["id"], sess)
            return [self._who_for(d)]
        return self._compose(d, sess, target, m.get("kind"), text, m.get("media_ref"), m.get("mime"), now)

    def _doctor_welcome(self, d, now) -> Out:
        stripe = ""
        if self.cfg.payments and self.stripe and not d.get("stripe_account"):
            acct = self.stripe.create_account(d.get("email"))
            d["stripe_account"] = acct
            self.store.put("doctor", d["id"], d)
            stripe = dsay("stripe_todo", url=self.stripe.onboarding_link(acct, f"{self.cfg.base_url}/doctor", f"{self.cfg.base_url}/doctor"))
        return self._console(d, dsay("welcome", name=d["name_en"], qr=f"{self.cfg.base_url}/j/{d['code']}", stripe=stripe))

    def _who_for(self, d) -> Out:
        recent = sorted(self.store.find("patient", doctor_id=d["id"]), key=lambda p: p.get("joined_at", ""), reverse=True)[:10]
        if not recent:
            return self._console(d, dsay("no_patients", qr=f"{self.cfg.base_url}/j/{d['code']}"))
        rows = [{"id": f"for:{p['id']}", "title": (p.get("name") or "?")[:24], "description": f"b. {p.get('dob', '')[:4]}, joined {p.get('joined_at', '')[:10]}"} for p in recent]
        return self._console(d, dsay("who_for"), type="list", rows=rows, button="Choose")

    def _command(self, d, cmd, arg, now) -> list[Out]:
        if cmd == "help":
            return [self._console(d, dsay("help"))]
        if cmd == "queue":
            hs = sorted([h for h in self.store.find("handoff", doctor_id=d["id"]) if h["status"] == "open"], key=lambda h: h["reply_by"])
            drafts = [x for x in self.store.find("plan", doctor_id=d["id"]) if x["status"] == "draft"]
            if not hs and not drafts:
                return [self._console(d, dsay("queue_empty"))]
            rows = [f"• {self.pname(self.store.get('patient', h['patient_id']))}: {h['kind']}, “{h['quote'][:60]}”, by {fmt_when(datetime.fromisoformat(h['reply_by']), now, 'en')}" for h in hs]
            rows += [f"• {self.pname(self.store.get('patient', x['patient_id']))}: plan draft waiting for Send" for x in drafts]
            return [self._console(d, dsay("queue", rows="\n".join(rows)))]
        if cmd == "web":
            return [self._console(d, dsay("web", url=self.login_link(d, now)))]
        if cmd == "qr":
            return [self._console(d, dsay("qr", url=f"{self.cfg.base_url}/j/{d['code']}"))]
        if cmd == "pause":
            days = int(arg or 1)
            d["paused_until"] = (now + timedelta(days=days)).date().isoformat()
            self.store.put("doctor", d["id"], d)
            return [self._console(d, dsay("paused", until=d["paused_until"]))]
        if cmd == "resume":
            d.pop("paused_until", None)
            self.store.put("doctor", d["id"], d)
            return [self._console(d, dsay("resumed"))]
        return [self._console(d, dsay("not_doctor_cmd"))]

    def _doctor_button(self, d, sess, rid, now) -> list[Out]:
        parts = rid.split(":")
        if rid == "dq":
            return self._command(d, "queue", None, now)
        if parts[0] == "plan" and len(parts) == 3:
            return self.plan_action(d, parts[2], parts[1], now)
        if parts[0] == "q" and len(parts) == 3:
            return self.question_action(d, parts[2], parts[1], now)
        if parts[0] == "rp" and len(parts) == 3:
            return self.repeat_decision(d, parts[2], parts[1], now)
        if parts[0] == "conf" and len(parts) == 3:
            p = self.store.get("patient", parts[2])
            if p and p["doctor_id"] == d["id"]:
                p["confirmed"] = False
                p["not_mine"] = iso(now)
                self.store.put("patient", p["id"], p)
                if sess.get("focus") == p["id"]:
                    sess.pop("focus", None)
                    self.store.put("dsession", d["id"], sess)
                return [self._console(d, f"Noted: {self.pname(p)} isn't your patient. Nothing from your notes will be shared with them.")]
        if parts[0] == "for" and len(parts) == 2:
            pend = sess.pop("pending", None)
            sess.update(focus=parts[1], focus_at=iso(now), chosen=True)
            self.store.put("dsession", d["id"], sess)
            if pend:
                return self._compose(d, sess, {"patient_id": parts[1]}, pend["kind"], pend.get("text", ""), pend.get("media_ref"), pend.get("mime"), now)
            return [self._console(d, "OK. Send the voice note or text for their plan.")]
        return [self._console(d, dsay("not_doctor_cmd"))]

    # ---- composing from the doctor's words ---------------------------------------------------------------------------------
    def _compose(self, d, sess, target, kind, text, media_ref, mime, now) -> list[Out]:
        p = self.store.get("patient", target["patient_id"])
        if not p or p["doctor_id"] != d["id"]:
            return [self._console(d, "That patient isn't on your line.")]
        h = self.store.get("handoff", target["handoff_id"]) if target.get("handoff_id") else None
        if h and h["status"] == "open" and kind == "text":                       # a typed reply: the doctor's own words
            sess.update(mode="write", patient_id=p["id"], handoff_id=h["id"], mode_at=iso(now))
            return self._doctor_writes(d, sess, text, now)
        audio = None
        if kind == "audio" and self.media and media_ref:
            audio, mime = self.media.get(media_ref)
        job = "reply" if h and h["status"] == "open" else "plan"
        try:
            raw = self.model.compose(kind=job, audio=audio, mime=mime, text=None if audio else text, patient=None, doctor=None,
                                     context=("Reply to the patient's message: " + h["quote"][:300]) if job == "reply" else "")
        except Exception as e:  # noqa: BLE001
            self.store.event(now, "model_failed", job="compose", why=type(e).__name__)
            return [self._console(d, dsay("model_failed", why=type(e).__name__))]
        chk = drafting.check(raw)
        if not chk["items"]:
            return [self._console(d, dsay("model_failed", why="nothing in it could be quoted from what you said"))]
        lang = p["lang"]
        if job == "plan":
            body = drafting.render_plan(chk["items"], lang, doctor={lg: self.dname(d, lg) for lg in ("en", "zh")},
                                        date={"en": f"{now.day} {now.strftime('%b')}", "zh": f"{now.month}月{now.day}日"},
                                        hours={lg: hours.hours_text(d, lg) for lg in ("en", "zh")})
        else:
            body = "\n".join(it[lang] for it in chk["items"])
        plan = {"id": new_id("PL"), "patient_id": p["id"], "doctor_id": d["id"], "job": job, "handoff_id": h["id"] if h else None,
                "status": "draft", "source": kind, "media_ref": media_ref, **chk, "text": body, "created_at": iso(now), "edited": False}
        self.store.put("plan", plan["id"], plan)
        self.store.event(now, "draft", plan=plan["id"], job=job, kept=len(chk["items"]), dropped=len(chk["dropped"]), model=str(chk.get("model")))
        sess.pop("mode", None)
        self.store.put("dsession", d["id"], sess)
        return self._show_draft(d, p, plan)

    def _show_draft(self, d, p, plan) -> list[Out]:
        lines = [dsay("plan_for", name=self.pname(p)) if plan["job"] == "plan" else f"Draft reply to {self.pname(p)}"]
        if plan.get("transcript") and plan["source"] == "audio":
            lines.append(dsay("heard", transcript=plan["transcript"][:600]))
        lines += ["—", plan["text"]]
        if plan.get("dropped"):
            lines.append("—")
            lines.append(dsay("dropped", items="; ".join((x.get("en") or x.get("quote") or "")[:80] for x in plan["dropped"])))
        ctx = {"patient_id": p["id"], "plan_id": plan["id"]}
        return [self._console(d, "\n".join(lines), ctx=ctx),
                self._console(d, dsay("send_q", name=self.pname(p)) if plan["job"] == "plan" else f"Send this reply to {self.pname(p)}?", ctx=ctx,
                              type="buttons", buttons=[{"id": f"plan:send:{plan['id']}", "title": dsay("btn_send")},
                                                       {"id": f"plan:edit:{plan['id']}", "title": dsay("btn_edit")},
                                                       {"id": f"plan:skip:{plan['id']}", "title": dsay("btn_skip")}])]

    def plan_action(self, d, plan_id, what, now) -> list[Out]:
        plan = self.store.get("plan", plan_id)
        if not plan or plan["doctor_id"] != d["id"]:
            return [self._console(d, "I can't find that draft.")]
        if plan["status"] != "draft":
            return [self._console(d, f"That draft was already {plan['status']}.")]
        p = self.store.get("patient", plan["patient_id"])
        sess = self._session(d)
        if what == "edit":
            sess.update(mode="edit", plan_id=plan_id, mode_at=iso(now))
            self.store.put("dsession", d["id"], sess)
            return [self._console(d, dsay("edit_prompt"), ctx={"patient_id": p["id"], "plan_id": plan_id})]
        if what == "skip":
            plan.update(status="skipped", skipped_at=iso(now))
            self.store.put("plan", plan_id, plan)
            return [self._console(d, dsay("skipped", name=self.pname(p)))]
        # send: the doctor's approval is this tap, for exactly this text
        plan.update(status="sent", approved_at=iso(now), approved_by=d["id"], approved_hash=hashlib.sha256(plan["text"].encode()).hexdigest(), sent_at=iso(now))
        self.store.put("plan", plan_id, plan)
        self.store.event(now, "approved", plan=plan_id, doctor=d["id"], edited=plan.get("edited", False), job=plan["job"])
        out = Out(to=p["wa"], body=f"{self._label(d, p['lang'], 'approved')}\n{plan['text']}", feature="AV-01" if plan["job"] == "plan" else "HE-02",
                  kind="approved", ctx={"patient_id": p["id"], "plan_id": plan_id, "approved_hash": plan["approved_hash"]})
        outs = [out]
        if plan["job"] == "plan":
            p.update(plan_id=plan_id, confirmed=True)
            self.store.put("patient", p["id"], p)
            days, rem = self._schedule(d, p, plan, now)
            outs.append(self.menu(p, d))
            r = f", and {rem} reminder(s)" if rem else ""
            outs.append(self._console(d, dsay("sent", name=self.pname(p), days=days, reminders=r)))
        else:
            if plan.get("handoff_id"):
                h = self.store.get("handoff", plan["handoff_id"])
                if h and h["status"] == "open":
                    self.close_handoff(now, h["id"], d["id"], "answered (approved draft)")
            outs.append(self._console(d, dsay("reply_sent", name=self.pname(p))))
        return outs

    def _plan_edited(self, d, sess, text, now) -> list[Out]:
        plan = self.store.get("plan", sess.get("plan_id", ""))
        sess.pop("mode", None)
        self.store.put("dsession", d["id"], sess)
        if not plan or plan["status"] != "draft":
            return [self._console(d, "That draft is no longer open.")]
        p = self.store.get("patient", plan["patient_id"])
        plan.update(text=text, edited=True, items=[{"section": "other", "quote": text, "en": text, "zh": text, "med": ""}] + [it for it in plan["items"] if it["section"] == "take"])
        self.store.put("plan", plan["id"], plan)
        return self._show_draft(d, p, plan)

    def question_action(self, d, hid, what, now) -> list[Out]:
        h = self.store.get("handoff", hid)
        if not h or h["doctor_id"] != d["id"]:
            return [self._console(d, "I can't find that message.")]
        p = self.store.get("patient", h["patient_id"])
        sess = self._session(d)
        if what == "write":
            sess.update(mode="write", patient_id=p["id"], handoff_id=hid, mode_at=iso(now))
            self.store.put("dsession", d["id"], sess)
            return [self._console(d, dsay("write_prompt", name=self.pname(p)), ctx={"patient_id": p["id"], "handoff_id": hid})]
        if what == "call":
            h["calling"] = iso(now)
            self.store.put("handoff", hid, h)
            return [self._console(d, dsay("call_noted", name=self.pname(p)))]
        if what == "send" and h.get("draft") and h["status"] == "open":
            text = h["draft"]
            plan = {"id": new_id("PL"), "patient_id": p["id"], "doctor_id": d["id"], "job": "reply", "handoff_id": hid, "status": "draft",
                    "source": "draft_reply", "text": text, "items": [], "dropped": [], "created_at": iso(now), "edited": False}
            self.store.put("plan", plan["id"], plan)
            return self.plan_action(d, plan["id"], "send", now)
        return [self._console(d, "That message is already dealt with.")]

    def _doctor_writes(self, d, sess, text, now) -> list[Out]:
        """The doctor's own words to a patient, sent exactly as written (labelled as the doctor's)."""
        p = self.store.get("patient", sess.get("patient_id", ""))
        hid = sess.get("handoff_id")
        for k in ("mode", "mode_at", "patient_id", "handoff_id"):
            sess.pop(k, None)
        self.store.put("dsession", d["id"], sess)
        if not p or p["doctor_id"] != d["id"]:
            return [self._console(d, "That patient isn't on your line.")]
        out = self._out(p, d, text, "HE-02", kind="doctor")    # outside 24 h it waits in the outbox until "Read it"
        if hid:
            h = self.store.get("handoff", hid)
            if h and h["status"] == "open":
                self.close_handoff(now, hid, d["id"], "answered by the doctor")
        self.store.event(now, "doctor_wrote", patient=p["id"], doctor=d["id"])
        return [out, self._console(d, dsay("reply_sent", name=self.pname(p)))]

    # ---- check-ins and reminders from the plan -------------------------------------------------------------------------------
    def _schedule(self, d, p, plan, now) -> tuple[int, int]:
        days = plan.get("checkin_days") or 3
        due = hours.next_daytime(d, (now + timedelta(days=days)).replace(hour=10, minute=0))
        self.store.put("item", (iid := new_id("I")), {"id": iid, "patient_id": p["id"], "doctor_id": d["id"], "kind": "checkin",
                                                     "days": days, "due": iso(due), "status": "pending", "plan_id": plan["id"]})
        n = 0
        for r in plan.get("reminders", []):
            due = hours.next_daytime(d, (now + timedelta(days=int(r["due_in_days"]))).replace(hour=10, minute=0))
            self.store.put("item", (iid := new_id("I")), {"id": iid, "patient_id": p["id"], "doctor_id": d["id"], "kind": "reminder",
                                                         "due": iso(due), "en": r["en"], "zh": r["zh"], "status": "pending", "plan_id": plan["id"]})
            n += 1
        return days, n

    def tick(self, now: datetime | None = None) -> list[str]:
        """Every minute: send due check-ins and reminders (in the doctor's daytime only), and nudge overdue items."""
        now = now or hours.now_hk()
        ids = []
        with self.store.lock:
            for it in self.store.find("item", status="pending"):
                if datetime.fromisoformat(it["due"]) > now:
                    continue
                p, d = self.store.get("patient", it["patient_id"]), self.doctor(it["doctor_id"])
                if not p or p.get("state") != "joined":
                    it["status"] = "skipped"
                    self.store.put("item", it["id"], it)
                    continue
                if not hours.is_daytime(d, now):
                    continue
                lang = p["lang"]
                if it["kind"] == "checkin":
                    o = self._out(p, d, say("checkin", lang, first=self.first(p), days=it["days"], doctor=self.dname(d, lang)), "AV-02",
                                  type="buttons", buttons=[{"id": "checkin:better", "title": say("btn_better", lang)},
                                                           {"id": "checkin:same", "title": say("btn_same", lang)},
                                                           {"id": "checkin:worse", "title": say("btn_worse", lang)}],
                                  template={"name": "line_checkin", "lang": lang_code(lang, "line_checkin"),
                                            "params": [self.first(p), str(it["days"]), self.dname(d, lang)],
                                            "payloads": ["checkin:better", "checkin:same", "checkin:worse"]},
                                  ctx={"patient_id": p["id"]})
                else:
                    plan = self.store.get("plan", it["plan_id"])
                    o = Out(to=p["wa"], body=f"{self._label(d, lang, 'approved')}\n{say('reminder', lang, text=it[lang])}", feature="AV-05",
                            kind="approved", template={"name": "line_reminder", "lang": lang_code(lang, "line_reminder"),
                                                       "params": [self.dname(d, lang), it[lang]]},
                            ctx={"patient_id": p["id"], "plan_id": it["plan_id"],
                                 "approved_hash": hashlib.sha256(say("reminder", lang, text=it[lang]).encode()).hexdigest()
                                 if plan and plan.get("status") == "sent" else None})
                it.update(status="sent", sent_at=iso(now))
                self.store.put("item", it["id"], it)
                ids += self.deliver([o], now)
            for d in self.store.all("doctor"):
                late = [h for h in self.store.find("handoff", doctor_id=d["id"]) if h["status"] == "open" and not h.get("nudged")
                        and datetime.fromisoformat(h["reply_by"]) <= now]
                if late and hours.is_daytime(d, now):
                    for h in late:
                        h["nudged"] = iso(now)
                        self.store.put("handoff", h["id"], h)
                    ids += self.deliver([self._console(d, dsay("overdue", n=len(late)))], now)
        return ids

    # ---- web sign-in ------------------------------------------------------------------------------------------------------
    def login_link(self, d, now) -> str:
        import secrets
        tok = secrets.token_urlsafe(24)
        self.store.put("token", hashlib.sha256(tok.encode()).hexdigest(), {"doctor_id": d["id"], "expires": iso(now + timedelta(minutes=15)), "used": False})
        return f"{self.cfg.base_url}/d/{tok}"

    def redeem(self, tok: str, now) -> str | None:
        k = hashlib.sha256(tok.encode()).hexdigest()
        t = self.store.get("token", k)
        if not t or t["used"] or datetime.fromisoformat(t["expires"]) < now:
            return None
        t["used"] = True
        self.store.put("token", k, t)
        return t["doctor_id"]
