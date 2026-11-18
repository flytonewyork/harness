"""In-process care line. A synthetic patient message goes in. A composed draft comes out.

Nothing is sent until the doctor taps. Consent, STOP, and crisis / 999 stay on the fixed
templates. Nothing is kept before labelled consent. The patient text does not include the
reasoning. The default composer does not call a model. A caller may pass another composer.
There is no second outbound judge and no network send. This does not start the care-line service.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .flow import QUOTE_MAX, CareFlow

# Patient-facing sentences only. The reasoning lives on the draft, never in these strings.
PATIENT_TEXT = {
    "en": "Thanks, I got your message.",
    "zh": "收到，多謝你。",
}
REASON_TEXT = {
    "en": "Held for a doctor tap. The draft gives no result, no dose, and no clearance.",
    "zh": "等醫生撳先至送出。草稿冇結果、冇劑量、亦冇話病人冇事。",
}

T0 = datetime(2026, 11, 18, 9, 0)


def compose_draft(st: dict, msg: dict) -> dict | None:
    """Deterministic composer for one consented message. No model and no network.

    The quote stays on the reasoning cite. `text` is the sentence the patient would see
    after a doctor tap, and it does not include that reasoning.
    """
    quote = (msg.get("text") or "").strip()[:QUOTE_MAX]
    if not quote:
        return None
    lang = st.get("lang")
    if lang not in PATIENT_TEXT:
        lang = "en"
    return {
        "text": PATIENT_TEXT[lang],
        "lang": lang,
        "reasoning": [{
            "step": "finding",
            "text": REASON_TEXT[lang],
            "cites": [{"record": msg["id"], "quote": quote}],
        }],
    }


class Transcript:
    """One synthetic thread, in this process. Not a socket and not a send."""

    def __init__(self, thread_id: str = "T1", patient_id: str = "P1", owner_id: str = "D1",
                 doctor_en: str = "Dr Chan", doctor_zh: str = "陳醫生", now: datetime | None = None):
        self.thread_id = thread_id
        self.now = now or T0
        self.msgs, self.items, self.alarms, self.writes, self.turns = [], {}, [], [], []
        self.turn, self.n = None, 0
        self.info = {"id": thread_id, "patient_id": patient_id, "owner_id": owner_id,
                     "doctor_en": doctor_en, "doctor_zh": doctor_zh}

    def _only(self, thread_id: str) -> None:
        if thread_id != self.thread_id:
            raise KeyError(thread_id)

    def thread(self, thread_id: str) -> dict:
        self._only(thread_id)
        return self.info

    def messages(self, thread_id: str) -> list[dict]:
        self._only(thread_id)
        return self.msgs

    def inbound(self, thread_id: str, text: str) -> str:
        """Record one synthetic patient message. This does not reply."""
        self._only(thread_id)
        self.now += timedelta(minutes=2)
        self.n += 1
        mid = f"i{self.n}"
        self.msgs.append({"id": mid, "dir": "in", "text": text, "act": None, "turn": None, "at": self.now})
        return mid

    def begin_turn(self, turn: int, silenced: bool, inbound: str | None) -> None:
        self.turn = turn
        self.turns.append({"n": turn, "silenced_at_start": silenced, "inbound": inbound, "outs": []})
        if inbound:
            for m in self.msgs:
                if m["id"] == inbound:
                    m["turn"] = turn

    def _out(self, text: str, act: str | None, by: str | None) -> str:
        self.n += 1
        rec = {"id": f"o{self.n}", "dir": "out", "text": text, "act": act, "by": by, "turn": self.turn, "at": self.now}
        self.msgs.append(rec)
        self.turns[-1]["outs"].append(rec)
        return rec["id"]

    def send_fixed(self, thread_id: str, act: str, lang: str, text: str) -> str:
        self._only(thread_id)
        return self._out(text, act, None)

    def send_tapped(self, thread_id: str, text: str, lang: str) -> str:
        self._only(thread_id)
        return self._out(text, None, "doctor")

    def open_item(self, patient_id: str, owner_id: str, family: str, reason: str, evidence: list[str],
                  urgency: int = 1, due: datetime | None = None, reasoning: list[dict] | None = None,
                  escalates: str | None = None) -> str:
        iid = f"I{len(self.items) + 1}"
        self.items[iid] = {"patient_id": patient_id, "owner": owner_id, "family": family, "reason": reason,
                           "evidence": list(evidence), "urgency": urgency, "opened_urgency": urgency, "due": due,
                           "reasoning": reasoning or [], "turn": self.turn}
        self.writes.append({"verb": "open_item", "turn": self.turn})
        return iid

    def raise_urgency(self, item_id: str, urgency: int, reason: str = "") -> None:
        prev = self.items[item_id]["urgency"]
        self.items[item_id]["urgency"] = urgency
        self.writes.append({"verb": "raise_urgency", "from": prev, "to": urgency, "turn": self.turn})

    def alarm(self, channel: str, reason: str) -> None:
        self.alarms.append({"channel": channel, "reason": reason, "turn": self.turn})
        self.writes.append({"verb": "alarm", "turn": self.turn})

    def attest_all_clear(self, scope: str = "all", note: str = "") -> None:
        self.writes.append({"verb": "attest_all_clear", "turn": self.turn})

    def out(self) -> list[dict]:
        return [m for m in self.msgs if m["dir"] == "out"]


class CareLine:
    """The path. `receive` returns a held draft, or None when the turn stayed on a fixed act."""

    def __init__(self, port: Transcript | None = None, *, composer=None):
        self.port = port if port is not None else Transcript()
        # The default does not call a model. A caller may pass a composer in.
        self.flow = CareFlow(self.port, composer=compose_draft if composer is None else composer)

    def start(self, thread_id: str = "T1") -> None:
        self.flow.start(thread_id)

    def receive(self, text: str, thread_id: str = "T1") -> dict | None:
        """Take one synthetic inbound. Return the new held draft when one was composed."""
        if thread_id not in self.flow.state or self.flow.state[thread_id]["step"] == "new":
            self.start(thread_id)
        before = len(self.flow.state[thread_id]["drafts"])
        self.flow.handle(thread_id, self.port.inbound(thread_id, text))
        drafts = self.flow.state[thread_id]["drafts"]
        if len(drafts) == before:
            return None
        return drafts[-1]

    def doctor_tap(self, thread_id: str, draft_id: str) -> str | None:
        """The doctor sends the draft's patient text. Nothing else calls this."""
        return self.flow.doctor_tap(thread_id, draft_id)
