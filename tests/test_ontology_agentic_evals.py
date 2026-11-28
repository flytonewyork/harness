"""End-to-end tests for Option C (C1 Per-Case OSCE Memory + Selective Context,
C2 Ontology-Guided Active Question Planner, C3 Risk-Stratified F4 Symbolic Veto + 1-Retry Repair)
and the co-designed 4-Tier Paired HK+AU Evaluation Suite.
"""
from __future__ import annotations

import ast
from pathlib import Path

from backend.agentic import (
    CaseEvidenceState,
    ClinicalMemoryManager,
    ContextCompiler,
    GovernedAgentRunner,
    plan_next_questions,
    verify_turn_against_ontology,
)
from backend.agentic.ontology_eval import (
    ReferenceClinicalOntology,
    cohens_kappa,
    render_ablation_markdown,
    run_paired_ablation_suite,
)
from backend.knowledge import KnowledgeGapLog


# ---------------------------------------------------------------------------
# C1: Per-Case OSCE Memory Isolation & Quote Verification
# ---------------------------------------------------------------------------
def test_c1_per_case_episodic_isolation_preserves_longitudinal_safety_and_blocks_fake_quotes():
    mem = ClinicalMemoryManager()
    tid, pid = "ten_hk", "pat_001"

    # Patient-wide safety alert
    mem.upsert_verified_fact(
        tenant_id=tid,
        patient_id=pid,
        topic="g6pd_status",
        summary_text="Known G6PD deficiency",
        verbatim_quote="known G6PD deficiency",
        source_corpus="EMR Problem List: known G6PD deficiency since birth.",
        tier="longitudinal",
    )
    # Case A episodic fact
    r_a = mem.upsert_verified_fact(
        tenant_id=tid,
        patient_id=pid,
        topic="acute_symptoms",
        summary_text="Dry cough and fever 38.6C",
        verbatim_quote="dry cough and fever 38.6",
        source_corpus="Flu chat: I have dry cough and fever 38.6 since yesterday.",
        tier="episodic",
        case_key="case_flu",
    )
    # Case B episodic fact on SAME topic -> must NOT supersede or false-contradict Case A
    r_b = mem.upsert_verified_fact(
        tenant_id=tid,
        patient_id=pid,
        topic="acute_symptoms",
        summary_text="Ankle sprain after jogging",
        verbatim_quote="twisted my ankle jogging",
        source_corpus="Ortho chat: I twisted my ankle jogging this morning.",
        tier="episodic",
        case_key="case_ankle",
    )
    assert r_a["stored"] is True and r_b["stored"] is True
    assert r_b["contradiction_detected"] is False
    assert r_b["superseded_ids"] == []

    # Retrieving layered memories for case_ankle includes G6PD + ankle, EXCLUDES flu
    layered = mem.retrieve_layered_memories(tenant_id=tid, patient_id=pid, case_key="case_ankle")
    assert any("G6PD" in m["summary_text"] for m in layered["safety_alerts"])
    assert any("Ankle sprain" in m["summary_text"] for m in layered["case_episodic"])
    assert not any("Dry cough" in m["summary_text"] for m in layered["case_episodic"])

    # Case-scoped private Doctor-Sam threads stay isolated per case_key
    mem.append_ephemeral_private_turn(tenant_id=tid, patient_id=pid, who="doctor", actor="Dr Wong", text="Flu private note", case_key="case_flu")
    mem.append_ephemeral_private_turn(tenant_id=tid, patient_id=pid, who="doctor", actor="Dr Wong", text="Ankle private note", case_key="case_ankle")
    assert len(mem.get_ephemeral_private_thread(tid, pid, case_key="case_flu")) == 1
    assert mem.get_ephemeral_private_thread(tid, pid, case_key="case_ankle")[0]["text"] == "Ankle private note"

    # CaseEvidenceState rejects fabricated quotes fail-closed
    st = mem.get_or_create_case_state(tenant_id=tid, patient_id=pid, case_key="case_flu", jurisdiction="HK")
    bad = st.record_finding(
        concept_id="sym.diaphoresis",
        status="excluded",
        verbatim_quote="patient denied sweating completely",
        source_corpus="Patient only said: I have a cough.",
    )
    assert bad["recorded"] is False and bad["verified"] is False
    assert "sym.diaphoresis" not in st.explicitly_excluded_findings


# ---------------------------------------------------------------------------
# C2: Zero-LLM-Call Ontology-Guided Active Question Planner
# ---------------------------------------------------------------------------
def test_c2_question_planner_prioritizes_mandatory_exclusions_and_stops_on_red_flag_or_clearance():
    ont = ReferenceClinicalOntology(jurisdiction="HK")
    st = CaseEvidenceState("ten_hk", "pat_cp", "case_cp", jurisdiction="HK")
    st.add_rfe("rfe.chest_pain")

    # Turn 1: mandatory exclusions unasked -> cannot attest_all_clear, ranks mandatory exclusions first
    plan1 = plan_next_questions(ont, st, lang="zh-HK", max_questions=2)
    assert plan1.can_attest_all_clear is False
    assert plan1.should_stop_questioning is False
    assert len(plan1.questions) == 2
    assert all(q.priority_tier == "mandatory_exclusion" for q in plan1.questions)
    assert "ONTOLOGY_QUESTION_PLAN" in plan1.to_prompt_block(lang="zh-HK")

    # Exclude all 3 mandatory findings with verbatim quotes -> can_attest_all_clear becomes True
    corpus = "無標冷汗，無氣促，痛楚無延伸至手臂或下巴。"
    for cid, q in (
        ("sym.diaphoresis", "無標冷汗"),
        ("sym.dyspnoea", "無氣促"),
        ("sym.radiation_arm_jaw", "無延伸至手臂或下巴"),
    ):
        res = st.record_finding(concept_id=cid, status="excluded", verbatim_quote=q, source_corpus=corpus, turn=2)
        assert res["recorded"] is True

    plan_cleared = plan_next_questions(ont, st, max_questions=2)
    assert plan_cleared.can_attest_all_clear is True
    assert plan_cleared.unasked_mandatory_findings == ()
    # Next question shifts to discriminating Murtagh PROMPT feature (exertional/pleuritic)
    assert all(q.priority_tier == "serious_discriminator" for q in plan_cleared.questions)

    # If a red flag IS present -> immediately stops questioning and escalates to urgency 4!
    st_red = CaseEvidenceState("ten_hk", "pat_cp2", "case_cp2", jurisdiction="HK")
    st_red.add_rfe("rfe.chest_pain")
    st_red.record_finding(
        concept_id="sym.diaphoresis",
        status="present",
        verbatim_quote="breaking out in cold sweats",
        source_corpus="Patient says I am breaking out in cold sweats right now.",
        turn=1,
    )
    plan_red = plan_next_questions(ont, st_red)
    assert plan_red.should_stop_questioning is True
    assert plan_red.stop_reason == "emergency_escalation_triggered"
    assert plan_red.max_urgency == 4
    assert plan_red.can_attest_all_clear is False


# ---------------------------------------------------------------------------
# C3: Risk-Stratified F4 Symbolic Verifier & Entity-Miss Gap Logging
# ---------------------------------------------------------------------------
def test_c3_symbolic_verifier_enforces_all_5_contracts_and_logs_entity_misses():
    ont_au = ReferenceClinicalOntology(jurisdiction="AU")
    gap_log = KnowledgeGapLog(clinic_id="default")
    st = CaseEvidenceState("ten_au", "pat_au", "case_au", jurisdiction="AU")
    st.add_rfe("rfe.chest_pain")
    st.record_finding(
        concept_id="sym.diaphoresis",
        status="present",
        verbatim_quote="sweating heavily",
        source_corpus="Patient: chest tightness and sweating heavily.",
        turn=1,
    )
    pack = ont_au.build_context_pack(st)

    # Wrong jurisdiction emergency number (999 instead of 000 for AU), urgency downgrade, fake code & unmapped drug
    res = verify_turn_against_ontology(
        "Please call 999 if worse, otherwise all clear.",
        {"attest_all_clear": True, "urgency": 1, "concept_ids": ["rfe.chest_pain", "made_up.code"]},
        ontology_graph=ont_au,
        case_state=st,
        context_pack=pack,
        channel="patient_chat",
        candidate_entities=["神秘新藥99"],
        gap_log=gap_log,
    )
    codes = {v.code for v in res.violations}
    assert res.vetoed is True
    assert "PREMATURE_ALL_CLEAR" in codes
    assert "URGENCY_FLOOR_VIOLATION" in codes
    assert "MISSING_EMERGENCY_ROUTING" in codes
    assert "JURISDICTION_EMERGENCY_MISMATCH" in codes
    assert "FABRICATED_ONTOLOGY_CODE" in codes
    assert "神秘新藥99" in res.unmapped_entities
    assert len(gap_log) == 1
    assert gap_log.events[0].kind == "unmapped_drug"


# ---------------------------------------------------------------------------
# 4-Tier, 4-Arm Paired HK+AU Ablation Suite (Monotone Improvement + 0 Hard Fails on Arm 3)
# ---------------------------------------------------------------------------
def test_paired_4_arm_hk_au_ablation_suite_proves_monotone_lift_and_zero_hard_fails_on_arm3():
    report = run_paired_ablation_suite(jurisdictions=("HK", "AU"))
    md = render_ablation_markdown(report)
    assert "arm3_full_f4_veto" in md

    arm0 = report["arms"]["arm0_baseline"]
    arm1 = report["arms"]["arm1_per_case_selective_f3"]
    arm2 = report["arms"]["arm2_active_questioner"]
    arm3 = report["arms"]["arm3_full_f4_veto"]

    # Tier 1: Zero-tolerance hard safety gates — Arm 3 achieves ZERO hard fails across HK + AU
    assert arm3["tier1_hard_safety"]["total_hard_fails"] == 0
    assert arm0["tier1_hard_safety"]["total_hard_fails"] > arm3["tier1_hard_safety"]["total_hard_fails"]

    # Tier 2: Multi-turn conversation efficiency & mandatory exclusion coverage jump from 0% -> 100%
    assert arm3["tier2_conversation"]["mandatory_exclusion_coverage_by_t4"] == 1.0
    assert arm3["tier2_conversation"]["avg_turns_to_escalation"] < arm0["tier2_conversation"]["avg_turns_to_escalation"]

    # Tier 3: HealthBench-style weighted rubric score improves monotonically Arm 0 -> Arm 1 -> Arm 2 -> Arm 3
    assert arm0["tier3_healthbench_rubric_avg"] < arm1["tier3_healthbench_rubric_avg"]
    assert arm1["tier3_healthbench_rubric_avg"] < arm2["tier3_healthbench_rubric_avg"]
    assert arm2["tier3_healthbench_rubric_avg"] < arm3["tier3_healthbench_rubric_avg"]
    assert arm3["tier3_healthbench_rubric_avg"] == 1.0

    # Tier 4: Operational guardrails — strict <4096 ceiling, 100% selective admin skip, 100% 1-retry veto recovery
    assert arm3["tier4_operational"]["max_tokens_per_turn"] <= 4096
    assert arm3["tier4_operational"]["selective_skip_rate_on_admin"] == 1.0
    assert arm3["tier4_operational"]["veto_to_repair_recovery_rate"] == 1.0
    assert arm3["tier4_operational"]["avg_symbolic_verifier_ms"] < 50.0

    # Cohen's kappa calibration helper sanity check
    assert cohens_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert 0.0 < cohens_kappa([True, True, True, False, False, False], [True, True, False, False, False, False]) < 1.0


def test_new_agentic_ontology_modules_never_import_loopbench():
    agentic_dir = Path(__file__).resolve().parents[1] / "backend" / "agentic"
    for fname in ("memory.py", "context.py", "runner.py", "question_planner.py", "symbolic_verifier.py", "ontology_eval.py"):
        path = agentic_dir / fname
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("loopbench"), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("loopbench"), f"{path} imports from {mod}"
