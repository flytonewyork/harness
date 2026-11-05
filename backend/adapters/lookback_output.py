"""Data in from the Phase 0 look-back: its flags become a follow-up list as of the extract date, so the same
screen can show what a look-back found (unclosed follow-ups to call patients about). Keys stay opaque: the
patient column shows the key, which only EC can turn back into a person."""
from __future__ import annotations

import csv
import json
from pathlib import Path

FAMILY = {"abnormal_result": "abnormal_result", "test_not_resulted": "test_never_resulted", "referral_reply": "referral",
          "fit_positive": "positive_screen", "mammogram_recall": "positive_screen", "drug_monitoring": "drug_monitoring",
          "planned_review": "planned_review", "committed_test": "followup_recommended",
          "hospital_followup": "followup_recommended", "imaging_followup": "followup_recommended"}
URGENCY = {"abnormal_result": 3, "fit_positive": 3, "mammogram_recall": 3}


def load_lookback(out_dir: str | Path, export_dir: str | Path | None = None) -> dict:
    out = Path(out_dir)
    summary = json.loads((out / "summary.json").read_text())
    flags = list(csv.DictReader((out / "flags.csv").open(encoding="utf-8")))
    texts = {}
    if export_dir:
        ex = Path(export_dir)
        for table, key, text in (("consultations", "encounter_key", "plan_text"), ("documents", "doc_key", "text")):
            p = ex / f"{table}.csv"
            if p.exists():
                for r in csv.DictReader(p.open(encoding="utf-8")):
                    texts[r[key]] = r.get(text) or ""
    docs, items, pats = {}, [], {}
    for i, f in enumerate(flags):
        keys = [k for k in f["source_keys"].split(";") if k]
        for k in keys:
            docs.setdefault(k, {"id": k, "kind": f["kind"], "channel": "export", "available_at": f["started"], "author": None,
                                "patient_id": f["patient_key"], "text": texts.get(k, "(record in the export: " + k + ")"), "data": {}})
        pats.setdefault(f["patient_key"], {"id": f["patient_key"], "name_en": f["patient_key"], "name_zh": "", "hkid": "",
                                           "sex": "", "dob": "", "mrn": "", "registered_doctor": "", "cdcc": False})
        u = URGENCY.get(f["kind"], 2)
        items.append({"id": f"LB{i:05d}", "patient_id": f["patient_key"], "owner_id": "clinic", "family": FAMILY.get(f["kind"], f["kind"]),
                      "reason": f"{f['status'].capitalize()}: {f['detail']}"[:200], "urgency_at_open": u, "urgency": u, "history": [],
                      "opened_at": f["owed_by"], "due": f["owed_by"], "resolved_at": None, "resolution": None, "escalates": None,
                      "evidence": keys, "reasoning": [{"step": "source", "text": f"Look-back: {f['kind'].replace('_', ' ')} owed from "
                                                       f"{f['started'][:10]}, due {f['owed_by'][:10]}; status {f['status']}.",
                                                       "cites": [{"record": k, "quote": f.get("quote") or None} for k in keys]}],
                      "checks": []})
    return {"format": "harness-run/1",
            "source": {"kind": "lookback", "simulated": False, "clinic": "", "clinic_name": "Phase 0 look-back",
                       "system": "", "harness": f"lookback[{summary.get('extractor')}]", "start": min((f["started"] for f in flags), default=None),
                       "end": summary.get("as_of")},
            "staff": [{"id": "clinic", "name": "Clinic team", "role": "team"}],
            "patients": pats, "documents": docs, "items": items, "feeds": [], "evaluation": {"lookback": summary}}
