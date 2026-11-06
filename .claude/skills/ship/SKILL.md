---
name: ship
description: Use for any build task in harness or loopbench, started from Slack, the web, the phone or a terminal. Takes one NOW.md item to a scored pull request.
---

# Ship one item

1. **Find the item.** Read `NOW.md`. Work on the item you were given, or the top one in progress. If the ask isn't in
   `NOW.md`, add it in order (or under Parked) in your first commit.
2. **Check the setup.** In a cloud session, `scripts/cloud_setup.sh` has already installed this repo and cloned
   `../loopbench`, and its notes say if it couldn't. Elsewhere: `pip install -e ".[dev]"`, with the test bed beside
   this repo. Screen changes also need `cd frontend && npm install && npm run build`.
3. **Plan before code.** Write down the files you will touch, the check that will prove the change, and any invariant
   it touches. A change that needs a why gets `docs/specs/NNN-name.md` first (template in `docs/specs/README.md`).
4. **Branch** `<item-slug>`. Small commits.
5. **Prove it.** Run `python -m pytest -q`. For engine changes, also run
   `cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1`. Hard fails and made-up quotes must be 0.
   If the numbers move on purpose, say so in the PR.
6. **Open the PR** with the template filled in, and update `NOW.md` in the same PR. One item per PR.
7. **Report back.** If Slack started you, reply in the thread with one line: the PR link and ✅, or ❓ and your question.

Never:
- commit to main;
- add close, dismiss, downgrade or reassure to the agent side;
- change rules v1.0;
- import LoopBench from the product;
- touch real patient data.
