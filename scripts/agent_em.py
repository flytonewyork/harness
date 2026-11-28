#!/usr/bin/env python3
"""Antigravity Multi-Agent PM & Engineering Management CLI.

Provides real-time cross-VM coordination across `flytonewyork/harness` and
`flytonewyork/loopbench` using GitHub GraphQL/REST (`gh`), `docs/em/LANES.yaml`,
and the pinned Active Agent Dispatch Board issue.

Subcommands:
  status       Show active branches (incl. invisible un-PR'd ones), open PRs,
               live lane leases, open Work Orders, and file/lane collisions.
  board        Show open Work Order issues grouped by priority and active leases.
  check-paths  Check if planned file edits collide with any active PR/branch/lease
               (exit 0 = clear, exit 2 = collision detected).
  claim        Claim an exclusive lane lease on the live Dispatch Board issue.
  handoff      Release or update a lane lease (ready / merged / blocked / released).
  pr-check     Run overlap & chokepoint guard for a PR (used by GitHub Actions).
  roster       Show the explicit 4-Agent Stream Roster and live lane ownership.
  slack-digest Build and optionally post the daily 09:00 HKT Slack engineering standup.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LANES_PATH = ROOT / "docs" / "em" / "LANES.yaml"
LEASE_MARKER_RE = re.compile(r"<!--\s*AGENT_LEASE\s*(\{.*?\})\s*-->", re.S)
PR_GUARD_MARKER = "<!-- AGENT_EM_OVERLAP_GUARD -->"
ACTIVE_LEASE_STATUSES = frozenset({"active", "blocked"})
VALID_HANDOFF_STATUSES = ("ready", "merged", "blocked", "released")


# ---------------------------------------------------------------------------
# Pure Config, Path Matching, Lease & Overlap Engine (100% offline-testable)
# ---------------------------------------------------------------------------
def load_lanes_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or DEFAULT_LANES_PATH
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "lanes" not in data:
        raise ValueError(f"Invalid LANES.yaml at {cfg_path}")
    return data


def match_glob(rel_path: str, pattern: str) -> bool:
    """Deterministic POSIX glob matcher supporting `dir/**`, `*.ext`, and exact paths."""
    norm_path = rel_path.strip().lstrip("./")
    norm_pat = pattern.strip().lstrip("./")
    if not norm_path or not norm_pat:
        return False
    if norm_pat.endswith("/**"):
        prefix = norm_pat[:-3].rstrip("/")
        return norm_path == prefix or norm_path.startswith(prefix + "/")
    if "**" in norm_pat:
        regex = "^" + re.escape(norm_pat).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$"
        return bool(re.match(regex, norm_path))
    return fnmatch.fnmatch(norm_path, norm_pat)


def classify_path(rel_path: str, config: dict[str, Any]) -> tuple[str | None, bool]:
    """Return `(lane_name, is_shared_chokepoint)` for a repository-relative path."""
    norm = rel_path.strip().lstrip("./")
    for cp in config.get("shared_chokepoints") or []:
        if match_glob(norm, cp):
            return "shared-chokepoints", True
    for lane_name, lane_cfg in (config.get("lanes") or {}).items():
        for pat in (lane_cfg.get("paths") or []):
            if match_glob(norm, pat):
                return lane_name, False
    return None, False


def classify_paths(paths: list[str], config: dict[str, Any]) -> dict[str, Any]:
    lanes: dict[str, list[str]] = defaultdict(list)
    chokepoints: list[str] = []
    unmapped: list[str] = []
    for p in paths:
        norm = p.strip().lstrip("./")
        if not norm:
            continue
        lane, is_cp = classify_path(norm, config)
        if is_cp:
            chokepoints.append(norm)
        elif lane:
            lanes[lane].append(norm)
        else:
            unmapped.append(norm)
    return {
        "lanes": {k: sorted(set(v)) for k, v in sorted(lanes.items())},
        "chokepoints": sorted(set(chokepoints)),
        "unmapped": sorted(set(unmapped)),
    }


def parse_leases(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse append-only `<!-- AGENT_LEASE {...} -->` comments; newest per (lane, branch) wins."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for c in comments:
        body = str(c.get("body") or "")
        for m in LEASE_MARKER_RE.finditer(body):
            try:
                rec = json.loads(m.group(1))
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            lane = str(rec.get("lane") or "").strip()
            branch = str(rec.get("branch") or "").strip()
            if not lane or not branch:
                continue
            rec.setdefault("updated_at", c.get("created_at") or c.get("createdAt") or "")
            rec.setdefault("comment_author", (c.get("user") or {}).get("login") or (c.get("author") or {}).get("login") or "")
            latest[(lane, branch)] = rec
    return sorted(latest.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)


def active_leases(leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in leases if str(r.get("status") or "").lower() in ACTIVE_LEASE_STATUSES]


def detect_overlaps(streams: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Compute file collisions, lane collisions, invisible un-PR'd branches, and chokepoint touches."""
    file_map: dict[str, list[str]] = defaultdict(list)
    lane_map: dict[str, list[str]] = defaultdict(list)
    cp_map: dict[str, list[str]] = defaultdict(list)
    invisible: list[dict[str, Any]] = []

    for s in streams:
        sid = str(s["id"])
        files = [str(f) for f in (s.get("files") or []) if f]
        cls = classify_paths(files, config)
        s["lanes"] = sorted(cls["lanes"].keys())
        s["chokepoints"] = cls["chokepoints"]
        for f in files:
            if sid not in file_map[f]:
                file_map[f].append(sid)
        for lane in cls["lanes"]:
            if sid not in lane_map[lane]:
                lane_map[lane].append(sid)
        for cp in cls["chokepoints"]:
            if sid not in cp_map[cp]:
                cp_map[cp].append(sid)
        if not s.get("pr_number") and s.get("branch") not in ("main", "master"):
            invisible.append({
                "id": sid,
                "repo": s.get("repo"),
                "branch": s.get("branch"),
                "updated_at": s.get("updated_at"),
                "file_count": len(files),
                "lanes": s["lanes"],
            })

    return {
        "file_collisions": {f: sorted(ids) for f, ids in sorted(file_map.items()) if len(ids) > 1},
        "lane_collisions": {ln: sorted(ids) for ln, ids in sorted(lane_map.items()) if len(ids) > 1},
        "chokepoint_touches": {cp: sorted(ids) for cp, ids in sorted(cp_map.items())},
        "invisible_branches": invisible,
    }


def evaluate_path_conflicts(
    requested_paths: list[str],
    *,
    streams: list[dict[str, Any]],
    leases: list[dict[str, Any]],
    config: dict[str, Any],
    ignore_branch: str | None = None,
) -> dict[str, Any]:
    """Check whether editing `requested_paths` conflicts with other active streams or lane leases."""
    req_cls = classify_paths(requested_paths, config)
    req_set = {p.strip().lstrip("./") for p in requested_paths if p.strip()}
    req_lanes = set(req_cls["lanes"].keys())

    file_hits: list[dict[str, Any]] = []
    lane_hits: list[dict[str, Any]] = []
    lease_hits: list[dict[str, Any]] = []

    for s in streams:
        if ignore_branch and s.get("branch") == ignore_branch:
            continue
        s_files = {str(f).strip().lstrip("./") for f in (s.get("files") or [])}
        overlap_files = sorted(req_set & s_files)
        if overlap_files:
            file_hits.append({"stream": s["id"], "branch": s.get("branch"), "pr": s.get("pr_number"), "files": overlap_files})
        s_cls = classify_paths(list(s_files), config)
        overlap_lanes = sorted(req_lanes & set(s_cls["lanes"].keys()))
        if overlap_lanes:
            lane_hits.append({"stream": s["id"], "branch": s.get("branch"), "pr": s.get("pr_number"), "lanes": overlap_lanes})

    for lease in active_leases(leases):
        if ignore_branch and lease.get("branch") == ignore_branch:
            continue
        if lease.get("lane") in req_lanes:
            lease_hits.append(lease)

    has_conflict = bool(file_hits or lane_hits or lease_hits)
    return {
        "ok": not has_conflict,
        "requested": req_cls,
        "file_conflicts": file_hits,
        "lane_conflicts": lane_hits,
        "lease_conflicts": lease_hits,
    }


def format_pr_overlap_comment(
    pr_info: dict[str, Any],
    other_streams: list[dict[str, Any]],
    leases: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """Build a crisp sticky GitHub PR comment summarizing lanes, chokepoints, and collisions."""
    pr_files = [str(f) for f in (pr_info.get("files") or [])]
    pr_branch = str(pr_info.get("branch") or "")
    res = evaluate_path_conflicts(
        pr_files,
        streams=other_streams,
        leases=leases,
        config=config,
        ignore_branch=pr_branch,
    )
    req = res["requested"]
    lanes_str = ", ".join(f"`{ln}`" for ln in req["lanes"]) or "_none (tests/docs only)_"
    cp_list = req["chokepoints"]
    board_issue = config.get("dispatch_board_issue")

    status_badge = "CLEAR — No active overlap detected" if res["ok"] else "OVERLAP WARNING — Coordinate before merging"
    lines = [
        PR_GUARD_MARKER,
        f"### Agent EM Overlap Guard — {status_badge}",
        "",
        f"- **Lanes touched:** {lanes_str}",
        f"- **Shared chokepoints:** {', '.join(f'`{c}`' for c in cp_list) if cp_list else '_none_'}",
    ]
    if board_issue:
        lines.append(f"- **Dispatch Board:** Issue #{board_issue} (`docs/em/LANES.yaml`)")

    if res["file_conflicts"]:
        lines.append("")
        lines.append("#### File Collisions with Other Active PRs / Branches")
        for hit in res["file_conflicts"]:
            files_preview = ", ".join(f"`{f}`" for f in hit["files"][:8])
            if len(hit["files"]) > 8:
                files_preview += f" (+{len(hit['files']) - 8} more)"
            lines.append(f"- **{hit['stream']}** (`{hit['branch']}`): {files_preview}")

    if res["lane_conflicts"]:
        lines.append("")
        lines.append("#### Lane Overlaps")
        for hit in res["lane_conflicts"]:
            lines.append(f"- **{hit['stream']}** (`{hit['branch']}`): {', '.join(f'`{ln}`' for ln in hit['lanes'])}")

    if res["lease_conflicts"]:
        lines.append("")
        lines.append("#### Active Dispatch Board Lease Conflicts")
        for lease in res["lease_conflicts"]:
            lines.append(
                f"- Lane `{lease['lane']}` currently leased by `{lease.get('branch')}` "
                f"(status: `{lease.get('status')}`, summary: {lease.get('summary', '')})"
            )

    if cp_list:
        lines.append("")
        lines.append(
            "> **Chokepoint note:** Keep edits to shared chokepoints minimal/additive and rebase onto `main` before merging."
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# GitHub CLI Transport Layer
# ---------------------------------------------------------------------------
def _run_gh(args: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _current_git_branch() -> str:
    proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def fetch_recent_branches(repo: str, days: int = 7) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        refs(refPrefix: "refs/heads/", first: 100) {
          nodes {
            name
            target {
              ... on Commit {
                oid
                committedDate
                messageHeadline
              }
            }
          }
        }
      }
    }
    """
    raw = _run_gh(["api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}"])
    nodes = json.loads(raw)["data"]["repository"]["refs"]["nodes"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for n in nodes:
        t = n.get("target") or {}
        dt_str = t.get("committedDate") or ""
        if not dt_str:
            continue
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt >= cutoff:
            out.append({
                "repo": repo,
                "branch": n["name"],
                "sha": (t.get("oid") or "")[:7],
                "updated_at": dt_str,
                "headline": t.get("messageHeadline") or "",
            })
    return sorted(out, key=lambda r: r["updated_at"], reverse=True)


def fetch_open_prs(repo: str) -> list[dict[str, Any]]:
    raw = _run_gh([
        "pr", "list", "--repo", repo, "--state", "open", "--limit", "50",
        "--json", "number,title,headRefName,baseRefName,isDraft,updatedAt,files,url",
    ])
    prs = json.loads(raw)
    out: list[dict[str, Any]] = []
    for pr in prs:
        out.append({
            "id": f"{repo.split('/')[-1]}#{pr['number']}",
            "repo": repo,
            "pr_number": pr["number"],
            "title": pr["title"],
            "branch": pr["headRefName"],
            "base": pr["baseRefName"],
            "is_draft": bool(pr.get("isDraft")),
            "updated_at": pr["updatedAt"],
            "url": pr.get("url") or "",
            "files": [f["path"] for f in (pr.get("files") or [])],
        })
    return out


def fetch_branch_diff_files(repo: str, branch: str) -> tuple[list[str], int, int]:
    """Compare `main...<branch>` via GitHub API to discover files modified on un-PR'd branches."""
    raw = _run_gh(["api", f"repos/{repo}/compare/main...{branch}"], check=False)
    if not raw.strip():
        return [], 0, 0
    try:
        data = json.loads(raw)
    except ValueError:
        return [], 0, 0
    if "files" not in data:
        return [], 0, 0
    files = [str(f["filename"]) for f in (data.get("files") or []) if f.get("filename")]
    return files, int(data.get("ahead_by") or 0), int(data.get("behind_by") or 0)


def fetch_dispatch_leases(config: dict[str, Any]) -> list[dict[str, Any]]:
    repo = config["dispatch_board_repo"]
    issue_num = config.get("dispatch_board_issue")
    if not issue_num:
        return []
    raw = _run_gh(["api", f"repos/{repo}/issues/{issue_num}/comments", "--paginate"], check=False)
    if not raw.strip():
        return []
    try:
        comments = json.loads(raw)
    except ValueError:
        return []
    return parse_leases(comments if isinstance(comments, list) else [])


def fetch_work_orders(repo: str) -> list[dict[str, Any]]:
    raw = _run_gh([
        "issue", "list", "--repo", repo, "--state", "open", "--label", "work-order", "--limit", "50",
        "--json", "number,title,labels,updatedAt,url",
    ], check=False)
    if not raw.strip():
        return []
    try:
        return json.loads(raw)
    except ValueError:
        return []


def collect_active_streams(
    config: dict[str, Any],
    days: int = 7,
    *,
    repos: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge open PRs + recently pushed un-PR'd branches across configured repos."""
    streams: list[dict[str, Any]] = []
    target_repos = repos if repos is not None else list(config.get("repos") or [])
    for repo in target_repos:
        short_repo = repo.split("/")[-1]
        try:
            prs = fetch_open_prs(repo)
            recent_branches = fetch_recent_branches(repo, days=days)
        except RuntimeError as exc:
            print(f"[agent_em] skipping inaccessible repo {repo}: {exc}", file=sys.stderr)
            continue
        pr_branches = {p["branch"] for p in prs}
        streams.extend(prs)

        for br in recent_branches:
            bname = br["branch"]
            if bname in ("main", "master") or bname in pr_branches:
                continue
            files, ahead, behind = fetch_branch_diff_files(repo, bname)
            if ahead == 0 and not files:
                continue
            streams.append({
                "id": f"{short_repo}:branch:{bname}",
                "repo": repo,
                "pr_number": None,
                "title": br["headline"],
                "branch": bname,
                "base": "main",
                "is_draft": False,
                "updated_at": br["updated_at"],
                "ahead_by": ahead,
                "behind_by": behind,
                "files": files,
            })
    return streams


# ---------------------------------------------------------------------------
# Subcommand Handlers
# ---------------------------------------------------------------------------
def cmd_status(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    streams = collect_active_streams(config, days=args.days)
    overlaps = detect_overlaps(streams, config)
    leases = fetch_dispatch_leases(config)
    work_orders = fetch_work_orders(config["dispatch_board_repo"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dispatch_board": f"{config['dispatch_board_repo']}#{config.get('dispatch_board_issue')}",
        "active_leases": active_leases(leases),
        "streams": streams,
        "overlaps": overlaps,
        "work_orders": work_orders,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"=== Antigravity Team Status (lookback={args.days}d | Dispatch Board: {payload['dispatch_board']}) ===\n")

    act_leases = active_leases(leases)
    print(f"## Active Lane Leases ({len(act_leases)})")
    if not act_leases:
        print("  (none claimed on Dispatch Board yet)")
    else:
        for l in act_leases:
            pr_txt = f" PR#{l['pr']}" if l.get("pr") else ""
            wo_txt = f" WO#{l['issue']}" if l.get("issue") else ""
            print(f"  - [{l.get('status')}] {l['lane']} -> {l['branch']}{pr_txt}{wo_txt} | {l.get('summary', '')}")

    print(f"\n## Active Streams Across Repos ({len(streams)})")
    if not streams:
        print("  (no open PRs or un-PR'd active branches)")
    for s in sorted(streams, key=lambda x: str(x.get("updated_at") or ""), reverse=True):
        kind = f"PR #{s['pr_number']}{' (DRAFT)' if s.get('is_draft') else ''}" if s.get("pr_number") else "UN-PR'D BRANCH (!)"
        lanes = ", ".join(s.get("lanes") or []) or "unmapped"
        print(f"  - {s['id']} [{kind}] {str(s.get('updated_at') or '')[:16]} | lanes: {lanes} | files: {len(s.get('files') or [])}")
        print(f"      branch={s['branch']} | {str(s.get('title') or '')[:90]}")

    inv = overlaps["invisible_branches"]
    if inv:
        print(f"\n## ALERT: Invisible Un-PR'd Active Branches ({len(inv)})")
        for b in inv:
            print(f"  ! {b['repo']} `{b['branch']}` (updated {str(b['updated_at'])[:16]}, {b['file_count']} files, lanes={b['lanes']}) — open a Draft PR!")

    fcol = overlaps["file_collisions"]
    lcol = overlaps["lane_collisions"]
    if fcol or lcol:
        print(f"\n## ALERT: Active Collisions ({len(fcol)} files, {len(lcol)} lanes)")
        for ln, ids in lcol.items():
            print(f"  ! LANE `{ln}` touched by: {', '.join(ids)}")
        for fp, ids in list(fcol.items())[:15]:
            print(f"  ! FILE `{fp}` touched by: {', '.join(ids)}")
        if len(fcol) > 15:
            print(f"  ... and {len(fcol) - 15} more colliding files")
    else:
        print("\n## Collisions: NONE")

    print(f"\n## Open Work Orders ({len(work_orders)})")
    for wo in work_orders:
        labels = [lb["name"] for lb in (wo.get("labels") or []) if lb["name"] in ("P0", "P1", "P2", "blocked")]
        tag = "/".join(labels) or "P2"
        print(f"  - #{wo['number']} [{tag}] {wo['title']}")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    work_orders = fetch_work_orders(config["dispatch_board_repo"])
    leases = active_leases(fetch_dispatch_leases(config))
    print(f"=== Product Work Orders ({config['dispatch_board_repo']}) ===")
    order_rank = {"P0": 0, "P1": 1, "P2": 2}
    def _rank(wo: dict[str, Any]) -> tuple[int, int]:
        labs = [lb["name"] for lb in (wo.get("labels") or [])]
        p = min((order_rank[l] for l in labs if l in order_rank), default=3)
        return (p, int(wo["number"]))
    for wo in sorted(work_orders, key=_rank):
        labs = [lb["name"] for lb in (wo.get("labels") or []) if lb["name"] != "work-order"]
        print(f"  #{wo['number']:<4} [{', '.join(labs) or 'P2'}] {wo['title']}")
    print(f"\n=== Active Leases (Issue #{config.get('dispatch_board_issue')}) ===")
    if not leases:
        print("  (none)")
    for l in leases:
        print(f"  - {l['lane']}: `{l['branch']}` ({l.get('status')}) — {l.get('summary', '')}")
    return 0


def cmd_check_paths(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    streams = collect_active_streams(config, days=args.days)
    leases = fetch_dispatch_leases(config)
    cur_branch = args.branch or _current_git_branch()
    res = evaluate_path_conflicts(
        args.paths,
        streams=streams,
        leases=leases,
        config=config,
        ignore_branch=cur_branch,
    )
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"Checked {len(args.paths)} path(s) (ignoring current branch `{cur_branch}`):")
        print(f"  Lanes: {', '.join(res['requested']['lanes'].keys()) or 'none'}")
        if res["requested"]["chokepoints"]:
            print(f"  Chokepoints: {', '.join(res['requested']['chokepoints'])}")
        if res["ok"]:
            print("  RESULT: CLEAR — safe to proceed.")
        else:
            print("  RESULT: CONFLICT DETECTED!")
            for h in res["file_conflicts"]:
                print(f"    ! File overlap with {h['stream']} ({h['branch']}): {', '.join(h['files'])}")
            for h in res["lane_conflicts"]:
                print(f"    ! Lane overlap with {h['stream']} ({h['branch']}): {', '.join(h['lanes'])}")
            for l in res["lease_conflicts"]:
                print(f"    ! Active Dispatch Board lease on `{l['lane']}` by `{l['branch']}` ({l.get('summary', '')})")
    return 0 if res["ok"] else 2


def _post_lease_comment(config: dict[str, Any], lease_record: dict[str, Any], header: str) -> None:
    repo = config["dispatch_board_repo"]
    issue_num = config["dispatch_board_issue"]
    marker = f"<!-- AGENT_LEASE {json.dumps(lease_record, sort_keys=True)} -->"
    lines = [
        marker,
        f"### {header}",
        f"- **Lane:** `{lease_record['lane']}`",
        f"- **Branch:** `{lease_record['branch']}`",
        f"- **Status:** `{lease_record['status']}`",
    ]
    if lease_record.get("agent"):
        lines.append(f"- **Agent:** {lease_record['agent']}")
    if lease_record.get("issue"):
        lines.append(f"- **Work Order:** #{lease_record['issue']}")
    if lease_record.get("pr"):
        lines.append(f"- **PR:** #{lease_record['pr']}")
    if lease_record.get("paths"):
        lines.append(f"- **Paths:** {', '.join(f'`{p}`' for p in lease_record['paths'])}")
    if lease_record.get("summary"):
        lines.append(f"- **Summary:** {lease_record['summary']}")
    if lease_record.get("next"):
        lines.append(f"- **Next:** {lease_record['next']}")
    _run_gh(["issue", "comment", str(issue_num), "--repo", repo, "--body", "\n".join(lines)])


def cmd_claim(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    valid_lanes = set((config.get("lanes") or {}).keys()) | {"shared-chokepoints"}
    if args.lane not in valid_lanes:
        print(f"ERROR: Unknown lane `{args.lane}`. Valid lanes: {sorted(valid_lanes)}", file=sys.stderr)
        return 1
    branch = args.branch or _current_git_branch()
    leases = active_leases(fetch_dispatch_leases(config))
    conflicts = [l for l in leases if l["lane"] == args.lane and l["branch"] != branch]
    if conflicts and not args.force:
        print(
            f"CONFLICT: Lane `{args.lane}` is already actively leased by `{conflicts[0]['branch']}` "
            f"({conflicts[0].get('summary', '')}). Coordinate or pass --force.",
            file=sys.stderr,
        )
        return 2

    rec = {
        "lane": args.lane,
        "branch": branch,
        "status": "active",
        "agent": args.agent or os.environ.get("ANTIGRAVITY_AGENT", "antigravity"),
        "issue": args.issue,
        "pr": args.pr,
        "paths": [p.strip() for p in (args.paths or "").split(",") if p.strip()],
        "summary": args.summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _post_lease_comment(config, rec, f"LEASE CLAIMED: `{args.lane}`")
    print(f"Claimed lane `{args.lane}` for branch `{branch}` on {config['dispatch_board_repo']}#{config['dispatch_board_issue']}.")
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    branch = args.branch or _current_git_branch()
    rec = {
        "lane": args.lane,
        "branch": branch,
        "status": args.status,
        "agent": args.agent or os.environ.get("ANTIGRAVITY_AGENT", "antigravity"),
        "issue": args.issue,
        "pr": args.pr,
        "summary": args.summary,
        "next": args.next or "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _post_lease_comment(config, rec, f"HANDOFF ({args.status.upper()}): `{args.lane}`")
    print(f"Recorded `{args.status}` handoff for `{args.lane}` (`{branch}`) on Issue #{config['dispatch_board_issue']}.")
    return 0


def cmd_pr_check(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    repo = args.repo or config["dispatch_board_repo"]
    raw_pr = _run_gh([
        "pr", "view", str(args.pr), "--repo", repo,
        "--json", "number,title,headRefName,baseRefName,isDraft,updatedAt,files,url",
    ])
    pr_obj = json.loads(raw_pr)
    pr_info = {
        "id": f"{repo.split('/')[-1]}#{pr_obj['number']}",
        "repo": repo,
        "pr_number": pr_obj["number"],
        "branch": pr_obj["headRefName"],
        "files": [f["path"] for f in (pr_obj.get("files") or [])],
    }
    all_streams = collect_active_streams(config, days=args.days, repos=[repo])
    other_streams = [s for s in all_streams if not (s.get("repo") == repo and s.get("pr_number") == pr_info["pr_number"])]
    leases = fetch_dispatch_leases(config)
    comment = format_pr_overlap_comment(pr_info, other_streams, leases, config)
    print(comment)

    if args.post_comment:
        existing_raw = _run_gh(["api", f"repos/{repo}/issues/{args.pr}/comments", "--paginate"], check=False)
        existing_id: int | None = None
        if existing_raw.strip():
            for c in json.loads(existing_raw):
                if PR_GUARD_MARKER in str(c.get("body") or ""):
                    existing_id = int(c["id"])
                    break
        if existing_id is not None:
            _run_gh(["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{existing_id}", "-f", f"body={comment}"])
        else:
            _run_gh(["pr", "comment", str(args.pr), "--repo", repo, "--body", comment])

    if getattr(args, "slack_on_conflict", False) and "OVERLAP WARNING" in comment:
        pr_url = pr_obj.get("url") or f"https://github.com/{repo}/pull/{args.pr}"
        alert_payload = {
            "text": f"Agent EM Overlap Alert on {repo}#{args.pr} ({pr_info['branch']})",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _clamp_slack_mrkdwn(
                            f":warning: *PR Overlap Alert* — <{pr_url}|{repo}#{args.pr}> (`{pr_info['branch']}`)\n"
                            f"{pr_obj.get('title', '')}\n"
                            f"Check the PR Overlap Guard comment & <https://github.com/{config['dispatch_board_repo']}/issues/{config.get('dispatch_board_issue')}|Dispatch Board #{config.get('dispatch_board_issue')}>."
                        ),
                    },
                }
            ],
        }
        res = post_to_slack(alert_payload)
        print(f"[agent_em] slack_on_conflict: {res}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Daily Slack Standup Digest (pure formatting + stdlib urllib posting)
# ---------------------------------------------------------------------------
SLACK_SECTION_LIMIT = 2900  # Safety margin under Slack's 3,000-char mrkdwn block limit


def _clamp_slack_mrkdwn(text: str, limit: int = SLACK_SECTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 16].rstrip() + "\n…_(truncated)_"


def parse_waiting_on_you(now_md_text: str) -> list[str]:
    """Extract `- ...` action items from the `## Waiting on you` section of NOW.md."""
    m = re.search(r"^## Waiting on you[^\n]*\n(.*?)(?=^## |\Z)", now_md_text, re.M | re.S)
    if not m:
        return []
    items = re.findall(r"^- .*?(?=^- |^\S|\Z)", m.group(1), re.M | re.S)
    return [" ".join(it[2:].split()) for it in items if it.strip()]


def build_daily_digest(
    *,
    config: dict[str, Any],
    active_streams: list[dict[str, Any]],
    leases: list[dict[str, Any]],
    overlaps: dict[str, Any],
    merged_prs: list[dict[str, Any]],
    work_orders: list[dict[str, Any]],
    waiting_on_you: list[str],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a pure, zero-PHI structured daily engineering standup digest."""
    now_utc = generated_at or datetime.now(timezone.utc)
    hkt = timezone(timedelta(hours=8))
    now_hkt = now_utc.astimezone(hkt)

    act_leases = active_leases(leases)
    open_pr_branches = {str(s.get("branch") or "") for s in active_streams if s.get("pr_number")}
    visible_leases = [
        l for l in leases
        if str(l.get("status") or "").lower() in ACTIVE_LEASE_STATUSES
        or (str(l.get("status") or "").lower() == "ready" and str(l.get("branch") or "") in open_pr_branches)
    ]
    lease_by_lane = {l["lane"]: l for l in visible_leases}
    pr_by_branch = {
        str(s.get("branch") or ""): s
        for s in active_streams
        if s.get("pr_number")
    }

    stream_pulse: list[dict[str, Any]] = []
    for sname, scfg in (config.get("streams") or {}).items():
        owned = list((scfg or {}).get("owned_lanes") or [])
        entries = []
        for ln in owned:
            lease = lease_by_lane.get(ln)
            if lease:
                br = str(lease.get("branch") or "")
                pr_obj = pr_by_branch.get(br)
                entries.append({
                    "lane": ln,
                    "branch": br,
                    "status": str(lease.get("status") or "active"),
                    "pr_number": lease.get("pr") or (pr_obj.get("pr_number") if pr_obj else None),
                    "pr_url": pr_obj.get("url") if pr_obj else "",
                    "issue": lease.get("issue"),
                    "summary": str(lease.get("summary") or "")[:120],
                })
        stream_pulse.append({
            "stream": sname,
            "role": str((scfg or {}).get("role") or ""),
            "owned_lanes": owned,
            "active": entries,
            "idle": len(entries) == 0,
        })

    return {
        "date_hkt": now_hkt.strftime("%a %d %b %Y %H:%M HKT"),
        "dispatch_board_repo": config["dispatch_board_repo"],
        "dispatch_board_issue": config.get("dispatch_board_issue"),
        "stream_pulse": stream_pulse,
        "merged_prs": merged_prs,
        "invisible_branches": overlaps.get("invisible_branches") or [],
        "file_collisions": overlaps.get("file_collisions") or {},
        "lane_collisions": overlaps.get("lane_collisions") or {},
        "waiting_on_you": waiting_on_you,
        "work_orders": work_orders,
    }


def format_slack_blocks(digest: dict[str, Any]) -> dict[str, Any]:
    """Render a zero-PHI daily digest into valid Slack Block Kit JSON (<3,000 chars/section)."""
    repo = digest["dispatch_board_repo"]
    board_num = digest.get("dispatch_board_issue")
    board_url = f"https://github.com/{repo}/issues/{board_num}" if board_num else f"https://github.com/{repo}/issues"
    date_label = digest["date_hkt"]

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Antigravity Daily Standup — {date_label}"[:150]},
        }
    ]

    # 1. 4-Stream Pulse
    pulse_lines: list[str] = ["*4-Agent Stream Pulse*"]
    for sp in digest.get("stream_pulse") or []:
        short_tag = sp["stream"].split("-")[1] if "-" in sp["stream"] else sp["stream"]
        if sp["idle"]:
            pulse_lines.append(f"• *[S{short_tag}]* `{sp['stream']}` — _idle / ready for next WO_")
        else:
            for item in sp["active"]:
                pr_str = f" <{item['pr_url']}|PR #{item['pr_number']}>" if item.get("pr_number") and item.get("pr_url") else (f" PR #{item['pr_number']}" if item.get("pr_number") else " _(no PR yet)_")
                wo_str = f" WO #{item['issue']}" if item.get("issue") else ""
                st_str = " `[READY]`" if item.get("status") == "ready" else ""
                pulse_lines.append(
                    f"• *[S{short_tag}]*{st_str} `{item['lane']}` → `{item['branch']}`{pr_str}{wo_str} — {item['summary']}"
                )
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clamp_slack_mrkdwn("\n".join(pulse_lines))}})

    # 2. Shipped in Last 24h
    merged = digest.get("merged_prs") or []
    if merged:
        ship_lines = [f"*Shipped (Last 24h — {len(merged)} merged)*"]
        for pr in merged[:8]:
            ship_lines.append(f"• <{pr['url']}|{pr['repo'].split('/')[-1]}#{pr['number']}> {pr['title'][:90]}")
        if len(merged) > 8:
            ship_lines.append(f"_…and {len(merged) - 8} more_")
    else:
        ship_lines = ["*Shipped (Last 24h):* _no merges in window_"]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clamp_slack_mrkdwn("\n".join(ship_lines))}})

    # 3. Alerts & Waiting on You
    inv = digest.get("invisible_branches") or []
    fcol = digest.get("file_collisions") or {}
    lcol = digest.get("lane_collisions") or {}
    waiting = digest.get("waiting_on_you") or []

    alert_lines: list[str] = ["*Risk Radar & Decisions*"]
    if not inv and not fcol and not lcol:
        alert_lines.append("• :white_check_mark: Zero active lane/file collisions and zero invisible un-PR'd branches.")
    else:
        if inv:
            inv_list = ", ".join(f"`{b['repo'].split('/')[-1]}:{b['branch']}`" for b in inv[:4])
            alert_lines.append(f"• :warning: *Invisible un-PR'd branches ({len(inv)}):* {inv_list}")
        if lcol or fcol:
            alert_lines.append(f"• :warning: *Collisions:* {len(lcol)} lane(s), {len(fcol)} file(s) — check Dispatch Board.")
    if waiting:
        alert_lines.append("*Waiting on Thomas (`NOW.md`):*")
        for w in waiting[:5]:
            alert_lines.append(f"• {w[:140]}")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clamp_slack_mrkdwn("\n".join(alert_lines))}})

    # 4. Top Open Work Orders + Quick Links
    wos = digest.get("work_orders") or []
    wo_lines = [f"*Open Work Orders ({len(wos)})* — <{board_url}|Dispatch Board #{board_num}>"]
    for wo in wos[:6]:
        labs = [lb["name"] for lb in (wo.get("labels") or []) if lb["name"] in ("P0", "P1", "P2", "blocked")]
        tag = "/".join(labs) or "P2"
        url = wo.get("url") or f"https://github.com/{repo}/issues/{wo['number']}"
        wo_lines.append(f"• <{url}|#{wo['number']}> `[{tag}]` {str(wo.get('title') or '')[:90]}")
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clamp_slack_mrkdwn("\n".join(wo_lines))}})

    fallback_text = (
        f"Antigravity Daily Standup ({date_label}): "
        f"{len(merged)} merged (24h), {len(inv)} un-PR'd branches, {len(fcol)} file collisions, {len(wos)} open WOs."
    )
    return {"text": fallback_text, "blocks": blocks}


def post_to_slack(
    payload: dict[str, Any],
    *,
    webhook_url: str | None = None,
    bot_token: str | None = None,
    channel_id: str | None = None,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """Post Block Kit payload via Bot Token (`chat.postMessage`) or Incoming Webhook (`SLACK_WEBHOOK_URL`)."""
    eff_bot = bot_token if bot_token is not None else os.environ.get("SLACK_BOT_TOKEN", "").strip()
    eff_chan = channel_id if channel_id is not None else os.environ.get("SLACK_CHANNEL_ID", "").strip()
    eff_hook = webhook_url if webhook_url is not None else os.environ.get("SLACK_WEBHOOK_URL", "").strip()

    if eff_bot and eff_chan:
        body = json.dumps({"channel": eff_chan, **payload}).encode("utf-8")
        req = urllib.request.Request(
            "https://slack.com/api/chat.postMessage",
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {eff_bot}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(f"Slack chat.postMessage failed: {data.get('error', 'unknown')}")
        return {"posted": True, "mode": "bot_token", "ts": data.get("ts"), "channel": data.get("channel")}

    if eff_hook:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            eff_hook,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
        return {"posted": True, "mode": "webhook", "status": status}

    return {"posted": False, "mode": "unconfigured", "reason": "No SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN+SLACK_CHANNEL_ID set"}


def fetch_recent_merged_prs(repos: list[str], since_hours: int = 24) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    merged: list[dict[str, Any]] = []
    for repo in repos:
        raw = _run_gh([
            "pr", "list", "--repo", repo, "--state", "merged", "--limit", "20",
            "--json", "number,title,mergedAt,url,headRefName",
        ], check=False)
        if not raw.strip():
            continue
        try:
            rows = json.loads(raw)
        except ValueError:
            continue
        for r in rows:
            mt = str(r.get("mergedAt") or "")
            if not mt:
                continue
            dt = datetime.fromisoformat(mt.replace("Z", "+00:00"))
            if dt >= cutoff:
                merged.append({
                    "repo": repo,
                    "number": r["number"],
                    "title": r["title"],
                    "merged_at": mt,
                    "url": r.get("url") or f"https://github.com/{repo}/pull/{r['number']}",
                    "branch": r.get("headRefName") or "",
                })
    return sorted(merged, key=lambda x: x["merged_at"], reverse=True)


def cmd_slack_digest(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    streams = collect_active_streams(config, days=args.days)
    overlaps = detect_overlaps(streams, config)
    leases = fetch_dispatch_leases(config)
    work_orders = fetch_work_orders(config["dispatch_board_repo"])
    merged = fetch_recent_merged_prs(list(config.get("repos") or []), since_hours=args.since_hours)
    now_path = ROOT / "NOW.md"
    waiting = parse_waiting_on_you(now_path.read_text(encoding="utf-8")) if now_path.is_file() else []

    digest = build_daily_digest(
        config=config,
        active_streams=streams,
        leases=leases,
        overlaps=overlaps,
        merged_prs=merged,
        work_orders=work_orders,
        waiting_on_you=waiting,
    )
    slack_payload = format_slack_blocks(digest)

    if args.json or args.dry_run or not args.post:
        print(json.dumps(slack_payload, indent=2, ensure_ascii=False))
    if args.post and not args.dry_run:
        res = post_to_slack(slack_payload)
        print(json.dumps(res, indent=2), file=sys.stderr)
    return 0


def validate_streams_config(config: dict[str, Any]) -> list[str]:
    """Verify every lane in `lanes` is owned by EXACTLY ONE stream in `streams`."""
    errors: list[str] = []
    defined_lanes = set((config.get("lanes") or {}).keys())
    streams = config.get("streams") or {}
    if not isinstance(streams, dict) or not streams:
        return ["Missing or empty `streams` roster in LANES.yaml"]

    owner_of: dict[str, str] = {}
    for sname, scfg in streams.items():
        owned = list((scfg or {}).get("owned_lanes") or [])
        if not owned:
            errors.append(f"Stream `{sname}` owns zero lanes")
        for ln in owned:
            if ln not in defined_lanes:
                errors.append(f"Stream `{sname}` references unknown lane `{ln}`")
            elif ln in owner_of:
                errors.append(f"Lane `{ln}` assigned to multiple streams: `{owner_of[ln]}` and `{sname}`")
            else:
                owner_of[ln] = sname

    unassigned = sorted(defined_lanes - set(owner_of.keys()))
    if unassigned:
        errors.append(f"Unassigned lanes not owned by any stream: {', '.join(unassigned)}")
    return errors


def cmd_roster(args: argparse.Namespace) -> int:
    config = load_lanes_config()
    errs = validate_streams_config(config)
    leases = active_leases(fetch_dispatch_leases(config)) if not args.offline else []
    lease_by_lane = {l["lane"]: l for l in leases}

    streams = config.get("streams") or {}
    print(f"=== 4-Agent Stream Roster (Dispatch Board: {config['dispatch_board_repo']}#{config.get('dispatch_board_issue')}) ===\n")
    for sname, scfg in streams.items():
        owned = scfg.get("owned_lanes") or []
        print(f"## {sname} — {scfg.get('role', '')}")
        print(f"  Owned lanes: {', '.join(owned)}")
        print(f"  Chokepoints: {scfg.get('chokepoint_role', 'none')}")
        active_here = [lease_by_lane[ln] for ln in owned if ln in lease_by_lane]
        if active_here:
            for l in active_here:
                print(f"  LIVE LEASE:  [{l['lane']}] `{l['branch']}` ({l.get('status')}) — {l.get('summary', '')}")
        elif not args.offline:
            print("  LIVE LEASE:  (open / unclaimed)")
        print()
    if errs:
        print("CONFIG ERRORS:", file=sys.stderr)
        for e in errs:
            print(f"  ! {e}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Antigravity Multi-Agent PM & EM Control Plane CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Real-time team status & collision report across repos")
    p_status.add_argument("--days", type=int, default=7)
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_board = sub.add_parser("board", help="Show open Work Orders and active lane leases")
    p_board.set_defaults(func=cmd_board)

    p_roster = sub.add_parser("roster", help="Show the explicit 4-Agent Stream Roster and live lane ownership")
    p_roster.add_argument("--offline", action="store_true", help="Print roster from LANES.yaml without querying GitHub")
    p_roster.set_defaults(func=cmd_roster)

    p_chk = sub.add_parser("check-paths", help="Check planned file paths for collisions (exit 2 on conflict)")
    p_chk.add_argument("paths", nargs="+")
    p_chk.add_argument("--branch", default="")
    p_chk.add_argument("--days", type=int, default=7)
    p_chk.add_argument("--json", action="store_true")
    p_chk.set_defaults(func=cmd_check_paths)

    p_claim = sub.add_parser("claim", help="Claim an exclusive lane lease on the Dispatch Board")
    p_claim.add_argument("--lane", required=True)
    p_claim.add_argument("--branch", default="")
    p_claim.add_argument("--issue", type=int, default=None)
    p_claim.add_argument("--pr", type=int, default=None)
    p_claim.add_argument("--agent", default="")
    p_claim.add_argument("--paths", default="")
    p_claim.add_argument("--summary", required=True)
    p_claim.add_argument("--force", action="store_true")
    p_claim.set_defaults(func=cmd_claim)

    p_ho = sub.add_parser("handoff", help="Update or release a lane lease on the Dispatch Board")
    p_ho.add_argument("--lane", required=True)
    p_ho.add_argument("--branch", default="")
    p_ho.add_argument("--status", choices=VALID_HANDOFF_STATUSES, required=True)
    p_ho.add_argument("--issue", type=int, default=None)
    p_ho.add_argument("--pr", type=int, default=None)
    p_ho.add_argument("--agent", default="")
    p_ho.add_argument("--summary", required=True)
    p_ho.add_argument("--next", default="")
    p_ho.set_defaults(func=cmd_handoff)

    p_pr = sub.add_parser("pr-check", help="Run PR overlap & chokepoint guard")
    p_pr.add_argument("--pr", type=int, required=True)
    p_pr.add_argument("--repo", default="")
    p_pr.add_argument("--days", type=int, default=7)
    p_pr.add_argument("--post-comment", action="store_true")
    p_pr.add_argument("--slack-on-conflict", action="store_true")
    p_pr.set_defaults(func=cmd_pr_check)

    p_sl = sub.add_parser("slack-digest", help="Build and optionally post the daily Slack standup digest")
    p_sl.add_argument("--since-hours", type=int, default=24)
    p_sl.add_argument("--days", type=int, default=7)
    p_sl.add_argument("--dry-run", action="store_true")
    p_sl.add_argument("--post", action="store_true")
    p_sl.add_argument("--json", action="store_true")
    p_sl.set_defaults(func=cmd_slack_digest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
