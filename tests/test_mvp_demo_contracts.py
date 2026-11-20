"""Regression & contract tests for Sprint 1 Rationalised MVP Demo upgrades.

Covers:
- CJK & brand `drug_key()` collision fix (Marevan / 華法林 / Warfarin -> warfarin; distinct Chinese drugs don't collide)
- Bilingual OCR-safe `verify_quote_span()` (NFKC, intra-CJK spacing, PDF hyphen wrap, raw offset recovery)
- Multi-page misfax `doc_identifiers()` & `identity_of()` collision detection
- Cross-chart duplicate therapeutic class Rx check (atorvastatin + simvastatin)
- Native Traditional Chinese MCHK Sick-Leave PDF generation (no '???')
- `PHISafeLogFilter` scrubbing `%s`-formatted args
- Unified `checks[]` calculation (real counts, elderly unknown-eGFR warning, QEH conflict fail, kill-switch unknown)
- Nurse RBAC boundary (`200` on queue/detail/ready-for-doctor; `403` on clinician `/decide` & `/resolve-conflict`)
- QEH Discharge conflict resolution (`1`/`2`/`Q`) -> unlock sign -> 10-second Undo-Send (`POST /undo`)
- Patient 365-day Companion & deterministic Medical 999 vs Mental-Health crisis separation (verified HK hotlines)
"""
from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.certificates import generate_sick_leave_pdf
from backend.acute.companion import HK_MENTAL_HEALTH_HOTLINES, classify_crisis
from backend.acute.db import Database
from backend.acute.rx_rules import check_duplicate_therapeutic_classes
from backend.acute.security import PHISafeLogFilter
from backend.acute.seed import reset_and_seed
from backend.case.model import drug_key
from backend.case.quote import QuoteError, verify_quote_span
from backend.engine.verify import doc_identifiers, identity_of
from backend.vault import DocumentVault


def test_cjk_and_brand_drug_key_normalization():
    assert drug_key("Warfarin Sodium 5mg nocte") == "warfarin"
    assert drug_key("Marevan 3mg daily") == "warfarin"
    assert drug_key("華法林 3mg 每日一次") == "warfarin"
    assert drug_key("薄血丸") == "warfarin"
    # Distinct CJK drugs must NEVER collapse to empty string and collide!
    assert drug_key("二甲雙胍") == "metformin"
    assert drug_key("華法林") != drug_key("二甲雙胍")
    assert drug_key("某中藥甲") != ""
    assert drug_key("某中藥甲") != drug_key("某中藥乙")


def test_bilingual_ocr_verify_quote_span():
    ocr = "出 院 藥 物 ： 華 法 林 ３mg（NEW）\nPatient took amoxi-\ncillin 500mg TDS."
    sp1 = verify_quote_span("出院藥物：華法林 3mg(NEW)", ocr)
    assert sp1.start_char >= 0 and sp1.end_char > sp1.start_char
    assert "華" in sp1.raw_quote
    sp2 = verify_quote_span("amoxicillin 500mg", ocr)
    assert "amoxi-" in sp2.raw_quote
    # 2-letter ASCII low-entropy substring bypass must fail
    with pytest.raises(QuoteError):
        verify_quote_span("mg", ocr)


def test_multipage_misfax_identity_collision():
    mixed_scan = (
        "Page 1 — Patient: CHAN, Tai Man | DOB: 18/03/1955 | HKID: D505XXX(X)\nCBC Normal\n\n"
        "Page 2 — Patient: CHAN, Tai Ming | DOB: 09/11/1962 | HKID: A391XXX(X)\nPotassium 6.2 mmol/L"
    )
    ids = doc_identifiers(mixed_scan)
    assert "hkid_collision" in ids
    assert "dob_collision" in ids
    status, detail = identity_of(
        {"name_en": "CHAN Tai Man", "dob": "1955-03-18", "hkid": "D505XXX(X)"},
        {"patient_id": "p1", "kind": "lab_report", "text": mixed_scan},
        "p1",
    )
    assert status == "fail"
    assert "multi_patient_document_collision" in detail


def test_cross_chart_duplicate_statin_detected():
    findings = check_duplicate_therapeutic_classes([
        "Atorvastatin 40mg nocte (QEH 14 Nov)",
        "Simvastatin 20mg nocte (GP active repeat)",
        "Bisoprolol 2.5mg OM",
    ])
    assert any(f.code == "duplicate_class_statin" and f.severity == "override_required" for f in findings)


def test_cjk_sick_leave_pdf_renders_without_question_marks():
    pdf_bytes, vhash = generate_sick_leave_pdf(
        mchk_reg_no="M14829",
        doctor_name="Dr. Kenneth Lam 林啟賢醫生",
        clinic_name="Central & Mid-Levels Family Medical 中環家庭醫務中心",
        patient_name="CHAN Tai Man 陳大文",
        hkid_masked="D505XXX(X)",
        encounter_id="enc_cjk_001",
        start_date="2026-11-20",
        days_off=2,
        diagnosis_icd10="J10.1",
        diagnosis_icpc2="R80",
    )
    assert pdf_bytes.startswith(b"%PDF-")
    assert b"M14829" in pdf_bytes
    assert vhash.encode("ascii") in pdf_bytes
    assert b"???" not in pdf_bytes


def test_phi_safe_log_filter_scrubs_percent_args():
    flt = PHISafeLogFilter()
    rec = logging.LogRecord(
        name="tmrw.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="Patient HKID %s phone %s", args=("A123456(7)", "+85291234567"), exc_info=None,
    )
    assert flt.filter(rec) is True
    assert "A123456" not in rec.msg
    assert "91234567" not in rec.msg
    assert "[REDACTED_HKID]" in rec.msg
    assert rec.args == ()


def test_crisis_classification_separates_medical_and_mental_health():
    assert classify_crisis("I have severe chest pain and can't breathe") == "medical_999"
    assert classify_crisis("home reading", systolic=186, diastolic=114) == "medical_999"
    assert classify_crisis("I cannot cope anymore and want to end my life") == "mental_health_crisis"
    assert classify_crisis("尋晚開始發燒38.2度") is None
    tels = {h["tel"] for h in HK_MENTAL_HEALTH_HOTLINES}
    assert tels == {"23892222", "28960000", "23820000"}


def test_mvp_demo_stories_nurse_rbac_conflict_undo_and_companion(tmp_path):
    from backend.vault.vault import LocalFSBackend
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    client = TestClient(app)

    # 1. Health exposes 4 clean runtime toggles
    h = client.get("/api/v1/health").json()
    assert h["runtime_toggles"]["harness_runtime_mode"] == "demo"
    assert h["runtime_toggles"]["dispatch_undo_seconds"] == 10

    # 2. Nurse persona can view queue/detail & mark Ready for Doctor, but gets 403 on MCHK sign/resolve
    nurse_tok = client.post("/api/v1/demo/login-as", json={"persona": "nurse:nurse_chan"}).json()["token"]
    nh = {"Authorization": f"Bearer {nurse_tok}"}
    q_nurse = client.get("/api/v1/clinician/queue", headers=nh)
    assert q_nurse.status_code == 200
    assert q_nurse.json()["viewer_role"] == "nurse"

    ready_r = client.post(
        "/api/v1/staff/proposals/prop_elderly_flu/ready-for-doctor",
        headers=nh,
        json={"prep_note": "Confirmed RAT+ Flu A and onset <24h; no recent eGFR on file."},
    )
    assert ready_r.status_code == 200 and ready_r.json()["nurse_ready"] is True

    # Nurse MUST be blocked (403) from signing Rx/cert or settling doctor conflicts
    assert client.post(
        "/api/v1/clinician/proposals/prop_elderly_flu/decide",
        headers=nh,
        json={"decision": "approved", "review_seconds": 8.0},
    ).status_code == 403
    assert client.post(
        "/api/v1/clinician/proposals/prop_qeh_discharge/resolve-conflict",
        headers=nh,
        json={"conflict_id": "conf_warfarin_qeh", "choice": "1"},
    ).status_code == 403

    # 3. Doctor Story A (QEH Discharge): blocked until Warfarin conflict settled, duplicate statin warned, then sign + 10s Undo!
    doc_tok = client.post("/api/v1/demo/login-as", json={"persona": "clinician:doc_dr_lam"}).json()["token"]
    dh = {"Authorization": f"Bearer {doc_tok}"}
    qeh = client.get("/api/v1/clinician/proposals/prop_qeh_discharge", headers=dh).json()
    assert qeh["checks_summary"]["can_sign"] is False
    assert "Settle 1 conflict" in qeh["checks_summary"]["sign_cta"]
    check_ids = {c["id"] for c in qeh["checks"]}
    assert "dose_conflict" in check_ids
    assert "duplicate_class_statin" in check_ids
    # Verbatim anchors populated
    assert all(q.get("verified") and isinstance(q.get("start_char"), int) for q in qeh["source_quotes"])

    # Attempting to sign while conflict unresolved returns 422
    assert client.post(
        "/api/v1/clinician/proposals/prop_qeh_discharge/decide",
        headers=dh,
        json={"decision": "approved", "review_seconds": 12.0},
    ).status_code == 422

    # Settle conflict with '1' (QEH 3mg nocte) -> Sign unlocks -> Sign -> Undo within 10s!
    res_c = client.post(
        "/api/v1/clinician/proposals/prop_qeh_discharge/resolve-conflict",
        headers=dh,
        json={"conflict_id": "conf_warfarin_qeh", "choice": "1"},
    )
    assert res_c.status_code == 200 and res_c.json()["unresolved_conflicts"] == 0

    signed = client.post(
        "/api/v1/clinician/proposals/prop_qeh_discharge/decide",
        headers=dh,
        json={"decision": "approved", "review_seconds": 15.0, "patient_reply": "陳先生，請今晚起改服華法林 3mg，並停服 Simvastatin。"},
    )
    assert signed.status_code == 200
    assert signed.json()["dispatch_id"]
    assert signed.json()["dispatch_undo_seconds"] == 10

    undone = client.post("/api/v1/clinician/proposals/prop_qeh_discharge/undo", headers=dh)
    assert undone.status_code == 200 and undone.json()["status"] == "pending_review"

    # 4. Doctor Story C Variant (70M Unknown eGFR): triggers elderly renal warning on Oseltamivir
    elderly = client.get("/api/v1/clinician/proposals/prop_elderly_flu", headers=dh).json()
    renal_chk = next((c for c in elderly["checks"] if c["id"] == "renal_unknown_elderly"), None)
    assert renal_chk is not None and renal_chk["status"] == "warn" and renal_chk["affects"] == "order:oseltamivir"

    # 5. Patient Story B (Chronic HTN 365-Day Companion): 0-tab home + grounded Q&A + home BP reading + MH crisis split
    pat_tok = client.post("/api/v1/demo/login-as", json={"persona": "patient:pat_ho_htn_drift"}).json()["token"]
    ph = {"Authorization": f"Bearer {pat_tok}"}
    comp = client.get("/api/v1/patient/companion", headers=ph).json()
    assert comp["now"]["kind"] == "bp_protocol"
    assert "135/85" in comp["goal_line"]["label_en"]
    assert len(comp["protocol_nudges"]) >= 1

    turn = client.post("/api/v1/patient/companion/turn", headers=ph, json={"message": "Should I double my amlodipine tonight?", "lang": "en"})
    assert turn.status_code == 200 and turn.json()["crisis"] is None
    assert turn.json()["message"]["review_sla"]["doctor_name"] == "Dr. Kenneth Lam"

    reading = client.post(
        "/api/v1/patient/companion/reading",
        headers=ph,
        json={"kind": "bp", "systolic": 148, "diastolic": 91, "source": "monitor_photo"},
    )
    assert reading.status_code == 200 and reading.json()["crisis"] is None

    mh = client.post(
        "/api/v1/patient/companion/turn",
        headers=ph,
        json={"message": "I cannot cope anymore and have suicidal thoughts", "lang": "en"},
    )
    assert mh.status_code == 200
    assert mh.json()["crisis"]["kind"] == "mental_health_crisis"
    assert mh.json()["crisis"]["pager_acked"] is True
    assert len(mh.json()["crisis"]["hotlines"]) == 3
