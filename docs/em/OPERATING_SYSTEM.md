# Antigravity Multi-Agent PM & Engineering Management OS

One lightweight operating system so parallel Antigravity / Claude / Cursor conversations build side-by-side on `flytonewyork/harness` and `flytonewyork/loopbench` with **zero duplicate builds and zero surprise merge conflicts**.

---

## 1. Two Planes of Truth

| Plane | Where It Lives | Purpose |
| :--- | :--- | :--- |
| **Product Plane (WHAT & WHY)** | `NOW.md` (<2 KB founder strip) + GitHub Issues labeled `work-order` + `decision` | Scoped priorities (`P0`/`P1`/`P2`), acceptance criteria, seam contracts, and ADRs. |
| **Live Engineering Control Plane (WHO & WHERE)** | Pinned GitHub Issue **#70 (`Active Agent Dispatch Board`)** + `docs/em/LANES.yaml` | Real-time cross-VM lane leases, active branch visibility, and overlap detection across isolated cloud VMs. |

Why a pinned GitHub Issue instead of only a file on `main`? Every cloud VM clones `main` in isolation — a file on `main` only updates *after* merge. Issue #70 + GitHub GraphQL (`scripts/agent_em.py`) gives every VM **real-time visibility** before line 1 is edited.

---

## 1.5 Explicit 4-Agent Stream Roster (`docs/em/LANES.yaml`)

All 8 repository lanes partition cleanly **2 lanes per stream** across **4 parallel agent streams** with **zero lane overlap** (verified by `tests/test_agent_em.py` and `python3 scripts/agent_em.py roster`):

| Stream | Role | Owned Lanes (2 each) | Chokepoint Role & Boundaries |
| :--- | :--- | :--- | :--- |
| **`stream-1-trust-gate`** | Trust, Safety, Gateway & Permission Gate Lead | `lane-a-safety-gateway`, `lane-c-mcp-integrations` | Owns `safety/`, `gateway/`, `mcp/`, `vault/`, `sre/`, `protocols/`, `triage/companion/security/rx_rules/adapters/emr_connector`. **Never touches** `engine/`, `case/`, `agentic/`, `ontology/`, `frontend/`, `evals/`. |
| **`stream-2-core-evals`** | Core Clinical Loop Engine & LoopBench Evals Lead | `lane-f-core-engine`, `lane-e-evals-loopbench` | Owns `engine/`, `case/`, `extract/`, `journeys/`, `precare/`, `api/`, `lookback/`, `evals/`, `../loopbench/`. Consumes `ModelGateway` read-only. **Never touches** `acute/`, `gateway/`, `mcp/`, `agentic/`, `ontology/`, `frontend/`. |
| **`stream-3-clinical-ai`** | Clinical Intelligence, Ontology, Graph & Packs Lead | `lane-b-agentic-graph`, `lane-h-clinical-ontology` | Owns `backend/ontology/`, `agentic/`, `practice_graph`, `pathway_runner`, `curator`, `workflows`, `scheduling`, `backend/knowledge/`, `packs/`, `skills/clinical/` (WO #76 & #62). **Never touches** `safety/`, `gateway/`, `mcp/`, `engine/`, `frontend/`. |
| **`stream-4-surfaces-em`** | Product UX Surfaces, Live Demos & Platform/EM Steward | `lane-d-workspace-ui`, `lane-g-platform-em` | Owns `frontend/`, `workspace_engine`, `live_demo*`, `live_bus`, `docs/em/`, `scripts/agent_em.py`, `.github/`, `deploy/`. **Integration Steward** for `shared-chokepoints` (`app.py` split WO #73, `db.py`, `NOW.md`). |

---

## 2. Mandatory 5-Step Agent Lifecycle

### Step 1 — Pre-Flight (3 seconds, BEFORE creating a branch or editing code)
```bash
bash scripts/team_status.sh
```
This queries GitHub across **both** `harness` and `loopbench` for:
- All branches pushed in the last 7 days (catching **invisible un-PR'd branches**),
- All open PRs and their touched files mapped to `docs/em/LANES.yaml`,
- Live leases on Issue #70 and open `work-order` issues,
- Automatic **file & lane collision warnings**.

Before modifying specific files, run:
```bash
python3 scripts/agent_em.py check-paths path/to/file1.py path/to/file2.py
```
If exit code is `2`, another active agent owns that file/lane — **stop and coordinate/rebase instead of duplicating**.

### Step 2 — Claim Your Exclusive Lane
```bash
python3 scripts/agent_em.py claim \
  --lane lane-a-safety-gateway \
  --branch feat/my-scoped-change \
  --issue 72 \
  --summary "Add FHIR R4 AuditEvent & Provenance serialization"
```
Only one active agent may hold a lane at a time. Cross-lane changes require a convergence Work Order or explicit handoff.

### Step 3 — Day-0 Draft PR ("No Invisible Branches")
Push your first commit (even a small scaffold) and open a **Draft PR immediately**:
```bash
gh pr create --draft --base main --title "..."
```
This exposes your diff to `scripts/team_status.sh` and triggers `.github/workflows/pr-overlap-guard.yml` automatically. Never stack multi-day mega-branches off `main` without a Draft PR.

### Step 4 — Respect Shared Chokepoints & Seam Contracts
- Shared chokepoints (`backend/acute/app.py`, `backend/acute/db.py`, `backend/acute/seed.py`, `NOW.md`, `CLAUDE.md`, `pyproject.toml`): keep edits minimal and additive.
- Keep branches short-lived (**target ≤ 24h off `main`**); rebase onto `main` daily.
- Run `bash scripts/check.sh` before marking a PR ready (must end `== all green`).

### Step 5 — Handoff & Release Your Lane
When your PR is ready, merged, or blocked:
```bash
python3 scripts/agent_em.py handoff \
  --lane lane-a-safety-gateway \
  --branch feat/my-scoped-change \
  --pr 75 \
  --status ready \
  --summary "R4 AuditEvent/Provenance green (check.sh pass); ready for review"
```

---

## 3. Automated Guardrails
- **`scripts/agent_em.py`** (`status`, `board`, `roster`, `check-paths`, `claim`, `handoff`, `pr-check`, `slack-digest`): single CLI for all PM/EM checks.
- **`.github/workflows/pr-overlap-guard.yml`**: runs on every PR open/push, posts a sticky overlap & chokepoint comment, and pings Slack *only* on real collisions (if Slack secret is set).
- **`.github/workflows/daily-em-slack-digest.yml`**: posts a daily **09:00 HKT (`01:00 UTC`)** Slack Block Kit standup across both `harness` and `loopbench` (zero PHI; skips cleanly if `SLACK_WEBHOOK_URL` / `SLACK_BOT_TOKEN` is unset).
- **`.github/ISSUE_TEMPLATE/work_order.yml`**: standard form for creating agent-ready Work Orders.
- **Antigravity Skill `agent-em-sync`**: persists across conversations so every newly spawned agent follows this protocol automatically.

---

## 4. Founder Operating Playbook (Antigravity UI + Daily Slack)

1. **Sidebar Naming (`[S1]`–`[S4]`):** Name every Antigravity chat `[S<1-4>] <Role> — WO #<N> (<Short Task>)` so you see immediately which stream seat each thread owns. Use `[S4]` as your **Command Center** (morning standup review, merges, `/plan`, `/grill-me`, Work Order creation).
2. **Context Lifecycle ("1 Work Order = 1 Fresh Chat"):** Keep a chat while finishing its active PR; once merged (or after ≥2 compactions), **retire it and open a fresh conversation** with this 3-line spawn prompt:
   > `You are Stream <N> (<stream-id>). Use agent-em-sync: run bash scripts/team_status.sh && python3 scripts/agent_em.py roster, read WO #<N> (gh issue view <N> --comments), claim your lane on Issue #70, open a Day-0 Draft PR, and execute until bash scripts/check.sh is all green.`
3. **Slack Setup (1 Secret):** Add `SLACK_WEBHOOK_URL` (Option A — simplest) or `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID` (Option B — bot API) in `flytonewyork/harness` → *Settings → Secrets and variables → Actions*. Test anytime via `python3 scripts/agent_em.py slack-digest --dry-run` or `workflow_dispatch`.

