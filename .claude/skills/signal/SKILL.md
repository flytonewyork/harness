---
name: signal
description: Use when Thomas shares what a partner (EC Healthcare, Amber Health, Apricot, Adrian or any clinic) said or did. Files it as a signal issue and proposes any change to the queue.
---

# File a partner signal

1. Pull out four things:
   - who and when;
   - what they said or did, in their words where Thomas gave them;
   - what it changes;
   - the next step they agreed to, and by when.

   If one is missing, ask in the thread rather than guessing.
2. Open an issue in flytonewyork/harness:
   - label `signal`;
   - title `Signal: <partner>, <date>`;
   - body under the four headings of `.github/ISSUE_TEMPLATE/signal.md`.
3. If it changes the order of work, open a PR that edits `NOW.md`. Also edit `docs/DECISIONS.md` if a decision was made. Link the issue.
4. Reply with the issue link and a one-line summary.

Partner names and commercial details are fine. Patient data never: if any appears, stop and say so.
