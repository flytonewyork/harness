#!/bin/bash
# Follow-up extraction with claude -p (LoopBench dev documents, synthetic patients only), on the Mac mini.
# Lives in ~/loopbench-data/extract beside claude_runner.py, prompt.py, docs.jsonl and docs_smoke300.jsonl
# (from `loopbench docs`). Start it from Terminal there: nohup ./run_extraction.sh &
# 1) one test batch, 2) the 300-patient smoke set, gated, 3) the full dev year, resumable. Logs go to run.log.
set -u
cd "$(dirname "$0")"
claude auth status 2>/dev/null | grep -q '"loggedIn": true' || { echo "Claude CLI is logged out: run 'claude', then /login, then rerun this."; exit 1; }
{
  echo "== $(date) test batch"
  python3 claude_runner.py --docs docs_smoke300.jsonl --out raw_smoke.jsonl --model sonnet --batch 40 --workers 1 --limit-batches 1 || exit 1
  echo "== $(date) smoke set (300 patients)"
  python3 claude_runner.py --docs docs_smoke300.jsonl --out raw_smoke.jsonl --model sonnet --batch 40 --workers 4 || exit 1
  echo "== $(date) smoke check"
  python3 - <<'PY' || exit 1
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
  python3 claude_runner.py --docs docs.jsonl --out raw_full.jsonl --model sonnet --batch 40 --workers 4
  echo "== $(date) finished"
} >> run.log 2>&1
