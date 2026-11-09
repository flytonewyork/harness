# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 9 Nov 2026 (care line).

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: EC Healthcare's Phase 0 look-back on their export, or an Amber Health pilot on Apricot VITA.

Toward the 26 Nov go/no-go, 4 partner conversations are logged: EC (#5), Amber (#6), Adrian (#8) and Virtus (#11).
Today's Apricot call (#7) makes 5. All of them point at doctors' workload, but no partner has yet named the specific
burden, so that is the question for every next contact. The bets behind the go/no-go, with odds set before testing
and a pass line for each: `docs/BETS.md`.

## In progress (three at most)

1. **Apricot call, 6 Nov.** Learn how VITA really works (`loopbench/docs/APRICOT_DEMO_PREP.md`). Studio polish for the
   call: merged 6 Nov (loopbench #2); republish Studio before the call. After the call: a signal note, then the VITA
   profile update below.
2. **EC Healthcare Phase 0 look-back.** The request is ready (`docs/EC_DATA_REQUEST.md`). Julie is arranging a call
   with EC's CTO (#5). Use it to find the burdens in EC's own system, then agree the Phase 0 scope, fee and data route.

3. **Josie demo, 8 Nov: the clinic backend** (spec 002, `demo/clinic-backend/`). Josie, a GP, said on 7 Nov she
   might be interested in a clinic running the agentic model. The demo shows the simulated clinic's back office at
   09:30 on a shut Sunday: the engine's real open queue (Mr Chan's critical potassium on top, joined to his WhatsApp
   request for more spironolactone) plus two WhatsApp cases, one queue for the doctor, how it works, the evidence and
   the rules, the patient's front door (QR after the consult, day-3 check-in) and the task catalogue with each
   task's gate status computed from LoopBench. It ends with the ask: shadow her clinic for two weeks, with nothing reaching a patient. After the
   meeting: update the `signal` issue (#20), then decide what she agreed to. The doctors' landing page
   (`demo/landing/`) is saved as v2; v3 (9 Nov) tests the front-desk menu and opening hours; v4 splits it into
   for clinics and for patients, each in English and Chinese. Its live 2-week pilot doesn't match the shadow-mode ask: pick one before either
   goes out. v4 is public on Vercel since 9 Nov (`tmrw-landing.vercel.app`, `scripts/build_landing.py --deploy`),
   with a privacy policy at `/privacy`; its pilot form sends nothing yet, so wire it up or take it down.

Done 6 Nov: **the operating system is locked in.** Its test was a task started in Slack coming back as a scored PR, and
#10 did (the seven checks in the product). What it set up: Slack with the Claude and GitHub apps; skills and a cloud
setup script in both repos; cloud sessions as the default; CI green with the scoreboard gate; the weekday brief; and
partner contacts as `signal` issues. How it works: `docs/OPERATING_SYSTEM.md`.

## Next, in order

0. **WhatsApp care line, after the visit** (spec 004, `backend/careline/`, 9 Nov). Built and running in sim:
   join by QR, written plan (doctor approves), bookings, "is my result back?", repeat requests, day-3 check-in,
   red flags, against the simulated clinic, with 63 eval cases on a dashboard (`/careline`). Next, Thomas, on the Mac
   mini: run `scripts/careline_mac_mini.sh setup` (asks for the Meta values, checks them, installs a
   background service in tester mode), point the tunnel and Meta's
   webhook at it, scan `/join` from a tester's phone. Then: held-out red-team cases written by someone else, an
   approved template for the check-in, and the shadow-mode protocol. Live use waits for the decision in
   `docs/DECISIONS.md` (Open, 9 Nov).
1. Update the VITA profile from the 6 Nov call (`loopbench/systems/apricot_vita.yaml`); fields still marked unverified
   stay marked.
2. Put clinic-agent-evals into git (loopbench). The zip isn't on the Mac mini; it is most likely on the MacBook Pro
   (`docs/INVENTORY.md`). Porting its tool contract and 32 cases waits for the 26 Nov go decision
   (`docs/PHASE1_CLOSEOUT.md`, Deferred).
3. Run the seven platform checks inside the product on every item it opens: the observability real data needs,
   before the EC look-back runs on a real export. Part done (#10, 6 Nov): the product has its own copy of the checks
   (`backend/engine/verify.py`, spec 001). Left: wire it into the EC look-back driver, and add a parity test that
   runs both copies on the same items, so the product's checks and LoopBench's can't drift apart.
4. Repeat dispensing in the LoopBench world model, so reasoning traces can cite current medicines. The clinic backend
   demo shows why: for Mr Chan the engine says "No relevant medicines dispensed at this clinic" while his note names
   lisinopril and spironolactone.
5. Critical results out of hours. The care line keeps the clinic's hours for patients (spec 003), but labs post at
   any hour. The engine runs at 08:30, 13:30 and 19:30, so Mr Chan's potassium of 6.6, posted at 19:40 on a
   Saturday, waited 13 hours. Add an overnight check that pages the doctor the clinic names (rubric row RT-07); score
   it in LoopBench as time from posting to page.
6. LoopBench realism bugs: seed 1 refers a 28-year-old man (FUNG Tai Man) to gynaecology (the referral generator's
   sex check), and a chloride printed at the top of its range (109 on 102-109) is flagged high, which the engine
   repeats as "above the report's range".
7. Task catalogue as the agents' allowlist, with its eval rubric (spec 003, `evals/agent_features.csv`: 54 features,
   each with what the agent may and must never do, hours, who it escalates to, hard fails and a gate). Part done
   9 Nov (spec 004): 63 scripted, red-team and fixed-reply cases cover 34 rows, and the message-side checks run on
   every reply (`evals/careline/`). Left: the scripted and red-team cases for the 15 unscored rows first (join, plan, check-in, questions, sending documents,
   repeats, booking, copies), then the message-side checks, then a shadow-mode protocol. Thomas signs off the gates.
   Then move the list from the demo into the product.
8. Doctor-minutes metric: review time per item with and without verified evidence.
9. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model. Staged in
   `~/loopbench-data/extract`: 4,064 dev documents, still current on 6 Nov, with prompt `fu-extract-v2`. Not started
   as of 6 Nov 14:45: the Mac mini's command-line Claude reads as logged out, and its saved login hadn't changed since
   that morning, so the 13:30 login didn't reach it. Thomas, in Terminal on the Mac mini: `claude`, then `/login`;
   then `cd ~/loopbench-data/extract && ./run_extraction.sh`, which checks the login and runs a test batch on screen,
   then carries on in the background. Scored honestly since 6 Nov: a document the model never answered counts as
   missed, not as the rules floor's answer (#12, loopbench #4).
10. LLM harness producing traces against the same seven checks.
11. Capacity-frontier experiment.
12. Thomas: held-out scenarios (outside both repos) and sign-off of `loopbench/data/clinical_policy.yaml`.

## Parked until the 1 Feb 2027 review

- Cloud Doctor's autonomy ladder above rung 1, until the open decision in `docs/DECISIONS.md` is made.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval on a branch, then leave it. It isn't on the Mac mini; most likely
  `~/heidi_eval/clinical` on the MacBook Pro.
- New tools and frameworks.
