# Inventory

Every piece of the work, where it lives and what it is. GitHub is the only home (`OPERATING_SYSTEM.md`, rule 1):
anything outside these repos is a view of something in them, a partner document, or retired. Last checked 6 Nov 2026;
re-check it in the Friday review.

## Code

| Repo | What it is | State on 6 Nov |
|---|---|---|
| [harness](https://github.com/flytonewyork/harness) (private) | The product. `NOW.md` is the one queue. | main `6691b6a`. Open: #1, Operating system v2 (this file) |
| [loopbench](https://github.com/flytonewyork/loopbench) (private) | The test bed: a simulated Hong Kong clinic year with a hidden answer key | main `74f6939`. Open: #1, scoreboard gate and CI; #2, Studio demo pass for the Apricot call |
| [heidi_eval](https://github.com/flytonewyork/heidi_eval) (private) | Parked. The Heidi work sample, not tmrw IP | main `da5bdb0`, one commit (26 Oct) |

Merge order: loopbench #1, then harness #1 (its score job needs the scoreboard on loopbench main), then loopbench #2.

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
| Mac mini (always on) | Claude Code by Remote Control from the phone; `claude -p` runs | Both repos side by side in `~/code` (cloned 6 Nov). Data and the staged extraction run in `~/loopbench-data`. The Claude CLI is logged out. System Python is 3.9: enough for the extraction runner, but the tests need 3.11 (loopbench) and 3.12 (harness). |
| MacBook Pro | The desk | Not reachable from the Mac mini (no SSH). Most likely holds the two pieces above. |
| iPhone | Remote Control and chats | — |
| Cloud sessions | claude.ai chats with both repos attached | Ephemeral: work survives only as a PR |

One machine owns a branch at a time; push before switching.

## Data outside git

`~/loopbench-data` on the Mac mini holds the raw downloads the simulator was built from (Synthea, NHANES 2017–18, Hong
Kong government tables, HA specialist-clinic waits, NAMCS, the vendored three.js build), with checksums in
`SHA256SUMS.local`. The MIMIC-IV demo folder is empty: PhysioNet access is blocked. `extract/` stages the `claude -p`
run: the documents (`docs.jsonl`, `docs_smoke300.jsonl`), the same `claude_runner.py` as `backend/extract/`, and
`run_extraction.sh`. Its outputs get committed to loopbench as the replay cache.

## Views and documents

Claude Docs, artifacts and Claude Project docs. Each one is a live view, a partner document, or retired.

**Live**

| Title | Kind | Source of truth |
|---|---|---|
| [Follow-ups: a simulated clinic year](https://claude.ai/artifact/LbR3fsj99mNgnVAPGCSRKR) | Artifact, 5 Nov | `frontend/`, built with `VITE_STATIC=1`. Republish after the model run. |
| [LoopBench Studio](https://claude.ai/artifact/AtVcwSKyQWNjyayG764Y5z) | Artifact, 5 Nov | `loopbench/scripts/build_studio.py`. Republish after loopbench #2 merges. |
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
| Phase 1 close-out (two copies) and Studio spec v1.1 | Project docs | `docs/PHASE1_CLOSEOUT.md` and `loopbench/docs/STUDIO_SPEC.md` |

**Waiting on clinic-agent-evals**

[Clinic admin agent scorecard](https://claude.ai/artifact/C46tpa2XDS3TP2KAS155pH) and
[Grade the judge](https://claude.ai/artifact/QkPcDKc6vRfvgxXMBMD25y) (3 Nov) are views of clinic-agent-evals. Keep them
until its zip is in git.

**Parked, outside this product**

Four Heidi documents (they go with heidi_eval) and six strategy, learning and venture documents. They are listed in
Thomas's private overview, not here, and wait for the 1 Feb 2027 review.

## Chats

Chats are scratch. What they decided is in `docs/DECISIONS.md`; what they built is in the repos. A session that ends
without a PR leaves nothing behind.
