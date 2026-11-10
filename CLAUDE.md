# CLAUDE.md

## Session protocol (every session, every surface)
- **Start:** read `NOW.md`. Work on the item you were given, or the top one in progress. If the ask isn't in `NOW.md`, add it in order (or under Parked) before starting.
- **Branch:** one branch and one PR per item. Never commit straight to main.
- **Prove:** tests here, and for anything the engine does, the scoreboard in the sibling checkout: `cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1` (CI runs seeds 1–3 on every PR).
- **End:** push, open a PR with the template filled in, and update `NOW.md` in the same PR. If you can't push, say so plainly; never leave work only in a container, chat, Claude Doc or artifact.
- **Skills** (`.claude/skills/`) are this protocol as steps: `ship` for any build task, `signal` for what a partner said, `park` for "not now", `brief` for where things stand, `review` before Thomas merges a PR.
- **Cloud sessions are the default** (environment `tmrw`, both repos side by side). They run `scripts/cloud_setup.sh` at start, which installs this repo and clones `../loopbench` if it's missing. If it reports a step it couldn't do, carry on and say so in the PR; CI scores every PR anyway. Use the Mac mini only for `claude -p` model runs and rebuilds from the raw data in `~/loopbench-data`.
- **Where things live:** how we work `docs/OPERATING_SYSTEM.md` · the queue `NOW.md` · decisions `docs/DECISIONS.md` · bets `docs/BETS.md` · specs `docs/specs/` · the map of everything `docs/INVENTORY.md`.

## Commands
- Tests: `python -m pytest -q` (no simulator needed)
- API + screen: `python -m backend.api --run <harness-run json>` (build the screen first: `cd frontend && npm run build`)
- Care line (WhatsApp, spec 004): `python -m backend.careline --run <harness-run json> --mode sim`, dashboard at `/careline`; evals `python -m backend.careline.evals --run tests/fixtures/careline_run.json`; on the Mac mini `scripts/careline_mac_mini.sh setup` once, then `status`, `logs`, `stop`
- Look-back: `python -m backend.lookback --export DIR --out DIR`
- `claude -p` extraction runs on the Mac (logged-in CLI), never in this container: `backend/extract/claude_runner.py`
- Full scoring happens in LoopBench (sibling checkout `../loopbench`): `PYTHONPATH=src python -m loopbench.cli run --harness rules-v1.1 ...`

## Design decisions to preserve
- **Asymmetric authority.** The engine's port (`backend/engine/ports.py`) has open_item, raise_urgency, alarm and attest_all_clear only. Never add close, dismiss, downgrade or reassure. The API's agent endpoints enforce the same; only doctor endpoints close items.
- **Reasoning traces with verbatim quotes** on every item. Never weaken LoopBench's platform checks to make the engine pass; fix the engine.
- **Rules v1.0 is the frozen floor.** Behaviour changes go behind a new version (`RulesHarness(version="1.1")`) so results stay comparable.
- **The product never imports LoopBench.** Adapters translate; the engine talks only to the port.
- **Never credit one model with another's answers.** A replay miss filled by the rules floor carries the floor's name and is cached under it (`fell_back`). LoopBench scores a replayed model run without the floor: a document the model never answered counts as missed.
- **Model choice is pluggable** (`backend/extract/models.py`): which model may touch EC data is still open. Outputs are cached by prompt version + model + document hash so runs are reproducible.
- Synthetic data only in this repo, its tests and every Claude session. Real patient data exists only in the live care line's Google Cloud project (spec 005): never in git, CI, logs, Slack, docs or chats.
