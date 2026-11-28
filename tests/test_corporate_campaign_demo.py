"""End-to-end verification for the Central Health · Corporate Wellness Demo (?pack=corporate_health).

Covers:
1. Pack isolation: default `/api/v1/demo/live/journeys` returns the 4 flagship GP cases untouched;
   `?pack=corporate_health` returns the 4 Central Health Corporate Wellness cases.
2. Case 1 (`corp_flu_clean`): Zero-doctor-workload routine flu shot -> HK CHP screen -> 10-min Nurse Bay booking card + Day +2 #ai-team loop.
3. Case 2 (`corp_flu_flag`): Prior flu-jab hives/wheeze + Apixaban -> blocks nurse bay self-serve -> escalates to Needs Decision with Flublok (CVX 185) + 30-min observation.
4. Case 3 (`corp_prev_bundle`): Cantonese default flu shot -> converts to HK Preventive (Shingrix, Mammogram, FIT) + Longevity Biomarkers (ApoB, Lp(a), hs-CRP, HbA1c, DEXA) -> lab slip + leaflet + bundled morning slot on sign-off.
5. Case 4 (`corp_flu_defer`): 38.6°C fever -> defers jab per 48h fever-free rule -> releases tomorrow's 10:15 nurse bay + arms Day +5 #ai-team auto-rebook with zero doctor load.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.seed import reset_and_seed
from backend.vault.vault import DocumentVault, LocalFSBackend


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["DEMO_MODE"] = "true"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    return TestClient(app)


def test_corporate_wellness_pack_4_cases_end_to_end(tmp_path: Path):
    client = _make_client(tmp_path)
    r_reset = client.post("/api/v1/demo/live/reset")
    assert r_reset.status_code == 200

    # 1. Pack isolation check
    gp_pack = client.get("/api/v1/demo/live/journeys").json()
    assert gp_pack["pack"] == "gp"
    assert [j["key"] for j in gp_pack["journeys"]] == ["wingyan", "tang_uti", "chan", "lau_chest"]

    corp_pack = client.get("/api/v1/demo/live/journeys?pack=corporate_health").json()
    assert corp_pack["pack"] == "corporate_health"
    assert [j["key"] for j in corp_pack["journeys"]] == [
        "corp_flu_clean",
        "corp_flu_flag",
        "corp_prev_bundle",
        "corp_flu_defer",
    ]

    # ---- CASE 1: corp_flu_clean (Zero Doctor Interruption -> Nurse Vaccination Bay) ----
    s1 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "corp_flu_clean", "lang": "en"}).json()
    assert len(s1["suggested_replies"]) == 1
    t1_1 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_clean", "message": s1["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    assert t1_1["case_state"] == "running"
    assert t1_1["booking_offer"] is None
    assert "!" not in t1_1["reply"]
    assert len(t1_1["suggested_replies"]) == 1

    t1_2 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_clean", "message": t1_1["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    # Zero doctor interruption: state is 'scheduled' (NOT needs_decision!) and Nurse Bay booking card attached
    assert t1_2["case_state"] == "scheduled"
    assert t1_2["lint_report"]["passed"] is True
    assert t1_2["booking_offer"] is not None
    assert t1_2["booking_offer"]["recommended"]["doctor_id"] == "nurse_chan"
    assert "Nurse Flu Vaccination Bay" in t1_2["booking_offer"]["visit_type_en"]

    # ---- CASE 2: corp_flu_flag (Prior Vaccine Reaction + Apixaban -> Blocks Nurse Bay & Escalates to GP) ----
    s2 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "corp_flu_flag", "lang": "en"}).json()
    t2_1 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_flag", "message": s2["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    assert t2_1["case_state"] == "running"
    assert t2_1["booking_offer"] is None
    assert len(t2_1["suggested_replies"]) == 1

    t2_2 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_flag", "message": t2_1["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    assert t2_2["case_state"] == "needs_decision"
    assert t2_2["lint_report"]["passed"] is True
    assert t2_2["booking_offer"] is None
    ov2 = client.get("/api/v1/workspace/overview?mode=live").json()
    flag_case = next(c for c in ov2["cases"] if c["case_key"] == "corp_flu_flag")
    assert flag_case["next_checkpoint_label"] == "Flu · GP Clearance Needed"
    flag_orders = flag_case["summary"]["issues"][0]["suggested_orders"]
    assert any("Flublok" in str(o.get("drug", "")) for o in flag_orders)

    # Doctor signs Flublok + supervised GP consult + post-vax leaflet -> delivers e-Rx, Leaflet, and doctor-supervised booking card!
    conf_flag = client.post(
        f"/api/v1/workspace/cases/{flag_case['id']}/confirm",
        json={
            "actor_name": "Locum Dr. Sarah Wong",
            "mchk_reg_no": "M16412",
            "instruction_text": "Flublok CVX 185 + 30-min observation + safety guide",
            "orders": flag_orders,
        },
    )
    assert conf_flag.status_code == 200
    st2_post = client.get("/api/v1/demo/live/chat/state/corp_flu_flag?lang=en").json()
    signed_flag = next(m for m in reversed(st2_post["messages"]) if m.get("care_plan"))
    assert len(signed_flag["care_plan"]["prescriptions"]) == 1
    assert len(signed_flag["care_plan"]["leaflets"]) == 1
    assert signed_flag["care_plan"]["leaflets"][0]["leaflet_id"] == "flu_post_vax_hk"
    assert st2_post["messages"][-1].get("booking_offer") is not None
    assert st2_post["messages"][-1]["booking_offer"]["recommended"]["doctor_id"] == "doc_dr_wong"

    # ---- CASE 3: corp_prev_bundle (Cantonese default -> HK Preventive + Longevity ApoB/Lp(a)/hs-CRP/DEXA Upsell) ----
    s3 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "corp_prev_bundle", "lang": "zh-HK"}).json()
    assert s3["journey"]["default_lang"] == "zh-HK"
    t3_1 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_prev_bundle", "message": s3["suggested_replies"][0], "lang": "zh-HK", "is_preset": True},
    ).json()
    assert t3_1["case_state"] == "running"
    assert t3_1["booking_offer"] is None
    assert "！" not in t3_1["reply"] and "!" not in t3_1["reply"]

    t3_2 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_prev_bundle", "message": t3_1["suggested_replies"][0], "lang": "zh-HK", "is_preset": True},
    ).json()
    assert t3_2["case_state"] == "needs_decision"
    assert t3_2["lint_report"]["passed"] is True
    assert t3_2["booking_offer"] is None
    ov3 = client.get("/api/v1/workspace/overview?mode=live").json()
    prev_case = next(c for c in ov3["cases"] if c["case_key"] == "corp_prev_bundle")
    prev_orders = prev_case["summary"]["issues"][0]["suggested_orders"]
    assert any("Shingrix" in str(o.get("drug", "")) for o in prev_orders)
    assert any("ApoB" in str(o.get("testName", "")) and "Lp(a)" in str(o.get("testName", "")) for o in prev_orders)
    assert any("DEXA" in str(o.get("modality", "")) and "Mammogram" in str(o.get("modality", "")) for o in prev_orders)

    # Doctor signs the combined Preventive + Longevity bundle -> delivers Lab Slip, Leaflet, and bundled morning Nurse Bay booking card!
    conf_prev = client.post(
        f"/api/v1/workspace/cases/{prev_case['id']}/confirm",
        json={
            "actor_name": "Locum Dr. Sarah Wong",
            "mchk_reg_no": "M16412",
            "instruction_text": "Shingrix + Longevity ApoB/Lp(a)/hs-CRP + Mammogram & DEXA + Guide",
            "orders": prev_orders,
        },
    )
    assert conf_prev.status_code == 200
    st3_post = client.get("/api/v1/demo/live/chat/state/corp_prev_bundle?lang=en").json()
    signed_prev = next(m for m in reversed(st3_post["messages"]) if m.get("care_plan"))
    assert len(signed_prev["care_plan"]["lab_slips"]) == 1
    assert "ApoB" in signed_prev["care_plan"]["lab_slips"][0]["title_en"]
    assert len(signed_prev["care_plan"]["leaflets"]) == 1
    assert signed_prev["care_plan"]["leaflets"][0]["leaflet_id"] == "longevity_preventive_hk"
    assert st3_post["messages"][-1].get("booking_offer") is not None
    assert st3_post["messages"][-1]["booking_offer"]["recommended"]["doctor_id"] == "nurse_chan"

    # ---- CASE 4: corp_flu_defer (38.6°C Fever -> Releases Tomorrow's Bay + Arms Day +5 #ai-team Rebook, Zero Doctor Load) ----
    s4 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "corp_flu_defer", "lang": "en"}).json()
    t4_1 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_defer", "message": s4["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    assert t4_1["case_state"] == "running"
    assert "48 hours" in t4_1["reply"]
    assert t4_1["booking_offer"] is None

    t4_2 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={"journey_key": "corp_flu_defer", "message": t4_1["suggested_replies"][0], "lang": "en", "is_preset": True},
    ).json()
    # Zero doctor interruption: state is 'scheduled' (in #ai-team), tomorrow's slot released, Day +5 rebook armed
    assert t4_2["case_state"] == "scheduled"
    assert t4_2["lint_report"]["passed"] is True
    assert t4_2["booking_offer"] is None
    ov4 = client.get("/api/v1/workspace/overview?mode=live").json()
    defer_case = next(c for c in ov4["cases"] if c["case_key"] == "corp_flu_defer")
    assert defer_case["next_checkpoint_label"] == "Slot released · AI Day-5 rebook"
    assert any("Day +5" in str(t.get("label", "")) for t in defer_case["summary"]["scheduled_tasks"])

    # Verify Inbox Zero for doctor & # clinic-team handoffs populated for both zero-doctor corporate cases
    assert ov4["counts"]["needs_decision"] == 0
    handoff_ids = {h["id"] for h in ov4["team_handoffs"]}
    assert "adm_corp_flu_clean" in handoff_ids
    assert "adm_corp_flu_defer" in handoff_ids
