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
- Tests: `python -m pytest -q`. Engine changes also: `cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1`
  (CI runs seeds 1–3 on every PR; hard fails and made-up quotes must be 0).
- `claude -p` model runs and rebuilds from `~/loopbench-data` happen on the Mac mini only.

## Invariants
- **Asymmetric authority:** the engine port (`backend/engine/ports.py`) and agent API only open_item, raise_urgency,
  alarm, attest_all_clear. Never add close, dismiss, downgrade or reassure. Only doctors close items.
- Every item carries a reasoning trace with verbatim quotes. Never weaken LoopBench checks; fix the engine.
- Rules v1.0 is frozen; behaviour changes go behind a new version (`RulesHarness(version="1.1")`).
- The product never imports LoopBench. A replay miss filled by the rules floor carries the floor's name (`fell_back`).
- Model choice is pluggable (`backend/extract/models.py`); outputs cached by prompt version + model + document hash.
- Synthetic data only. No real patient data, ever.
