---
name: brief
description: Use when asked for a status brief, "where are we", or the morning brief. Reports what merged, what waits on Thomas, CI on main and the top of NOW.md.
---

# Brief

1. With `gh`, cover flytonewyork/harness and flytonewyork/loopbench:
   - PRs merged and opened in the last 24 hours (since Friday morning on a Monday);
   - open PRs waiting for review, oldest first, with how long each has waited;
   - the latest CI run on main in each repo, with its conclusion.
2. From `NOW.md`, take the milestone and the "In progress" items.
3. Write at most 12 short lines, in plain words, with links:
   - Lead with the one thing that most needs Thomas today.
   - If a PR has waited more than a day, or CI on main is red, say that first.
4. On Fridays, add a weekly review:
   - what merged this week;
   - whether CI on main stayed green;
   - at most three suggestions for re-ordering `NOW.md`. These are suggestions only.

Read-only: change nothing and open nothing.
