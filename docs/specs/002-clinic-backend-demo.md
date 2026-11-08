# 002: The clinic backend demo (for Josie)

**Problem.** Josie, a Hong Kong GP, said on 7 Nov she might be interested in a clinic that runs on the agentic model.
Thomas said he would show her something on 8 Nov. Instagram is full of "AI agents for your GP practice" that bolt
admin automation onto one task. The demo has to show the other thing: the whole back office of a GP clinic run by
agents, with the doctor working from one queue. Josie needs to see (1) her own week's back-office work handled,
(2) that she stays in control and is safer, not less safe, (3) that it fits the Medical Council's telemedicine
guidelines, and (4) that it's real, not slides.

**History.** The first version (8 Nov, same PR) was a refill desk: three WhatsApp refill requests. Thomas asked to
step away from that and show a GP clinic AI backend. Refills became one input among many.

**Options considered.**

| Option | For | Against | Verdict |
|---|---|---|---|
| A. Replay the follow-up list (the product screen) | Real engine, real numbers | Results only; no patient side | Its engine items are now the core of the queue |
| B. Walk through the tmrw care line site | Polished | Marketing; she's a doctor, not a buyer | Updated, not shown first |
| C. Slides on decompiling the clinic | Quick | Static; can't touch it | No |
| D. A refill desk | Her most common visit | One lane of the back office | First version, replaced |
| E. **The clinic backend: one Sunday morning at the simulated clinic, with the engine's real open queue and three WhatsApp conversations in it** | Shows the whole back office and the one-queue idea; most of it is engine output with checked quotes | Two WhatsApp cases are illustrative | **Chosen** |
| F. A live model she messages as a patient | Biggest wow | Which model may touch HK patient data is still open | After she agrees to shadow mode |

**Change.** `demo/clinic-backend/index.html`, one self-contained, phone-first page:
1. **Since Saturday's session ended:** results read, criticals, recalls (all from the engine) and WhatsApp messages.
2. **Your queue at 09:30 on Sunday:** every item the engine had open, most urgent first, with the two WhatsApp-only
   cases placed by urgency. Mr Chan's critical potassium is joined to his WhatsApp request for more spironolactone;
   a toggle shows a message-only refill bot queueing it. Each engine item shows the engine's reasoning and quotes, and
   only the doctor's buttons close an item.
3. **How the backend works:** in, read, queue, you, watch, and the authority line.
4. **Tested before it touches a patient:** the year's totals from the run, LoopBench's committed results, and what it
   still misses.
5. **Your numbers:** results, messages and repeat-medicine visits, with the assumptions written out.
6. **The rules:** what agents may do, what stays the doctor's, fixed rules, remote repeats; what isn't solved.
7. **The front door:** after a one-off cough visit the doctor offers the care line, Ms Kwok scans a QR card, confirms
   who she is and consents in WhatsApp, and gets the signed plan, her documents and a menu of what she can do. Her
   day-3 check-in can be played as better (waits on the doctor's list to close) or worse (fixed rule, doctor alerted,
   into the queue).
8. **The task catalogue** (`tasks.json`, after Meta's Muse behaviour evals): the fixed list of what agents may do, in
   a doctor's view and a patient's view. A task is live only if it is on time in every committed LoopBench year with
   zero hard fails and zero made-up quotes; the page computes that from the results, and the list never states its
   own status. Today 3 of 7 scored tasks pass; the 8 WhatsApp tasks aren't scored yet, so the doctor approves every
   reply; red flags are a fixed rule; 6 things are never an agent's.
9. **The ask:** shadow her clinic for two weeks; nothing reaches a patient.

`build_data.py` takes a LoopBench product feed and the committed results for the same configuration, and writes `data.json` (the items open at Sun 13 Jun 09:30, their
cited records, Mr Chan's note, the weekend's and the year's counts, each task family's on-time rate per year, the catalogue), and embeds the same JSON in the page. It reads
the run file as the product's adapters do; no LoopBench import. `tests/test_demo_backend.py` checks that the page
embeds exactly that data, every quote is word for word in its record, no engine text is typed into the markup by
hand, the catalogue never states its own status and the gate matches the results, the care team's copy never claims a doctor's act, and only the doctor's decision box closes.

**Landing page (8 Nov).** For sending to doctors: a Design canvas, [tmrw one-page demo](https://claude.ai/artifact/7MzAFXNeTSNMJDLqnvS8Zw), built as a startup landing page: the promise ("Every patient followed up. More of them back with you."), a free-pilot call to action repeated down the page, simulation results labelled as such, the problem, three steps, four follow-up types with their Hong Kong value, why now, safety, pricing, objections and a sign-up form. The monthly price is a placeholder. It lives only in the canvas (`docs/INVENTORY.md`).

**Done when.** The page works at 390 px and 1440 px with no script errors; the tests pass; the authority line holds.

**Invariants touched.** None in the engine. Synthetic data only: the engine's items come from LoopBench; Mrs Wong and
Mrs Ho are illustrative and labelled that way.

**Out of scope.** A live model, a real WhatsApp number or QR code, WhatsApp intake in the engine, refill logic in the
engine, test cases for the WhatsApp tasks (`NOW.md` Next 7).

**Found while building.**
1. The engine's context step says "No relevant medicines dispensed at this clinic" for Mr Chan, whose note names
   lisinopril and spironolactone (`NOW.md` Next 4).
2. The engine runs at 08:30, 13:30 and 19:30. His critical potassium, posted at 19:40 on a Saturday, waited 13 hours
   (Next 5).
3. LoopBench realism: seed 1 refers a 28-year-old man to gynaecology, and a chloride printed at the top of its range
   (109 on 102-109) is flagged high, which the engine then repeats as "above the report's range" (Next 6).
