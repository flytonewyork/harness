"""Offline unit & contract tests for the Antigravity Multi-Agent PM/EM system."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_em", ROOT / "scripts" / "agent_em.py")
assert SPEC is not None and SPEC.loader is not None
agent_em = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_em)


def test_lanes_yaml_schema_and_path_classification():
    cfg = agent_em.load_lanes_config()
    assert cfg["dispatch_board_repo"] == "flytonewyork/harness"
    assert cfg["dispatch_board_issue"] == 70
    assert "flytonewyork/harness" in cfg["repos"]
    assert "flytonewyork/loopbench" in cfg["repos"]

    assert agent_em.classify_path("backend/safety/kernel.py", cfg) == ("lane-a-safety-gateway", False)
    assert agent_em.classify_path("backend/gateway/gateway.py", cfg) == ("lane-a-safety-gateway", False)
    assert agent_em.classify_path("backend/agentic/runner.py", cfg) == ("lane-b-agentic-graph", False)
    assert agent_em.classify_path("backend/mcp/clinic_emr_server.py", cfg) == ("lane-c-mcp-integrations", False)
    assert agent_em.classify_path("frontend/src/v2/workspace/ClinicWorkspaceApp.tsx", cfg) == ("lane-d-workspace-ui", False)
    assert agent_em.classify_path("evals/turn_eval_suite.py", cfg) == ("lane-e-evals-loopbench", False)
    assert agent_em.classify_path("backend/case/context.py", cfg) == ("lane-f-core-engine", False)
    assert agent_em.classify_path("scripts/agent_em.py", cfg) == ("lane-g-platform-em", False)
    assert agent_em.classify_path("backend/ontology/schema.py", cfg) == ("lane-h-clinical-ontology", False)

    # Shared chokepoints are explicitly identified
    assert agent_em.classify_path("backend/acute/app.py", cfg) == ("shared-chokepoints", True)
    assert agent_em.classify_path("backend/acute/db.py", cfg) == ("shared-chokepoints", True)
    assert agent_em.classify_path("NOW.md", cfg) == ("shared-chokepoints", True)


def test_parse_leases_newest_wins_and_active_filter():
    comments = [
        {
            "created_at": "2026-11-28T09:00:00Z",
            "user": {"login": "flytonewyork"},
            "body": '<!-- AGENT_LEASE {"lane": "lane-a-safety-gateway", "branch": "feat/s1", "status": "active", "summary": "start"} -->',
        },
        {
            "created_at": "2026-11-28T10:00:00Z",
            "user": {"login": "flytonewyork"},
            "body": '<!-- AGENT_LEASE {"lane": "lane-c-mcp-integrations", "branch": "feat/mcp", "status": "active", "summary": "mcp work"} -->',
        },
        {
            "created_at": "2026-11-28T11:00:00Z",
            "user": {"login": "flytonewyork"},
            "body": '<!-- AGENT_LEASE {"lane": "lane-a-safety-gateway", "branch": "feat/s1", "status": "merged", "summary": "done"} -->',
        },
    ]
    leases = agent_em.parse_leases(comments)
    assert len(leases) == 2
    active = agent_em.active_leases(leases)
    assert len(active) == 1
    assert active[0]["lane"] == "lane-c-mcp-integrations"
    assert active[0]["branch"] == "feat/mcp"


def test_detect_overlaps_catches_invisible_branches_and_file_lane_collisions():
    cfg = agent_em.load_lanes_config()
    streams = [
        {
            "id": "harness#69",
            "repo": "flytonewyork/harness",
            "pr_number": 69,
            "branch": "feat/stage2",
            "updated_at": "2026-11-28T12:00:00Z",
            "files": ["backend/gateway/gateway.py", "backend/acute/app.py"],
        },
        {
            "id": "harness:branch:feat/intelligent-harness-v1",
            "repo": "flytonewyork/harness",
            "pr_number": None,
            "branch": "feat/intelligent-harness-v1",
            "updated_at": "2026-11-28T11:20:00Z",
            "files": ["backend/gateway/gateway.py", "backend/mcp/clinic_emr_server.py", "backend/acute/app.py"],
        },
    ]
    overlaps = agent_em.detect_overlaps(streams, cfg)
    assert len(overlaps["invisible_branches"]) == 1
    assert overlaps["invisible_branches"][0]["branch"] == "feat/intelligent-harness-v1"
    assert "backend/gateway/gateway.py" in overlaps["file_collisions"]
    assert "backend/acute/app.py" in overlaps["file_collisions"]
    assert "lane-a-safety-gateway" in overlaps["lane_collisions"]
    assert "backend/acute/app.py" in overlaps["chokepoint_touches"]


def test_evaluate_path_conflicts_and_pr_guard_comment():
    cfg = agent_em.load_lanes_config()
    other_streams = [
        {
            "id": "harness:branch:feat/intelligent-harness-v1",
            "branch": "feat/intelligent-harness-v1",
            "pr_number": None,
            "files": ["backend/mcp/clinic_emr_server.py", "backend/acute/app.py"],
        }
    ]
    leases = [
        {
            "lane": "lane-b-agentic-graph",
            "branch": "feat/graph-work",
            "status": "active",
            "summary": "Updating practice graph",
        }
    ]

    # Clean non-overlapping lane -> ok=True
    clear = agent_em.evaluate_path_conflicts(
        ["backend/safety/kernel.py"],
        streams=other_streams,
        leases=leases,
        config=cfg,
        ignore_branch="feat/my-safety-pr",
    )
    assert clear["ok"] is True

    # Overlapping MCP file + leased lane -> ok=False
    conflict = agent_em.evaluate_path_conflicts(
        ["backend/mcp/clinic_emr_server.py", "backend/agentic/runner.py"],
        streams=other_streams,
        leases=leases,
        config=cfg,
        ignore_branch="feat/my-other-pr",
    )
    assert conflict["ok"] is False
    assert len(conflict["file_conflicts"]) == 1
    assert len(conflict["lane_conflicts"]) == 1
    assert len(conflict["lease_conflicts"]) == 1

    comment = agent_em.format_pr_overlap_comment(
        {
            "branch": "feat/my-other-pr",
            "files": ["backend/mcp/clinic_emr_server.py", "backend/acute/app.py"],
        },
        other_streams,
        leases,
        cfg,
    )
    assert agent_em.PR_GUARD_MARKER in comment
    assert "OVERLAP WARNING" in comment
    assert "backend/mcp/clinic_emr_server.py" in comment
    assert "backend/acute/app.py" in comment
    assert "Issue #70" in comment


def test_four_stream_roster_partitions_all_lanes_without_overlap():
    cfg = agent_em.load_lanes_config()
    assert len(cfg["streams"]) == 4
    assert agent_em.validate_streams_config(cfg) == []

    # Catch duplicate lane assignment
    bad_dup = {
        "lanes": {"a": {"paths": ["a/**"]}, "b": {"paths": ["b/**"]}},
        "streams": {
            "s1": {"owned_lanes": ["a"]},
            "s2": {"owned_lanes": ["a", "b"]},
        },
    }
    assert any("multiple streams" in e for e in agent_em.validate_streams_config(bad_dup))

    # Catch unassigned lane
    bad_gap = {
        "lanes": {"a": {"paths": ["a/**"]}, "b": {"paths": ["b/**"]}},
        "streams": {"s1": {"owned_lanes": ["a"]}},
    }
    assert any("Unassigned lanes" in e for e in agent_em.validate_streams_config(bad_gap))


def test_parse_waiting_on_you_and_daily_slack_digest_blocks():
    now_text = (ROOT / "NOW.md").read_text(encoding="utf-8")
    waiting = agent_em.parse_waiting_on_you(now_text)
    assert len(waiting) >= 1
    assert all("Default:" in item for item in waiting)

    cfg = agent_em.load_lanes_config()
    streams = [
        {
            "id": "harness#78",
            "repo": "flytonewyork/harness",
            "pr_number": 78,
            "branch": "feat/stage3-permission-gate",
            "url": "https://github.com/flytonewyork/harness/pull/78",
            "files": ["backend/mcp/clinic_emr_server.py"],
        }
    ]
    leases = [
        {
            "lane": "lane-c-mcp-integrations",
            "branch": "feat/stage3-permission-gate",
            "status": "active",
            "issue": 72,
            "summary": "Stage 3 Permission Gate",
        }
    ]
    overlaps = agent_em.detect_overlaps(streams, cfg)
    merged = [
        {
            "repo": "flytonewyork/harness",
            "number": 79,
            "title": "Explicit 4-Agent Stream Roster",
            "merged_at": "2026-11-28T17:04:00Z",
            "url": "https://github.com/flytonewyork/harness/pull/79",
        }
    ]
    work_orders = [
        {
            "number": 73,
            "title": "[WO] Split backend/acute/app.py",
            "labels": [{"name": "work-order"}, {"name": "P1"}],
            "url": "https://github.com/flytonewyork/harness/issues/73",
        }
    ]
    digest = agent_em.build_daily_digest(
        config=cfg,
        active_streams=streams,
        leases=leases,
        overlaps=overlaps,
        merged_prs=merged,
        work_orders=work_orders,
        waiting_on_you=waiting,
    )
    payload = agent_em.format_slack_blocks(digest)
    assert "text" in payload and "blocks" in payload
    assert payload["blocks"][0]["type"] == "header"
    for blk in payload["blocks"]:
        if blk["type"] == "section":
            assert len(blk["text"]["text"]) <= 3000
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "PR #78" in dumped
    assert "harness#79" in dumped
    assert "Dispatch Board #70" in dumped
    assert "idle / ready for next WO" in dumped

    # Unconfigured Slack env returns clean non-crashing result
    res = agent_em.post_to_slack(payload, webhook_url="", bot_token="", channel_id="")
    assert res["posted"] is False
    assert res["mode"] == "unconfigured"


