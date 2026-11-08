# Inventory

Every piece of the work, where it lives and what it is. GitHub is the only home (`OPERATING_SYSTEM.md`, rule 1):
anything outside these repos is a view of something in them, a partner document, or retired. Last checked 6 Nov 2026;
re-check it in the Friday review.

## Code

| Repo | What it is | State on 6 Nov |
|---|---|---|
| [harness](https://github.com/flytonewyork/harness) (private) | The product. `NOW.md` is the one queue; `.claude/skills/` holds the protocol as steps. | main `69adb94` (#2, Slack, skills and cloud sessions, merged 6 Nov) |
| [loopbench](https://github.com/flytonewyork/loopbench) (private) | The test bed: a simulated Hong Kong clinic year with a hidden answer key | main `f7d9075` (#3, cloud sessions, merged 6 Nov) |
| [heidi_eval](https://github.com/flytonewyork/heidi_eval) (private) | Parked. The Heidi work sample, not tmrw IP | main `da5bdb0`, one commit (26 Oct) |

The setup PRs (harness #1 and #2, loopbench #1 to #3) merged on 6 Nov. CI on main is red in both repos until the new
`REPOS_TOKEN` is in (`OPERATING_SYSTEM.md`, Install): the tests pass, but the checkout of the sibling repo fails.

## Not in git yet

Neither piece is on the Mac mini (checked 6 Nov). The MacBook Pro is the likely home of both: the 5 Nov sessions place
clinical_eval at `~/heidi_eval/clinical`, and clinic-agent-evals in a zip in Downloads.

| Piece | What it holds | Goes to |
|---|---|---|
| clinic-agent-evals (3 Nov) | Tool contract, 32 single-task cases, runners (API, `claude -p`, OpenAI-compatible), scorecard | A loopbench branch, then the port in `NOW.md` |
| clinical_eval (4 Nov) | A judge that quotes its evidence: 249 gold facts, 74 traps, a blinded labelling workbook | A heidi_eval branch, then parked |

Before either is committed: no keys or `.env` files, and synthetic data only.

## Machines

| Machine | Role | State on 6 Nov |
|---|---|---|
| Mac mini (always on) | Only when a job needs it (6 Nov): `claude -p` model runs and rebuilds from the raw data, by Remote Control from the phone | Both repos side by side in `~/code` (cloned 6 Nov). Data and the staged extraction run in `~/loopbench-data`. The Claude CLI is logged out. System Python is 3.9: enough for the extraction runner, but the tests need 3.11 (loopbench) and 3.12 (harness). |
| MacBook Pro | The desk: starts cloud sessions from the desktop app | Not reachable from the Mac mini (no SSH). Most likely holds the two pieces above. |
| iPhone | Slack, chats and cloud sessions; Remote Control when the Mac mini is needed | — |
| Cloud sessions | The default for all work (6 Nov): claude.ai/code, the desktop app, the phone, `@Claude` in Slack, chats and scheduled tasks | The `tmrw` environment (made 6 Nov) is the default for new sessions on claude.ai/code: both repos, limited network with package managers on. Slack sessions run in the `Default` cloud environment, where each repo's `scripts/cloud_setup.sh` clones its sibling. Ephemeral: work survives only as a PR |

One machine owns a branch at a time; push before switching.

## Slack

| Channel | Use | State on 6 Nov |
|---|---|---|
| `#thomas-claude` (private) | One `@Claude` thread per task | Claude app connected (Code + Chat). Works end to end: a task on 6 Nov ran as a Claude Code session on harness and replied in its thread. The channel's default repo is harness. |
| `#signals` (private) | Every partner contact within 24 hours | Claude app in the channel |
| `#ship` | PRs, reviews and CI from both repos | GitHub app subscribed to both repos |
| `#daily` | The weekday brief | The `tmrw morning brief` scheduled task posts here on weekdays at 06:43 (first run 9 Nov), with prep for the day's `[tmrw]` calls. It reads the calendar and Gmail as well as GitHub and Slack |

## Data outside git

`~/loopbench-data` on the Mac mini holds the raw downloads the simulator was built from (Synthea, NHANES 2017–18, Hong
Kong government tables, HA specialist-clinic waits, NAMCS, the vendored three.js build), with checksums in
`SHA256SUMS.local`. The MIMIC-IV demo folder is empty: PhysioNet access is blocked. `extract/` stages the `claude -p`
run: the documents (`docs.jsonl`, `docs_smoke300.jsonl`), the same `claude_runner.py` as `backend/extract/`, and
`run_extraction.sh`. Its outputs get committed to loopbench as the replay cache.

## Views and documents

Claude Docs, artifacts and Claude Project docs. Each one is a live view, a partner document, or retired. On the
Artifacts page (filed 6 Nov), the live ones sit at the top level, the retired ones are in the Archive folder, and the
parked ones are in "Parked until 1 Feb 2027".

**Live**

| Title | Kind | Source of truth |
|---|---|---|
| [Follow-ups: a simulated clinic year](https://claude.ai/artifact/LbR3fsj99mNgnVAPGCSRKR) | Artifact, 5 Nov | `frontend/`, built with `VITE_STATIC=1`. Republish after the model run. |
| [LoopBench Studio](https://claude.ai/artifact/P5fyvrfUo6xsoNuiT9hSDS) | Artifact, 6 Nov | `loopbench/scripts/build_studio.py`, built from `d7c06c0` (loopbench #2) |
| [tmrw clinic backend](https://claude.ai/artifact/2DTTaQspyFm2W8hManhSYu) | Artifact, 8 Nov | `demo/clinic-backend/index.html` (spec 002). Rebuild with `build_data.py`, then republish the file. |
| [tmrw one-page demo](https://claude.ai/artifact/7MzAFXNeTSNMJDLqnvS8Zw) | Design canvas, 8 Nov | The one-pager to send doctors: four jobs that bring in income (results, day-3 check-ins, chronic and Co-Care reviews, vaccines), Cantonese mock-ups and sourced Hong Kong figures. Lives only in the canvas. |
| [tmrw care line](https://claude.ai/artifact/1PjRfqEUpFFqwPELnZ1Ddp) | Design canvas, 6 Nov; clinic backend section 8 Nov | The landing site: home and one page each for doctors, patients and clinics. Lives only in the canvas. |
| [Work overview and system map](https://claude.ai/artifact/MvEhKpPrcoi4DY7tWWwPzD) | Artifact, 6 Nov | A snapshot of this file and `OPERATING_SYSTEM.md` on 6 Nov |
| [Data request: Phase 0 follow-up look-back](https://claude.ai/artifact/KKX86CZh4miqqAs4TC37tn) | Doc, 5 Nov | `docs/EC_DATA_REQUEST.md` |
| [EC Healthcare demo: talk track and Q&A](https://claude.ai/artifact/81g9RX2ddNQ9mSv6fXpGmp) | Doc, 5 Nov | Partner document |
| [Amber Health visit: workflow capture sheet](https://claude.ai/artifact/TDPqEUMAnpxwshUjn94hYn) | Doc, 3 Nov | Partner document |
| [Cloud Doctor: AGI-pilled spec](https://claude.ai/artifact/65tzMpzrrtSp4tbVVoqTmH) | Doc, 5 Nov | The vision. Its open question is in `docs/DECISIONS.md`. |
| [Hong Kong primary care: problems ranked by size](https://claude.ai/artifact/AhFSz2DBnZCj5eGAzZ35f2) | Doc, 5 Nov | Reference for the 26 Nov go/no-go |
| Apricot demo prep | Project doc | A copy of `loopbench/docs/APRICOT_DEMO_PREP.md`, kept for the 6 Nov call |

**Retired: superseded, kept as a record**

| Title | Kind | Replaced by |
|---|---|---|
| [Clinical Harness: Core Architecture and 8-Week Plan](https://claude.ai/artifact/8huANJmN4dfmhyhfXWkzht) | Doc, 4 Nov | `loopbench/docs/PLAN.md` and `docs/PHASE1_CLOSEOUT.md` |
| [Clinical Agent Harness: MVP Product Spec and Eval Rubric](https://claude.ai/artifact/Yabe7gcZxJfo1LXgE5ko3o) | Doc, 4 Nov | The same. Bring back what's still needed as `docs/specs/`. |
| [AI-Native Builder: Operating System](https://claude.ai/artifact/9dkkz1P8ftUTnKyQgY9fy8) | Doc, 28 Oct | `docs/OPERATING_SYSTEM.md` |
| [The Case Loop](https://claude.ai/artifact/LDdYcKFkr3RqMwFEB33UvG) and [Clinical AI Evaluation Map](https://claude.ai/artifact/Cm6tBPMMyKgfDJQW4YAg5h) | Artifacts, 4 Nov | LoopBench |
| [LoopBench Studio, 5 Nov build](https://claude.ai/artifact/AtVcwSKyQWNjyayG764Y5z) | Artifact, 5 Nov | The 6 Nov build above |
| Phase 1 close-out (two copies) and Studio spec v1.1 | Project docs, for Thomas to delete | `docs/PHASE1_CLOSEOUT.md` and `loopbench/docs/STUDIO_SPEC.md` |

**Waiting on clinic-agent-evals**

[Clinic admin agent scorecard](https://claude.ai/artifact/C46tpa2XDS3TP2KAS155pH) and
[Grade the judge](https://claude.ai/artifact/QkPcDKc6vRfvgxXMBMD25y) (3 Nov) are views of clinic-agent-evals. Keep them
until its zip is in git.

**Parked, outside this product**

Four Heidi documents (they go with heidi_eval) and six strategy, learning and venture documents, filed in the "Parked
until 1 Feb 2027" folder. They are listed in Thomas's private overview, not here, and wait for the 1 Feb 2027 review.

## Chats

Chats are scratch. What they decided is in `docs/DECISIONS.md`; what they built is in the repos. A session that ends
without a PR leaves nothing behind.
