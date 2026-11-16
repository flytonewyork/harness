"""The care line's view of a clinic (its EMR), and the clinics it can plug into.

`CarePort` is everything the care line may read or ask for. It is not the engine's port (`backend/engine/ports.py`):
the care line reads a narrower slice (identity, the last visit's plan, whether results are back, what was dispensed,
the appointment book) and its only writes are a booking and a request for a person. It never closes anything.

Implementations:
- `SimClinic`: the simulated clinic, read from a LoopBench run file (harness-run/1, written by
  `loopbench product-feed`) through the product's adapter, never by importing LoopBench. Synthetic patients only.
- `PlaybackClinic`: wraps any clinic, records every call and answer to JSONL, and can replay a recording with no
  clinic behind it, so a conversation captured once can be re-run against a changed agent (play-back testing).
- `ApricotClinic`, `ECClinic`: where the real EMR endpoints go. Not built: no partner has agreed a data route.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from backend.adapters.loopbench_run import load_run

from .settings import Settings


class CarePort(Protocol):
    def clinic(self) -> dict: ...
    def find_patients(self, name: str, dob: str) -> list[dict]: ...
    def patient(self, pid: str) -> dict: ...
    def last_visit(self, pid: str) -> dict | None: ...               # {"id","date","doctor","text"} consult note
    def results(self, pid: str, days: int = 90) -> list[dict]: ...   # [{"date","status": back_unreviewed|back_reviewed|waiting}]
    def medicines(self, pid: str) -> list[str]: ...                  # as dispensed, word for word
    def free_slots(self, start: datetime, days: int = 4) -> list[datetime]: ...
    def book(self, pid: str, when: datetime) -> dict: ...
    def cancel(self, pid: str) -> dict | None: ...
    def bookings(self, pid: str) -> list[dict]: ...


def norm_name(s: str) -> str:
    s = re.sub(r"[,.\s]+", " ", s.strip()).lower()
    return " ".join(sorted(s.split())) if s.isascii() else s.replace(" ", "")


def parse_dob(s: str) -> str | None:
    s = s.strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.fullmatch(r"(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{4})", s) or re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
        if not m:
            return None
        if "年" in s:
            y, mo, d = m.groups()
        else:
            d, mo, y = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).date().isoformat()
    except ValueError:
        return None


class SimClinic:
    """The simulated clinic at a fixed moment (`as_of`): the chart shows only what existed then."""
    kind = "sim"

    def __init__(self, run_path: str | Path, settings: Settings, as_of: str = "2027-06-13T09:30"):
        self.run = load_run(run_path)
        self.settings = settings
        self.as_of = as_of
        self.source = {**self.run["source"], "as_of": as_of, "file": Path(run_path).name}
        self._bookings: dict[str, list[dict]] = {}
        self._docs = sorted(self.run["documents"].values(), key=lambda d: d["available_at"])
        self._staff = {s["name"]: s["id"] for s in self.run["staff"]}

    def _chart(self, pid: str, kind: str | None = None) -> list[dict]:
        return [d for d in self._docs if d.get("patient_id") == pid and d["available_at"] <= self.as_of
                and (kind is None or d["kind"] == kind)]

    def clinic(self) -> dict:
        return {"name": self.source["clinic_name"], "system": self.source.get("system"), "simulated": True, "as_of": self.as_of}

    def find_patients(self, name: str, dob: str) -> list[dict]:
        n = norm_name(name)
        return [p for p in self.run["patients"].values()
                if p["dob"] == dob and n in (norm_name(p["name_en"]), norm_name(p.get("name_zh", "")))]

    def patient(self, pid: str) -> dict:
        return self.run["patients"][pid]

    def last_visit(self, pid: str) -> dict | None:
        notes = self._chart(pid, "consult_note")
        if not notes:
            return None
        n = notes[-1]
        return {"id": n["id"], "date": n["available_at"][:10], "doctor": n.get("author") or "the doctor", "text": n["text"]}

    def results(self, pid: str, days: int = 90) -> list[dict]:
        since = (datetime.fromisoformat(self.as_of) - timedelta(days=days)).isoformat(timespec="minutes")
        reports = [d for d in self._chart(pid, "lab_report") if d["available_at"] >= since]
        requests = [d for d in self._chart(pid, "lab_request") if d["available_at"] >= since]
        out = []
        for r in reports:
            items = [it for it in self.run["items"] if r["id"] in it["evidence"] and it["opened_at"] <= self.as_of]
            reviewed = any(it["resolved_at"] and it["resolved_at"] < self.as_of for it in items)
            out.append({"date": r["available_at"][:10], "status": "back_reviewed" if reviewed else "back_unreviewed", "doc": r["id"]})
        for q in requests:
            no = (q.get("data") or {}).get("lab_no")
            if no and not any(no in r["text"] for r in reports):
                out.append({"date": q["available_at"][:10], "status": "waiting", "doc": q["id"]})
        return sorted(out, key=lambda x: x["date"])

    def medicines(self, pid: str) -> list[str]:
        meds = []
        for d in self._chart(pid, "dispensing"):
            for m in (d.get("data") or {}).get("items", "").split(" / "):
                m = m.strip()
                if m and m not in meds:
                    meds.append(m)
        return meds

    def free_slots(self, start: datetime, days: int = 4) -> list[datetime]:
        taken = {b["when"] for bs in self._bookings.values() for b in bs}
        step = timedelta(minutes=self.settings["slot_minutes"])
        out, d = [], start.date()
        for i in range(days + 1):
            for a, b in self.settings.sessions(d + timedelta(days=i)):
                t, n = max(a, start + timedelta(hours=1)), 0
                t = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0) + (step if t.minute % 15 else timedelta())
                while t < b and n < 2:          # two shown per session keeps the list short
                    if t.isoformat(timespec="minutes") not in taken and t > start:
                        out.append(t)
                        n += 1
                    t += step * 3
        return out[:9]

    def book(self, pid: str, when: datetime) -> dict:
        b = {"when": when.isoformat(timespec="minutes"), "doctor": self.settings.person(self.settings["named_doctor"])["name"]}
        self._bookings.setdefault(pid, []).append(b)
        return b

    def cancel(self, pid: str) -> dict | None:
        bs = self._bookings.get(pid) or []
        return bs.pop() if bs else None

    def bookings(self, pid: str) -> list[dict]:
        return list(self._bookings.get(pid, []))

    def personas(self, n: int = 6) -> list[dict]:
        """Synthetic patients worth testing as: the demo's Mr Chan first, then others with a recent visit."""
        seen, out = set(), []
        for d in reversed(self._docs):
            pid = d.get("patient_id")
            if d["kind"] == "consult_note" and d["available_at"] <= self.as_of and pid not in seen:
                seen.add(pid)
                out.append(pid)
        if "PF99D4DB079" in out:
            out.remove("PF99D4DB079")
            out.insert(0, "PF99D4DB079")
        rows = []
        for pid in out[:n]:
            p = self.patient(pid)
            rows.append({"id": pid, "name_en": p["name_en"], "name_zh": p.get("name_zh"), "dob": p["dob"],
                         "last_visit": self.last_visit(pid)["date"], "results": len(self.results(pid)), "medicines": len(self.medicines(pid))})
        return rows


class PlaybackClinic:
    """Record a clinic's answers, or replay them with no clinic behind it.

    record: PlaybackClinic(SimClinic(...), path, mode="record"); replay: PlaybackClinic(None, path, mode="replay").
    In replay a call that was never recorded raises, so a changed agent that asks the EMR something new is caught,
    never silently answered."""
    kind = "playback"
    METHODS = ("clinic", "find_patients", "patient", "last_visit", "results", "medicines", "free_slots", "book", "cancel", "bookings")

    def __init__(self, inner, path: str | Path, mode: str = "record"):
        self.inner, self.path, self.mode = inner, Path(path), mode
        self.tape: dict[str, list] = {}
        if mode == "replay":
            for line in self.path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                self.tape.setdefault(rec["key"], []).append(rec["out"])
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")
        self.settings = getattr(inner, "settings", None)

    def __getattr__(self, name):
        if name not in self.METHODS:
            raise AttributeError(name)

        def call(*args):
            key = json.dumps([name, [a.isoformat() if isinstance(a, datetime) else a for a in args]], ensure_ascii=False)
            if self.mode == "replay":
                if not self.tape.get(key):
                    raise LookupError(f"not on the tape: {key}")
                out = self.tape[key].pop(0)
                return [datetime.fromisoformat(x) for x in out] if name == "free_slots" else out
            out = getattr(self.inner, name)(*args)
            ser = [x.isoformat() for x in out] if name == "free_slots" else out
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "out": ser}, ensure_ascii=False) + "\n")
            return out
        return call


class _NotConnected:
    """A real EMR goes here once a partner agrees a data route. Each method maps to one endpoint (CarePort)."""
    vendor = "?"

    def __getattr__(self, name):
        raise NotImplementedError(f"{self.vendor}: no data route agreed yet; use SimClinic or PlaybackClinic")


class ApricotClinic(_NotConnected):
    vendor = "Apricot VITA"


class ECClinic(_NotConnected):
    vendor = "EC Healthcare"
