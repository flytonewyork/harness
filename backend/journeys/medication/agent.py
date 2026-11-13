"""The medication journey: a cooperative mixin over the engine (compose it before AgenticHarness or RulesHarness).

What it watches, and what it does (open or raise only; it never closes, dismisses, downgrades or reassures, and
never prescribes, changes a dose or approves a repeat):

1. A patient's message about their medicines (care line, WhatsApp shown in the system, or a phone memo):
   - a possible reaction, side effect, confusion, missed doses, a dose question or a crisis
     -> a `med_change` item for the prescriber, urgency from the symptom and the medicine's class, with the
        patient's words quoted and the recent change cited. A later, worse message raises the same item.
   - a repeat request -> a `repeat_prescription` item for the prescriber, with what the agent checked:
     on the list, stopped since, early, controlled, short course, monitoring bloods on file, last review.
     Running out of a medicine where a gap is dangerous makes it same-day.
2. A medicine dispensed again after a doctor or a hospital letter stopped it -> a `med_change` reconciliation item.
3. Each tick: an open repeat item whose supply runs out before the doctor has acted is raised.

Every item's request step carries the suggested fixed reply (templates.py) for the doctor to send.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from . import formulary as fx
from . import templates as tpl
from .reader import RulesReader

FAMILIES = {
    "med_change": "A problem after a medicine was started, stopped or changed: side effect, confusion, a dose question, "
                  "or a stopped medicine dispensed again",
    "repeat_prescription": "A patient asks for a repeat prescription; the doctor approves, declines or books a visit",
}
PATIENT_KINDS = ("message",)
PATIENT_CHANNELS = ("careline", "whatsapp", "phone")
STAFF_KINDS = ("consult_note", "action_note")
CHANGE_KINDS = ("consult_note", "discharge_summary", "specialist_letter", "aed_summary")
START = re.compile(r"\b(re-?start(ed|ing)?|start(ed|ing)?|commenc(e|ed)|initiat(e|ed)|add(ed)?|began|new(ly started)?|trial of)\b", re.I)
STOP = re.compile(r"\b(stop(ped)?|discontinu(e|ed)|cease(d)?|withh(e|o)ld|withheld|withdraw(n)?|off)\b", re.I)
CHANGE = re.compile(r"\b(increas(e|ed)|decreas(e|ed)|reduc(e|ed)|titrat(e|ed)|uptitrat(e|ed)|halv(e|ed)|switch(ed)?|chang(e|ed))\b", re.I)
FROM_TO = re.compile(r"(?:switch(?:ed)?|chang(?:e|ed))\s+(?:from\s+)?(?P<a>[A-Za-z][\w-]+).{0,30}?\bto\s+(?P<b>[A-Za-z][\w-]+)", re.I)
DISPENSED = re.compile(r"Dispensed:\s*(?P<drug>.+?)\s+x(?P<days>\d+) days", re.I)
ALL_MEDS = re.compile(r"\b(all|usual|regular|everything|same as (before|usual|last time))\b|平時(啲|嘅)?藥|全部|所有(嘅)?藥|長期藥|成套", re.I)
RAN_OUT = re.compile(r"\b(ran out|run out|have none|(none|no more|nothing) left|finished|(used|took) (up )?(my )?(last|final))\b|食晒|冇晒藥|冇藥(喇|啦)?|已經冇", re.I)
OUTSIDE_REPORT = re.compile(r"報告|report", re.I)
RECENT_DAYS = 60
AFTER_MED = re.compile(r"(食|吃|服)(咗|完|了).{0,8}藥.{0,6}(之後|後|就)|(after|since) (taking|starting|i started|the new) .{0,20}(medicine|tablets?|pills?|antibiotics?)", re.I)
NET_DAYS = 14
ACK = re.compile(r"(?i)[\W_]*((ok(ay)?|noted|thanks?( you)?|thank you|ty|got it|sure|fine|bye|see you[\w ]*|received|👍|🙏|多謝(晒)?(醫生)?|唔該(晒)?(醫生)?|收到|好(的|呀|嘅)|明白|拜拜|新年快樂|happy [\w ]+)[\W_]*)+(doctor|dr|醫生)?[\W_]*")
MED_WORD = re.compile(r"\b(medicines?|meds|medication|tablets?|pills?|capsules?|prescription|script|dose)\b|(?<!退燒)藥(?!房)", re.I)
ACTION_WORD = {"start": "starting", "stop": "stopping", "change": "a change to"}


def _n(s: str) -> str:
    return " ".join(str(s).split())


def _t(r: dict) -> datetime:
    return datetime.fromisoformat(r["available_at"])


def _lines(text: str):
    for ln in (text or "").splitlines():
        for part in re.split(r"(?<=[.;])\s+", ln):
            if _n(part):
                yield _n(part)


class MedicationJourney:
    """Mixin: class Suite(MedicationJourney, AgenticHarness). Every hook calls super() first."""

    FAMILIES = FAMILIES
    med_reader = None                    # set to a ModelReader to let a model add to the floor

    # ------------------------------------------------------------------------------------- state
    def reset(self, api):
        super().reset(api)
        if self.med_reader is None:
            self.med_reader = RulesReader()
        self.mx_changes = defaultdict(list)     # pid -> [{t, rid, action, drug, quote, author}]
        self.mx_stops = defaultdict(dict)       # pid -> generic -> change
        self.mx_meds = defaultdict(dict)        # pid -> generic -> {name, t, rid, quote, days, author}
        self.mx_inbox: list[dict] = []          # routine messages, read at the scheduled run
        self.mx_items: dict[str, dict] = {}     # item id -> what this journey opened
        self.mx_by_pid = defaultdict(list)      # pid -> [item id]
        self.mx_stats = defaultdict(int)

    # ---------------------------------------------------------------------------------- reading
    def claims(self, api, r) -> bool:
        """Patient messages about medicines are this journey's: core's generic message rule skips them."""
        parent = getattr(super(), "claims", None)
        return MedicationJourney.bid(self, api, r) > 0 or bool(parent and parent(api, r))

    def bid(self, api, r) -> float:
        """How strongly this journey owns a patient message (the suite gives it to the highest bid): 3 for a repeat
        request or a message about a medicine, 1.5 for symptoms within 60 days of a medicine change (so a reply to
        another journey's own check-in stays with that journey), else 0."""
        if not self._is_patient_msg(r) or not r.get("patient_id"):
            return 0
        pid, t, text = r["patient_id"], _t(r), r["text"]
        if ACK.fullmatch(text.strip()):
            return 1.5 if self._recent(pid, t) else 0
        if not self._wants(r):
            return 0
        fs = self.med_reader.read(text, self._classes(pid))
        if any(f["kind"] == "repeat" for f in fs) or fx.mentions(text) or fx.purposes(text) or MED_WORD.search(text):
            return 3
        if fs and any(self.mx_items[i]["family"] == "repeat_prescription" for i in self.mx_by_pid.get(pid, ())
                      if any(it["id"] == i for it in api.items(status="open"))):
            return 3                                 # a follow-up on their open repeat request ("any update?")
        return 1.5 if self._recent(pid, t) else 0

    def _mine(self, api, r) -> bool:
        won = getattr(self, "won", None)               # the suite's one-owner-per-message routing, when composed
        return MedicationJourney.bid(self, api, r) > 0 and (won is None or won(api, r, MedicationJourney))

    # An acknowledgement ("收到，多謝") within 60 days of a change is this journey's too (bid 1.5) and needs nothing.

    def on_cms(self, api, r: dict):
        super().on_cms(api, r)
        pid, k = r.get("patient_id"), r["kind"]
        if not pid:
            return
        if k == "dispensing":
            self._dispensed(api, pid, r)
        if k in CHANGE_KINDS:
            self._read_changes(pid, r)
        if self._is_patient_msg(r) and r["data"].get("direction") != "out" and self._mine(api, r):
            fs = self._read(pid, _t(r), r["text"])
            if max((f["urgency"] for f in fs), default=0) >= 3 or any(f["kind"] == "repeat" for f in fs):
                self._safe(self._message, api, r)    # urgent or a repeat: read as it lands (the care line pushes it live)
            else:
                self.mx_inbox.append(r)              # the rest at the scheduled run, after the doctor's own replies

    def tick(self, api):
        super().tick(api)
        inbox, self.mx_inbox = self.mx_inbox, []
        for r in inbox:
            self._safe(self._message, api, r)
        self._watch_supply(api)

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except KeyError:                             # a patient or doctor the port no longer knows: never crash
            self.mx_stats["skipped_unknown"] += 1

    def chan_label(self, ch: str) -> str:
        if ch == "careline":
            return "the clinic's care line on WhatsApp"
        return super().chan_label(ch)

    def _is_patient_msg(self, r) -> bool:
        if r["kind"] in PATIENT_KINDS and r["channel"] in PATIENT_CHANNELS:
            return True
        return r["kind"] == "phone_memo" and r.get("author") is None and bool(re.search(r"\b(patient|pt)\b.{0,20}(called|rang|phoned|says)", r["text"], re.I))

    def _read(self, pid, t, text) -> list[dict]:
        """The reader's findings, plus the safety net: within 14 days of a high-risk change, a message the reader
        cannot classify (and that is not just thanks or an acknowledgement) still goes to the doctor at urgency 2."""
        fs = self.med_reader.read(text, self._classes(pid))
        if not fs and not ACK.fullmatch(text.strip()) and any(
                c["action"] != "stop" and fx.drug_class(c["drug"]) in fx.HIGH_RISK and t - c["t"] <= timedelta(days=NET_DAYS)
                for c in self._recent(pid, t)):
            q = next((ln for ln in _lines(text)), text.strip())[:240]
            fs = [{"kind": "unclassified", "label": "a message the agent could not classify, soon after a high-risk change",
                   "urgency": 2, "quote": q, "note": "", "classes": [], "via": "safety net"}]
            self.mx_stats["safety_net"] += 1
        elif not fs and not ACK.fullmatch(text.strip()) and self._recent(pid, t):
            q = next((ln for ln in _lines(text)), text.strip())[:240]
            fs = [{"kind": "reply", "label": "a reply after a medicine change with nothing the agent could flag",
                   "urgency": 1, "quote": q, "note": "", "classes": [], "via": self.med_reader.name}]
        new_drug = any(c["action"] != "stop" and t - c["t"] <= timedelta(days=NET_DAYS) for c in self._recent(pid, t))
        for f in fs:                             # a new rash on a new medicine may be a drug reaction: seen the same day
            if f["label"] == "rash" and f["urgency"] < 3 and (new_drug or AFTER_MED.search(text)):
                f["urgency"], f["note"] = 3, (f.get("note") or "") + " New rash soon after a new medicine: possible drug reaction."
        return fs

    def _wants(self, r) -> bool:
        text = r["text"]
        if OUTSIDE_REPORT.search(text) and not fx.mentions(text):
            return False                         # an outside report: core's rule owns it
        fs = self._read(r.get("patient_id"), _t(r), text)
        if any(f["kind"] != "question" for f in fs):
            return True
        return bool(fs) and bool(fx.mentions(text) or fx.purposes(text) or self._recent(r.get("patient_id"), _t(r)))

    def _classes(self, pid) -> set[str]:
        out = set()
        for g, m in self.mx_meds.get(pid, {}).items():
            if g not in self.mx_stops.get(pid, {}) or self.mx_stops[pid][g]["t"] < m["t"]:
                c = fx.drug_class(g)
                if c:
                    out.add(c)
        for c in self.mx_changes.get(pid, ()):
            if c["action"] != "stop":
                k = fx.drug_class(c["drug"])
                if k:
                    out.add(k)
        return out

    def _recent(self, pid, t) -> list[dict]:
        return [c for c in self.mx_changes.get(pid, ()) if timedelta(0) <= t - c["t"] <= timedelta(days=RECENT_DAYS)]

    def _read_changes(self, pid, r):
        t = _t(r)
        for ln in _lines(r["text"]):
            drugs = fx.mentions(ln)
            if not drugs:
                continue
            ft = FROM_TO.search(ln)
            pairs = []
            if ft and fx.mentions(ft.group("a")) and fx.mentions(ft.group("b")):
                pairs = [("stop", fx.mentions(ft.group("a"))[0]), ("start", fx.mentions(ft.group("b"))[0])]
            else:
                act = "stop" if STOP.search(ln) else "change" if CHANGE.search(ln) else "start" if START.search(ln) else None
                if act is None:
                    continue
                pairs = [(act, d) for d in drugs]
            for act, d in pairs:
                ch = {"t": t, "rid": r["id"], "action": act, "drug": d, "quote": ln[:240], "author": r.get("author"),
                      "kind": r["kind"]}
                self.mx_changes[pid].append(ch)
                if act == "stop":
                    self.mx_stops[pid][d] = ch
                elif d in self.mx_stops[pid] and self.mx_stops[pid][d]["t"] <= t:
                    self.mx_stops[pid].pop(d)    # restarted on purpose

    def _dispensed(self, api, pid, r):
        m = DISPENSED.search(r["text"])
        if not m:
            return
        name, days = m.group("drug").strip(), int(m.group("days"))
        g = fx.generic(name)
        t = _t(r)
        quote = _n(m.group(0))
        stop = self.mx_stops.get(pid, {}).get(g)
        if g not in self.mx_meds[pid] and not any(c["drug"] == g and c["action"] != "stop" for c in self.mx_changes[pid]):
            self.mx_changes[pid].append({"t": t, "rid": r["id"], "action": "start", "drug": g, "quote": quote,
                                         "author": r.get("author"), "kind": "dispensing"})    # first supply here = a start
        self.mx_meds[pid][g] = {"name": name, "t": t, "rid": r["id"], "quote": quote, "days": days, "author": r.get("author")}
        if stop and stop["t"] < t and not r["data"].get("new_start"):
            self._reconcile(api, pid, r, name, quote, stop)

    # ----------------------------------------------------------------------------- the items
    def _why(self):
        from backend.engine.rules import Why        # the engine's trace builder (imported late: no cycle)
        return Why()

    def _open(self, api, key, pid, family, reason, evidence, urgency, hint, why, meta, escalates=None):
        before = set(self.raised.values()) if hasattr(self, "raised") else set()
        if escalates is None:
            self.raise_item(api, key, pid, family, reason[:200], evidence, urgency, hint, None, why=why)
        elif key not in self.raised:                 # the engine's raise_item has no escalates: same steps, linked
            from backend.engine.rules import URGENCY_LABEL, WINDOW, when
            owner, route_txt = self.route(api, hint, pid, urgency)
            due = api.now + timedelta(days=WINDOW[urgency])
            why.add("routing", route_txt)
            why.add("request", f"Opened for {self.nm(owner)} at urgency {urgency} ({URGENCY_LABEL[urgency]}); due {when(due)}.")
            self.raised[key] = api.open_item(pid, owner, family, reason[:200], list(evidence), urgency, due,
                                             reasoning=why.steps, escalates=escalates)
        iid = self.raised.get(key)
        if iid and iid not in before:
            self.mx_items[iid] = {"pid": pid, "family": family, "urgency": urgency, **meta}
            self.mx_by_pid[pid].append(iid)
            self.mx_stats[f"opened_{family}"] += 1
        return iid

    def _open_mine(self, api, pid, family) -> dict | None:
        """This journey's item for this patient and family that the doctor has not actioned yet."""
        open_ids = {it["id"]: it for it in api.items(status="open") if it["patient_id"] == pid}
        for iid in reversed(self.mx_by_pid.get(pid, [])):
            if iid in open_ids and self.mx_items[iid]["family"] == family:
                return {**self.mx_items[iid], "id": iid, "urgency": open_ids[iid]["urgency"]}
        return None

    def _handled_since(self, api, pid, t) -> int:
        n, _ = self.chart_search(api, pid, list(STAFF_KINDS), t + timedelta(minutes=1))
        return n

    def _message(self, api, r):
        pid, t, text = r["patient_id"], _t(r), r["text"]
        fs = self._read(pid, t, text)
        if not fs:
            return
        if self._handled_since(api, pid, t):
            self.mx_stats["already_handled"] += 1
            return
        rep = [f for f in fs if f["kind"] == "repeat"]
        other = [f for f in fs if f["kind"] != "repeat"]
        if rep:                                  # "can I have more of my BP pills?" is a repeat, not a dose question
            other = [f for f in other if f["kind"] not in ("question", "dose_question")]
        if other:
            self._concern(api, r, pid, t, text, other)
        if rep:
            self._repeat(api, r, pid, t, text, rep[0])

    def _concern(self, api, r, pid, t, text, fs):
        urgency = max(f["urgency"] for f in fs)
        kinds = {f["kind"] for f in fs}
        recent = self._recent(pid, t)
        named = fx.mentions(text)
        rel = [c for c in recent if c["drug"] in named] or recent[-1:]
        hint = rel[-1]["author"] if rel else next((self.mx_meds[pid][g]["author"] for g in named if g in self.mx_meds.get(pid, {})), None)
        top = fs[0]
        existing = self._open_mine(api, pid, "med_change")
        escalates = None
        if existing and urgency >= 4 > existing["urgency"]:
            escalates = existing["id"]               # now an emergency: a linked copy with its own verified quotes
        elif existing:
            if urgency > existing["urgency"]:
                api.raise_urgency(existing["id"], urgency, f"New message from the patient: {top['label']}: “{top['quote'][:120]}”")
                self.mx_items[existing["id"]]["urgency"] = urgency
                self.mx_stats["raised_by_new_message"] += 1
            else:
                self.mx_stats["duplicate_message"] += 1
            return
        drug = rel[-1]["drug"] if rel else (named[0] if named else None)
        what = {"crisis": "Patient may be at risk of self-harm", "unclassified": "Message after a high-risk change: please read",
                "reply": "Reply after a medicine change: nothing flagged; doctor to read and close", "confusion": "Patient unsure what to take",
                "adherence": "Patient stopped or skipping a medicine", "dose_question": "Patient asks to change a dose",
                "question": "Question about a medicine"}
        head = what.get(top["kind"], f"Possible reaction: {top['label']}")
        reason = f"{head}{f' after {ACTION_WORD[rel[-1]["action"]]} {drug}' if rel else (f' ({drug})' if drug else '')}"
        w = self._why()
        w.add("source", f"Message from the patient on {t:%a %d %b %H:%M} ({self.chan_label(r['channel'])}).",
              {"record": r["id"], "quote": top["quote"]})
        w.add("identity", f"Filed to {self.pname(pid)}'s chart by the number they registered.", {"record": r["id"]})
        for f in fs[:4]:
            extra = f" ({f['note']})" if f["note"] else ""
            cls = f"; more urgent because they take {', '.join(c.replace('_', ' ') for c in f['classes'])}" if f["classes"] else ""
            w.add("finding", f"{f['label'].capitalize()}{extra}{cls}. Read by {f['via']}.", {"record": r["id"], "quote": f["quote"]})
        ev = [r["id"]]
        for c in rel[-2:]:
            w.add("context", f"{c['action'].capitalize()} of {c['drug']} on {c['t']:%d %b %Y}"
                             f"{' by ' + self.nm(c['author']) if c['author'] in self.staff else ''}.", {"record": c["rid"], "quote": c["quote"]})
            ev.append(c["rid"])
        if not rel:
            w.add("context", "No medicine change recorded here in the last 60 days" +
                  (f"; they take {', '.join(sorted(self.mx_meds.get(pid, {})))}." if self.mx_meds.get(pid) else "."))
        n, q = self.chart_search(api, pid, list(STAFF_KINDS), t + timedelta(minutes=1))
        w.add("loop_status", f"{'No' if n == 0 else n} consultation or call note since the message.", search=q)
        w.add("assessment", f"Working problem: {top['label']}{' after a medicine change' if rel else ''}. "
                            "The agent makes no diagnosis and has not told the patient to stop, start or change anything.")
        tid = tpl.pick(kinds, urgency)
        w.add("request", "Suggested reply once you have decided (fixed template, sent by you): "
                         f"“{tpl.render(tid, 'en', doctor=self.nm(hint) if hint in self.staff else 'your doctor', when='today' if urgency >= 3 else 'within 2 days')}”")
        if escalates:
            w.add("loop_status", "Escalates the open item for this patient: the earlier message was less urgent.")
        self._open(api, ("mx", r["id"]), pid, "med_change", reason, ev, urgency, hint, w,
                   {"kind": "concern", "labels": [f["label"] for f in fs], "template": tid}, escalates=escalates)

    def _requested(self, pid, text) -> tuple[list[str], str]:
        meds = self.mx_meds.get(pid, {})
        named = fx.mentions(text)
        if named:
            return named, "named"
        cls = fx.purposes(text)
        by_cls = [g for g in meds if fx.drug_class(g) in cls]
        if by_cls:
            return by_cls, "by purpose"
        live = [g for g in meds if fx.drug_class(g) not in fx.SHORT_COURSE]
        if ALL_MEDS.search(text) or len(live) == 1:
            return live, "usual medicines"
        return live, "not specified"

    def _repeat(self, api, r, pid, t, text, f):
        meds = self.mx_meds.get(pid, {})
        want, how = self._requested(pid, text)
        ran_out = bool(RAN_OUT.search(text))
        existing = self._open_mine(api, pid, "repeat_prescription")
        if existing:
            gap = ran_out and any(fx.drug_class(g) in fx.NO_GAP for g in existing.get("meds", []))
            u = 3 if gap else 2 if ran_out else existing["urgency"]
            if u > existing["urgency"]:
                api.raise_urgency(existing["id"], u, f"The patient wrote again and has run out: “{f['quote'][:120]}”")
                self.mx_items[existing["id"]]["urgency"] = u
                self.mx_stats["raised_by_new_message"] += 1
            else:
                self.mx_stats["duplicate_message"] += 1
            return
        done = {it["id"] for it in api.items() if it["patient_id"] == pid and it["status"] != "open"}
        if any(i in done and self.mx_items[i]["family"] == "repeat_prescription" and set(self.mx_items[i].get("meds", [])) & set(want)
               and t - self.mx_items[i]["asked_at"] <= timedelta(days=7) for i in self.mx_by_pid.get(pid, ())):
            self.mx_stats["after_decision"] += 1      # the doctor has decided this request; the clinic answers the chase
            return
        w = self._why()
        w.add("source", f"Repeat request from the patient on {t:%a %d %b %H:%M} ({self.chan_label(r['channel'])}).",
              {"record": r["id"], "quote": f["quote"]})
        w.add("identity", f"Filed to {self.pname(pid)}'s chart by the number they registered.", {"record": r["id"]})
        ev, flags, urgency = [r["id"]], [], 1
        if not want:
            flags.append("no medicine dispensed here to repeat")
            w.add("finding", "Nothing has been dispensed here that matches the request; the doctor needs to ask what they take.")
            urgency = 2
        for g in want[:6]:
            m = meds.get(g)
            cls = fx.drug_class(g)
            label = m["name"] if m else g
            notes = []
            if m is None:
                notes.append("not dispensed here before")
                flags.append(f"{g}: not on the medication list")
                urgency = max(urgency, 2)
            else:
                ev.append(m["rid"])
                left = m["t"] + timedelta(days=m["days"]) - t
                if left > timedelta(days=round(m["days"] * 0.25)):
                    notes.append(f"early: dispensed {(t - m['t']).days} days ago for {m['days']} days")
                    flags.append(f"{g}: early")
                    urgency = max(urgency, 2)
                if ran_out and cls in fx.NO_GAP:
                    notes.append("has run out, and a gap in this medicine is unsafe")
                    flags.append(f"{g}: run out")
                    urgency = max(urgency, 3)
            stop = self.mx_stops.get(pid, {}).get(g)
            if stop and (m is None or stop["t"] > m["t"]):
                notes.append(f"stopped on {stop['t']:%d %b %Y}")
                flags.append(f"{g}: stopped")
                ev.append(stop["rid"])
                urgency = max(urgency, 3 if cls in fx.HIGH_RISK or cls in fx.NO_GAP else 2)
            if cls in fx.CONTROLLED:
                notes.append("controlled medicine")
                flags.append(f"{g}: controlled")
                urgency = max(urgency, 2)
            if cls in fx.SHORT_COURSE:
                notes.append("a short course, not usually repeated")
                flags.append(f"{g}: short course")
                urgency = max(urgency, 2)
            mon = fx.monitoring_for(g)
            search = None
            if mon and m is not None and not (stop and stop["t"] > m["t"]):
                tests, days = mon
                n, search = self.chart_search(api, pid, ["lab_report"], t - timedelta(days=days), list(tests))
                if n == 0:
                    notes.append(f"no {' / '.join(tests[:2])} on file in {days} days")
                    flags.append(f"{g}: monitoring due")
                    urgency = max(urgency, 2)
            cites = [{"record": m["rid"], "quote": m["quote"]}] if m else []
            if stop and stop["rid"] in ev:
                cites.append({"record": stop["rid"], "quote": stop["quote"]})
            w.add("finding", f"{label} ({cls.replace('_', ' ') if cls else 'class unknown'}): "
                             f"{'; '.join(notes) if notes else 'on the list, due, nothing flagged'}.", *cites, search=search)
        n, q = self.chart_search(api, pid, ["consult_note"], t - timedelta(days=fx.REVIEW_DAYS))
        w.add("context", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} here in the last {fx.REVIEW_DAYS} days.", search=q)
        if n == 0:
            flags.append("no review in a year")
            urgency = max(urgency, 2)
        w.add("loop_status", f"Request {'names ' + ', '.join(want) if how == 'named' else how}. Not approved: only a doctor can approve a repeat.")
        w.add("assessment", "Working problem: repeat prescription request" +
              (f" with {len(flags)} thing{'s' if len(flags) != 1 else ''} to check: {'; '.join(flags)}." if flags else ", nothing flagged."))
        hint = next((meds[g]["author"] for g in want if g in meds), None)
        names = ", ".join(meds[g]["name"] if g in meds else g for g in want[:3]) or "medicines not named"
        w.add("request", "Approve, decline or book a visit. Suggested reply once you decide (fixed template, sent by you): "
                         f"“{tpl.render('repeat_received', 'en', doctor=self.nm(hint) if hint in self.staff else 'your doctor', medicine=names, when='within 2 working days')}”")
        reason = f"Repeat request: {', '.join(want[:4]) or 'not named'}" + (f". Check: {'; '.join(flags)}" if flags else "")
        self._open(api, ("rx", r["id"]), pid, "repeat_prescription", reason, list(dict.fromkeys(ev)), urgency, hint, w,
                   {"kind": "repeat", "meds": want, "flags": flags, "template": "repeat_received", "asked_at": t})

    def _reconcile(self, api, pid, r, name, quote, stop):
        t = _t(r)
        w = self._why()
        w.add("source", f"Dispensing record on {t:%a %d %b %H:%M}.", {"record": r["id"], "quote": quote})
        w.add("identity", f"Dispensed from {self.pname(pid)}'s chart.", {"record": r["id"]})
        src = {"consult_note": "a consultation here", "discharge_summary": "a hospital discharge summary",
               "specialist_letter": "a specialist letter", "aed_summary": "an A&E summary"}.get(stop["kind"], "a record")
        w.add("finding", f"{name} was dispensed again, but {src} on {stop['t']:%d %b %Y} stopped it.",
              {"record": stop["rid"], "quote": stop["quote"]})
        n, q = self.chart_search(api, pid, ["consult_note"], stop["t"] + timedelta(minutes=1), [fx.generic(name).capitalize(), fx.generic(name)])
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} since the stop that mention{'s' if n == 1 else ''} it.", search=q)
        w.add("assessment", "Working problem: a stopped medicine is being taken again (reconciliation). The agent has "
                            "not told the patient to stop, start or change anything.")
        w.add("request", "Check whether it should have been restarted, and tell the patient yourself.")
        cls = fx.drug_class(name)
        self._open(api, ("recon", r["id"]), pid, "med_change", f"Stopped medicine dispensed again: {name}",
                   [r["id"], stop["rid"]], 3 if cls in fx.HIGH_RISK or cls in fx.NO_GAP or cls in BLEEDY else 2, r.get("author"), w,
                   {"kind": "reconcile", "meds": [fx.generic(name)]})

    def _watch_supply(self, api):
        now = api.now
        open_ids = {it["id"]: it for it in api.items(status="open")}
        for iid, m in self.mx_items.items():
            if m["family"] != "repeat_prescription" or iid not in open_ids or open_ids[iid]["urgency"] >= 3:
                continue
            meds = self.mx_meds.get(m["pid"], {})
            ends = [meds[g]["t"] + timedelta(days=meds[g]["days"]) for g in m.get("meds", []) if g in meds]
            if ends and now >= min(ends) - timedelta(days=1) and now - m["asked_at"] >= timedelta(days=1):
                api.raise_urgency(iid, 3, f"Still not decided and the supply ends {min(ends):%a %d %b}.")
                self.mx_items[iid]["urgency"] = 3
                self.mx_stats["raised_supply_ending"] += 1


BLEEDY = {"antiplatelet"}
