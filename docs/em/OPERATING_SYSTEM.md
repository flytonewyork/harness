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
- **`scripts/agent_em.py`** (`status`, `board`, `check-paths`, `claim`, `handoff`, `pr-check`): single CLI for all PM/EM checks.
- **`.github/workflows/pr-overlap-guard.yml`**: runs on every PR open/push and posts/updates a sticky comment flagging file/lane collisions with other open PRs or recently-pushed branches.
- **`.github/ISSUE_TEMPLATE/work_order.yml`**: standard form for creating agent-ready Work Orders.
- **Antigravity Skill `agent-em-sync`**: persists across conversations so every newly spawned agent follows this protocol automatically.
