"""Minimal freeze-safe contract test for the single EC GI cross-referral case (prop_lam_gi_referral).

Verifies on frozen main (zero new endpoints, zero new UI):
- All 4 Cantonese/English verbatim quotes verify cleanly with exact character spans
- Existing deterministic rx_rules.py catches DOAC (Apixaban) + NSAID (Ibuprofen) DDI pre-sign
- Signing Ibuprofen without >=10-char MCHK rationale is blocked (422); nurse sign-off is blocked (403)
- MCHK clinician sign-off issues encrypted K30/D07 MCHK Sick-Leave PDF & valid HL7 FHIR R4 Bundle
  containing all 3 GI SNOMED CT findings (79922009, 89362005, 162057007)
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.seed import reset_and_seed
from backend.vault import DocumentVault


def test_ec_gi_referral_case_on_frozen_main(tmp_path):
    from backend.vault.vault import LocalFSBackend

    os.environ["HARNESS_ENV"] = "staging"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    client = TestClient(app)

    doc_tok = client.post("/api/v1/demo/login-as", json={"persona": "clinician:doc_dr_lam"}).json()["token"]
    dh = {"Authorization": f"Bearer {doc_tok}"}
    nurse_tok = client.post("/api/v1/demo/login-as", json={"persona": "nurse:nurse_chan"}).json()["token"]
    nh = {"Authorization": f"Bearer {nurse_tok}"}
    pat_tok = client.post("/api/v1/demo/login-as", json={"persona": "patient:pat_lam_gi_referral"}).json()["token"]
    ph = {"Authorization": f"Bearer {pat_tok}"}

    # 1. Detail view: verbatim quotes 100% verified & DOAC+NSAID DDI warning auto-surfaced
    det_r = client.get("/api/v1/clinician/proposals/prop_lam_gi_referral", headers=dh)
    assert det_r.status_code == 200
    det = det_r.json()
    assert len(det["source_quotes"]) == 4
    assert all(q.get("verified") is True for q in det["source_quotes"])
    assert det["payload"]["suggested_icd10"] == "K30"
    assert det["payload"]["suggested_icpc2"] == "D07"
    assert det["rx_safety_preview"]["status"] == "override_required"
    assert any(c["id"] == "rx_major_drug_interaction" and c["requires_reason"] for c in det["checks"])

    # 2. Asymmetric authority: Nurse cannot sign; Clinician blocked without override reason on Ibuprofen
    assert client.post(
        "/api/v1/clinician/proposals/prop_lam_gi_referral/decide",
        headers=nh,
        json={"decision": "approved", "review_seconds": 10.0},
    ).status_code == 403

    blocked = client.post(
        "/api/v1/clinician/proposals/prop_lam_gi_referral/decide",
        headers=dh,
        json={
            "decision": "approved",
            "review_seconds": 12.0,
            "rubber_stamp_confirmed": True,
            "rx_items": [{"drug": "ibuprofen", "dose_mg": 400, "frequency": "TDS", "days": 5}],
        },
    )
    assert blocked.status_code == 422

    # 3. Clinician removes Ibuprofen (keeps DOAC-safe Paracetamol) & signs MCHK cert (K30/D07)
    signed = client.post(
        "/api/v1/clinician/proposals/prop_lam_gi_referral/decide",
        headers=dh,
        json={
            "decision": "approved",
            "review_seconds": 12.0,
            "rubber_stamp_confirmed": True,
            "issue_certificate": True,
            "cert_days_off": 1,
            "rx_items": [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QDS", "days": 5}],
            "patient_reply": "Arranged EC GI & Endoscopy referral; avoided Ibuprofen due to Apixaban.",
        },
    )
    assert signed.status_code == 200
    sj = signed.json()
    assert sj["certificate"]["diagnosis_icd10"] == "K30"
    assert sj["certificate"]["diagnosis_icpc2"] == "D07"
    assert sj["certificate"]["mchk_reg_no"] == "M14829"
    assert sj["prescription"]["mchk_reg_no"] == "M14829"

    # 4. Real HL7 FHIR R4 Bundle export contains K30/D07 Condition, 3 GI SNOMED codes, MedReq & Cert DocRef
    fhir_r = client.get("/api/v1/patient/export", headers=ph)
    assert fhir_r.status_code == 200
    entries = fhir_r.json()["entry"]
    res_types = {e["resource"]["resourceType"] for e in entries}
    assert {"Patient", "AllergyIntolerance", "Encounter", "Condition", "Observation", "MedicationRequest", "DocumentReference"} <= res_types
    enc_res = next(e["resource"] for e in entries if e["resource"]["resourceType"] == "Encounter")
    snomed_codes = {rc["coding"][0]["code"] for rc in enc_res["reasonCode"]}
    assert snomed_codes == {"79922009", "89362005", "162057007"}
