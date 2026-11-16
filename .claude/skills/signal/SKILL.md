---
name: signal
description: Use when Thomas shares what a partner (EC Healthcare, Amber Health, Apricot, Virtus Healthcare, Adrian or any clinic) said or did. Files it as a signal issue and proposes any change to the queue.
---

# File a partner signal

1. Pull out four things:
   - who and when;
   - what they said or did, in their words where Thomas gave them;
   - what it changes;
   - the next step they agreed to, and by when.

   If one is missing, ask in the thread rather than guessing. Then suggest a stage: Talking, Burden named, Next step
   in writing, Data access, Pilot or Paused (`docs/OPERATING_SYSTEM.md`, Product management). Go by what the partner
   did or agreed, not by what they hinted.
2. Open an issue in flytonewyork/harness:
   - label `signal`;
   - title `Signal: <partner>, <date>`;
   - body under the five headings of `.github/ISSUE_TEMPLATE/signal.md`.
3. Close the partner's previous open signal with the comment "Superseded by #N", so each partner has one open
   signal. Nothing else closes a signal.
4. If it changes the order of work, open a PR that edits `NOW.md`. Also edit `docs/DECISIONS.md` if a decision was
   made, and `docs/BETS.md` if it carries a bet's result (the result and its Brier score). Link the issue.
5. Reply with the issue link, a one-line summary and the suggested stage, for Thomas to set on the board.

Filing from older notes (a backfill)? Put "(backfilled)" at the end of the title, and start the body with one line
saying where the notes came from. Mark anything unknown **[check]** instead of guessing. When Thomas corrects one,
edit the issue and remove the marks.

Partner names and commercial details are fine. Patient data never: if any appears, stop and say so.
