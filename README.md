# harness

The tmrw follow-up harness: the product. It finds the loops a clinic leaves open (abnormal results,
tests that never came back, hospital and imaging follow-ups, positive screens, referrals, drug monitoring,
planned reviews) and puts each one, with its reasoning and evidence, in front of the doctor who owns it.

The test bed is [LoopBench](https://github.com/flytonewyork/loopbench): a simulated Hong Kong clinic year
with a hidden answer key. LoopBench imports this repo's engine, so every change here is scored in the
simulator before it touches real data.

```
backend/          Python 3.12+, FastAPI, Pydantic
  engine/         follow-up engine: rules (v1.0 floor, v1.1), escalation, agentic layer
  extract/        free-text follow-up extractor; pluggable model (rules, claude -p, replay cache)
  lookback/       Phase 0 look-back over a clinic's export
  adapters/       data in: LoopBench runs, EC-style export
  api/            items, evidence, actions (agent: open or raise only; doctor closes)
frontend/         React + TypeScript (Vite): the doctor's follow-up list
docs/             operating system, decisions, specs, EC data request
NOW.md            the one queue for this repo and LoopBench
```

**How work gets done:** [`docs/OPERATING_SYSTEM.md`](docs/OPERATING_SYSTEM.md). Every session starts from
[`NOW.md`](NOW.md) and ends in a pull request that LoopBench has scored.

## Rules that hold everywhere
- The agent can only **open** an item or **raise** its urgency. Only a doctor closes one.
- Every item carries a reasoning trace; quotes must appear word for word in the cited record.
- The product never imports the test bed (`tests/test_engine.py` checks this).

## Run
```
pip install -e ".[dev]"        # or put this repo on PYTHONPATH
pytest -q
```

**The follow-up list** (API + React screen), fed by a LoopBench year:
```
cd ../loopbench && PYTHONPATH=src python -m loopbench.cli product-feed --clinic hk_solo --system apricot_vita \
    --harness agentic --extractor replay:results/extract/raw.jsonl --docs results/extract/docs.jsonl --seeds 1
cd ../harness/frontend && npm install && npm run build          # once; the API serves frontend/dist
cd .. && python -m backend.api --run ../loopbench/results/product/<run>.json      # http://127.0.0.1:8000
```
The screen replays the recorded year: pick a moment (Mr Chan's critical potassium, the day-7 raise, the day-14
copy to the locum, the PathLab outage) or any date. `--lookback <out dir>,<export dir>` shows a Phase 0
look-back's flags instead.

**Phase 0 look-back** on an export in `backend/lookback/schema.py` format (what `docs/EC_DATA_REQUEST.md` asks for):
```
python -m backend.lookback --export <dir> --out <dir> [--extractor replay:<raw.jsonl> --docs <docs.jsonl>]
```

**Free-text extraction with `claude -p`** (on a machine where the Claude CLI is logged in; standard library only):
```
python3 backend/extract/claude_runner.py --docs docs.jsonl --out raw.jsonl --model sonnet --batch 40 --workers 4
```
On the Mac mini the inputs sit in `~/loopbench-data/extract` (the runner, `prompt.py`, and `docs.jsonl` plus
`docs_smoke300.jsonl` from `loopbench docs`). From Terminal there, `./run_extraction.sh` checks the login and runs
one test batch on screen, then carries on in the background: the 300-patient smoke set (the full run starts only if
it comes back clean), then the full dev year, logging to `run.log`. Rerun it to resume; it won't start a second
copy. Start it from Terminal: a background connection may not see the CLI's login.
Outputs are cached by prompt version, model and document hash, so scoring runs replay them without calling a model.
