---
name: ship
description: Use for any build task in harness or loopbench. Takes one NOW.md item to a tested pull request.
---

1. Pick the item from `NOW.md` (add it under In progress or Parked if missing).
2. Branch, small commits. Touch only what the item needs.
3. `python -m pytest -q`; engine changes also run the scoreboard (`--seeds 1`) in `../loopbench`.
4. Open a draft PR with the template, `NOW.md` updated in the same PR. Reply with the link in one line.
