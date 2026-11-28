"""PR-blocking CI test gate for the 10-scenario Zero-Trust Turn Eval & 4-Bucket Error-Analysis Suite."""
from __future__ import annotations

from evals.turn_eval_suite import ERROR_BUCKETS, classify_error_bucket, run_turn_eval_suite


def test_error_analysis_classifier_maps_all_four_fix_levers():
    assert classify_error_bucket("context_cjk_ceiling", "CONTEXT_OVERFLOW") == "CONTEXT_OR_SKILL_MISS"
    assert classify_error_bucket("quote_grounding", "LINTER_FAIL") == "PROMPT_OR_SCHEMA_AMBIGUITY"
    assert classify_error_bucket("pre_gate_999_recall", "MISSED_RED_FLAG_999") == "MISSING_DETERMINISTIC_GATE"
    assert classify_error_bucket("pdpo_off_hk_scrub", "PDPO_PHI_LEAK") == "MISSING_DETERMINISTIC_GATE"
    assert classify_error_bucket("mcp_server_authority_gate", "AUTHORITY_DENIED") == "BAD_TOOL_OR_AUTHORITY_BOUNDARY"
    assert classify_error_bucket("negative_search_receipt", "SEARCH_RECEIPT_MISMATCH") == "BAD_TOOL_OR_AUTHORITY_BOUNDARY"


def test_full_zero_trust_turn_eval_suite_passes_10_of_10(tmp_path):
    report = run_turn_eval_suite(tmp_dir=tmp_path)
    assert report.total == 10
    assert report.passed == 10, report.render_markdown()
    assert report.failed == 0
    assert report.hard_fails == 0
    assert report.all_passed is True
    assert set(report.error_analysis_by_bucket.keys()) == set(ERROR_BUCKETS)
    assert all(len(v) == 0 for v in report.error_analysis_by_bucket.values())
    md = report.render_markdown()
    assert "PASS (CI GREEN)" in md
    assert "eval_03_negative_search_receipt_mcp" in md
    assert "eval_10_declarative_longevity_loops" in md
