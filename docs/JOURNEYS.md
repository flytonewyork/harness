# Patient journeys: the shared contract

One engine, many journeys. Each journey teaches the engine one more part of the clinic's follow-up work (post-checkup
results, insurance paperwork, inpatient and outpatient follow-on, medication changes and repeats, the day-3 care-line
follow-up, precare). They share one safety model and one eval.

## Product side (this repo)
- `backend/journeys/<name>/__init__.py` exports `JOURNEY` (a cooperative mixin over the engine), `FAMILIES`
  ({family: description}), `NAME` and `SUMMARY`.
- The mixin may override `reset`, `on_cms`, `on_portal`, `tick` and must call `super()` first in each. It may override
  `claims(api, r)` (return True, or `super().claims(...)`) for a record it reads more fully than the core rule
  (discharge summaries, patient messages); the core's simpler rule then skips it, and the record still reaches every journey.
- `backend.journeys.suite(*names)` builds one engine: `Suite(Push, *journeys, AgenticHarness)`. `Push` lets the care
  line deliver patient messages as they arrive, between the three scheduled runs.
- **Port unchanged:** reads, plus open_item / raise_urgency / alarm / attest_all_clear. No close, dismiss, downgrade or
  reassure, and no messaging from the engine: a journey may suggest a fixed template in the request step for the doctor.
  `tests/test_journeys.py` checks every journey's source against this list.
- Rules v1.0 and v1.1 are untouched; with no journeys loaded the engine behaves exactly as before.

## Benchmark side (loopbench)
- `src/loopbench/journeys/<name>.py` registers its families (`register_family(name, needs_patient, verb, downstream,
  compliance)`) and scenario types (`@scenario_type("type")`).
- Scenarios in `scenarios/journeys/<name>/*.yaml` (out of `scenarios/dev`, so the main scoreboard does not move). Each:
  `id`, `type`, `what` (one line, printed in the eval report), `traps`, optional `expect` (`min_urgency`, `max_urgency`,
  `must_quote`, `reason_contains`, `family`, `raised_before_due`, `max_items`, `quiet`). `requires: [capability]` (any key of
  `system.capabilities`, e.g. `ehealth_in_cms`) marks the scenario n/a on setups without it. A file with `blind: true`
  holds scenarios written after the reader was frozen: its first run is kept (`results/journeys/*_firstrun.json`) and
  reported as is; fixes go in, but new blind phrasings go in a new file.
- Channel `careline`: the product's own WhatsApp line, always visible to the engine (assumes the care line is installed).
- `scripts/journey_eval.py --journey <name>|all --seeds 1 2 3`: 3 clinic setups x seeds, no harness vs rules v1.1 vs the
  journey engine; gate = every scenario passes in every run, zero hard fails, zero made-up quotes. CI runs it on every PR.
