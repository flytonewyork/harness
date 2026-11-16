"""Everything the care line said, checked and decided, on record (RP-01), plus the people's queue.

Two kinds of write, kept apart like the engine's port:
- the agent may open a hand-off, add a draft for the doctor, and log; it can never close, dismiss or approve.
- a person (staff or doctor, by id from the clinic's settings) claims or closes a hand-off and approves a draft.
`tick(now)` moves an unclaimed hand-off past its reply-by time to the next person on the clinic's list (HE-05).
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .settings import Settings


class AuthorityError(PermissionError):
    pass


def _id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:10].upper()


class CareStore:
    def __init__(self, settings: Settings, path: str | Path | None = None):
        self.settings = settings
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.conversations: dict[str, dict] = {}
        self.handoffs: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.events: list[dict] = []
        if self.path and self.path.exists():
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.conversations, self.handoffs, self.drafts, self.events = d["conversations"], d["handoffs"], d["drafts"], d["events"]

    def save(self):
        if not self.path:
            return
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"conversations": self.conversations, "handoffs": self.handoffs, "drafts": self.drafts,
                                       "events": self.events}, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    # ---- log -------------------------------------------------------------------------------------------------
    def log(self, at: datetime, wa: str, kind: str, **detail):
        self.events.append({"at": at.isoformat(timespec="seconds"), "wa": wa, "kind": kind, **detail})

    def conversation(self, wa: str) -> dict:
        if wa not in self.conversations:
            self.conversations[wa] = {"wa": wa, "state": "new", "lang": "en", "patient_id": None, "consent": None,
                                      "invites": None, "stopped": False, "verified_at": None, "pending": {}, "messages": []}
        return self.conversations[wa]

    def forget(self, wa: str):
        """A person who said no at the consent question: nothing kept but the fact that we asked."""
        self.conversations.pop(wa, None)

    # ---- agent side: open only ---------------------------------------------------------------------------
    def open_handoff(self, now: datetime, wa: str, kind: str, feature: str, quote: str, patient_id: str | None,
                     note: str = "") -> dict:
        chain = self.settings["chains"][kind]
        h = {"id": _id("H"), "wa": wa, "patient_id": patient_id, "kind": kind, "feature": feature, "quote": quote, "note": note,
             "opened_at": now.isoformat(timespec="minutes"), "reply_by": self.settings.reply_by(now, kind).isoformat(timespec="minutes"),
             "chain": chain, "owner": chain[0], "claimed_by": None, "status": "open", "history": []}
        self.handoffs[h["id"]] = h
        self.log(now, wa, "handoff_opened", id=h["id"], owner=h["owner"], reply_by=h["reply_by"], feature=feature)
        return h

    def open_handoff_for(self, wa: str) -> list[dict]:
        return [h for h in self.handoffs.values() if h["wa"] == wa and h["status"] == "open"]

    def add_draft(self, now: datetime, wa: str, patient_id: str, feature: str, text: str, source: str) -> dict:
        for d in self.drafts.values():
            if d["patient_id"] == patient_id and d["source"] == source:
                return d
        d = {"id": _id("D"), "wa": wa, "patient_id": patient_id, "feature": feature, "text": text, "source": source,
             "status": "pending", "created_at": now.isoformat(timespec="minutes"), "approved_by": None, "approved_at": None,
             "approved_text": None, "sent": []}
        self.drafts[d["id"]] = d
        self.log(now, wa, "draft_added", id=d["id"], feature=feature)
        return d

    def tick(self, now: datetime) -> list[dict]:
        """Unclaimed and past reply-by: move to the next person on the list. The last person keeps it, flagged."""
        moved = []
        for h in self.handoffs.values():
            if h["status"] != "open" or h["claimed_by"] or now < datetime.fromisoformat(h["reply_by"]):
                continue
            i = h["chain"].index(h["owner"])
            if i + 1 < len(h["chain"]):
                h["history"].append({"at": now.isoformat(timespec="minutes"), "moved_from": h["owner"], "to": h["chain"][i + 1]})
                h["owner"] = h["chain"][i + 1]
                h["reply_by"] = self.settings.reply_by(now, h["kind"]).isoformat(timespec="minutes")
                self.log(now, h["wa"], "handoff_moved", id=h["id"], owner=h["owner"])
            elif not h.get("overdue"):
                h["overdue"] = True
                self.log(now, h["wa"], "handoff_overdue", id=h["id"], owner=h["owner"])
            moved.append(h)
        return moved

    # ---- person side ---------------------------------------------------------------------------------------
    def _person(self, by: str, roles: tuple[str, ...]):
        p = self.settings["people"].get(by)
        if not p or p["role"] not in roles:
            raise AuthorityError(f"{by!r} may not do this (needs {' or '.join(roles)})")
        return p

    def claim(self, now: datetime, hid: str, by: str):
        self._person(by, ("staff", "doctor"))
        h = self.handoffs[hid]
        h["claimed_by"] = by
        h["history"].append({"at": now.isoformat(timespec="minutes"), "claimed_by": by})
        self.log(now, h["wa"], "handoff_claimed", id=hid, by=by)

    def close(self, now: datetime, hid: str, by: str, resolution: str):
        """Only a person ends a hand-off. Clinical hand-offs only a doctor."""
        h = self.handoffs[hid]
        self._person(by, ("doctor",) if h["kind"] != "staff" else ("staff", "doctor"))
        h.update(status="closed", closed_by=by, closed_at=now.isoformat(timespec="minutes"), resolution=resolution)
        self.log(now, h["wa"], "handoff_closed", id=hid, by=by, resolution=resolution)

    def approve(self, now: datetime, did: str, by: str, text: str | None = None):
        self._person(by, ("doctor",))
        d = self.drafts[did]
        d.update(status="approved", approved_by=by, approved_at=now.isoformat(timespec="minutes"), approved_text=text or d["text"])
        self.log(now, d["wa"], "draft_approved", id=did, by=by, edited=bool(text and text != d["text"]))
