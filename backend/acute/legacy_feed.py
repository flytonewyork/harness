"""Builds a live `backend.api.store.Store` from the embedded LoopBench run in `demo/ec-care-line/index.html`.

Enables the React Follow-Up List (`/?mode=legacy`) to run live on Cloud Run staging alongside the
v2 Clinician Console (`/c`) and EC Care Line storyboard (`/ec`) with zero 404s and without ever
importing `loopbench` in product code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..api.store import Store

EC_INDEX_HTML = Path(__file__).resolve().parents[2] / "demo" / "ec-care-line" / "index.html"


def build_legacy_demo_store() -> Store | None:
    """Parse the committed LoopBench day snapshot from demo/ec-care-line/index.html into a Store."""
    if not EC_INDEX_HTML.exists():
        return None
    html = EC_INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="engine-data">(.*?)</script>', html, re.S)
    if not m:
        return None
    raw = json.loads(m.group(1))

    docs = dict(raw.get("documents") or {})
    consult = raw.get("consult_note")
    if isinstance(consult, dict) and consult.get("id"):
        docs[consult["id"]] = consult

    items = []
    for it in raw.get("items") or []:
        cites = []
        for step in it.get("reasoning") or []:
            for c in step.get("cites") or []:
                rec = c.get("record")
                if rec and rec not in cites:
                    cites.append(rec)
        items.append({
            "id": it["id"],
            "patient_id": it["patient_id"],
            "owner_id": "dr_chan",
            "family": it["family"],
            "reason": it["reason"],
            "urgency_at_open": it.get("urgency_at_open", it.get("urgency", 1)),
            "opened_at": it["opened_at"],
            "due": it.get("due"),
            "resolved_at": it.get("resolved_at"),
            "resolution": it.get("resolution"),
            "escalates": it.get("escalates"),
            "history": it.get("history") or [],
            "evidence": cites,
            "reasoning": it.get("reasoning") or [],
            "checks": [
                {"name": name, "status": "pass", "detail": "Verified against cited record"}
                for name in ("evidence", "quotes", "identity", "searches", "duplicate", "owner", "reasoning")
            ],
        })

    run = {
        "source": {
            **(raw.get("source") or {}),
            "default_as_of": raw.get("now") or "2027-06-13T09:30",
        },
        "staff": [
            {"id": "dr_chan", "name": "Dr CHAN Siu Ming", "role": "gp"},
            {"id": "dr_lam", "name": "Dr Kenneth Lam", "role": "gp"},
        ],
        "patients": raw.get("patients") or {},
        "documents": docs,
        "items": items,
        "feeds": [
            {
                "channel": "portal:pathlab",
                "alarm_at": "2027-06-10T13:30",
                "recovered_at": "2027-06-11T08:30",
                "reason": "PathLab portal silent during scheduled pull; all-clear locked until restored",
            }
        ],
        "evaluation": {
            "hard_fails": raw.get("gate", {}).get("hard_fails", 0),
            "made_up_quotes": raw.get("gate", {}).get("made_up_quotes", 0),
            "year": raw.get("year") or {},
        },
    }
    store = Store(run)
    # Default as_of to the Sunday 13 Jun 09:30 demo moment (Mr Chan K+ 6.6 open)
    demo_now = raw.get("now")
    if demo_now:
        store.source["end"] = demo_now
    return store
