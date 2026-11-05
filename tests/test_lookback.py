"""Phase 0 look-back on a tiny hand-made export (synthetic, opaque keys)."""
import csv

from backend.lookback import TABLES, columns, run


def export(tmp_path, rows: dict):
    for t in TABLES:
        with (tmp_path / f"{t}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns(t))
            w.writeheader()
            for r in rows.get(t, []):
                w.writerow({c: r.get(c, "") for c in columns(t)})
    return tmp_path


def res(key, pid, lab_no, t, test, value, lo, hi, flag, via="portal"):
    return {"report_key": key, "patient_key": pid, "lab_no": lab_no, "reported_datetime": t, "received_datetime": t,
            "received_via": via, "test": test, "value": value, "ref_low": lo, "ref_high": hi, "flag": flag}


def test_lookback_flags_what_never_closed(tmp_path):
    rows = {
        "lab_orders": [{"order_key": "O1", "patient_key": "P1", "datetime": "2027-03-01T10:00", "lab_no": "PA-1", "tests": "RFT"},
                       {"order_key": "O2", "patient_key": "P2", "datetime": "2027-03-01T10:00", "lab_no": "KI-2", "tests": "CBC"}],
        "lab_results": [res("R1", "P1", "PA-1", "2027-03-02T18:00", "Potassium", 6.2, 3.5, 5.1, "H"),
                        res("R2", "", "KI-2", "2027-03-02T18:00", "Haemoglobin", 13.1, 11.5, 15.5, "", via="fax")],
        "referrals": [{"referral_key": "F1", "patient_key": "P3", "datetime": "2027-03-01T11:00", "destination": "HA",
                       "specialty": "Eye", "urgency": "stable"},
                      {"referral_key": "F2", "patient_key": "P3", "datetime": "2027-03-01T11:05", "destination": "private",
                       "specialty": "Eye", "urgency": "stable"}],
        "prescriptions": [{"rx_key": "X1", "patient_key": "P4", "datetime": "2027-03-01T10:00", "drug": "Lisinopril 10 MG",
                           "new_start": "Y"}],
        "consultations": [{"encounter_key": "E1", "patient_key": "P5", "datetime": "2027-03-01T10:00", "visit_type": "chronic follow-up",
                           "plan_text": "BP 150/95. RV 4/52."},
                          {"encounter_key": "E9", "patient_key": "P9", "datetime": "2027-12-31T10:00", "visit_type": "walk-in",
                           "plan_text": "URTI."}],
    }
    out = run(export(tmp_path, rows))
    got = {(f.kind, tuple(f.source_keys), f.status) for f in out["flags"]}
    assert ("abnormal_result", ("R1",), "unclosed") in got                    # K 6.2, nobody called
    assert ("referral_reply", ("F1",), "unknown") in got                      # HA booking lives in eHealth
    assert ("referral_reply", ("F2",), "unclosed") in got                     # private: no reply letter
    assert ("drug_monitoring", ("X1",), "unclosed") in got                    # ACE inhibitor, no RFT
    assert ("planned_review", ("E1",), "unclosed") in got                     # RV 4/52, never seen again
    assert not any(k == "test_not_resulted" and s == ("O2",) for k, s, _ in got)   # unfiled result linked by lab no
    assert out["summary"]["unfiled_results_linked_by_request_number"] == 1


def test_data_request_lists_every_field():
    """docs/EC_DATA_REQUEST.md is what EC is asked for; it must match the format the look-back reads."""
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "EC_DATA_REQUEST.md").read_text(encoding="utf-8")
    for t, (_, fields) in TABLES.items():
        assert f"`{t}`" in doc, t
        for f in fields:
            assert f"`{f.name}`" in doc, f"{t}.{f.name}"
