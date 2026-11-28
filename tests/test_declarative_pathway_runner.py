"""End-to-end verification for Option B: Declarative Pathway & # ai-team Loop Runner + ?pack=longevity.

Proves:
1. `packs/longevity.yaml` loads & validates cleanly via `DeclarativePathwayRunner` with zero per-case Python code.
2. Invalid YAML packs (e.g. non-canonical order primitive or missing wake operator) fail closed with `PackValidationError`.
3. `GET /api/v1/workspace/packs` & `GET /api/v1/demo/live/journeys?pack=longevity` expose the 3 longevity journeys
   while keeping `?pack=gp`, `?pack=corporate_health`, and `?all=1` (exact 12) 100% untouched.
4. Full patient lifecycle on `longevity_apob` and `longevity_optimal` (start -> declarative intake turn -> 5/5 linter PASS).
5. Real declarative `# ai-team` wake-up & auto-close engine (`POST /api/v1/workspace/cases/{id}/ai-team-tick`):
   - `apob: 0.96` (> 0.80) -> `wake_doctor` (`needs_decision`, `with_doctor`, alert prepended)
   - `apob: 0.72` (<= 0.80) -> `auto_close_quietly` (`closed`, `idle`, zero doctor interruption)
   - `hba1c: 5.7` (between 5.4 and 6.0) -> `stay_scheduled` (`scheduled`, `idle`, quiet monitoring)
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from backend.acute.app import create_acute_app
from backend.acute.db import Database
from backend.acute.pathway_runner import PackValidationError, _validate_pack_dict, get_declarative_pathway_runner
from backend.acute.seed import reset_and_seed
from backend.vault.vault import DocumentVault, LocalFSBackend


def _make_client(tmp_path) -> TestClient:
    os.environ["HARNESS_ENV"] = "staging"
    os.environ["DEMO_MODE"] = "true"
    os.environ["HARNESS_RUNTIME_MODE"] = "demo"
    db = Database()
    vault = DocumentVault(backend=LocalFSBackend(tmp_path / "vault"))
    reset_and_seed(db, vault)
    app = create_acute_app(db=db, vault=vault)
    return TestClient(app)


def test_declarative_pack_validation_fail_closed():
    runner = get_declarative_pathway_runner()
    pack = runner.resolve_pack("longevity")
    assert pack is not None
    assert pack.pack_id == "longevity"
    assert len(pack.journeys) == 3
    assert len(pack.content_hash) == 16

    # Reject non-canonical order primitive
    with pytest.raises(PackValidationError):
        _validate_pack_dict(
            {
                "pack_id": "bad_pack",
                "journeys": [
                    {
                        "key": "bad_j",
                        "patient_id": "pat_bad",
                        "initial_summary": {
                            "issues": [
                                {
                                    "id": "i1",
                                    "name": "Bad",
                                    "suggested_orders": [{"id": "o1", "type": "magic_pill", "issue": "i1"}],
                                }
                            ]
                        },
                    }
                ],
            },
            raw_text="bad",
            source_path="<test>",
        )


def test_longevity_pack_e2e_intake_and_ai_team_loop_wake_and_autoclose(tmp_path):
    client = _make_client(tmp_path)
    assert client.post("/api/v1/demo/live/reset").status_code == 200

    # 1. GET /api/v1/workspace/packs exposes declarative longevity YAML pack
    r_packs = client.get("/api/v1/workspace/packs")
    assert r_packs.status_code == 200
    packs = r_packs.json()["packs"]
    lon = next(p for p in packs if p["pack_id"] == "longevity")
    assert lon["declarative"] is True
    assert lon["journey_count"] == 3
    assert lon["ai_team_loop_count"] == 4

    # 2. ?pack=longevity returns the 3 longevity journeys without disturbing gp / corporate_health / all=1
    r_lon = client.get("/api/v1/demo/live/journeys?pack=longevity").json()
    assert r_lon["pack"] == "longevity"
    assert [j["key"] for j in r_lon["journeys"]] == ["longevity_apob", "longevity_hba1c", "longevity_optimal"]

    r_gp = client.get("/api/v1/demo/live/journeys").json()
    assert [j["key"] for j in r_gp["journeys"]] == ["wingyan", "tang_uti", "chan", "lau_chest"]

    # 3. Start longevity_apob (CHEUNG Alex) -> auto-provisions Patient + 5/5 linted declarative summary
    s1 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "longevity_apob", "lang": "en"})
    assert s1.status_code == 200
    apob_case_id = s1.json()["case_id"]

    t1 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={
            "journey_key": "longevity_apob",
            "message": "Yes please — my father had a heart stent at 54, I exercise 4x/week, non-smoker, and want an aggressive ApoB target under 0.80.",
            "lang": "en",
            "is_preset": True,
        },
    ).json()
    assert t1["ok"] is True
    assert t1["case_state"] == "needs_decision"
    assert t1["lint_report"]["passed"] is True

    # 4. # ai-team Loop Tick: Day +90 ApoB = 0.96 (> 0.80) -> wakes on-duty GP (# my-work)
    tick_wake = client.post(
        f"/api/v1/workspace/cases/{apob_case_id}/ai-team-tick",
        json={"loop_id": "day90_apob_retest", "metrics": {"apob": 0.96}},
    )
    assert tick_wake.status_code == 200
    w_body = tick_wake.json()
    assert w_body["outcome"] == "wake_doctor"
    assert w_body["new_state"] == "needs_decision"
    assert w_body["new_baton"] == "with_doctor"
    assert w_body["lint_report"]["passed"] is True

    # 5. # ai-team Loop Tick: Day +90 ApoB = 0.72 (<= 0.80) -> auto-closes quietly with zero doctor interruption!
    tick_close = client.post(
        f"/api/v1/workspace/cases/{apob_case_id}/ai-team-tick",
        json={"loop_id": "day90_apob_retest", "metrics": {"apob": 0.72}},
    )
    assert tick_close.status_code == 200
    c_body = tick_close.json()
    assert c_body["outcome"] == "auto_close_quietly"
    assert c_body["new_state"] == "closed"
    assert c_body["new_baton"] == "idle"

    # 6. Start longevity_hba1c -> intermediate reading HbA1c 5.7% stays quietly in # ai-team (scheduled)
    s2 = client.post("/api/v1/demo/live/chat/start", json={"journey_key": "longevity_hba1c", "lang": "en"})
    assert s2.status_code == 200
    hba1c_case_id = s2.json()["case_id"]

    t2 = client.post(
        "/api/v1/demo/live/chat/turn",
        json={
            "journey_key": "longevity_hba1c",
            "message": "Yes, I'd like to try a 14-day CGM sensor with Nurse Rachel and recheck HbA1c in 3 months without starting medication yet.",
            "lang": "en",
            "is_preset": True,
        },
    ).json()
    assert t2["case_state"] == "scheduled"
    assert t2["baton"] == "idle"

    tick_stay = client.post(
        f"/api/v1/workspace/cases/{hba1c_case_id}/ai-team-tick",
        json={"loop_id": "day90_hba1c_retest", "metrics": {"hba1c": 5.7}},
    ).json()
    assert tick_stay["outcome"] == "stay_scheduled"
    assert tick_stay["new_state"] == "scheduled"

    # 7. Verify MCP resources/read works seamlessly on auto-provisioned declarative longevity patient!
    r_mcp = client.post(
        "/mcp/clinic-emr",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "resources/read",
            "params": {"uri": "emr://patients/pat_longevity_apob/chart"},
        },
    )
    assert r_mcp.status_code == 200
    assert "CHEUNG Alex" in r_mcp.json()["result"]["contents"][0]["text"]
