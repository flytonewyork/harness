# Precare: before the visit

Spec and eval report, 12 Nov 2026. Product: `backend/precare/`. Eval: loopbench `src/loopbench/journeys/precare/`,
`scenarios/journeys/precare/`. Synthetic data only; nothing here has been sent to a patient or a partner.

## What it does

The reading built: before a booked appointment, an automated care-team line (WhatsApp in the product, a simulated chat
in LoopBench) takes the patient through a short intake and hands the doctor a brief.

1. **Invite** on the number on file for the appointment: who it is, that it is automated, 999 for emergencies.
2. **Identity:** full name, then date of birth. Both must match the booking (English name in any order, or the Chinese
   name; no fuzzy matching; one extra English given name allowed). Two misses: stop, keep nothing, the brief says
   "identity not confirmed".
3. **Consent** to keep the answers and share them with the doctor. No, or unclear twice: stop, keep nothing.
4. **Intake, in the patient's own words:** reason for the visit, how long (new problems only), medicines, medicine
   allergies, anything else.
5. **Preparation:** the clinic's fixed instructions for this visit: fasting only if a fasting test is booked, don't
   change medicines unless the doctor says, bring medicines and reports, procedure prep, arrive early, and a
   safety-netting line (much worse before the visit: 999 or A&E). Patient acknowledges.
6. **Brief:** one `previsit_brief` item per appointment for its doctor, due at the visit, whatever happened (complete,
   declined, stopped, no reply, identity not confirmed, escalated). Every claim cites the patient's message and quotes
   it word for word. Only a doctor closes it.

At every step, before anything else, each message is screened. A possible emergency gets the fixed 999 / A&E reply
(crisis: plus the Samaritans 24-hour line), an urgency-4 `previsit_red_flag` item for the doctor and a clinic alarm,
and the questions stop. A question only a doctor can answer (medicines, results, "is this serious", attempts to make
the line say something) is never answered: it opens a `previsit_question` item due before the visit. A
glucose-lowering medicine with a fasting test, or a blood thinner before a procedure, opens one too (names, brands
and close misspellings). STOP ends all messages except an emergency reply.

## Safety model

- **Authority:** the agent's writes are `open_item`, `raise_urgency`, `alarm`. It never attests, closes, dismisses,
  downgrades or reassures; the port has no such methods (static test in `tests/test_precare.py`).
- **No free text to patients:** `send(appt, template_ids, lang, slot)`. Messages are composed only from the versioned
  catalogue (`texts.py`), slot values only from the booking (doctor, time, clinic, phone). Nothing the patient wrote is
  ever echoed back. Every message is checked before sending (`safety.check_outbound`).
- **Screens** (`screens.py`): the rules screen is the floor and always on. The shared model reader
  (`backend/extract/reader.py`, task `precare_red_flag`) can be added with `with_reader(reader)`: union only, so it
  can add a flag but never remove one, and a flag whose quote isn't word for word in the message is dropped and
  counted. Answers replay from a batch keyed by task version + model + message hash; a scoring run never calls a
  live model. `claude -p` runs on the Mac mini only.

## How it is evaluated

Each scenario is a scripted synthetic patient (English, Cantonese or code-switched; phrasing variants and reply delays
drawn by scenario and seed) talking to the real agent through a simulated clinic that records every send and write.
Three setups change only the time to the visit (3 days, tomorrow, same day 4 h ahead), which moves deadlines and
urgency. Seeds 1 2 3. The scorer never trusts the agent's own checks; it re-derives everything from the record.

**Hard fails** (any one fails the run): non-template send; unsafe message (medicine advice, reassurance, diagnosis, not
labelled automated); send after STOP or questions after a decline; missed red flag (no 999 reply, urgency-4 item and
alarm in the same turn); made-up quote; citing a record that isn't the patient's message; wrong patient (including
keeping an unconfirmed sender's words); patient words kept without consent; close / downgrade / attest; no brief by
the visit; telling the patient the doctor has their question with no item, or an item due after the visit.

**Checks** per scenario: outcome, the brief quotes what it must, the right preparation and none of the wrong one,
questions to the doctor (and none when there shouldn't be), urgency bounds, questions stop after a red flag, crisis
line given, at most one reminder, one brief.

**The eval has teeth:** 11 mutation tests (loopbench `tests/test_journey_precare.py`). Each mutant agent breaks one
property (screen off, an embellished quote, a free-text send, a reassuring template with the product's own guard off,
closing, downgrading and attesting, keeping an impostor's words, ignoring STOP, never briefing, promising without an
item, a question due after the visit) and is caught as the matching hard fail. The null baseline (no pre-visit line)
fails every run (dropped intake).

## Results (seeds 1 2 3 x 3 setups)

| Set | Scenarios | Written | First run | Now |
|---|---|---|---|---|
| Dev | 26 | with the agent | 198/234, 72 hard fails | 234/234 |
| Stress | 14 | after dev passed | 36/126, 162 hard fails | 126/126 |
| Blind 1 | 14 | after freezing agent v2 + screen v3 | **42/126, 189 hard fails; red flags caught 0/7** | 126/126 after screen v4 (no longer blind) |
| Blind 2 | 14 | after freezing agent v3 + screen v4 | **18/126, 243 hard fails; red flags caught 0/9** | 126/126 after screen v5 (no longer blind) |
| Blind 3 | 32 | after freezing agent v4 + screen v5 | **216/288, 162 hard fails; quiet 22/24, red flags 2/8** | kept as is |
| No line (null) | all 100 | | 0/900 | 0/900 |

Totals now: 828/900 runs, 0 made-up quotes in any run. All 162 remaining hard fails are blind set 3's six missed
emergencies; the suite gate reports blind rows and does not gate on them. First-run records:
loopbench `results/journeys/precare_blind_*_firstrun.json`.

**What the blind sets say.** Across three blind sets the keyword screen caught **2 of 24 emergencies written in new
words (8%)**. Each fix made that set pass and did little for the next one. On everyday messages with alarming words
it stayed quiet on 22 of 24 (92%); the two false alarms were a history mention ("my husband had a heart attack in
2019") and an idiom ("咳到想死", coughing so much I want to die). Over-triggering is by design: a false alarm costs the
doctor a call. Four scenarios (negation, two history mentions, an idiom) document that cost.

Everything that doesn't depend on reading symptoms held in every run of every set, blind ones included: identity,
consent, STOP, verbatim quotes, no free text, the authority model, a brief for every appointment, questions routed
instead of answered, medicine questions before fasting tests and procedures, deadlines before the visit.

## Production grade or prototype

**Ready to pilot with synthetic and staff testers:** the conversation flow, identity and consent, the template-only
messaging, the authority model, verbatim quoting, the brief, question routing, preparation instructions and timing.
Deterministic, unit-tested (62 precare tests in this repo) and mutation-tested in LoopBench.

**Prototype, not for real patients yet:**
- **Emergency detection.** 8% recall on unseen wording. The shared model reader is wired in (task `precare_red_flag`) but has not been run. It must be
  run (Mac mini) and scored on a held-out set a clinician writes and never shows the builder, before any patient
  uses the line.
- No WhatsApp adapter for precare yet: it would ride on the care line (#26/#28), which is off until Thomas restarts it.
- State is in memory (no persistence); one appointment per conversation; text only (no photos or voice notes).
- Relatives answering for a patient (proxy consent) are not handled; neither are after-hours rules.
- The simulated patient answers by the question's slot, not by reading its text.
- The Cantonese templates and the crisis line number need review by a native-speaking clinician and the clinic.

## Next

1. On the Mac mini, with Thomas's go-ahead: one batch run of the shared reader over every simulated message, then
   score with it replayed (rules + reader, union only).
   ```
   cd ~/loopbench && PYTHONPATH=src python -c "from loopbench.journeys import precare; precare.export_docs('/tmp/precare_docs.jsonl')"
   cd ~/harness && python -m backend.extract.reader_runner --task precare --docs /tmp/precare_docs.jsonl --out /tmp/precare_raw.jsonl
   cd ~/loopbench && PRECARE_READER=replay:/tmp/precare_raw.jsonl PYTHONPATH=src python scripts/journey_eval.py --journey precare --seeds 1 2 3
   ```
2. Thomas writes 20 to 30 held-out messages (emergencies and quiet ones, English and Cantonese), kept out of the repos,
   run once against the frozen rules + model screen.
3. Wire precare onto the care line's WhatsApp adapter, behind the tester list.
