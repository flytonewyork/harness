"""Where the line keeps things: a small document store with three back ends.

- `MemoryStore`: tests and sim.
- `SqliteStore`: a local file, for rehearsals on a laptop.
- `FirestoreStore`: the live deployment (Google Cloud, `asia-east2`). The only place real patient data is kept.

Kinds: doctor, patient, wa (one per WhatsApp number: last message time, outbox, which patient record is current),
plan, item (scheduled check-ins and reminders), handoff, payment, msg (every message in and out), ctx (our sent
message id → what it was about, so a doctor's swipe-reply finds its patient), dsession (the doctor console's state),
token (one-time web links), seen (webhook message ids already handled), event (the audit trail: ids and event names,
never message text).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime


def new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12].upper()


class Store:
    lock: threading.RLock

    def get(self, coll: str, id: str) -> dict | None: ...
    def put(self, coll: str, id: str, doc: dict) -> dict: ...
    def delete(self, coll: str, id: str) -> None: ...
    def all(self, coll: str) -> list[dict]: ...

    def find(self, coll: str, **eq) -> list[dict]:
        return [d for d in self.all(coll) if all(d.get(k) == v for k, v in eq.items())]

    def one(self, coll: str, **eq) -> dict | None:
        r = self.find(coll, **eq)
        return r[0] if r else None

    def event(self, at: datetime, ev: str, **ids):
        """The audit trail. Only ids, names, counts and flags: never text, names or numbers of patients."""
        bad = [k for k in ids if k in ("text", "body", "name", "dob", "wa", "quote", "transcript")]
        if bad:
            raise ValueError(f"events never carry {bad}")
        eid = new_id("E")
        self.put("event", eid, {**ids, "id": eid, "at": at.isoformat(timespec="seconds"), "event": ev})


class MemoryStore(Store):
    def __init__(self):
        self.lock = threading.RLock()
        self.data: dict[str, dict[str, dict]] = {}

    def get(self, coll, id):
        d = self.data.get(coll, {}).get(id)
        return json.loads(json.dumps(d)) if d is not None else None

    def put(self, coll, id, doc):
        self.data.setdefault(coll, {})[id] = json.loads(json.dumps(doc, ensure_ascii=False, default=str))
        return doc

    def delete(self, coll, id):
        self.data.get(coll, {}).pop(id, None)

    def all(self, coll):
        return [json.loads(json.dumps(d)) for d in self.data.get(coll, {}).values()]


class SqliteStore(Store):
    def __init__(self, path: str):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists docs (coll text, id text, doc text, primary key (coll, id))")
        self.db.commit()

    def get(self, coll, id):
        r = self.db.execute("select doc from docs where coll=? and id=?", (coll, id)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, coll, id, doc):
        with self.lock:
            self.db.execute("insert or replace into docs values (?,?,?)", (coll, id, json.dumps(doc, ensure_ascii=False, default=str)))
            self.db.commit()
        return doc

    def delete(self, coll, id):
        with self.lock:
            self.db.execute("delete from docs where coll=? and id=?", (coll, id))
            self.db.commit()

    def all(self, coll):
        return [json.loads(r[0]) for r in self.db.execute("select doc from docs where coll=?", (coll,))]


class FirestoreStore(Store):
    """One collection per coll, prefixed so the line can share a project with other things."""

    def __init__(self, project: str | None = None, prefix: str = "line_"):
        from google.cloud import firestore  # only in the live image
        self.lock = threading.RLock()
        self.db = firestore.Client(project=project or None)
        self.prefix = prefix

    def _c(self, coll):
        return self.db.collection(self.prefix + coll)

    @staticmethod
    def _key(id: str) -> str:
        """A Firestore document id can't hold '/', be '.' or '..', or look like __x__. WhatsApp message ids are base64
        and can hold '/'."""
        k = str(id).replace("/", "_")
        return ("id_" + k) if k in (".", "..") or (k.startswith("__") and k.endswith("__")) or not k else k[:1400]

    def get(self, coll, id):
        s = self._c(coll).document(self._key(id)).get()
        return s.to_dict() if s.exists else None

    def put(self, coll, id, doc):
        self._c(coll).document(self._key(id)).set(json.loads(json.dumps(doc, ensure_ascii=False, default=str)))
        return doc

    def delete(self, coll, id):
        self._c(coll).document(self._key(id)).delete()

    def all(self, coll):
        return [s.to_dict() for s in self._c(coll).stream()]

    def find(self, coll, **eq):
        from google.cloud.firestore_v1.base_query import FieldFilter
        q = self._c(coll)
        for k, v in eq.items():
            q = q.where(filter=FieldFilter(k, "==", v))
        return [s.to_dict() for s in q.stream()]


def open_store(spec: str, project: str = "", prefix: str = "line_") -> Store:
    if spec == "memory":
        return MemoryStore()
    if spec.startswith("sqlite:"):
        return SqliteStore(spec[7:])
    if spec == "firestore":
        return FirestoreStore(project, prefix)
    raise ValueError(f"unknown store {spec!r}")
