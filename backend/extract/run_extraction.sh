#!/bin/bash
# Follow-up extraction with claude -p (LoopBench dev documents, synthetic patients only), on the Mac mini.
# Lives in ~/loopbench-data/extract beside claude_runner.py, prompt.py, docs.jsonl and docs_smoke300.jsonl
# (from `loopbench docs`). Start it from Terminal there: ./run_extraction.sh
# On screen: the login check and one test batch (a few minutes). Then it carries on in the background, so the
# window can close: the 300-patient smoke set, gated, then the full dev year. Resumable: rerun it to pick up where
# it stopped. Everything is logged to run.log.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [ "${1:-}" != "--background" ]; then
  if pgrep -f "claude_runner.py" >/dev/null || pgrep -f "run_extraction.sh --background" >/dev/null; then
    echo "An extraction is already running. Progress: tail -f $HERE/run.log"; exit 1
  fi
  claude auth status 2>/dev/null | grep -q '"loggedIn": true' || {
    echo "Claude CLI is logged out: run 'claude', then /login, then rerun ./run_extraction.sh"; exit 1; }
  echo "== $(date) test batch" | tee -a run.log
  python3 claude_runner.py --docs docs_smoke300.jsonl --out raw_smoke.jsonl --model sonnet --batch 40 --workers 1 \
    --limit-batches 1 2>&1 | tee -a run.log
  [ "${PIPESTATUS[0]}" -eq 0 ] || { echo "The test batch failed (above, and in run.log); nothing else started."; exit 1; }
  nohup "$HERE/run_extraction.sh" --background >/dev/null 2>&1 &
  echo "Test batch ok. The rest runs in the background (pid $!), so this window can close."
  echo "Progress: tail -f $HERE/run.log"
  exit 0
fi

{
  echo "== $(date) smoke set (300 patients)"
  python3 claude_runner.py --docs docs_smoke300.jsonl --out raw_smoke.jsonl --model sonnet --batch 40 --workers 4 || {
    echo "== $(date) smoke set stopped early: rerun ./run_extraction.sh to resume"; exit 1; }
  echo "== $(date) smoke check"
  python3 - <<'PY' || { echo "== $(date) smoke check failed: the full run was not started"; exit 1; }
import json, sys
rows = [json.loads(l) for l in open("raw_smoke.jsonl")]
ok = [r for r in rows if r["returncode"] == 0]
parsed = sum(1 for r in ok if json.loads(r["stdout"]).get("structured_output") is not None)
done = {d for r in ok for d in r["doc_ids"]}
need = {json.loads(l)["doc_id"] for l in open("docs_smoke300.jsonl")}
print(f"smoke: {len(ok)}/{len(rows)} batches ok, {parsed} with structured output, {len(done & need)}/{len(need)} documents")
sys.exit(0 if ok and parsed >= 0.9 * len(ok) and len(done & need) >= 0.95 * len(need) else 1)
PY
  echo "== $(date) full dev year"
  python3 claude_runner.py --docs docs.jsonl --out raw_full.jsonl --model sonnet --batch 40 --workers 4 || {
    echo "== $(date) full run stopped early: rerun ./run_extraction.sh to resume"; exit 1; }
  echo "== $(date) finished"
} >> run.log 2>&1
