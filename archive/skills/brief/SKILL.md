---
name: brief
description: Use when asked for a status brief, "where are we", or the morning brief. Reports what merged, what waits on Thomas, CI on main, partner calls and signals, and the top of NOW.md.
---

# Brief

1. With `gh`, cover flytonewyork/harness and flytonewyork/loopbench:
   - PRs merged and opened in the last 24 hours (since Friday morning on a Monday);
   - open PRs waiting for review, oldest first, with how long each has waited. Count them against the cap of three
     (a stack counts as one; drafts don't count), and name any open PR that `NOW.md` doesn't mention or that has
     been idle for five days or more;
   - the latest CI run on main in each repo, with its conclusion;
   - the latest `signal` issue for each partner, searching open and closed issues, with its next step. Flag a next
     step whose date has passed, and a latest signal that is closed.
2. From `NOW.md`, take the milestone, the "Waiting on you" list (flag any item past its date) and the "In progress"
   items.
3. If the session can read Thomas's calendar and email (the scheduled brief can; a Slack session can't), add:
   - **Partner calls:** only events whose title starts with `[tmrw]`; never mention any other event. For each one
     today, a prep block of at most three lines: who and when, the latest `signal` issue for that partner with its
     next step, and the top open questions from a matching prep doc in the repos. For each one yesterday with no
     `signal` issue for that partner since it began, one line asking for notes in `#signals`.
   - **Email from the last 24 hours** that needs a reply or an action, from partners or about tmrw's company admin.
     Subject and sender only; never quote message bodies.
4. Write at most 15 short lines, in plain words, with links:
   - Lead with the one thing that most needs Thomas today: a partner call, red CI on main, a decision past its date,
     or a PR that has waited more than a day.
5. On Fridays, add a weekly review in the order of the Friday review (`docs/OPERATING_SYSTEM.md`, Cadence):
   - the signals filed this week, and each partner's suggested stage where it changed;
   - bets with a result due (`docs/BETS.md`);
   - the decisions waiting on Thomas;
   - what merged this week, whether CI on main stayed green, the PRs to merge, close or park, and at most three
     suggestions for re-ordering `NOW.md`. These are suggestions only.

Read-only: change nothing and open nothing. No patient data, tokens, secrets or personal calendar events.
