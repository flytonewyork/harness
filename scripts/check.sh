#!/usr/bin/env bash
# The CI gate, locally, for both repos: harness tests, loopbench tests, the scoreboard and the journey gate.
#   scripts/check.sh            seed 1 (about a minute)
#   scripts/check.sh 1 2 3      the seeds CI runs
# Run from harness; needs ../loopbench beside it, on the branch you want to pair (CI pairs same-named branches).
# Stops at the first failure and exits non-zero. It leaves both checkouts as it found them (no --write; journey results restored).
# A missing ../loopbench is a failed gate. Restoring results/journeys never deletes the backup until every
# *_firstrun.json is back in place; a failed restore leaves the backup where it is.
set -euo pipefail

# Put results/journeys back from a backup directory that already contains journeys/.
# On any failure the backup directory is left in place and live *_firstrun.json files are not deleted first.
restore_saved_journeys() {
  local keep="$1" root="$2" staged rel missing f
  if [ ! -d "$keep/journeys" ]; then
    echo "restore of results/journeys refused: backup $keep/journeys is missing; backup left in place" >&2
    return 1
  fi
  find "$keep/journeys" -name '*_firstrun.json' -type f | sort > "$keep/firstrun.list"
  staged=$(mktemp -d)
  if ! cp -a "$keep/journeys" "$staged/journeys"; then
    echo "restore copy failed; backup kept at $keep" >&2
    rm -rf "$staged"
    return 1
  fi
  missing=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rel=${f#"$keep/journeys/"}
    if [ ! -f "$staged/journeys/$rel" ]; then
      echo "restore would drop $rel; backup kept at $keep" >&2
      missing=1
    fi
  done < "$keep/firstrun.list"
  if [ "$missing" != 0 ]; then
    rm -rf "$staged"
    return 1
  fi
  mkdir -p "$root/results"
  if [ -d "$root/results/journeys" ]; then
    mv "$root/results/journeys" "$staged/journeys.run"
  fi
  if ! mv "$staged/journeys" "$root/results/journeys"; then
    if [ -d "$staged/journeys.run" ]; then
      mv "$staged/journeys.run" "$root/results/journeys" || echo "could not put the live results back; backup kept at $keep" >&2
    fi
    echo "restore swap failed; backup kept at $keep" >&2
    return 1
  fi
  missing=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    rel=${f#"$keep/journeys/"}
    if [ ! -f "$root/results/journeys/$rel" ]; then
      echo "restore dropped $rel; backup kept at $keep" >&2
      missing=1
    fi
  done < "$keep/firstrun.list"
  if [ "$missing" != 0 ]; then
    return 1
  fi
  rm -rf "$staged" "$keep"
}

run_gate() {
  cd "$(dirname "$0")/.."
  local lb keep out rc
  local -a seeds
  seeds=("${@:-1}")
  lb=../loopbench
  if [ ! -d "$lb" ]; then
    echo "no ../loopbench beside harness; refusing to run a partial gate" >&2
    exit 1
  fi
  step() { printf '\n== %s\n' "$*"; }
  step "harness tests"; python -m pytest -q
  echo "loopbench on $(git -C "$lb" rev-parse --abbrev-ref HEAD), harness on $(git rev-parse --abbrev-ref HEAD)"
  cd "$lb"
  step "loopbench tests"; PYTHONPATH=src python -m pytest -q
  step "scoreboard, seeds ${seeds[*]}"; PYTHONPATH=src python scripts/scoreboard.py --seeds "${seeds[@]}" | tail -15
  step "journey gate, seeds ${seeds[*]}"
  keep=$(mktemp -d)
  if [ -d results/journeys ]; then
    if ! cp -a results/journeys "$keep/journeys"; then
      echo "backup of results/journeys failed; left untouched" >&2
      rm -rf "$keep"
      exit 1
    fi
  else
    mkdir -p "$keep/journeys"
  fi
  out=$(PYTHONPATH=src python scripts/journey_eval.py --journey all --seeds "${seeds[@]}") && rc=0 || rc=$?
  restore_saved_journeys "$keep" "$PWD" || exit 1
  grep -E "Gate|guard|Hard fails|FAIL" <<<"$out" || echo "$out" | tail -20
  [ "$rc" = 0 ] || { echo "journey gate failed (exit $rc)"; exit "$rc"; }
  step "all green"
}

if [ "${CHECK_LIB_ONLY:-}" != 1 ]; then
  run_gate "$@"
fi
