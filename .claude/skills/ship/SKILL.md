---
name: ship
description: Use for any build task in harness or loopbench. Takes one NOW.md item to a tested pull request.
---

1. Pick the item from `NOW.md` (add it under In progress or Parked if missing).
2. Branch, small commits. Touch only what the item needs; a journey follows `docs/JOURNEYS.md`. Pair both repos on
   the same branch name (CI tests them together).
3. `scripts/check.sh` (tests, scoreboard and journey gate for both repos) must end "all green"; say in the PR if it couldn't run.
4. Open a draft PR with the template, `NOW.md` updated in the same PR. Reply with the link in one line.
