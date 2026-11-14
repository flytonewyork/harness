"""In-memory store behind the API: one clinic's follow-up list, viewable as it stood at any moment (`as_of`), so
a recorded year can be replayed for review or a demo.

Authority is asymmetric, as in the engine's port: the agent may open an item or raise its urgency, never lower,
close, dismiss or reassure. Only a doctor closes an item, or confirms, rejects or closes a case fact.
The case projection is attached beside an item. It does not replace that item's reasoning trace.
"""
from __future__ import annotations

from datetime import datetime
from itertools import count

from ..case import AuthorityError as CaseAuthorityError, CaseError, CaseLog, QuoteError

LABEL = {4: "Critical", 3: "Urgent", 2: "Soon", 1: "Routine"}
RESOLUTIONS = ("acted", "already_done", "not_relevant")


class AuthorityError(PermissionError):
    pass


def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, run: dict, case: CaseLog | None = None):
        self.run = run
        self.case = case if case is not None else CaseLog()
        self.source = run["source"]
        self.staff = {s["id"]: s for s in run["staff"]}
        self.patients = run["patients"]
        self.documents = run["documents"]
        self.items = {it["id"]: dict(it) for it in run["items"]}
        self.feeds = run["feeds"]
        self.evaluation = run.get("evaluation") or {}
        self.copies: dict[str, list[str]] = {}
        for it in self.items.values():
            if it.get("escalates"):
                self.copies.setdefault(it["escalates"], []).append(it["id"])
        self._seq = count(1)
        self.audit: list[dict] = []

    # ------------------------------------------------------------------------------------- reading
    def clock(self) -> dict:
        times = [it["opened_at"] for it in self.items.values()]
        return {"start": self.source.get("start") or (min(times) if times else None),
                "end": self.source.get("end") or (max(times) if times else None)}

    def default_as_of(self) -> datetime:
        return _t(self.clock()["end"]) or datetime.now()

    def view(self, it: dict, as_of: datetime) -> dict:
        """The item as it stood at as_of: status, urgency (raises up to then) and escalation links."""
        u = it["urgency_at_open"]
        hist = []
        for h in it.get("history", []):
            if _t(h[0]) <= as_of:
                u = h[3]
                hist.append({"at": h[0], "what": h[1], "from": h[2], "to": h[3], "reason": h[4]})
        res_at = _t(it.get("resolved_at"))
        status = "open" if res_at is None or res_at > as_of else (it.get("resolution") or "closed")
        copies = [c for c in self.copies.get(it["id"], []) if _t(self.items[c]["opened_at"]) <= as_of]
        owner = self.staff.get(it["owner_id"], {"name": it["owner_id"]})
        p = self.patients.get(it["patient_id"], {})
        due = _t(it.get("due"))
        return {"id": it["id"], "patient_id": it["patient_id"], "patient": {k: p.get(k) for k in ("name_en", "name_zh", "hkid", "sex", "dob", "mrn", "cdcc")},
                "owner_id": it["owner_id"], "owner": owner.get("name"), "family": it["family"], "reason": it["reason"],
                "urgency": u, "urgency_label": LABEL.get(u, str(u)), "opened_at": it["opened_at"], "due": it.get("due"),
                "overdue": bool(due and due < as_of and status == "open"), "status": status,
                "resolved_at": it.get("resolved_at") if status != "open" else None,
                "escalates": it.get("escalates"), "escalated_to": copies, "history": hist,
                "evidence": it["evidence"], "checks_passed": all(c.get("status") in ("pass", "na") for c in it.get("checks", []))}

    def list_items(self, as_of: datetime, status: str = "open", owner: str | None = None, patient: str | None = None) -> list[dict]:
        out = []
        for it in self.items.values():
            if _t(it["opened_at"]) > as_of or (owner and it["owner_id"] != owner) or (patient and it["patient_id"] != patient):
                continue
            v = self.view(it, as_of)
            if status == "open" and v["status"] != "open":
                continue
            out.append(v)
        out.sort(key=lambda v: (v["status"] != "open", -v["urgency"], v["due"] or "9999", v["opened_at"]))
        return out

    def item(self, iid: str, as_of: datetime) -> dict:
        it = self.items[iid]
        if _t(it["opened_at"]) > as_of:
            raise KeyError(iid)
        v = self.view(it, as_of)
        # A copy: attaching the case must not rewrite the item's own trace.
        v["reasoning"] = list(it.get("reasoning") or [])
        v["checks"] = it.get("checks") or []
        v["documents"] = [self.doc_meta(d) for d in it["evidence"] if d in self.documents]
        v["case"] = self.case_view(it["patient_id"], as_of)
        return v

    def doc_meta(self, did: str) -> dict:
        d = self.documents[did]
        return {k: d.get(k) for k in ("id", "kind", "channel", "available_at", "author")}

    def document(self, did: str) -> dict:
        return self.documents[did]

    def feed_status(self, as_of: datetime) -> list[dict]:
        out = {}
        for f in self.feeds:
            a, r = _t(f["alarm_at"]), _t(f.get("recovered_at"))
            if a <= as_of and (r is None or r > as_of):
                out.setdefault(f["channel"], {"channel": f["channel"], "status": "down", "since": f["alarm_at"],
                                              "detail": f["detail"], "expected_back": None})
        return list(out.values())

    def patient(self, pid: str, as_of: datetime) -> dict:
        docs = sorted((d for d in self.documents.values() if d.get("patient_id") == pid and _t(d["available_at"]) <= as_of),
                      key=lambda d: d["available_at"])
        return {"card": self.patients[pid], "items": self.list_items(as_of, status="all", patient=pid),
                "documents": [self.doc_meta(d["id"]) for d in docs],
                "evaluation": (self.evaluation.get("patients") or {}).get(pid),
                "case": self.case_view(pid, as_of)}

    def case_view(self, pid: str, as_of: datetime) -> dict:
        """`case-projection/1` for this patient at `as_of`. Empty when the log has no facts yet."""
        return self.case.project(pid, as_of).to_dict()

    # ------------------------------------------------------------------------------------- acting
    def agent_open(self, patient_id: str, owner_id: str, family: str, reason: str, evidence: list[str], urgency: int,
                   due: str | None, reasoning: list | None, now: datetime) -> str:
        if patient_id not in self.patients or owner_id not in self.staff:
            raise KeyError("unknown patient or owner")
        if not evidence or any(e not in self.documents for e in evidence):
            raise ValueError("an item must cite documents the platform holds")
        if not 1 <= urgency <= 4:
            raise ValueError("urgency is 1-4")
        iid = f"A{next(self._seq):05d}"
        self.items[iid] = {"id": iid, "patient_id": patient_id, "owner_id": owner_id, "family": family, "reason": reason[:200],
                           "urgency_at_open": urgency, "urgency": urgency, "history": [], "opened_at": now.isoformat(timespec="minutes"),
                           "due": due, "resolved_at": None, "resolution": None, "escalates": None, "evidence": list(evidence),
                           "reasoning": reasoning or [], "checks": []}
        self.audit.append({"at": now.isoformat(timespec="minutes"), "who": "agent", "did": "open", "item": iid})
        return iid

    def agent_raise(self, iid: str, urgency: int, reason: str, now: datetime) -> dict:
        it = self.items[iid]
        cur = self.view(it, now)
        if cur["status"] != "open":
            raise AuthorityError("the item is closed; only a doctor reopens work")
        if urgency <= cur["urgency"]:
            raise AuthorityError("the agent can only raise urgency, never lower or keep it")
        it.setdefault("history", []).append([now.isoformat(timespec="minutes"), "raised", cur["urgency"], int(urgency), reason[:300]])
        self.audit.append({"at": now.isoformat(timespec="minutes"), "who": "agent", "did": "raise", "item": iid, "to": urgency})
        return self.view(it, now)

    def doctor_close(self, iid: str, resolution: str, doctor: str, note: str, now: datetime) -> dict:
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution is one of {RESOLUTIONS}")
        it = self.items[iid]
        if self.view(it, now)["status"] != "open":
            raise ValueError("already closed")
        it["resolved_at"], it["resolution"] = now.isoformat(timespec="minutes"), resolution
        self.audit.append({"at": now.isoformat(timespec="minutes"), "who": doctor, "did": "close", "item": iid,
                           "resolution": resolution, "note": note[:300]})
        return self.view(it, now)

    def doctor_fact(self, pid: str, fact_id: str, action: str, record_id: str, quote: str, doctor: str,
                    note: str, now: datetime) -> dict:
        """Append a doctor confirm, reject or close. The subject fact is left as it was."""
        if action not in ("confirm", "reject", "close"):
            raise KeyError(action)
        if pid not in self.patients:
            raise KeyError(pid)
        subject = next((f for f in self.case.facts() if f.id == fact_id), None)
        if subject is None or subject.patient_id != pid:
            raise KeyError(fact_id)
        try:
            getattr(self.case, action)(doctor_id=doctor or "doctor", fact_id=fact_id, record_id=record_id,
                                       quote=quote, at=now, note=note)
        except QuoteError as e:
            raise ValueError(str(e)) from e
        except CaseAuthorityError as e:
            raise AuthorityError(str(e)) from e
        except CaseError as e:
            raise ValueError(str(e)) from e
        self.audit.append({"at": now.isoformat(timespec="minutes"), "who": doctor or "doctor", "did": action,
                           "patient": pid, "fact": fact_id})
        return self.case_view(pid, now)
