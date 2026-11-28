"""Integration tests wiring Clinical Knowledge Factory (WO #62) into lane-b-agentic-graph:
1. ContextCompiler Slot 2 bounded ClinicKnowledge injection, <4096 ceiling, and deterministic context_hash.
2. Practice Curator gap-to-proposal bridge (deduplication, blind-set hygiene, locum block, Lead GP approved_for_pr).
3. Playbook Q&A grounded in ClinicKnowledge (drug aliases, lab synonyms, local shorthand, cover rules).
4. DeclarativePathwayRunner fail-closed validation of `clinic_knowledge_id` on Deployment Packs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from backend.acute.curator import (
    bridge_knowledge_gaps_to_curator,
    decide_curator_proposal,
    get_effective_practice_graph,
    list_curator_proposals,
    query_practice_playbook,
)
from backend.acute.db import Database
from backend.acute.pathway_runner import DeclarativePathwayRunner, PackValidationError
from backend.acute.practice_graph import SafetyFloorViolation as GraphSafetyFloorViolation
from backend.acute.seed import reset_and_seed
from backend.agentic import LIVE_REGISTRAR_TURN_V1, ContextCompiler, SkillRegistry
from backend.knowledge import (
    DEFAULT_KNOWLEDGE_DIR,
    BlindSetHygieneError,
    GapEmitters,
    KnowledgeGapLog,
    load_knowledge,
)
from backend.vault.vault import DocumentVault, LocalFSBackend


def test_context_compiler_injects_clinic_knowledge_within_budget_and_updates_hash():
    compiler = ContextCompiler(token_ceiling=4096)
    skills = SkillRegistry.load_from_directory().select_skills(pathway_node_id="ebm.pathway.chronic_htn_home_bp")
    nodes = [
        {
            "id": "ebm.pathway.chronic_htn_home_bp",
            "title": "Chronic HTN Home BP Review",
            "guideline_ref": "HK Reference Framework",
            "safety_floor": False,
            "summary": "Review 7-day home BP log and check RFT before titrating ACEi/ARB.",
        }
    ]
    base_kwargs = dict(
        template=LIVE_REGISTRAR_TURN_V1,
        skills=skills,
        active_graph_nodes=nodes,
        clinical_state={"patient_key": "PAT_001", "active_meds": ["Amlodipine 5mg"]},
        current_input="Patient asking about F/U R/LFT and 保栓通.",
        template_vars={
            "careline_name": "Sam",
            "clinic_name": "EC Clinic",
            "language": "en",
            "patient_key": "PAT_001",
            "case_key": "case_001",
        },
    )

    snap_none = compiler.compile(**base_kwargs)
    snap_default = compiler.compile(**base_kwargs, clinic_knowledge=load_knowledge("default"))
    snap_ec_1 = compiler.compile(**base_kwargs, clinic_knowledge=load_knowledge("ec_clinic_1"))
    snap_ec_2 = compiler.compile(**base_kwargs, clinic_knowledge=load_knowledge("ec_clinic_1"))

    # Backward compatibility when clinic_knowledge=None
    assert snap_none.knowledge_clinic_id is None
    assert "knowledge_clinic_id" not in snap_none.to_telemetry_dict()

    # Deterministic semantic_context_hash shift when ClinicKnowledge changes
    assert snap_ec_1.within_budget is True
    assert snap_ec_1.total_tokens_est <= 4096
    assert snap_ec_1.slots[0].truncated is False  # Slot 1 Safety Floor is NEVER truncated
    assert snap_ec_1.semantic_context_hash == snap_ec_2.semantic_context_hash
    assert snap_ec_1.semantic_context_hash != snap_none.semantic_context_hash
    assert snap_ec_1.semantic_context_hash != snap_default.semantic_context_hash
    assert "CLINIC_KNOWLEDGE:" in snap_ec_1.user_message
    assert "ec_clinic_1" in snap_ec_1.user_message
    assert snap_ec_1.to_telemetry_dict()["knowledge_clinic_id"] == "ec_clinic_1"
    assert snap_ec_1.to_telemetry_dict()["knowledge_fingerprint"] == load_knowledge("ec_clinic_1").fingerprint


def test_bridge_knowledge_gaps_to_curator_dedups_blocks_locum_and_stays_propose_only(tmp_path: Path):
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)

    gaps_file = tmp_path / "ec_gaps.jsonl"
    log = KnowledgeGapLog(clinic_id="ec_clinic_1", path=gaps_file)
    emitters = GapEmitters(log)
    for _ in range(3):
        emitters.emit_unmapped_test("ferritin", record_kind="consult_note", channel="cms")
    for _ in range(4):
        emitters.emit_unmapped_drug("降膽寧", record_kind="consult_note", channel="cms")

    before_domain = (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "domain.yaml").read_text(encoding="utf-8")
    before_access = (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "data_access.yaml").read_text(encoding="utf-8")

    with db.tenant_session("ten_hk_central") as session:
        # Blind-set hygiene enforced at curator bridge boundary
        blind_file = tmp_path / "insurance_blind.jsonl"
        blind_file.write_text('{"blind": true}\n', encoding="utf-8")
        with pytest.raises(BlindSetHygieneError):
            bridge_knowledge_gaps_to_curator(session, tenant_id="ten_hk_central", gaps_or_path=blind_file)

        # Stage proposals from PHI-scrubbed JSONL
        res1 = bridge_knowledge_gaps_to_curator(
            session, tenant_id="ten_hk_central", gaps_or_path=gaps_file, clinic_id="ec_clinic_1", min_count=3
        )
        assert res1["proposals_generated"] == 2
        assert len(res1["staged_proposal_ids"]) == 2
        assert res1["updated_proposal_ids"] == []

        # Re-ingesting the same gap log deduplicates instead of creating duplicate pending proposals
        res2 = bridge_knowledge_gaps_to_curator(
            session, tenant_id="ten_hk_central", gaps_or_path=gaps_file, clinic_id="ec_clinic_1", min_count=3
        )
        assert res2["staged_proposal_ids"] == []
        assert len(res2["updated_proposal_ids"]) == 2

        prop_id = res1["staged_proposal_ids"][0]
        listed = [p for p in list_curator_proposals(session, "ten_hk_central") if p["id"] == prop_id]
        assert len(listed) == 1
        assert listed[0]["ebm_safety_check"]["source"] == "clinical_knowledge_factory"
        assert listed[0]["ebm_safety_check"]["propose_only"] is True

        # Locum clinician CANNOT approve a Knowledge Factory proposal
        with pytest.raises(GraphSafetyFloorViolation):
            decide_curator_proposal(
                session,
                tenant_id="ten_hk_central",
                proposal_id=prop_id,
                decision="approve",
                decided_by="Dr. Locum Temp",
            )

        # Lead GP sign-off transitions to approved_for_pr WITHOUT mutating knowledge/*.yaml on disk
        approved = decide_curator_proposal(
            session,
            tenant_id="ten_hk_central",
            proposal_id=prop_id,
            decision="approve",
            decided_by="Dr. Sarah Wong",
        )
        assert approved["status"] == "approved_for_pr"
        assert approved["propose_only"] is True
        assert approved["target_file"].startswith("knowledge/ec_clinic_1/")
        assert approved["unified_diff"]
        assert approved["synthetic_scenario_name"].startswith("synth_ec_clinic_1_")

    assert (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "domain.yaml").read_text(encoding="utf-8") == before_domain
    assert (DEFAULT_KNOWLEDGE_DIR / "ec_clinic_1" / "data_access.yaml").read_text(encoding="utf-8") == before_access


def test_playbook_qa_grounds_locum_answers_in_clinic_knowledge(tmp_path: Path):
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)

    with db.tenant_session("ten_hk_central") as session:
        eff_graph = get_effective_practice_graph(session, "ten_hk_central")

    # Backward-compatible when clinic_knowledge=None
    plain = query_practice_playbook(eff_graph, "What is our warfarin policy?")
    assert "knowledge_clinic_id" not in plain

    # Grounded in ec_clinic_1 ClinicKnowledge: Cantonese brand drug, HK lab panel, shorthand & cover rule
    ec = load_knowledge("ec_clinic_1")
    ans = query_practice_playbook(
        eff_graph,
        "Locum cover check: patient on 保栓通 and Marevan needs R/LFT for CDCC F/U",
        clinic_knowledge=ec,
    )
    assert ans["knowledge_clinic_id"] == "ec_clinic_1"
    assert ans["knowledge_fingerprint"] == ec.fingerprint
    joined = "\n".join(ans["answer_bullets"])
    assert "clopidogrel" in joined
    assert "warfarin" in joined
    assert "INR" in joined
    assert "Creatinine" in joined and "ALT" in joined
    assert "CDCC = Chronic Disease Co-Care" in joined
    assert "ec_multi_site_sessional_cover" in joined


def test_declarative_pathway_runner_validates_clinic_knowledge_id(tmp_path: Path):
    # Existing packs/longevity.yaml binds to default ClinicKnowledge cleanly
    runner = DeclarativePathwayRunner.load_from_directory()
    longevity = runner.resolve_pack("longevity")
    assert longevity is not None
    meta = longevity.to_metadata()
    assert meta["clinic_knowledge_id"] == "default"
    assert meta["clinic_knowledge_fingerprint"] == load_knowledge("default").fingerprint
    init_sum = runner.build_initial_summary("longevity_apob", welcome_msg="Hello")
    assert init_sum["clinic_knowledge_id"] == "default"

    # Custom pack referencing ec_clinic_1 loads cleanly
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()
    src_pack = Path(__file__).resolve().parents[1] / "packs" / "longevity.yaml"
    pack_obj = yaml.safe_load(src_pack.read_text(encoding="utf-8"))
    pack_obj["pack_id"] = "ec_longevity"
    pack_obj["clinic_knowledge_id"] = "ec_clinic_1"
    (packs_dir / "ec_longevity.yaml").write_text(yaml.safe_dump(pack_obj, sort_keys=False), encoding="utf-8")

    ec_runner = DeclarativePathwayRunner.load_from_directory(packs_dir)
    ec_pack = ec_runner.resolve_pack("ec_longevity")
    assert ec_pack is not None
    assert ec_pack.clinic_knowledge_id == "ec_clinic_1"
    assert ec_pack.clinic_knowledge_fingerprint == load_knowledge("ec_clinic_1").fingerprint

    # Pack referencing non-existent clinic_knowledge_id fails closed with PackValidationError
    bad_dir = tmp_path / "bad_packs"
    bad_dir.mkdir()
    pack_obj["clinic_knowledge_id"] = "nonexistent_clinic_xyz"
    (bad_dir / "bad.yaml").write_text(yaml.safe_dump(pack_obj, sort_keys=False), encoding="utf-8")
    with pytest.raises(PackValidationError, match="invalid clinic_knowledge_id"):
        DeclarativePathwayRunner.load_from_directory(bad_dir)
