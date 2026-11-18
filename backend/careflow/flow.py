"""US-00 consent, and compose lock v4 for these two weeks.

Decline, or two unclear answers, stops the flow and keeps nothing. STOP is respected. Consent, STOP, and
crisis / 999 still send as fixed templates. Any other reply is a draft: it is not sent until the doctor taps.
The patient's words go on an item only after labelled consent. No live model. The patient text is the draft
itself, never the reasoning.
"""
from __future__ import annotations

import re

from backend.precare import safety as precare_safety
from backend.precare.agent import NO, has_cjk, is_yes

from . import guard
from .texts import TEXT_VERSION, render

QUOTE_MAX = 300
# Compose lock v4. A composed draft waits for a doctor tap during this phase. There is no timer that sends it.
DRAFT_HOLD = "two-weeks"


def screen_inbound(text: str) -> str | None:
    """The rules floor. Tests may replace this binding; the scorer still uses precare.safety.screen."""
    return precare_safety.screen(text)


class CareFlow:
    def __init__(self, port, composer=None):
        self.port = port
        self.composer = composer          # drafts only; this class does not call a model itself
        self.state: dict[str, dict] = {}
        self.blocked: list[dict] = []

    def _st(self, thread_id: str) -> dict:
        if thread_id not in self.state:
            t = self.port.thread(thread_id)
            self.state[thread_id] = {
                "patient_id": t["patient_id"], "owner_id": t["owner_id"],
                "doctor_en": t["doctor_en"], "doctor_zh": t.get("doctor_zh") or t["doctor_en"],
                "step": "new", "lang": "en", "consent_asks": 0, "consent": None, "kept": [], "drafts": [],
                "silenced": False, "needs_review": False, "flags": [], "turn": -1,
            }
        return self.state[thread_id]

    def _doctor(self, st: dict, lang: str) -> str:
        return st["doctor_zh"] if lang == "zh" else st["doctor_en"]

    def _send(self, thread_id: str, st: dict, act: str) -> str | None:
        lang = "en" if act == "consent" else st["lang"]
        text = render(act, lang, self._doctor(st, "en" if act == "consent" else lang))
        if guard.blocked(text):
            self.blocked.append({"thread": thread_id, "act": act, "fails": guard.clinical_closure(text)})
            self.port.alarm("careflow", f"outbound blocked on {thread_id}: {act}")
            return None
        return self.port.send_fixed(thread_id, act, lang, text)

    def start(self, thread_id: str):
        st = self._st(thread_id)
        if st["step"] != "new":
            return
        st["step"] = "consent"
        st["consent_asks"] = 1
        st["turn"] = 0
        self.port.begin_turn(0, silenced=False, inbound=None)
        self._send(thread_id, st, "consent")

    def handle(self, thread_id: str, msg_id: str):
        st = self._st(thread_id)
        msg = next(m for m in self.port.messages(thread_id) if m["id"] == msg_id)
        text = msg.get("text") or ""
        st["turn"] += 1
        self.port.begin_turn(st["turn"], silenced=st["silenced"], inbound=msg["id"])
        if has_cjk(text):
            st["lang"] = "zh"
        kind = screen_inbound(text)
        if kind:
            return self._escalate(thread_id, st, msg, kind)
        if precare_safety.is_stop(text):
            return self._stop(thread_id, st)
        if st["silenced"]:
            return None
        if st["step"] == "consent":
            return self._consent(thread_id, st, msg)
        if st["step"] == "consented":
            return self._on_consented_message(thread_id, st, msg)
        return None

    def _consent(self, thread_id: str, st: dict, msg: dict):
        text = msg.get("text") or ""
        if is_yes(text):
            st["consent"] = {"at": self.port.now.isoformat(), "text_version": TEXT_VERSION}
            st["step"] = "consented"
            return None
        if re.search(NO, text, re.I) or st["consent_asks"] >= 2:
            return self._decline(thread_id, st)
        st["consent_asks"] += 1
        return self._send(thread_id, st, "unclear")

    def _decline(self, thread_id: str, st: dict):
        self._send(thread_id, st, "declined")
        st["kept"] = []
        st["step"] = "declined"
        st["silenced"] = True
        return None

    def _stop(self, thread_id: str, st: dict):
        if not st["silenced"]:
            self._send(thread_id, st, "stopped")
        st["step"] = "stopped"
        st["silenced"] = True
        return None

    def _on_consented_message(self, thread_id: str, st: dict, msg: dict):
        """Consent is on file, so the message may be kept. A composed reply is held for the doctor."""
        st["kept"].append({"record": msg["id"], "text": msg.get("text") or "", "patient_id": st["patient_id"]})
        if self.composer:
            draft = self.composer(st, msg)
            if draft:
                self.hold_draft(st, draft["text"], draft.get("lang") or st["lang"], draft.get("reasoning"))
        st["needs_review"] = True
        st["silenced"] = True
        st["step"] = "needs_review"
        return None

    def hold_draft(self, st: dict, text: str, lang: str, reasoning=None) -> str:
        """Keep a composed reply off the transcript until the doctor taps. Reasoning stays on the draft."""
        did = f"d{len(st['drafts']) + 1}"
        st["drafts"].append({"id": did, "text": text, "lang": lang, "reasoning": reasoning, "status": "held", "tapped": False})
        return did

    def doctor_tap(self, thread_id: str, draft_id: str) -> str | None:
        """Send the draft's patient text only. Fixed templates do not use this; they already went out."""
        st = self.state[thread_id]
        draft = next(d for d in st["drafts"] if d["id"] == draft_id)
        if draft["tapped"] or draft["status"] != "held":
            return None
        draft["tapped"] = True
        draft["status"] = "sent"
        return self.port.send_tapped(thread_id, draft["text"], draft["lang"])

    def _escalate(self, thread_id: str, st: dict, msg: dict, kind: str):
        self._send(thread_id, st, kind)
        text = msg.get("text") or ""
        quote = text.strip()[:QUOTE_MAX]
        if st["consent"] and not any(f["msg"] == msg["id"] for f in st["flags"]):
            iid = self.port.open_item(
                st["patient_id"], st["owner_id"], "care_red_flag",
                "Possible urgent symptom on the care line. The fixed screen matched; this is not a diagnosis.",
                [msg["id"]], urgency=4, due=self.port.now,
                reasoning=[{
                    "step": "finding",
                    "text": "Possible urgent symptom. The patient was sent the fixed 999 reply.",
                    "cites": [{"record": msg["id"], "quote": quote}],
                }],
            )
            st["flags"].append({"kind": kind, "item": iid, "msg": msg["id"]})
        self.port.alarm("careflow", f"{kind} on {thread_id}; fixed 999 reply")
        st["step"] = "escalated"
        st["silenced"] = True
        return None
