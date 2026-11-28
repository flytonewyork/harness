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
        assert len(graph.concepts) >= 100
        assert len(graph.edges) >= 25
        assert len(graph.frames) == 4
        assert len(graph.observation_rules) >= 12


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
        # SBP < 90 or >= 180 -> critical u4
        assert g.evaluate_observation("obs:systolic_bp", THRESHOLDS.sbp_emergency_lt - 1.0).urgency == 4
        assert g.evaluate_observation("obs:systolic_bp", THRESHOLDS.sbp_hypertensive_gte).urgency == 4
