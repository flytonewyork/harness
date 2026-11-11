# Phase 1 close-out: test bed and discovery (5 Nov to 26 Nov 2026)

> Moved into git from the Claude Project on 6 Nov 2026, unchanged below this note. It is the plan to the 26 Nov
> go/no-go; `NOW.md` is the live queue.

This phase ends on 26 Nov 2026 with a go/no-go on which follow-up problem to build for. Only the build below is needed to close it.

## What the simulator is for

1. **Showing clients** the harness working on one patient's year, the off-versus-on number, and failing safely.
2. **Evals and verification:** every harness change is scored against the hidden answer key before it touches real data.
3. **The first stage of sim-to-real.** Get the full agentic harness running end to end and tune its evals on simulated patients. Then apply it to one narrow slice of real practice (one kind of follow-up at one clinic) to get real-world evals and retune.

Guardrails:
- Tune on the dev split only. The test split and the clinician's held-out scenarios stay untouched until a pre-registered run.
- Before each real slice, write down what the simulator predicts for it. The gap between that prediction and the real result is the main real-world eval: it shows how far to trust the simulator for the next slice.
- A simulator pass is a gate to go real, not proof of safety.

## Stack decision (5 Nov)

The product is a Python backend with a React frontend, the same stack Heidi's Clinical AI Engineer ad names. The work is split across two private tmrw repos:

```
loopbench/          test bed (github.com/flytonewyork/loopbench, private)
  sim, answer key, scorer, Studio viewer (stays vanilla JS)
harness/            the product (github.com/flytonewyork/harness, private)
  backend/          Python 3.12, FastAPI, Pydantic
    engine/         follow-up engine: rules v1.0 (frozen floor), v1.1, escalation, agentic layer
    extract/        free-text follow-up extractor; pluggable model
    lookback/       Phase 0 runner and the export format
    adapters/       data in: LoopBench runs, look-back output; later EC's live system
    api/            items, evidence, actions (agent: open or raise only; doctor closes)
  frontend/         React + TypeScript (Vite)
    follow-up list, item evidence, feed-status banner, the patient's year
```

LoopBench imports the engine through `harness_api`, so every change to the product is scored in the simulator first.

"Complete" (real connectors, hosting inside EC's environment, login, multiple clinics) waits for a go decision and for EC IT's requirements, since EC runs its own clinical software.

## Where things stand (5 Nov, end of day)

Build items 0 and 2–8 are done, except the model run in items 2–3. The Mac mini's Claude CLI is logged out, so the `claude -p` extraction is staged but not yet run. One command starts it once the CLI is logged in: `loopbench-data/extract/run_extraction.sh`. Item 1 waits for the Apricot demo on 6 Nov; the demo prep has fresh sweep numbers, and the published Studio shows the updated world.

**Links:**
- The follow-up list, shareable with no server: https://claude.ai/artifact/LbR3fsj99mNgnVAPGCSRKR
- The data request for Julie: https://claude.ai/artifact/KKX86CZh4miqqAs4TC37tn

Both are private until shared.

**Results** (dev split, 3 seeds; harm-weighted missed follow-ups per 1,000 patient-years, harness off → on)

| Setup | Off | Rules v1.0 | Rules v1.1 | Agentic (regex extractor) | Scenarios passing all 3 seeds (v1.0 / v1.1 / agentic) |
|---|---|---|---|---|---|
| Solo GP, ClinicSolution X | 192.4 | 16.7 | 15.5 | 13.7 | 11 / 11 / 12 of 22 |
| Solo GP, Apricot VITA | 192.4 | 25.4 | 24.4 | 21.3 | 11 / 12 / 13 of 22 |
| Group practice, Apricot VITA | 224.5 | 27.1 | 26.9 | 24.3 | 17 / 17 / 17 of 22 |

- **Safety:** 0 automatic fails for all three harnesses. 98.5–99.9% of items pass every platform check. The noisy control makes the clinic 29–58% worse, as it should.
- **Money:** the agentic harness recovers 274–301 consultations per clinic-year, HK$108k–157k. The fee band is now sourced: HK$400–540 is the median private GP consultation fee for non-specialists and specialists in a March 2025 survey (Hong Kong Med J 2026;32:354–6). Co-care visits are valued at HK$316 (HK$166 subsidy plus the HK$150 recommended co-pay).
- **Phase 0 on a simulated EC export:** it finds 96–98% of the missed follow-ups the clinic's own records can settle, and 97–98% of its flags are correct. That is a pass on all 9 runs (3 setups × 3 seeds).
- **HA referrals:** a look-back on clinic data cannot settle them, because the booking shows only in eHealth. They are listed for an eHealth check: 609 of the 640 missed ones were on that list.
- **Extraction (regex floor):** on the simulated notes, 98.7% recall and 100% precision. Synthetic text is easy for a regex, so the model's edge has to show on varied or real text and on Cantonese. The model run is the next step.

These are simulation results, not a claim about real-world safety. The numbers changed from the morning's because the world changed. The clinician plan sentences are now in use. The patient's own visit counts even while a reminder is in flight. A later chronic visit counts as the review. There are 3 new scenarios. Each change has a dated note in `loopbench/docs/PROTOCOL.md`.

**Known gaps**
- The model extraction run is pending: the Claude CLI needs a fresh login on the Mac.
- Four realism checks fail and are reported, not tuned: V3 investigations per consult (12.4% against 5–10%), V5 mean consult age, V6 hypertension and V8 diabetes prevalence.
- Co-care exists as two scenarios, not yet as population-wide recall loops.
- `CALIBRATION.md` is not written, and MIMIC-IV is blocked.

**Also exists:** clinic-agent-evals (32 single-task cases, with API, `claude -p` and OpenAI-compatible runners), heidi_eval, and clinical_eval (not yet committed).

## Exit criteria for 26 Nov

| # | Criterion | Status (5 Nov) |
|---|---|---|
| 1 | **A go/no-go on the problem**, from 5–6 logged conversations. See the doc "Hong Kong primary care: problems ranked by size". | Thomas's conversations; not started here. |
| 2 | **The agentic harness runs end to end in LoopBench:** documents in, LLM extraction, engine, escalation, the follow-up list. It is scored against the rules baseline on the dev split, with 0 automatic fails. | Runs end to end with the regex extractor, 0 automatic fails. The `claude -p` run needs the Mac's CLI login. |
| 3 | **Phase 0 ready to run within a week of EC granting data access.** The pipeline passes on a simulated EC-style export. | Done: passes on all 9 runs. The data request is ready to send. |
| 4 | **The scorer reports money as well as harm:** recovered visits × fee. | Done. |
| 5 | **The demo runs on the product's own screen:** Mr Chan's year with the harness off and on (including the day-7 and day-14 escalation), the PathLab outage, and the headline number, all shown in the React follow-up list. | Done in the React list, which can also be shared as a link. Mr Chan's HbA1c commitment ("HbA1c in 3 months") needs the model extractor: the regex floor misses it. |

## Remaining build, in order

0. **Code homes.** Done. Both repos are private under flytonewyork. The rules engine lives in `harness/backend/engine`, and LoopBench calls it through `harness_api`. The 16.94 headline was unchanged by the move.
1. **After 6 Nov:** update `loopbench/systems/apricot_vita.yaml` with what Apricot confirms.
2. **The free-text extractor** (`harness/backend/extract/`). Done; the model run is pending.
   - Three models: a regex floor, a replay cache, and `claude -p` (with a stdlib batch runner for the Mac).
   - The prompt is `fu-extract-v2`; English and Cantonese count.
   - Every quote is checked word for word against its document, and repaired or dropped.
   - Outputs are cached by prompt version, model and document hash.
   - It is scored against the answer key with `loopbench extract-eval`.
   - The 4,064 documents (dev, 3 setups × 3 seeds) are staged on the Mac with a 300-patient smoke set.
3. **The LLM harness runner in LoopBench.** Done for the regex floor.
   - `loopbench run --harness agentic --extractor rules|replay:<raw>` runs a full year.
   - The agentic layer is rules v1.1 plus commitments read from notes and letters, so the rules stay the floor.
   - Next: smoke the model on 300 patients, then run the full dev year, then score it against rules v1.0, v1.1 and the regex floor.
4. **The Phase 0 look-back** (`harness/backend/lookback/`). Done.
   - a. **The data request:** a shareable doc (link above), with a copy at `harness/docs/EC_DATA_REQUEST.md`. It is one page for Julie and EC's IT lead. It covers 11 tables, opaque HMAC keys, birth year only, and optional de-identified free text. A test checks it lists every field the runner reads.
   - b. **The simulated export:** `loopbench lookback` writes it in exactly that format.
   - c. **The runner:** `python -m backend.lookback`. It covers abnormal results, tests never resulted, referral replies, positive FIT, abnormal mammograms, monitoring bloods, planned reviews, committed tests and hospital follow-ups. Each is reported as unclosed, late or "check eHealth". Unfiled results are linked back by request number.
   - d. **Pass criterion:** met on all 9 runs (recall 0.96–0.98, flags correct 0.97–0.98).
5. **A revenue line in the scorer.** Done. It counts recovered consultations net of any lost to overload, by family, at the fee band, with Co-care at the scheme rate.
6. **The follow-up kinds most likely to win.** Done.
   - Already there: positive FIT (3 scenarios) and referrals (an urgent referral with no booking).
   - New: `dev_mr_chan`, which includes an unanswered eye-screening referral.
   - New for Co-care: screened but never back for the diagnosis visit; and a treatment-phase visit that lapsed, with the plan only in free text.
7. **The doctor's follow-up list** (FastAPI + React). Done.
   - It shows the list as it stood at any moment.
   - Each item is reviewed like a pull request: the reasoning steps sit beside the original document, with the quoted lines highlighted, plus the seven platform checks.
   - A lead-off banner appears when a feed is down, with "not an all-clear" if the list is empty.
   - There is a patient's-year panel with a with/without table.
   - Agent endpoints can only open or raise; only the doctor endpoint closes. Tests check this.
   - It builds as a page with no server behind it (`VITE_STATIC=1`), published at the link above.
   - Restyle it to EC's screens once there are screenshots.
8. **A demo check.** Done; screenshots taken. These show in the list:
   - Mr Chan's critical potassium on a Saturday night (paged Sunday 09:30);
   - the unanswered referral raised on day 7, then copied to the locum on day 14 while Dr Chan is on leave;
   - the PathLab outage banner;
   - the headline and money.

   Rerun it after the model run, so the HbA1c moment appears too.

## Deferred until after a go decision

- The wider reasoning layer (hidden diagnoses, knowledge graph), until a real slice shows what it should be tuned on
- Porting the 32 clinic-agent-evals cases as task-level tests (their runners are reused now)
- Real connectors to EC's live system
- Hosting, login and deployment inside EC's environment (their IT sets the requirements)
- Scenarios beyond the winning kinds
- `CALIBRATION.md`
- 3D diorama polish
- MIMIC-IV

## After 26 Nov

- **Go:**
  1. Pick the narrow real slice: one kind of follow-up at one clinic.
  2. Write down the simulator's prediction for it.
  3. Run the follow-up work by hand there for two weeks, then run Phase 0 on real EC data.
  4. Measure the gap between prediction and reality, recalibrate LoopBench, and only then complete the product: connectors, hosting and login inside EC's environment.
- **No-go:** take on EC's AI rollout and new-practice work, and put the harness in the parking lot until the 1 Feb review.
