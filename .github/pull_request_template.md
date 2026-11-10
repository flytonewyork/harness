## What this moves
`NOW.md` item:
Bet it tests (`docs/BETS.md`), or none:
Spec (if any):

## Proof
- [ ] Tests pass (`python -m pytest -q`)
- [ ] Scoreboard: CI posts it in the job summary. Hard fails 0 and made-up quotes 0 in every cell
- [ ] If the numbers moved on purpose: refreshed results committed in loopbench (`scripts/scoreboard.py --write`)

## Invariants
- [ ] The agent still only opens or raises: no close, dismiss, downgrade or reassure
- [ ] Every item still carries a reasoning trace with verbatim quotes
- [ ] Rules v1.0 unchanged; new behaviour sits behind a new version
- [ ] The product still never imports LoopBench
- [ ] Synthetic data only

## NOW.md
- [ ] Updated in this PR: item moved or closed; anything new added in order or parked
