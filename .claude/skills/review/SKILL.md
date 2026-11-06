---
name: review
description: Use when asked to review a pull request in harness or loopbench before Thomas merges it. Produces one review comment he can act on in two minutes.
---

# Pre-review a PR

Thomas's review time is the bottleneck, so the goal is one comment he can act on quickly. Post it on the PR if you
can; otherwise make it your reply where you were asked (the Slack thread). Use this shape:

1. **What changed:** one or two sentences, and which `NOW.md` item it moves.
2. **Gate:** the scoreboard from the CI job summary: hard fails, made-up quotes, and any number that moved against
   main. If you can't read CI, run it yourself on the PR branch (`--seeds 1`) and say so. A missing scoreboard is a
   finding.
3. **Invariants:** check each line of the PR template against the diff, and quote any line of the diff that breaks one:
   - the agent side gains no close, dismiss, downgrade or reassure;
   - every item still carries verbatim-quote reasoning;
   - rules v1.0 is unchanged;
   - the product doesn't import LoopBench;
   - the data is synthetic only.
4. **Risks:** at most three, most serious first. Anything a doctor would see on the follow-up list counts double.
5. **Recommendation:** merge, merge after a named fix, or don't merge.

Then add every lesson worth keeping as a line in `CLAUDE.md`, in the same PR or a follow-up.
