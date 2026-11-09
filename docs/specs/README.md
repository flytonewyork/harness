# Specs

One page per change that needs a why, written before the code. Name it `NNN-short-name.md`, link it from its `NOW.md`
item, and link the PR to it.

Take the next number free on main when you open the PR. If main takes it first, renumber before merging:
`tests/test_queue.py` fails on a number used twice.

```markdown
# NNN: <name>

**Problem.** Who it hurts, what goes wrong, and the evidence (a signal note, a scoreboard row, a check failure).
**Change.** What will be different for the doctor or the clinic.
**Done when.** What the scoreboard or a platform check must show. Write the eval before the build.
**Invariants touched.** Authority line, verbatim quotes, rules floor, no LoopBench imports, synthetic data. Usually none.
**Out of scope.** What this deliberately doesn't do.
```
