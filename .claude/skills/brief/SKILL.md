---
name: brief
description: Use when asked for a status brief, "where are we", or the morning brief. Reports what merged, what waits on Thomas, CI on main, partner calls and signals, and the top of NOW.md.
---

# Brief

1. With `gh`, cover flytonewyork/harness and flytonewyork/loopbench:
   - PRs merged and opened in the last 24 hours (since Friday morning on a Monday);
   - open PRs waiting for review, oldest first, with how long each has waited;
   - the latest CI run on main in each repo, with its conclusion;
   - `signal` issues opened in the last 7 days.
2. From `NOW.md`, take the milestone and the "In progress" items.
3. If the session can read Thomas's calendar and email (the scheduled brief can; a Slack session can't), add:
   - **Partner calls:** only events whose title starts with `[tmrw]`; never mention any other event. For each one
     today, a prep block of at most three lines: who and when, the latest `signal` issue for that partner with its
     next step, and the top open questions from a matching prep doc in the repos. For each one yesterday with no
     `signal` issue for that partner since it began, one line asking for notes in `#signals`.
   - **Email from the last 24 hours** that needs a reply or an action, from partners or about tmrw's company admin.
     Subject and sender only; never quote message bodies.
4. Write at most 15 short lines, in plain words, with links:
   - Lead with the one thing that most needs Thomas today: a partner call, red CI on main, or a PR that has waited
     more than a day.
5. On Fridays, add a weekly review:
   - what merged this week, and the signals filed;
   - whether CI on main stayed green;
   - at most three suggestions for re-ordering `NOW.md`. These are suggestions only.

Read-only: change nothing and open nothing. No patient data, tokens, secrets or personal calendar events.
