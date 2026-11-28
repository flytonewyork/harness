"""Comprehensive test suite for the Primary Care Clinical Knowledge Ontology (Wave 0 + Slices 1-3).

Verifies:
1. Deterministic CI linter passes with 0 errors and 0 warnings for HK, AU, and HK+EC Healthcare.
2. Mathematical check-digit validators (SNOMED Verhoeff, LOINC Mod-10) catch bad codes.
3. Minimal HK <-> AU toggle parity (~90%+ shared core, exact jurisdictional deltas).
4. Automatic UCUM unit conversion (HK g/dL <-> AU g/L Haemoglobin; NGSP % <-> IFCC mmol/mol HbA1c).
5. Monotonic Safety Guard blocks unapproved safety relaxations in overlays and allows clinician-approved ones.
6. All 3 Gold-Standard Vertical Slices (Cardiometabolic/Med Safety, Acute Resp/Red Flags, Fatigue/Masquerades).
7. openEHR Positive Exclusion gate blocks `attest_all_clear` whenever any mandatory red flag is `not_asked`.
8. Progressive 1-hop `build_context_pack()` stays strictly within token budget and preserves audience boundaries.
9. Read-only `compat.py` bridge exports legacy formulary/lab views and fixes known table bugs (`paracetamol`, `methotrexate`).
"""
from __future__ import annotations

from datetime import date

import pytest

from backend.ontology import (
    OverlayMergeError,
    SafetyMonotonicityError,
    build_context_pack,
    compile_ontology,
    get_ontology,
    lint_ontology,
    load_core_bundle,
    valid_loinc_code,
    valid_snomed_sctid,
)
from backend.ontology.compat import export_formulary_view, export_lab_bands_view
from backend.ontology.loader import DATA_DIR, load_overlay
from backend.ontology.schema import (
    OverlayPatch,
    Provenance,
    SafetyOverrideApproval,
)


# ---------------------------------------------------------------------------
# 1. Deterministic Linter & Mathematical Check Digits
# ---------------------------------------------------------------------------
def test_linter_clean_for_hk_au_and_clinic():
    for jur, clinic in (("HK", None), ("AU", None), ("HK", "ec_healthcare")):
        graph = get_ontology(jur, clinic=clinic)
        issues = lint_ontology(graph, strict_prod=False)
        errors = [i for i in issues if i.severity == "ERROR"]
        assert errors == [], f"Unexpected lint errors for {jur}/{clinic}: {errors}"
        assert len(graph.concepts) >= 595
        assert len(graph.edges) >= 160
        rfe_ids = {cid for cid, c in graph.concepts.items() if c.kind == "rfe"}
        frame_rfe_ids = {f.rfe_concept_id for f in graph.frames.values()}
        assert len(rfe_ids) == 45
        assert frame_rfe_ids == rfe_ids  # 100% RFE-to-Murtagh-Frame bijection
        assert len(graph.observation_rules) >= 56


def test_math_check_digits_catch_corrupted_codes():
    assert valid_snomed_sctid("124134002")      # G6PD deficiency
    assert valid_snomed_sctid("44054006")       # T2DM
    assert not valid_snomed_sctid("90735008")   # Corrupt Verhoeff check digit
    assert not valid_snomed_sctid("12345")      # Too short

    assert valid_loinc_code("718-7")            # Haemoglobin
    assert valid_loinc_code("98979-8")          # CKD-EPI 2021 eGFR
    assert not valid_loinc_code("718-8")        # Corrupt Mod-10 check digit
    assert not valid_loinc_code("ABC-1")


# ---------------------------------------------------------------------------
# 2. Minimal HK <-> AU Toggle & 3-Tier Clinic Overlay Inheritance
# ---------------------------------------------------------------------------
def test_hk_vs_au_minimal_toggle_and_shared_core():
    hk = get_ontology("HK")
    au = get_ontology("AU")
    ec = get_ontology("HK", clinic="ec_healthcare")

    # Shared universal core: identical concept & edge IDs (>90% shared)
    assert set(hk.concepts.keys()) == set(au.concepts.keys())
    assert set(hk.edges.keys()) == set(au.edges.keys())
    assert hk.ontology_hash != au.ontology_hash
    assert ec.ontology_hash != hk.ontology_hash

    # Emergency & crisis routing toggles cleanly
    assert hk.defaults.emergency_number == "999"
    assert au.defaults.emergency_number == "000"
    assert "2389 2222" in hk.defaults.crisis_hotlines.values()
    assert "13 11 14" in au.defaults.crisis_hotlines.values()

    # Legal drug schedule toggles cleanly
    assert hk.get_concept("drug:codeine").drug_facet.schedule == "HK_CAP134_DANGEROUS_DRUG"
    assert au.get_concept("drug:codeine").drug_facet.schedule == "AU_SUSMP_S8"
    assert hk.get_concept("drug:salbutamol").drug_facet.schedule == "HK_CAP138_PART_I_SCH3_RX"
    assert au.get_concept("drug:salbutamol").drug_facet.schedule == "AU_SUSMP_S3"
    assert hk.get_concept("drug:amoxicillin_clavulanate").drug_facet.schedule == "HK_CAP137_ANTIBIOTIC"
    assert au.get_concept("drug:amoxicillin_clavulanate").drug_facet.schedule == "AU_SUSMP_S4"

    # 3-tier clinic overlay (Core -> HK -> EC Healthcare) overrides initiation cadence cleanly
    assert hk.edges["edge:raas_monitored_by_rft"].monitoring_spec.initiation_due_days == 14
    assert ec.edges["edge:raas_monitored_by_rft"].monitoring_spec.initiation_due_days == 10
    assert ec.field_provenance["edge:raas_monitored_by_rft.monitoring_spec.initiation_due_days"] == "clinic:ec_healthcare"


def test_unit_conversion_hk_g_dl_vs_au_g_l():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    # Same critical severe anaemia: 6.8 g/dL in HK == 68 g/L in AU
    ev_hk = hk.evaluate_observation("obs:haemoglobin", 6.8, ucum="g/dL", facts={"sex": "F"})
    ev_au = au.evaluate_observation("obs:haemoglobin", 68.0, ucum="g/L", facts={"sex": "F"})

    assert ev_hk.canonical_value == 68.0
    assert ev_au.canonical_value == 68.0
    assert ev_hk.band == "critical" and ev_hk.urgency == 4
    assert ev_au.band == "critical" and ev_au.urgency == 4
    assert ev_hk.display_ucum == "g/dL" and ev_hk.display_value == 6.8
    assert ev_au.display_ucum == "g/L" and ev_au.display_value == 68.0

    # HbA1c IFCC mmol/mol -> NGSP % conversion (86 mmol/mol ≈ 10.0% -> urgent)
    ev_a1c = au.evaluate_observation("obs:hba1c", 86.0, ucum="mmol/mol")
    assert ev_a1c.urgency == 3 and ev_a1c.band == "urgent"


# ---------------------------------------------------------------------------
# 3. Monotonic Safety Guard & Patch Hygiene
# ---------------------------------------------------------------------------
def test_overlay_rejects_bare_list_and_unapproved_safety_relaxation():
    core = load_core_bundle(DATA_DIR)
    hk_ov = load_overlay(DATA_DIR / "overlays" / "hk.yaml")

    # Bare list in patch must be rejected
    bad_list_ov = OverlayPatch(
        overlay_id="clinic:bad_list",
        layer="clinic",
        extends="jurisdiction:hk",
        jurisdiction="HK",
        concept_patches={"drug:warfarin": {"aliases": {"en": ["ShouldUseAdd"]}}},
        provenance=Provenance(author="test"),
    )
    with pytest.raises(OverlayMergeError, match="Bare list"):
        compile_ontology(core, (hk_ov, bad_list_ov))

    # Lowering urgency_floor without clinician SafetyOverrideApproval must fail
    weak_ov = OverlayPatch(
        overlay_id="clinic:unsafe_relax",
        layer="clinic",
        extends="jurisdiction:hk",
        jurisdiction="HK",
        edge_patches={"edge:raas_monitored_by_rft": {"urgency_floor": 1}},
        provenance=Provenance(author="test"),
    )
    with pytest.raises(SafetyMonotonicityError, match="SafetyOverrideApproval"):
        compile_ontology(core, (hk_ov, weak_ov))

    # With explicit clinician SafetyOverrideApproval, relaxation is permitted and audited
    approved_ov = OverlayPatch(
        overlay_id="clinic:approved_relax",
        layer="clinic",
        extends="jurisdiction:hk",
        jurisdiction="HK",
        edge_patches={"edge:raas_monitored_by_rft": {"urgency_floor": 1}},
        safety_override_approvals=(
            SafetyOverrideApproval(
                clinician_signoff_id="dr.thomas.hu",
                approved_date=date(2026, 11, 28),
                rationale="Dedicated cardiometabolic nurse clinic manages routine RFT recall workflow.",
                target_paths=("edge:raas_monitored_by_rft.urgency_floor",),
            ),
        ),
        provenance=Provenance(author="test"),
    )
    compiled = compile_ontology(core, (hk_ov, approved_ov))
    assert compiled.edges["edge:raas_monitored_by_rft"].urgency_floor == 1


# ---------------------------------------------------------------------------
# 4. Vertical Slice 1 — Cardiometabolic & Medication Safety (IS_A Inheritance)
# ---------------------------------------------------------------------------
def test_slice1_triple_whammy_raas_metformin_sglt2_warfarin_pgx():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    # Bilingual Cantonese & brand alias resolution
    assert hk.resolve_drug("薄血丸").id == "drug:warfarin"
    assert hk.resolve_drug("Coversyl 5mg").id == "drug:perindopril"
    assert hk.resolve_drug("Diamicron MR 60mg").id == "drug:gliclazide"
    assert hk.resolve_drug("Panadol").id == "drug:paracetamol"
    assert hk.resolve_drug("安滅菌").id == "drug:amoxicillin_clavulanate"

    # IS_A inheritance: perindopril -> ace_inhibitor -> raas_agent
    assert hk.is_a("drug:perindopril", "drug_class:raas_agent")
    assert hk.is_a("drug:apixaban", "drug_class:anticoagulant")

    # Triple Whammy (Perindopril + HCTZ + Ibuprofen) + Metformin eGFR<30 + SGLT2i sick day
    fired = hk.evaluate_patient_edges(
        active_med_names=["Coversyl", "hydrochlorothiazide", "Brufen", "metformin", "Jardiance"],
        facts={"egfr_ml_min": 24, "acute_dehydrating_illness": True},
    )
    fired_ids = {f.edge.id for f in fired}
    assert "edge:triple_whammy_aki" in fired_ids
    assert "edge:metformin_renal_gate" in fired_ids
    assert "edge:sglt2_sick_day_edka" in fired_ids
    assert "edge:raas_monitored_by_rft" in fired_ids

    # Warfarin + NSAID + HK TCM herb use
    w_fired = hk.evaluate_patient_edges(
        active_med_names=["薄血丸", "Nurofen"],
        facts={"tcm_herb_use": True},
    )
    w_ids = {f.edge.id for f in w_fired}
    assert "edge:anticoagulant_nsaid_bleed" in w_ids
    assert "edge:warfarin_tcm_herbal_ddi" in w_ids

    # HLA-B*58:01 toggle: HK hard_blocks regardless of ethnicity; AU gates on ancestry
    hk_allo = hk.evaluate_patient_edges(active_med_names=["allopurinol"], facts={"ethnicity": "european"})
    au_allo_euro = au.evaluate_patient_edges(active_med_names=["allopurinol"], facts={"ethnicity": "european"})
    au_allo_han = au.evaluate_patient_edges(active_med_names=["allopurinol"], facts={"ethnicity": "han_chinese"})
    assert any(f.edge.id == "edge:allopurinol_pgx_hla_b_5801" and f.edge.severity == "hard_block" for f in hk_allo)
    assert not any(f.edge.id == "edge:allopurinol_pgx_hla_b_5801" for f in au_allo_euro)
    assert any(f.edge.id == "edge:allopurinol_pgx_hla_b_5801" for f in au_allo_han)


# ---------------------------------------------------------------------------
# 5. Vertical Slice 2 — Acute Respiratory / Fever & openEHR Positive Exclusions
# ---------------------------------------------------------------------------
def test_slice2_acute_resp_positive_exclusion_and_paed_gates():
    hk = get_ontology("HK")

    # openEHR Positive Exclusion: unasked mandatory red flag blocks `attest_all_clear`
    partial = hk.check_positive_exclusions(
        "rfe:cough",
        {"rf:dyspnoea_at_rest": "explicitly_excluded", "rf:chest_pain_cardiac": "explicitly_excluded"},
    )
    assert not partial.can_attest_all_clear
    assert "rf:haemoptysis" in partial.unasked_mandatory_findings

    # Only when ALL mandatory red flags are explicitly excluded can `attest_all_clear` proceed
    complete = hk.check_positive_exclusions(
        "rfe:cough",
        {
            "rf:dyspnoea_at_rest": "explicitly_excluded",
            "rf:chest_pain_cardiac": "explicitly_excluded",
            "rf:haemoptysis": "explicitly_excluded",
        },
    )
    assert complete.can_attest_all_clear

    # Present red flag immediately raises urgency 4 and blocks attest_all_clear
    red = hk.check_positive_exclusions("rfe:cough", {"rf:chest_pain_cardiac": "present"})
    assert not red.can_attest_all_clear
    assert red.max_urgency == 4

    # Febrile infant <3m and codeine <12y hard blocks
    inf = hk.evaluate_patient_edges(
        active_concept_ids=["rfe:fever"],
        active_med_names=["codeine"],
        facts={"age_years": 0.2, "age_months": 2, "obs_values": {"obs:temperature": 38.4}},
    )
    inf_ids = {f.edge.id for f in inf}
    assert "edge:febrile_infant_lt_3m" in inf_ids
    assert "edge:codeine_paed_cyp2d6_ci" in inf_ids


# ---------------------------------------------------------------------------
# 6. Vertical Slice 3 — Undifferentiated Fatigue, 7+7 Masquerades & HK vs AU Pearls
# ---------------------------------------------------------------------------
def test_slice3_fatigue_14_masquerades_and_hk_vs_au_workup_deltas():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    frame = hk.get_frame_for_rfe("rfe:fatigue")
    assert frame is not None
    primary = [m for m in frame.murtagh_prompt.masquerades if m.tier == "primary"]
    secondary = [m for m in frame.murtagh_prompt.masquerades if m.tier == "secondary"]
    assert len(primary) == 7
    assert len(secondary) == 7

    # HK MCV <80 without ferritin triggers critical no-empiric-iron guard + thalassaemia pearl
    hk_mcv = hk.evaluate_observation("obs:mcv", 71.0)
    assert any("thalassaemia" in p.lower() for p in hk_mcv.pearls)
    hk_iron = hk.evaluate_patient_edges(
        active_med_names=["oral iron"],
        facts={"obs_values": {"obs:mcv": 71.0}},
    )
    assert any(f.edge.id == "edge:no_empiric_iron_without_ferritin" and f.edge.severity == "critical" for f in hk_iron)

    # AU Ferritin triggers Coeliac + Haemochromatosis pearls, and gluten-free diet flags anti-tTG trap
    au_fer = au.evaluate_observation("obs:ferritin", 12.0, facts={"sex": "F"})
    assert any("coeliac" in p.lower() for p in au_fer.pearls)
    au_celiac = au.evaluate_patient_edges(
        active_concept_ids=["obs:anti_ttg_iga"],
        facts={"gluten_free_diet": True},
    )
    assert any(f.edge.id == "edge:coeliac_gluten_diet_prerequisite" for f in au_celiac)

    # Confirmed IDA in 58M triggers urgent bidirectional GI endoscopy workup
    ida_male = hk.evaluate_patient_edges(
        active_concept_ids=["cond:iron_deficiency_anaemia"],
        facts={"sex": "M", "age_years": 58},
    )
    assert any(f.edge.id == "edge:ida_gi_malignancy_endoscopy" and f.edge.urgency_floor == 3 for f in ida_male)

    # Folic acid without B12 triggers critical spinal degeneration guard
    folate = hk.evaluate_patient_edges(active_med_names=["folic acid"], facts={"obs_values": {}})
    assert any(f.edge.id == "edge:never_folate_alone_before_b12" for f in folate)


# ---------------------------------------------------------------------------
# 7. Progressive 1-Hop ContextPack, FHIR Export & Legacy Compat Bridge
# ---------------------------------------------------------------------------
def test_progressive_context_pack_token_budget_and_fhir_license_hygiene():
    hk = get_ontology("HK")
    pack = build_context_pack(
        hk,
        patient_context={"sex": "M", "age_years": 62, "egfr_ml_min": 28, "tcm_herb_use": True},
        rfe_ids=["rfe:fatigue"],
        med_names=["薄血丸", "Brufen", "metformin"],
        lab_results=[
            {"concept_id": "obs:haemoglobin", "value": 8.4, "ucum": "g/dL"},
            {"concept_id": "obs:mcv", "value": 72.0, "ucum": "fL"},
        ],
        max_tokens=1500,
    )
    assert pack.token_estimate <= 1500
    assert pack.audience == "clinician_only"
    assert pack.ontology_hash == hk.ontology_hash
    assert "999" in pack.text
    assert "edge:anticoagulant_nsaid_bleed" in pack.text
    assert "edge:metformin_renal_gate" in pack.text
    assert "thalassaemia" in pack.text.lower()
    assert "obs:haemoglobin" in pack.allowed_codes["concept_id"]

    # FHIR R4 export: Tier-1 default omits Tier-2 refs; Tier-2 opt-in emits bare code only (no redistributed rubric)
    fhir_t1 = hk.to_fhir_codeable_concept("cond:t2dm", include_tier2_refs=False)
    assert all("icd-10" not in c["system"] and "icpc-2" not in c["system"] for c in fhir_t1["coding"])
    fhir_t2 = hk.to_fhir_codeable_concept("cond:t2dm", include_tier2_refs=True)
    tier2_entries = [c for c in fhir_t2["coding"] if "icd-10" in c["system"] or "icpc-2" in c["system"]]
    assert len(tier2_entries) == 2
    assert all("display" not in c for c in tier2_entries)


def test_compat_bridge_fixes_known_harness_table_bugs():
    hk = get_ontology("HK")
    form = export_formulary_view(hk)
    # Fixes formulary.py bug where paracetamol was missing from CLASSES
    assert "paracetamol" in form["CLASSES"]["analgesic_non_opioid"]
    assert form["ALIASES"]["panadol"] == "paracetamol"
    assert form["ALIASES"]["薄血丸"] == "warfarin"
    # Fixes rules.py bug where methotrexate lacked LFT monitoring alongside CBC
    mtx_edges = hk.edges_for("drug:methotrexate", relations=["MONITORED_BY"])
    assert {e.target_id for e in mtx_edges} == {"panel:cbc", "panel:lft"}

    bands = export_lab_bands_view(hk)
    assert bands["CRIT"]["Potassium"] == (2.8, 6.5)
    assert bands["CRIT"]["eGFR"] == (15.0, None)


def test_safety_kernel_threshold_parity():
    """Ontology vital thresholds must NEVER undercut canonical backend/safety/kernel.py:THRESHOLDS."""
    from backend.safety.kernel import THRESHOLDS

    for jur in ("HK", "AU"):
        g = get_ontology(jur)
        # SpO2 < 94% -> critical u4; SpO2 == 94% -> urgent u3
        assert g.evaluate_observation("obs:spo2", THRESHOLDS.spo2_emergency_lt - 1.0).urgency == 4
        assert g.evaluate_observation("obs:spo2", THRESHOLDS.spo2_urgent_eq).urgency == 3
        # RR >= 25 -> critical u4
        assert g.evaluate_observation("obs:respiratory_rate", THRESHOLDS.rr_emergency_gte).urgency == 4
        # Temp >= 40.0 / <= 35.0 -> critical u4; 39.5 -> urgent u3
        assert g.evaluate_observation("obs:temperature", THRESHOLDS.temp_emergency_gte).urgency == 4
        assert g.evaluate_observation("obs:temperature", THRESHOLDS.temp_hypothermia_lte).urgency == 4
        assert g.evaluate_observation("obs:temperature", THRESHOLDS.temp_urgent_gte).urgency == 3
        # SBP < 90 or >= 180 -> critical u4; DBP >= 110 -> critical u4; HR >= 130 -> critical u4
        assert g.evaluate_observation("obs:systolic_bp", THRESHOLDS.sbp_emergency_lt - 1.0).urgency == 4
        assert g.evaluate_observation("obs:systolic_bp", THRESHOLDS.sbp_hypertensive_gte).urgency == 4
        assert g.evaluate_observation("obs:diastolic_bp", THRESHOLDS.dbp_hypertensive_gte).urgency == 4
        assert g.evaluate_observation("obs:heart_rate", THRESHOLDS.hr_emergency_gte).urgency == 4
        # Glucose < 4.0 mmol/L -> critical u4 (matching THRESHOLDS.glucose_emergency_lt)
        assert g.evaluate_observation("obs:fasting_glucose", THRESHOLDS.glucose_emergency_lt - 0.5).urgency == 4


# ---------------------------------------------------------------------------
# 8. Wave 1 Adversarial Verification Scenarios (Stage 3 Critic Suite)
# ---------------------------------------------------------------------------
def test_w1_carbimazole_agranulocytosis_and_beta_blocker_chronic_asthma_predicates():
    hk = get_ontology("HK")

    # P0-1: Carbimazole + fever OR sore throat MUST fire critical u4 agranulocytosis workup
    for rfe in ("rfe:fever", "rfe:sore_throat"):
        fired = hk.evaluate_patient_edges(active_concept_ids=[rfe], active_med_names=["carbimazole"])
        assert any(
            f.edge.id == "edge:carbimazole_agranulocytosis_cbc" and f.edge.urgency_floor == 4
            for f in fired
        ), f"Carbimazole agranulocytosis failed to fire on {rfe}"

    # P0-3: Beta-blocker contraindicated in BOTH chronic asthma AND acute asthma exacerbation via IS_A
    assert hk.is_a("cond:asthma_exacerbation", "cond:asthma_chronic")
    for cond in ("cond:asthma_chronic", "cond:asthma_exacerbation"):
        fired = hk.evaluate_patient_edges(active_concept_ids=[cond], active_med_names=["propranolol"])
        assert any(
            f.edge.id == "edge:beta_blocker_asthma_bronchospasm" and f.edge.severity == "hard_block"
            for f in fired
        ), f"Beta-blocker asthma hard_block failed on {cond}"


def test_w1_cross_pod_ddis_clopidogrel_trimethoprim_snri_zdrug():
    hk = get_ontology("HK")

    # Pod A x Pod B: Clopidogrel + omeprazole fires CYP2C19 DDI; pantoprazole does NOT
    clo_ome = hk.evaluate_patient_edges(active_med_names=["clopidogrel", "omeprazole"])
    clo_pan = hk.evaluate_patient_edges(active_med_names=["clopidogrel", "pantoprazole"])
    assert any(f.edge.id == "edge:clopidogrel_omeprazole_cyp2c19" for f in clo_ome)
    assert not any("cyp2c19" in f.edge.id for f in clo_pan)

    # Trimethoprim cross-pod high-consequence interactions (warfarin, spironolactone, MTX, pregnancy)
    tmp_multi = hk.evaluate_patient_edges(
        active_med_names=["trimethoprim", "warfarin", "spironolactone", "methotrexate"],
        facts={"pregnant": True},
    )
    tmp_ids = {f.edge.id for f in tmp_multi}
    assert {
        "edge:trimethoprim_warfarin_inr_spike",
        "edge:trimethoprim_spironolactone_hyperkalaemia",
        "edge:trimethoprim_methotrexate_pancytopenia",
        "edge:trimethoprim_pregnancy_t1_ci",
        "edge:methotrexate_pregnancy_ci",
        "edge:warfarin_pregnancy_ci",
    }.issubset(tmp_ids)

    # SNRI + tramadol (serotonin toxicity) and Z-drug + opioid (respiratory depression)
    snri = hk.evaluate_patient_edges(active_med_names=["venlafaxine", "tramadol", "ibuprofen"])
    snri_ids = {f.edge.id for f in snri}
    assert "edge:snri_tramadol_serotonin" in snri_ids
    assert "edge:snri_nsaid_gi_bleed" in snri_ids

    zdrug = hk.evaluate_patient_edges(active_med_names=["zolpidem", "codeine"])
    assert any(f.edge.id == "edge:z_drug_opioid_respiratory_depression" and f.edge.severity == "hard_block" for f in zdrug)


def test_w1_ordinal_observations_and_fit_negative_vs_positive_gate():
    hk = get_ontology("HK")

    # Negative FIT (0.0) -> normal u0, MUST NOT fire urgent colonoscopy
    fit_neg = hk.evaluate_observation("obs:fit_stool", 0.0)
    assert fit_neg.band == "normal" and fit_neg.urgency == 0 and fit_neg.direction is None
    neg_edges = hk.evaluate_patient_edges(
        active_concept_ids=["obs:fit_stool"],
        facts={"obs_values": {"obs:fit_stool": 0.0}},
    )
    assert not any(f.edge.id == "edge:fit_positive_urgent_colonoscopy" for f in neg_edges)

    # Positive FIT (1.0) -> urgent u3, direction="positive", fires colonoscopy workup
    fit_pos = hk.evaluate_observation("obs:fit_stool", 1.0)
    assert fit_pos.direction == "positive" and fit_pos.urgency == 3 and fit_pos.working_problem is not None
    pos_edges = hk.evaluate_patient_edges(
        active_concept_ids=["obs:fit_stool"],
        facts={"obs_values": {"obs:fit_stool": 1.0}},
    )
    assert any(f.edge.id == "edge:fit_positive_urgent_colonoscopy" for f in pos_edges)

    # Other {Ordinal} rules emit positive direction & working problem
    for obs_id in ("obs:bhcg_urine", "obs:h_pylori_stool_ag", "obs:anti_hcv"):
        ev = hk.evaluate_observation(obs_id, 1.0)
        assert ev.direction == "positive" and ev.urgency >= 2 and ev.working_problem is not None


def test_w1_hk_vs_au_toggles_pgx_screening_and_schedules():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    # Carbamazepine HLA-B*15:02: HK mandatory hard_block regardless of ethnicity; AU ancestry-gated
    hk_cbz = hk.evaluate_patient_edges(active_med_names=["carbamazepine"], facts={"ethnicity": "european"})
    au_cbz_euro = au.evaluate_patient_edges(active_med_names=["carbamazepine"], facts={"ethnicity": "european"})
    au_cbz_han = au.evaluate_patient_edges(active_med_names=["carbamazepine"], facts={"ethnicity": "han_chinese"})
    assert any(f.edge.id == "edge:carbamazepine_pgx_hla_b_1502" and f.edge.severity == "hard_block" for f in hk_cbz)
    assert not any(f.edge.id == "edge:carbamazepine_pgx_hla_b_1502" for f in au_cbz_euro)
    assert any(f.edge.id == "edge:carbamazepine_pgx_hla_b_1502" for f in au_cbz_han)

    # Schedule & screening toggles
    assert hk.get_concept("drug:levonorgestrel_ec").drug_facet.schedule == "HK_CAP138_PART_I_SCH3_RX"
    assert au.get_concept("drug:levonorgestrel_ec").drug_facet.schedule == "AU_SUSMP_S3"
    assert "50-75" in hk.get_concept("screen:crc_fit").display.resolve("HK", "en")
    assert "45-49" in au.get_concept("screen:crc_fit").display.resolve("AU", "en")
    assert hk.edges["edge:oral_steroid_hbv_reactivation"].severity == "hard_block"
    assert au.edges["edge:oral_steroid_hbv_reactivation"].severity == "critical"


def test_w1_complex_multimorbid_context_pack_retains_safety_labs_and_primary_frame():
    for jur, expected_num, forbidden_num in (("HK", "999", "000"), ("AU", "000", "999")):
        g = get_ontology(jur)
        pack = build_context_pack(
            g,
            patient_context={"sex": "M", "age_years": 72, "egfr_ml_min": 26},
            rfe_ids=["rfe:fatigue", "rfe:dyspnoea", "rfe:ankle_oedema"],
            med_names=[
                "warfarin", "perindopril", "spironolactone", "ibuprofen",
                "metformin", "empagliflozin", "clopidogrel", "omeprazole",
            ],
            lab_results=[
                {"concept_id": "obs:haemoglobin", "value": 8.2 if jur == "HK" else 82.0, "ucum": "g/dL" if jur == "HK" else "g/L"},
                {"concept_id": "obs:potassium", "value": 6.2, "ucum": "mmol/L"},
                {"concept_id": "obs:egfr", "value": 26.0, "ucum": "mL/min/{1.73_m2}"},
                {"concept_id": "obs:fasting_glucose", "value": 3.4, "ucum": "mmol/L"},
            ],
            max_tokens=1500,
        )
        import re

        assert pack.token_estimate <= 1500
        assert pack.audience == "clinician_only"
        assert expected_num in pack.text
        assert re.search(rf"\b{forbidden_num}\b", pack.text) is None
        # Critical/urgent labs AND primary RFE Murtagh frame MUST survive multimorbid packing
        assert "obs:potassium" in pack.text
        assert "obs:fasting_glucose" in pack.text
        assert "### RFE Frame:" in pack.text
        assert "edge:anticoagulant_nsaid_bleed" in pack.text


# ---------------------------------------------------------------------------
# 9. Full 95% Scope, Wave 2 Clinical Reasoning & 100% HK/AU Statutory Schedules
# ---------------------------------------------------------------------------
def test_w2_full_95pct_scope_bijection_and_zero_unspecified_schedules():
    for jur in ("HK", "AU"):
        g = get_ontology(jur)
        assert len(g.concepts) >= 595
        assert len(g.edges) >= 160
        assert len(g.frames) == 45
        assert len(g.observation_rules) >= 56
        assert len(g.pointers) >= 25

        # Strict 45/45 RFE <-> PresentationFrame bijection
        rfe_ids = {c.id for c in g.concepts.values() if c.kind == "rfe"}
        frame_rfe_ids = {f.rfe_concept_id for f in g.frames.values()}
        assert rfe_ids == frame_rfe_ids and len(rfe_ids) == 45

        # 100% of all 134 drugs MUST have jurisdiction-appropriate statutory schedules (0 UNSPECIFIED)
        drugs = [c for c in g.concepts.values() if c.kind == "drug"]
        assert len(drugs) >= 134
        for d in drugs:
            assert d.drug_facet is not None, f"{d.id} missing drug_facet"
            sched = d.drug_facet.schedule
            assert sched != "UNSPECIFIED", f"{jur}: {d.id} still UNSPECIFIED"
            if jur == "HK":
                assert not sched.startswith("AU_"), f"HK drug {d.id} has AU schedule {sched}"
            else:
                assert sched.startswith("AU_"), f"AU drug {d.id} has non-AU schedule {sched}"

        # Deterministic linter reports 0 errors and 0 warnings
        issues = lint_ontology(g)
        assert issues == [], f"{jur} lint issues: {issues}"


def test_w2_paeds_geriatrics_palliative_safety_and_hk_au_toggles():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    # Paediatric emergencies: Kawasaki 5d fever, intussusception, neonatal jaundice <24h
    kaw = hk.evaluate_patient_edges(active_concept_ids=["rf:kawasaki_5d_fever_mucositis"])
    assert any(f.edge.id == "edge:kawasaki_5d_fever_ivig_emergency" and f.edge.urgency_floor == 4 for f in kaw)
    assert "HA paediatric" in kaw[0].clinician_rationale

    intus = au.evaluate_patient_edges(active_concept_ids=["rf:intussusception_currant_jelly_bilious"])
    assert any(f.edge.id == "edge:intussusception_surgical_emergency" and f.edge.urgency_floor == 4 for f in intus)

    # Geriatrics STOPP: oxybutynin / amitriptyline in >=65y; haloperidol + Parkinson's hard_block
    stopp_elder = hk.evaluate_patient_edges(
        active_med_names=["oxybutynin", "amitriptyline"],
        facts={"age_years": 78},
    )
    stopp_ids = {f.edge.id for f in stopp_elder}
    assert "edge:oxybutynin_elderly_stopp_anticholinergic" in stopp_ids
    assert "edge:amitriptyline_elderly_stopp_anticholinergic" in stopp_ids

    stopp_young = hk.evaluate_patient_edges(
        active_med_names=["oxybutynin", "amitriptyline"],
        facts={"age_years": 32},
    )
    assert "edge:oxybutynin_elderly_stopp_anticholinergic" not in {f.edge.id for f in stopp_young}

    park_halo = hk.evaluate_patient_edges(
        active_concept_ids=["cond:parkinsons_disease"],
        active_med_names=["haloperidol"],
    )
    assert any(f.edge.id == "edge:haloperidol_parkinsons_ci" and f.edge.severity == "hard_block" for f in park_halo)

    # Palliative / controlled opioid safety: morphine in CKD eGFR <30 & strong opioid + benzo
    morph_ckd = hk.evaluate_patient_edges(
        active_med_names=["morphine", "lorazepam"],
        facts={"egfr_ml_min": 22},
    )
    m_ids = {f.edge.id for f in morph_ckd}
    assert "edge:morphine_ckd_egfr30_neurotoxicity" in m_ids
    assert "edge:strong_opioid_benzo_respiratory_block" in m_ids

    # HK Cap. 134 vs AU SUSMP S8 controlled schedules & telehealth block
    for cid in ("drug:morphine", "drug:oxycodone", "drug:methylphenidate"):
        assert hk.get_concept(cid).drug_facet.schedule == "HK_CAP134_DANGEROUS_DRUG"
        assert au.get_concept(cid).drug_facet.schedule == "AU_SUSMP_S8"
        assert hk.get_concept(cid).drug_facet.telehealth_blocked is True
        assert au.get_concept(cid).drug_facet.telehealth_blocked is True


def test_w2_derm_eye_ent_msk_neuro_and_cross_wave_safety_edges():
    hk = get_ontology("HK")
    au = get_ontology("AU")

    # Teratogens: isotretinoin + sodium valproate in pregnancy
    preg = hk.evaluate_patient_edges(
        active_med_names=["isotretinoin", "sodium valproate", "co-trimoxazole", "ciprofloxacin"],
        facts={"pregnant": True, "age_years": 28},
    )
    preg_ids = {f.edge.id for f in preg}
    assert {
        "edge:isotretinoin_pregnancy_teratogen_ci",
        "edge:sodium_valproate_pregnancy_teratogen_ci",
        "edge:cotrimoxazole_pregnancy_ci",
        "edge:ciprofloxacin_pregnancy_paed_ci",
    }.issubset(preg_ids)

    # Eye, ENT, Derm emergencies
    for rf_id, expected_edge in (
        ("rf:flashes_floaters_visual_curtain", "edge:flashes_floaters_retinal_detachment"),
        ("rf:contact_lens_painful_red_eye", "edge:contact_lens_red_eye_corneal_ulcer"),
        ("rf:sudden_hearing_loss_72h", "edge:sudden_hearing_loss_72h_ssnhl"),
        ("rf:sjs_ten_mucosal_blistering", "edge:sjs_ten_mucosal_blistering_emergency"),
    ):
        fired = hk.evaluate_patient_edges(active_concept_ids=[rf_id])
        assert any(f.edge.id == expected_edge and f.edge.urgency_floor >= 3 for f in fired)

    # Cross-wave safety edges: PDE5i + nitrate hard_block, PDE5i + alpha-blocker, haloperidol + amiodarone QTc,
    # ciprofloxacin + warfarin, amitriptyline + tramadol, gabapentin + oxycodone, oral steroid + T2DM
    combo = hk.evaluate_patient_edges(
        active_concept_ids=["cond:t2dm", "cond:bipolar_disorder"],
        active_med_names=[
            "sildenafil", "gtn_nitrate", "tamsulosin",
            "haloperidol", "amiodarone",
            "ciprofloxacin", "warfarin",
            "amitriptyline", "tramadol",
            "gabapentin", "oxycodone",
            "prednisolone", "sertraline",
        ],
    )
    combo_ids = {f.edge.id for f in combo}
    assert {
        "edge:sildenafil_nitrate_fatal_hypotension",
        "edge:sildenafil_alpha_blocker_hypotension",
        "edge:haloperidol_amiodarone_qtc_torsades",
        "edge:ciprofloxacin_warfarin_inr_spike",
        "edge:amitriptyline_tramadol_serotonin_seizure",
        "edge:gabapentin_opioid_respiratory_depression",
        "edge:oral_steroid_t2dm_hyperglycaemia",
        "edge:ssri_bipolar_mania_switch",
    }.issubset(combo_ids)

    # HK vs AU schedule & epidemiology toggle checks across Wave 2
    assert hk.get_concept("drug:chloramphenicol_eye").drug_facet.schedule == "HK_CAP137_ANTIBIOTIC"
    assert au.get_concept("drug:chloramphenicol_eye").drug_facet.schedule == "AU_SUSMP_S3"
    assert hk.get_concept("drug:gtn_nitrate").drug_facet.schedule == "HK_CAP138_PART_I_POISON"
    assert au.get_concept("drug:gtn_nitrate").drug_facet.schedule == "AU_SUSMP_S3"
    assert hk.get_concept("drug:adrenaline_autoinjector").drug_facet.schedule == "HK_CAP138_PART_I_SCH3_RX"
    assert au.get_concept("drug:adrenaline_autoinjector").drug_facet.schedule == "AU_SUSMP_S3"

    # Wave 2 ObservationRules: HbA2 HPLC beta-thalassaemia trait & anti-tTG IgA coeliac & {Ordinal} STI rules
    hba2 = hk.evaluate_observation("obs:hba2_hplc", 4.6)
    assert hba2.urgency >= 1 and any("beta-thalassaemia" in p.lower() for p in hba2.pearls)
    ttg = au.evaluate_observation("obs:anti_ttg_iga", 45.0)
    assert ttg.urgency >= 2 and any("gluten" in p.lower() for p in ttg.pearls)
    for ord_obs in ("obs:hiv_ag_ab", "obs:syphilis_serology", "obs:ct_ng_naat", "obs:ana_screen"):
        neg = hk.evaluate_observation(ord_obs, 0.0)
        pos = hk.evaluate_observation(ord_obs, 1.0)
        assert neg.band == "normal" and neg.urgency == 0
        assert pos.direction == "positive" and pos.urgency >= 2


# ---------------------------------------------------------------------------
# 10. End-to-End Agentic System Integration (Tools, ContextCompiler, Runner)
# ---------------------------------------------------------------------------
def test_agentic_context_compiler_with_ontology_subgraph_and_pdpo_scrubber():
    from backend.agentic import (
        CARE_LINE_PATIENT_TURN_V1,
        CLINICAL_PRESENTATION_SLOT_FILL_V1,
        HARD_TOKEN_CEILING,
        ContextCompiler,
    )
    from backend.ontology.agentic_adapter import (
        build_ontology_skill_spec,
        ontology_to_graph_nodes,
    )

    skill = build_ontology_skill_spec()
    assert skill.authority_ceiling == "CDS"
    assert "emr.write_signed_orders" not in skill.allowed_tools

    compiler = ContextCompiler()
    for jur, region in (("HK", "asia-east2"), ("AU", "australia-southeast1")):
        g = get_ontology(jur)
        nodes = ontology_to_graph_nodes(
            g,
            patient_context={"sex": "M", "age_years": 64, "egfr_ml_min": 38, "g6pd_deficient": True},
            rfe_ids=["rfe:scrotal_pain_swelling"],
            med_names=["sildenafil", "gtn_nitrate", "co-trimoxazole"],
            lab_results=[{"concept_id": "obs:potassium", "value": 6.1, "ucum": "mmol/L"}],
            max_tokens=720,
            lang="zh-HK" if jur == "HK" else "en",
        )
        snap = compiler.compile(
            template=CLINICAL_PRESENTATION_SLOT_FILL_V1,
            skills=[skill],
            active_graph_nodes=nodes,
            total_graph_nodes=len(g.concepts),
            current_input="I have sudden scrotal pain and take sildenafil with GTN spray.",
            region=region,
            channel="workspace",
        )
        assert snap.within_budget and snap.total_tokens_est <= HARD_TOKEN_CEILING
        assert snap.subgraph_pruning_ratio == f"{len(nodes)}/{len(g.concepts)} nodes injected"
        assert snap.truncated_slots == []
        slot2 = next(s for s in snap.slots if s.name == "slot2_skills_and_graph")
        assert g.defaults.emergency_number in slot2.content
        # P0-2 regression: BOTH critical safety edge AND critical lab MUST survive Slot 2 <=180-char node packing
        assert "edge:sildenafil_nitrate_fatal_hypotension" in slot2.content
        assert "obs:potassium" in slot2.content
        assert "edge:sildenafil_nitrate_fatal_hypotension" in snap.user_message
        assert "obs:potassium" in snap.user_message

    # Off-HK PDPO PHIScrubber redacts HKID/phone/patient name when region != asia-east2
    hk = get_ontology("HK")
    off_hk_snap = compiler.compile(
        template=CARE_LINE_PATIENT_TURN_V1,
        skills=[skill],
        active_graph_nodes=ontology_to_graph_nodes(hk, rfe_ids=["rfe:fatigue"], med_names=["warfarin"]),
        total_graph_nodes=len(hk.concepts),
        current_input="Patient Chan Tai Man (HKID A123456(7), tel 9123 4567) taking warfarin.",
        template_vars={"careline_name": "Sam", "clinic_name": "Clinic", "language_label": "English"},
        region="us-central1",
        patient_names_for_scrub=["Chan Tai Man"],
    )
    assert off_hk_snap.phi_scrubbed is True
    assert "A123456(7)" not in off_hk_snap.user_message
    assert "Chan Tai Man" not in off_hk_snap.user_message


def test_governed_agent_runner_end_to_end_with_ontology_tools_and_authority_guard():
    from backend.agentic import (
        CARE_LINE_PATIENT_TURN_V1,
        GovernedAgentRunner,
        ModelTurnResponse,
        ScriptedReplayTransport,
        SkillRegistry,
        ToolAuthority,
        ToolCallRequest,
        build_default_clinic_tool_registry,
    )
    from backend.ontology.agentic_adapter import (
        ONTOLOGY_TOOL_NAMES,
        build_ontology_skill_spec,
        register_ontology_tools,
    )

    # Register all 5 ontology READ/CDS tools into the production ToolRegistry
    tool_reg = build_default_clinic_tool_registry(handlers={
        "emr.write_signed_orders": lambda a, c: {"signed": True},
    })
    register_ontology_tools(tool_reg, default_jurisdiction="HK")
    for tname in ONTOLOGY_TOOL_NAMES:
        spec = tool_reg.get(tname)
        assert spec is not None
        assert spec.authority in (ToolAuthority.READ, ToolAuthority.CDS)

    # P1-14 regression: unknown obs ID & invalid exclusion state return structured {found: False, error: ...}
    bad_obs = tool_reg.execute(
        ToolCallRequest("bad1", "ontology.evaluate_observation", {"observation_concept_id": "obs:unknown_xyz", "value": 1.0}),
        allowed_tools=set(ONTOLOGY_TOOL_NAMES),
    )
    assert bad_obs.ok and bad_obs.result["found"] is False and "error" in bad_obs.result
    bad_exc = tool_reg.execute(
        ToolCallRequest("bad2", "ontology.check_positive_exclusions", {"rfe_id": "rfe:fatigue", "exclusion_states": {"rf:chest_pain": "bogus"}}),
        allowed_tools=set(ONTOLOGY_TOOL_NAMES),
    )
    assert bad_exc.ok and bad_exc.result["found"] is False and "Invalid exclusion_states" in bad_exc.result["error"]

    # Register ontology skill alongside clinic skills
    skill_reg = SkillRegistry.load_from_directory()
    skill_reg.register(build_ontology_skill_spec())

    # Multi-step governed agent turn:
    # Round 1: invokes 4 ontology tools (RFE frame, med safety, critical Hb observation, positive exclusion gate)
    #          AND attempts forbidden SIGNED_WRITE (emr.write_signed_orders) -> MUST be AUTHORITY_DENIED!
    # Round 2: emits banned reassurance ("nothing to worry") -> MUST trigger output violation + 1-retry repair!
    # Round 3: emits clean safety-netted clinical handoff -> PASSES with 0 violations!
    transport = ScriptedReplayTransport([
        ModelTurnResponse(
            tool_calls=[
                ToolCallRequest("ot1", "ontology.lookup_rfe_frame", {"rfe_id": "rfe:fatigue", "jurisdiction": "HK", "lang": "zh-HK"}),
                ToolCallRequest("ot2", "ontology.evaluate_med_safety", {"med_names": ["warfarin", "布洛芬", "allopurinol"], "facts": {"age_years": 68}, "jurisdiction": "HK"}),
                ToolCallRequest("ot3", "ontology.evaluate_observation", {"observation_concept_id": "obs:haemoglobin", "value": 6.8, "ucum": "g/dL", "facts": {"sex": "M"}, "jurisdiction": "HK"}),
                ToolCallRequest("ot4", "ontology.check_positive_exclusions", {"rfe_id": "rfe:fatigue", "exclusion_states": {}, "jurisdiction": "HK"}),
                ToolCallRequest("ot5", "emr.write_signed_orders", {"proposal_id": "rogue_rx_1"}),
            ],
            provider="vertex",
            model="gemini-2.5-flash",
        ),
        ModelTurnResponse(
            text="Don't worry, everything looks normal and there is nothing to worry about.",
            provider="vertex",
            model="gemini-2.5-flash",
        ),
        ModelTurnResponse(
            text="Your blood result and medication combination require urgent doctor review today. If you develop chest pain, severe shortness of breath, fainting, or black stools, please call 999 or attend A&E immediately.",
            provider="vertex",
            model="gemini-2.5-flash",
        ),
    ])

    runner = GovernedAgentRunner(
        skill_registry=skill_reg,
        tool_registry=tool_reg,
    )
    trace = runner.run_turn(
        template=CARE_LINE_PATIENT_TURN_V1,
        transport=transport,
        tenant_id="clinic_hk_demo",
        patient_id="pat_ont_e2e",
        intent="lab_review",
        current_input="我好攰，食緊薄血丸同布洛芬，想問血色素6.8。",
        template_vars={
            "careline_name": "Sam · Clinic Careline AI",
            "clinic_name": "Central Family Medical",
            "language_label": "English",
        },
    )

    assert trace.fallback_used is False
    assert trace.retries == 1
    assert trace.output_violations == []
    assert len(trace.tool_calls) == 5

    # Verify all 4 ontology tool executions succeeded with exact clinical semantics
    by_id = {tc["call_id"]: tc for tc in trace.tool_calls}
    assert by_id["ot1"]["ok"] is True and by_id["ot1"]["result"]["frame_id"] == "frame:fatigue"
    assert by_id["ot2"]["ok"] is True
    fired_ids = {e["edge_id"] for e in by_id["ot2"]["result"]["fired_edges"]}
    assert "edge:anticoagulant_nsaid_bleed" in fired_ids
    assert "edge:allopurinol_pgx_hla_b_5801" in fired_ids
    assert by_id["ot3"]["ok"] is True and by_id["ot3"]["result"]["urgency"] == 4
    assert by_id["ot4"]["ok"] is True and by_id["ot4"]["result"]["can_attest_all_clear"] is False
    assert len(by_id["ot4"]["result"]["unasked_mandatory_findings"]) >= 3

    # Verify Asymmetric Clinical Authority hard-denied SIGNED_WRITE
    assert by_id["ot5"]["ok"] is False and by_id["ot5"]["error_code"] == "AUTHORITY_DENIED"
    assert "999" in trace.final_text


def test_w2_critic_pass2_p0_p1_regressions():
    hk = get_ontology("HK")

    # P0-3: Unopposed estradiol MHT fires alert; adding micronised progesterone clears it unless breast cancer present
    unopposed = hk.evaluate_patient_edges(active_med_names=["estradiol_mht"])
    assert any(f.edge.id == "edge:estradiol_mht_unopposed_endometrial_risk" for f in unopposed)
    opposed = hk.evaluate_patient_edges(active_med_names=["estradiol_mht", "progesterone_micronised"])
    assert not any(f.edge.id == "edge:estradiol_mht_unopposed_endometrial_risk" for f in opposed)
    opposed_breast_ca = hk.evaluate_patient_edges(
        active_concept_ids=["cond:breast_cancer"],
        active_med_names=["estradiol_mht", "progesterone_micronised"],
    )
    assert any(f.edge.id == "edge:estradiol_mht_unopposed_endometrial_risk" for f in opposed_breast_ca)

    # P0-4: Contextual physiological intervals (paediatric phosphate, neonatal retics, pregnant AFP) -> normal u0
    assert hk.evaluate_observation("obs:phosphate", 2.20, facts={"age_years": 1}).band == "normal"
    assert hk.evaluate_observation("obs:reticulocytes", 4.5, facts={"age_months": 0.2}).band == "normal"
    assert hk.evaluate_observation("obs:afp", 80.0, facts={"pregnant": True}).band == "normal"
    assert hk.evaluate_observation("obs:afp", 80.0, facts={"pregnant": False}).urgency == 3

    # P1-10: Exact boundary >=180 U/L lipase (urgent u3) and >=3.5% HbA2 (significant u2)
    assert hk.evaluate_observation("obs:lipase", 180.0).urgency == 3
    assert hk.evaluate_observation("obs:lipase", 5.0).band == "normal"
    assert hk.evaluate_observation("obs:hba2_hplc", 3.5).urgency == 2

    # P1-5, P1-6, P1-7, P1-8, P1-9, P1-11, P1-12, P1-13
    ent_edges = hk.evaluate_patient_edges(active_med_names=["entecavir"])
    assert all((f.edge.urgency_floor or 0) <= 2 for f in ent_edges)
    assert any(f.edge.id == "edge:isotretinoin_pregnancy_teratogen_ci" for f in hk.evaluate_patient_edges(active_concept_ids=["cond:pregnancy_normal"], active_med_names=["isotretinoin"]))
    assert any(f.edge.id == "edge:cotrimoxazole_g6pd_haemolysis" for f in hk.evaluate_patient_edges(active_concept_ids=["pgx:g6pd_deficiency"], active_med_names=["co_trimoxazole"]))
    assert any(f.edge.id == "edge:ciprofloxacin_tb_masking_and_tendon" for f in hk.evaluate_patient_edges(active_concept_ids=["cond:cap"], active_med_names=["ciprofloxacin"]))
    assert any(f.edge.id == "edge:oral_steroid_t2dm_hyperglycaemia" for f in hk.evaluate_patient_edges(active_concept_ids=["cond:t1dm"], active_med_names=["prednisolone"]))
    assert any(f.edge.id == "edge:snri_bipolar_mania_switch" for f in hk.evaluate_patient_edges(active_concept_ids=["cond:bipolar_disorder"], active_med_names=["venlafaxine"]))
    assert hk.is_a("drug:quetiapine", "drug_class:antipsychotic")
    assert any(e.id == "edge:ciprofloxacin_warfarin_inr_spike" for e in hk.edges_for("drug:warfarin"))
    male_sti = hk.check_positive_exclusions("rfe:urethral_vaginal_discharge", {}, facts={"sex": "M"})
    assert "rf:ectopic_rupture_signs" not in male_sti.unasked_mandatory_findings
    assert hk.get_concept("drug:ciprofloxacin").drug_facet.schedule == "HK_CAP138_PART_I_SCH3_RX"
    assert hk.get_concept("drug:co_trimoxazole").drug_facet.schedule == "HK_CAP138_PART_I_SCH3_RX"


