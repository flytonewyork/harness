# CLAUDE.md

## Session protocol (every session, every surface)
- **Start:** read `NOW.md`. Work on the item you were given, or the top one in progress. If the ask isn't in `NOW.md`, add it in order (or under Parked) before starting.
- **Branch:** one branch and one PR per item. Never commit straight to main.
- **Prove:** tests here, and for anything the engine does, the scoreboard in the sibling checkout: `cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1` (CI runs seeds 1–3 on every PR).
- **End:** push, open a PR with the template filled in, and update `NOW.md` in the same PR. If you can't push, say so plainly; never leave work only in a container, chat, Claude Doc or artifact.
- **Where things live:** how we work `docs/OPERATING_SYSTEM.md` · the queue `NOW.md` · decisions `docs/DECISIONS.md` · specs `docs/specs/`.

## Commands
- Tests: `python -m pytest -q` (no simulator needed)
- API + screen: `python -m backend.api --run <harness-run json>` (build the screen first: `cd frontend && npm run build`)
- Look-back: `python -m backend.lookback --export DIR --out DIR`
- `claude -p` extraction runs on the Mac (logged-in CLI), never in this container: `backend/extract/claude_runner.py`
- Full scoring happens in LoopBench (sibling checkout `../loopbench`): `PYTHONPATH=src python -m loopbench.cli run --harness rules-v1.1 ...`

## Design decisions to preserve
- **Asymmetric authority.** The engine's port (`backend/engine/ports.py`) has open_item, raise_urgency, alarm and attest_all_clear only. Never add close, dismiss, downgrade or reassure. The API's agent endpoints enforce the same; only doctor endpoints close items.
- **Reasoning traces with verbatim quotes** on every item. Never weaken LoopBench's platform checks to make the engine pass; fix the engine.
- **Rules v1.0 is the frozen floor.** Behaviour changes go behind a new version (`RulesHarness(version="1.1")`) so results stay comparable.
- **The product never imports LoopBench.** Adapters translate; the engine talks only to the port.
- **Model choice is pluggable** (`backend/extract/models.py`): which model may touch EC data is still open. Outputs are cached by prompt version + model + document hash so runs are reproducible.
- Synthetic data only in this repo and its tests. No real patient data, ever.
