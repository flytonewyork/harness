# CLAUDE.md

Small project: the product (`harness`) and its test bed (`../loopbench`). Keep context small.

## Working rules
- Start from `NOW.md` (the one queue for both repos). Work only on its In progress items unless Thomas says otherwise.
- One branch and one PR per item; never commit to main; update `NOW.md` in the same PR. Thomas merges.
- Don't read `archive/`, `demo/landing/`, `demo/clinic-backend/` or loopbench `data/`, `web/`, `results/` unless the
  task is about them. Large HTML: grep and read line ranges, never the whole file.
- No extra threads, subagents or Remote Control sessions unless Thomas asks. Short replies.
- Nothing reaches a partner or patient without Thomas's explicit go-ahead. Never start or stop the care line on the
  Mac mini without him.

## Commands
- **Pre-flight (before creating a branch or editing code):** `bash scripts/team_status.sh` — checks active branches across
  both repos (including un-PR'd ones), open PRs, Dispatch Board leases (Issue #70), and file/lane collisions. Claim your
  lane (`python3 scripts/agent_em.py claim --lane <lane> ...`), open a **Day-0 Draft PR** on the first commit, check
  paths (`python3 scripts/agent_em.py check-paths ...`), and release via `handoff`. Lane rules: `docs/em/LANES.yaml` & `docs/em/OPERATING_SYSTEM.md`.
- Tests: `python -m pytest -q`. Before a PR: `scripts/check.sh` runs what CI gates, for both repos (harness and loopbench
  tests, scoreboard, journey gate; seed 1, or `scripts/check.sh 1 2 3` as CI). Hard fails and made-up quotes must be 0.
- `claude -p` model runs and rebuilds from `~/loopbench-data` happen on the Mac mini only.

## Layout
- `backend/engine/` core (port, rules v1.0/v1.1, agentic), `backend/journeys/<name>/` plug-in journeys, `backend/extract/`
  models and the shared reader, `backend/lookback/` and `backend/api/`. A new journey follows `docs/JOURNEYS.md` (both repos).
- Blind sets (`blind: true`) are reported, not gated; never edit a blind file to pass: new phrasings go in a new file.

## Invariants
- **Asymmetric authority:** the engine port (`backend/engine/ports.py`) and agent API only open_item, raise_urgency,
  alarm, attest_all_clear. Never add close, dismiss, downgrade or reassure. Only doctors close items.
  Journeys never message patients; they may suggest a fixed template for the doctor to send.
- Every item carries a reasoning trace with verbatim quotes. Never weaken LoopBench checks; fix the engine.
- Rules v1.0 is frozen; behaviour changes go behind a new version (`RulesHarness(version="1.1")`).
- The product never imports LoopBench. A replay miss filled by the rules floor carries the floor's name (`fell_back`).
- Model choice is pluggable (`backend/extract/models.py`); outputs cached by prompt version + model + document hash.
- Synthetic data only. No real patient data, ever.
