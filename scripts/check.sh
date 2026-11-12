#!/usr/bin/env bash
# The CI gate, locally, for both repos: harness tests, loopbench tests, the scoreboard and the journey gate.
#   scripts/check.sh            seed 1 (about a minute)
#   scripts/check.sh 1 2 3      the seeds CI runs
# Run from harness; needs ../loopbench beside it, on the branch you want to pair (CI pairs same-named branches).
# Stops at the first failure and exits non-zero. It leaves both checkouts as it found them (no --write; journey results restored).
set -euo pipefail
cd "$(dirname "$0")/.."
seeds=("${@:-1}")
lb=../loopbench
step() { printf '\n== %s\n' "$*"; }

step "harness tests"; python -m pytest -q
[ -d "$lb" ] || { echo "no ../loopbench: tests only, CI scores the rest"; exit 0; }
echo "loopbench on $(git -C "$lb" rev-parse --abbrev-ref HEAD), harness on $(git rev-parse --abbrev-ref HEAD)"
cd "$lb"
step "loopbench tests"; PYTHONPATH=src python -m pytest -q
step "scoreboard, seeds ${seeds[*]}"; PYTHONPATH=src python scripts/scoreboard.py --seeds "${seeds[@]}" | tail -15
step "journey gate, seeds ${seeds[*]}"
keep=$(mktemp -d); cp -a results/journeys "$keep/" 2>/dev/null || true   # the gate writes results; put them back
out=$(PYTHONPATH=src python scripts/journey_eval.py --journey all --seeds "${seeds[@]}") && rc=0 || rc=$?
rm -rf results/journeys; cp -a "$keep/journeys" results/ 2>/dev/null || true; rm -rf "$keep"
grep -E "Gate|guard|Hard fails|FAIL" <<<"$out" || echo "$out" | tail -20
[ "$rc" = 0 ] || { echo "journey gate failed (exit $rc)"; exit "$rc"; }
step "all green"
