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
docs/             architecture, EC data request
```

## Rules that hold everywhere
- The agent can only **open** an item or **raise** its urgency. Only a doctor closes one.
- Every item carries a reasoning trace; quotes must appear word for word in the cited record.
- The product never imports the test bed (`tests/test_engine.py` checks this).

## Run
```
pip install -e ".[dev]"        # or put this repo on PYTHONPATH
pytest -q
```
