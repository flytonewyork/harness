# CLAUDE.md

## Commands
- Tests: `python -m pytest -q` (no simulator needed)
- Full scoring happens in LoopBench (sibling checkout `../loopbench`): `PYTHONPATH=src python -m loopbench.cli run --harness rules-v1.1 ...`

## Design decisions to preserve
- **Asymmetric authority.** The engine's port (`backend/engine/ports.py`) has open_item, raise_urgency, alarm and attest_all_clear only. Never add close, dismiss, downgrade or reassure. The API's agent endpoints enforce the same; only doctor endpoints close items.
- **Reasoning traces with verbatim quotes** on every item. Never weaken LoopBench's platform checks to make the engine pass; fix the engine.
- **Rules v1.0 is the frozen floor.** Behaviour changes go behind a new version (`RulesHarness(version="1.1")`) so results stay comparable.
- **The product never imports LoopBench.** Adapters translate; the engine talks only to the port.
- **Model choice is pluggable** (`backend/extract/models.py`): which model may touch EC data is still open. Outputs are cached by prompt version + model + document hash so runs are reproducible.
- Synthetic data only in this repo and its tests. No real patient data, ever.
