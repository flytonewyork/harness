"""Comprehensive tests for the Clinical Knowledge Factory (WO #62).

Verifies:
1. Exact 1:1 parity of knowledge/default/ with hardcoded engine/case/formulary/insurance/verify defaults.
2. Additive clinic overlay merging (knowledge/ec_clinic_1/) without weakening baseline entries.
3. Immutable safety floor enforcement (v1.0 / union_only / asymmetric authority / clinician-signed monitoring / WhatsApp zero-leakage).
4. Zero-PHI boundary on KnowledgeGap & KnowledgeGapLog (PHIScrubber applied to every field before serialization).
5. All 5 zero-LLM-call GapEmitters + non-blocking log behavior.
6. Deterministic Task.fingerprint() invalidation when shorthand_glossary changes (and 1:1 preservation on default).
7. Offline propose-only Curator CLI (clustering, unified YAML diffs, synthetic LoopBench scenario stubs, blind-set hygiene).
8. Product invariant: backend/knowledge/ never imports loopbench.
"""
from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest
import yaml

from backend.acute.security import PHIScrubber
from backend.case import model as case_model
from backend.engine import agentic as engine_agentic
from backend.engine import verify as engine_verify
from backend.extract.reader import Task, doc_key
from backend.journeys import insurance as journey_insurance
from backend.journeys.medication import formulary
from backend.knowledge import (
    BlindSetHygieneError,
    DEFAULT_KNOWLEDGE_DIR,
    GAP_KINDS,
    GapEmitters,
    KnowledgeError,
    KnowledgeGap,
    KnowledgeGapLog,
    SafetyFloorViolation,
    assert_blind_set_hygiene,
    cluster_gaps,
    curate_gaps,
    default_knowledge,
    list_clinics,
    load_gap_log,
    load_knowledge,
    write_curation_artifacts,
)
from backend.knowledge.curate import main as curate_cli_main


# ---------------------------------------------------------------------------
# 1. Exact 1:1 baseline parity (knowledge/default/ vs hardcoded defaults)
# ---------------------------------------------------------------------------
def test_default_pack_matches_formulary_and_case_model_1_to_1():
    kn = default_knowledge()
    assert kn.clinic_id == "default"
    assert "default" in list_clinics()
    assert "ec_clinic_1" in list_clinics()

    # Every alias in formulary.ALIASES and case_model._DRUG_ALIASES is present in default pack
    for alias, expected_gen in formulary.ALIASES.items():
        assert kn.resolve_generic(alias) == expected_gen, f"formulary alias mismatch for {alias!r}"
        assert kn.resolve_generic(alias) == formulary.generic(alias)

    for alias in case_model._DRUG_ALIASES:
        assert kn.resolve_drug_key(alias) == case_model.drug_key(alias), f"case drug_key mismatch for {alias!r}"

    # Sample inputs shared by both tables match bit-for-bit
    shared_inputs = [
        "Warfarin Sodium 5mg",
        "華法林",
        "薄血丸",
        "Augmentin 1g BD",
        "Amlodipine 5mg daily",
        "Panadol 500mg",
        "Eliquis 5mg bd",
        "二甲雙胍 500mg",
        "Levothyroxine 50mcg",
        "未知中藥",
    ]
    for text in shared_inputs:
        assert kn.resolve_generic(text) == formulary.generic(text), f"generic mismatch for {text!r}"
        assert kn.resolve_drug_key(text) == case_model.drug_key(text), f"drug_key mismatch for {text!r}"
        assert kn.drug_class(text) == formulary.drug_class(text), f"drug_class mismatch for {text!r}"
        assert kn.monitoring_for(text) == formulary.monitoring_for(text), f"monitoring mismatch for {text!r}"

    # Unified table resolves aliases previously present in only one of the two hardcoded dicts
    assert kn.resolve_generic("Marevan 3mg nocte") == "warfarin"
    assert kn.resolve_drug_key("Marevan 3mg nocte") == "warfarin"
    assert kn.drug_class("Marevan 3mg nocte") == "anticoagulant"
    assert kn.resolve_generic("Diamicron 30mg") == "gliclazide"
    assert kn.resolve_drug_key("Diamicron 30mg") == "gliclazide"
    assert kn.resolve_generic("安滅菌 1g") == "amoxicillin-clavulanate"
    assert kn.resolve_drug_key("安滅菌 1g") == "amoxicillin clavulanate"

    # Every drug class and monitoring rule matches formulary 1:1
    assert dict(kn.domain.drug_classes) == formulary.CLASSES
    assert kn.domain.high_risk_classes == formulary.HIGH_RISK
    assert kn.domain.controlled_classes == formulary.CONTROLLED
    assert kn.domain.short_course_classes == formulary.SHORT_COURSE
    assert kn.domain.no_gap_classes == formulary.NO_GAP
    assert kn.domain.review_days == formulary.REVIEW_DAYS
    assert (kn.domain.doac_renal_monitoring.tests, kn.domain.doac_renal_monitoring.max_days) == formulary.DOAC_RENAL
    for cls_name, expected in formulary.MONITORING.items():
        rule = kn.domain.monitoring_rules[cls_name]
        assert (rule.tests, rule.max_days) == expected


def test_default_pack_matches_engine_data_access_tables_1_to_1():
    kn = default_knowledge()

    # Exact match with backend/engine/agentic.py:TEST_WORDS
    assert {k: list(v) for k, v in kn.data_access.lab_test_synonyms.items()} == engine_agentic.TEST_WORDS
    assert kn.tests_for("repeat RFT in 2 weeks") == ["Creatinine", "Potassium", "Sodium", "Urea", "eGFR"]
    assert kn.tests_for("recheck HbA1c and lipid") == ["HbA1c", "cholesterol", "Triglycerides"]

    # Exact match with backend/journeys/insurance/__init__.py:DONE_WORDS
    assert list(kn.data_access.closure_markers) == journey_insurance.DONE_WORDS

    # Exact match with backend/engine/verify.py:_NAME_PATTERNS
    expected_patterns = [p.pattern for p in engine_verify._NAME_PATTERNS]
    assert list(kn.data_access.document_id_patterns) == expected_patterns
    assert len(kn.compiled_name_patterns()) == len(expected_patterns)


# ---------------------------------------------------------------------------
# 2. Additive clinic overlay merging (ec_clinic_1)
# ---------------------------------------------------------------------------
def test_ec_clinic_1_overlay_merges_additively_over_default():
    base = load_knowledge("default")
    ec = load_knowledge("ec_clinic_1")

    assert ec.clinic_id == "ec_clinic_1"
    assert ec.fingerprint != base.fingerprint

    # Every default alias, test synonym, and closure marker is preserved
    for alias, canon in base.domain.drug_aliases.items():
        assert ec.domain.drug_aliases[alias] == canon
    for token, tests in base.data_access.lab_test_synonyms.items():
        assert set(tests).issubset(set(ec.data_access.lab_test_synonyms[token]))
    for marker in base.data_access.closure_markers:
        assert marker in ec.data_access.closure_markers

    # EC-specific additions resolve cleanly
    assert ec.resolve_generic("保栓通 75mg") == "clopidogrel"
    assert ec.resolve_drug_key("保栓通 75mg") == "clopidogrel"
    assert ec.drug_class("保栓通 75mg") == "antiplatelet"
    assert "ALT" in ec.tests_for("repeat R/LFT in 1 month")
    assert "Creatinine" in ec.tests_for("repeat R/LFT in 1 month")
    assert "TSH" in ec.tests_for("check TFT")
    assert "已填妥" in ec.data_access.closure_markers
    assert ec.data_access.emr_profile.ehealth_pull_available is True
    assert "F/U" in ec.domain.shorthand_glossary


# ---------------------------------------------------------------------------
# 3. Immutable safety floor enforcement
# ---------------------------------------------------------------------------
def _clone_knowledge_root(tmp_path: Path) -> Path:
    dest = tmp_path / "knowledge"
    shutil.copytree(DEFAULT_KNOWLEDGE_DIR, dest)
    return dest


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"override_v1": True},
        {"disable_red_flags": True},
        {"union_only": False},
        {"safety_floor": "optional"},
        {"auto_close": True},
        {"autonomous_rx": True},
    ],
)
def test_safety_floor_blocks_forbidden_override_keys(tmp_path: Path, bad_payload: dict):
    root = _clone_knowledge_root(tmp_path)
    clinic_dir = root / "bad_clinic"
    clinic_dir.mkdir()
    (clinic_dir / "domain.yaml").write_text(yaml.safe_dump(bad_payload), encoding="utf-8")

    with pytest.raises(SafetyFloorViolation):
        load_knowledge("bad_clinic", root=root)


def test_monitoring_override_cannot_weaken_without_clinician_signature(tmp_path: Path):
    root = _clone_knowledge_root(tmp_path)
    clinic_dir = root / "clinic_mon"
    clinic_dir.mkdir()

    # Unsigned loosening of warfarin INR window (42 -> 90 days) must FAIL
    (clinic_dir / "domain.yaml").write_text(
        yaml.safe_dump({
            "pack_id": "clinic_mon",
            "monitoring_overrides": {
                "anticoagulant": {"tests": ["INR"], "max_days": 90},
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="clinician_signed"):
        load_knowledge("clinic_mon", root=root)

    # Dropping required baseline test (eGFR from metformin) must FAIL even if signed
    (clinic_dir / "domain.yaml").write_text(
        yaml.safe_dump({
            "pack_id": "clinic_mon",
            "monitoring_overrides": {
                "metformin": {
                    "tests": ["Creatinine"],
                    "max_days": 180,
                    "clinician_signed": True,
                    "signed_by": "Dr Chan (M12345)",
                    "signed_at": "2026-11-28",
                },
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="cannot drop baseline monitoring tests"):
        load_knowledge("clinic_mon", root=root)

    # Tightening (stricter window: 42 -> 28 days) succeeds without signature
    (clinic_dir / "domain.yaml").write_text(
        yaml.safe_dump({
            "pack_id": "clinic_mon",
            "monitoring_overrides": {
                "anticoagulant": {"tests": ["INR"], "max_days": 28},
            },
        }),
        encoding="utf-8",
    )
    tight = load_knowledge("clinic_mon", root=root)
    assert tight.monitoring_for("Warfarin 3mg") == (("INR",), 28)

    # Explicitly clinician-signed loosening succeeds while keeping all baseline tests
    (clinic_dir / "domain.yaml").write_text(
        yaml.safe_dump({
            "pack_id": "clinic_mon",
            "monitoring_overrides": {
                "statin": {
                    "tests": ["LDL cholesterol", "ALT"],
                    "max_days": 540,
                    "clinician_signed": True,
                    "signed_by": "Dr Sarah Wong M12345",
                    "signed_at": "2026-11-28",
                },
            },
        }),
        encoding="utf-8",
    )
    signed = load_knowledge("clinic_mon", root=root)
    assert signed.monitoring_for("Lipitor 20mg") == (("LDL cholesterol", "ALT"), 540)


def test_routing_enforces_doctor_sent_only_and_whatsapp_zero_leakage(tmp_path: Path):
    root = _clone_knowledge_root(tmp_path)
    clinic_dir = root / "clinic_route"
    clinic_dir.mkdir()

    # doctor_sent_only: false must FAIL
    (clinic_dir / "routing.yaml").write_text(
        yaml.safe_dump({
            "templates": [{
                "id": "bad_auto",
                "channel": "whatsapp",
                "lang": "en",
                "doctor_sent_only": False,
                "body": "Please call reception.",
            }]
        }),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="doctor_sent_only"):
        load_knowledge("clinic_route", root=root)

    # Clinical term ("warfarin") inside a WhatsApp template must FAIL WhatsAppLeakageGuard
    (clinic_dir / "routing.yaml").write_text(
        yaml.safe_dump({
            "templates": [{
                "id": "bad_wa_clinical",
                "channel": "whatsapp",
                "lang": "en",
                "doctor_sent_only": True,
                "body": "Please take your warfarin and come for blood test.",
            }]
        }),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="WhatsApp zero-leakage"):
        load_knowledge("clinic_route", root=root)

    # Assigning doctor-only action ("close_item") to front_desk_actions must FAIL
    (clinic_dir / "routing.yaml").write_text(
        yaml.safe_dump({
            "role_split": {
                "front_desk_actions": ["close_item"],
            }
        }),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="Mandatory doctor-only"):
        load_knowledge("clinic_route", root=root)


# ---------------------------------------------------------------------------
# 4. Deterministic replay & Task.fingerprint() invalidation
# ---------------------------------------------------------------------------
def test_shorthand_glossary_changes_task_fingerprint_and_default_preserves_it():
    base_task = Task(
        name="insurance_read",
        version="v1",
        instructions="Read the insurer letter and extract deadline.",
        schema={"type": "object", "properties": {"ask": {"type": ["string", "null"]}}},
        quoted={"ask": "ask_quote"},
    )
    default_kn = load_knowledge("default")
    ec_kn = load_knowledge("ec_clinic_1")

    # Default pack has empty glossary -> Task and fingerprint are bit-for-bit identical
    t_default = default_kn.enrich_task(base_task)
    assert t_default is base_task
    assert t_default.fingerprint() == base_task.fingerprint()
    assert doc_key(t_default, "Sample letter") == doc_key(base_task, "Sample letter")

    # EC pack has shorthand_glossary -> Task.fingerprint() and doc_key() change deterministically
    t_ec_1 = ec_kn.enrich_task(base_task)
    t_ec_2 = ec_kn.enrich_task(base_task)
    assert t_ec_1.fingerprint() != base_task.fingerprint()
    assert t_ec_1.fingerprint() == t_ec_2.fingerprint()
    assert doc_key(t_ec_1, "Sample letter") != doc_key(base_task, "Sample letter")
    assert "F/U: follow-up visit" in t_ec_1.instructions


# ---------------------------------------------------------------------------
# 5. Zero-PHI KnowledgeGapLog & 5 initial emitters
# ---------------------------------------------------------------------------
def test_knowledge_gap_scrubs_all_phi_before_serialization(tmp_path: Path):
    jsonl_path = tmp_path / "knowledge_gaps.jsonl"
    log = KnowledgeGapLog(clinic_id="ec_clinic_1", path=jsonl_path)
    emitters = GapEmitters(log)

    gap = emitters.emit_doctor_close(
        "already_done",
        family="insurance_paperwork",
        journey="insurance",
        note=(
            "CHAN Tai Man HKID A123456(7) phone +852 9123-4567 DOB 1978-04-19 "
            "email chan@example.com — form already faxed to insurer"
        ),
        record_kind="insurer_letter",
        channel="fax",
        extra_names=["CHAN Tai Man"],
    )
    assert gap is not None
    serialized = jsonl_path.read_text(encoding="utf-8")

    # Zero raw PHI survives anywhere in serialized JSONL
    for raw_phi in ("CHAN Tai Man", "A123456", "9123-4567", "1978-04-19", "chan@example.com"):
        assert raw_phi not in serialized

    # Passes PHIScrubber with zero further redactions
    _, further_redactions = PHIScrubber.scrub(serialized, extra_names=["CHAN Tai Man"])
    assert further_redactions == []

    parsed = json.loads(serialized.strip())
    assert set(parsed.keys()) == {
        "ts", "clinic_id", "kind", "journey", "family", "pattern", "record_kind", "channel", "detail"
    }
    assert len(parsed["detail"]) <= 200
    assert parsed["kind"] == "doctor_already_done"


def test_all_five_gap_emitters_and_non_blocking_behavior():
    log = KnowledgeGapLog(clinic_id="ec_clinic_1")
    emitters = GapEmitters(log)

    emitters.emit_unmapped_test("repeat ferritin")
    emitters.emit_unmapped_drug("未知降壓藥")
    emitters.emit_quote_dropped("deadline", task_name="insurance_v1", journey="insurance")
    emitters.emit_reader_fallback("insurance_v1", journey="insurance")
    emitters.emit_parser_miss(pattern="APS Part C")
    emitters.emit_verify_checks([
        {"check": "evidence", "status": "pass"},
        {"check": "identity", "status": "warn", "detail": "header format not matched"},
        {"check": "owner", "status": "fail", "detail": "sessional doctor not rostered"},
    ])
    emitters.emit_doctor_close("already_done", family="insurance_paperwork", note="already submitted")
    emitters.emit_doctor_close("not_relevant", family="followup", note="duplicate notice")
    assert emitters.emit_doctor_close("acted", family="followup") is None
    emitters.emit_doctor_fact_reject(fact_kind="medication", pattern="降糖寶", note="wrong brand")

    kinds_recorded = {g.kind for g in log.events}
    assert kinds_recorded == GAP_KINDS
    summary = log.summary()
    assert summary["total"] == 10
    assert summary["by_kind"]["verify_warn"] == 2

    # Non-blocking: invalid kind does not crash engine caller when strict=False
    bad = log.emit(kind="not_a_real_kind", journey="core", family="f", pattern="p")
    assert bad is None
    assert log.dropped_errors == 1


# ---------------------------------------------------------------------------
# 6. Offline propose-only Curator CLI & blind-set hygiene
# ---------------------------------------------------------------------------
def test_curator_clusters_proposes_diffs_and_synthetic_stubs_without_mutating_packs(tmp_path: Path):
    gaps_file = tmp_path / "ec_lookback_gaps.jsonl"
    log = KnowledgeGapLog(clinic_id="ec_clinic_1", path=gaps_file)
    emitters = GapEmitters(log)

    # 3x recurring unmapped lab test -> should trigger proposal
    for _ in range(3):
        emitters.emit_unmapped_test("ferritin", record_kind="consult_note", channel="cms")
    # 4x recurring unmapped Cantonese drug -> should trigger proposal
    for _ in range(4):
        emitters.emit_unmapped_drug("降膽寧", record_kind="consult_note", channel="cms")
    # 3x doctor_already_done on EMR marker -> should trigger proposal
    for _ in range(3):
        emitters.emit_doctor_close(
            "already_done", family="insurance_paperwork", journey="insurance", note="eClaim submitted"
        )
    # 2x noise below threshold (min_count=3) -> must NOT generate proposal
    for _ in range(2):
        emitters.emit_unmapped_test("one_off_test")

    before_domain = (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "domain.yaml").read_text(encoding="utf-8")
    before_access = (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "data_access.yaml").read_text(encoding="utf-8")

    out_dir = tmp_path / "curation_out"
    rc = curate_cli_main([
        "--gaps", str(gaps_file),
        "--clinic", "ec_clinic_1",
        "--min-count", "3",
        "--out-dir", str(out_dir),
    ])
    assert rc == 0

    # Propose-only invariant: live knowledge/ files are NEVER mutated
    assert (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "domain.yaml").read_text(encoding="utf-8") == before_domain
    assert (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "data_access.yaml").read_text(encoding="utf-8") == before_access

    report_md = (out_dir / "proposal_report.md").read_text(encoding="utf-8")
    patch_txt = (out_dir / "proposed.patch").read_text(encoding="utf-8")
    assert "PROPOSE-ONLY" in report_md
    assert "ferritin" in patch_txt
    assert "降膽寧" in patch_txt
    assert "eClaim submitted" in patch_txt
    assert "one_off_test" not in patch_txt

    # Synthetic scenario stubs generated, strictly synthetic, blind: false, zero PHI
    stub_files = sorted((out_dir / "synthetic_scenarios").glob("*.yaml"))
    assert len(stub_files) == 3
    for sf in stub_files:
        raw_text = sf.read_text(encoding="utf-8")
        _, redactions = PHIScrubber.scrub(raw_text)
        assert redactions == []
        doc = yaml.safe_load(raw_text)
        assert doc["synthetic"] is True
        assert doc["blind"] is False
        assert doc["expected"]["hard_fails"] == 0
        assert doc["expected"]["made_up_quotes"] == 0


@pytest.mark.parametrize(
    "forbidden_name,content",
    [
        ("insurance_blind.yaml", "blind: true\ncases: []\n"),
        ("medication_firstrun.json", '{"results": []}\n'),
        ("clinical_policy.yaml", "rules: []\n"),
        ("calibration.yaml", "alpha: 1\n"),
        ("eval_v3_holdout.jsonl", "{}\n"),
        ("normal_name.jsonl", '{"blind": true, "kind": "unmapped_test"}\n'),
        ("split_test.jsonl", '{"split": "test", "kind": "unmapped_test"}\n'),
    ],
)
def test_curator_enforces_blind_set_and_holdout_hygiene(tmp_path: Path, forbidden_name: str, content: str):
    bad_file = tmp_path / forbidden_name
    bad_file.write_text(content, encoding="utf-8")
    with pytest.raises(BlindSetHygieneError):
        assert_blind_set_hygiene(bad_file)
    with pytest.raises(BlindSetHygieneError):
        load_gap_log(bad_file)


# ---------------------------------------------------------------------------
# 7. Product invariant: backend/knowledge never imports loopbench
# ---------------------------------------------------------------------------
def test_knowledge_package_never_imports_loopbench():
    # Product invariant: backend/knowledge never imports loopbench
    pkg_dir = Path(__file__).resolve().parents[1] / "backend" / "knowledge"
    py_files = sorted(pkg_dir.glob("*.py"))
    assert len(py_files) >= 4
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("loopbench"), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("loopbench"), f"{path} imports from {mod}"


def test_mentions_and_purposes_match_formulary_and_block_baseline_alias_remap(tmp_path: Path):
    kn = default_knowledge()
    for sentence in (
        "Patient takes 薄血丸 and Lipitor 20mg daily for blood pressure and 膽固醇",
        "Requesting repeat 胃藥 and Glucophage plus aspirin",
    ):
        assert set(kn.mentions(sentence)) == set(formulary.mentions(sentence))
        assert kn.purposes(sentence) == formulary.purposes(sentence)

    # Remapping a baseline alias (marevan -> paracetamol) must raise SafetyFloorViolation
    root = _clone_knowledge_root(tmp_path)
    bad_dir = root / "bad_remap"
    bad_dir.mkdir()
    (bad_dir / "domain.yaml").write_text(
        yaml.safe_dump({"pack_id": "bad_remap", "drug_aliases": {"marevan": "paracetamol"}}),
        encoding="utf-8",
    )
    with pytest.raises(SafetyFloorViolation, match="cannot remap baseline alias"):
        load_knowledge("bad_remap", root=root)


def test_curator_recursive_additive_diff_preserves_existing_insurer_patterns_and_supports_new_clinic(tmp_path: Path):
    log = KnowledgeGapLog(clinic_id="ec_clinic_1")
    emitters = GapEmitters(log)
    for _ in range(3):
        emitters.emit_parser_miss(pattern="Please complete Corporate Claim Part D")

    report = curate_gaps(log.events, clinic_id="ec_clinic_1", min_count=3)
    assert len(report.proposals) == 1
    diff = report.proposals[0].unified_diff
    # Must add the new pattern WITHOUT deleting existing EC insurer patterns
    assert "Please\\ complete\\ Corporate\\ Claim\\ Part\\ D" in diff
    assert "-    - 主診醫生報告" not in diff

    # Onboarding a brand-new clinic without an existing directory includes safe header
    new_report = curate_gaps(log.events, clinic_id="clinic_new_demo", min_count=3)
    assert len(new_report.proposals) == 1
    assert "+safety_floor: immutable" in new_report.proposals[0].unified_diff
    assert "+pack_id: clinic_new_demo" in new_report.proposals[0].unified_diff

