# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 9 Nov 2026.

**The system is frozen until 26 Nov.** The operating system, skills and templates change only at a Friday review.
Every session builds toward the milestone instead.

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: a discovery at one EC Healthcare clinic, or an Amber Health pilot on Apricot VITA. On the board's
Partners view, that is a card reaching "Next step in writing".

Toward the 26 Nov go/no-go, six partners are in conversation. Their latest signals: EC (#23), Josie (#20), Amber
(#6), Apricot (#15), Adrian (#8) and Virtus (#11). All of them point at doctors' workload, but no partner has yet
named the specific burden, so all six are at Talking, and that is the question for every next contact. The bets
behind the go/no-go, with odds set before testing and a pass line for each: `docs/BETS.md`.

## Waiting on you

Decisions and steps only Thomas can take, each with Claude's default and a date. Until he decides, work goes ahead on
the default, but nothing reaches a partner or a patient on a default.

- **The board.** Turn off Auto-close issue, add the `Stage` field and the Partners view (`docs/OPERATING_SYSTEM.md`,
  Install 5), and reopen #23, #20, #6 and #15, which closed when their cards moved to Done. Default: none. By Wed
  11 Nov, so the brief preps the EC meeting from an open #23.
- **Josie, 8 Nov.** Your notes on what she agreed to and her answers for bets 1 and 5, so #20 and `docs/BETS.md` get
  their results. Default: none. By Tue 10 Nov.
- **The pilot offer.** Landing v4 offers a free 2-week pilot with up to 30 patients on the care line; the Josie ask is
  shadow mode; `docs/BETS.md` runs bets 2 and 3 by hand, with no AI and no data leaving her clinic. Default: by hand,
  as `docs/BETS.md` plans; shadow mode waits for an agreed data route, and v4's pilot copy changes to match before it
  goes out. By Fri 13 Nov.
- **Price and who pays.** The pages say the clinic pays [PRICE] a month after the pilot and the line is free to
  patients. Default: keep that model, with the price off every page until bet 1 has a result. By Fri 13 Nov.
- **The Chinese pages.** Default: nobody sees them until a native reader has checked them. By Fri 13 Nov.
- **The rubric's gates** (Next 7). Default: the gates stand as drafted in spec 003. By Fri 13 Nov.
- **Four PRs outside the queue:** harness #13 (the product direction), #16 (the care line) and #18 (the self-service
  catalogue), stacked in that order, and loopbench #5 (the self-service suite, from #18's spec). #13 and #16 use spec
  numbers 002 and 003, which main has already taken. Default: Claude renumbers them and rebases the stack, and you
  merge, close or park each at the Friday review. By Fri 13 Nov.
- **In progress 1 is done:** the Apricot call happened on 6 Nov (#15). Default: Next 1, the VITA profile update, takes
  its place. By Fri 13 Nov.
- **The EC discovery's fee.** Default: the one-page scope (`docs/EC_DISCOVERY_SCOPE.md`, drafted 10 Nov) names a
  fixed fee you set, and nobody names one at the meeting. By Thu 12 Nov, when the scope goes out.

## In progress (three at most)

1. **Apricot call, 6 Nov.** Learn how VITA really works (`loopbench/docs/APRICOT_DEMO_PREP.md`). Studio polish for the
   call: merged 6 Nov (loopbench #2); republish Studio before the call. After the call: a signal note, then the VITA
   profile update below. Bet: none in `docs/BETS.md`; it serves the Amber pilot on VITA.
2. **EC Healthcare: exploratory, Julie on Wed 11 Nov at 4pm** (#23). Bets 6 and 7. Thomas isn't formally engaged,
   so the ask needs no patient data: a follow-up with a clinic lead and IT, then a two-to-three-week discovery at one
   clinic. The demo is the care line and the clinic backend on EC's EMR (`demo/ec-care-line/`); the prep is
   `docs/EC_MEETING_PREP.md`. The look-back becomes the safety analysis once EC is ready; its report waits on the
   branch `ec-phase0-report`, and the data request (`docs/EC_DATA_REQUEST.md`) waits with it.

3. **Josie demo, 8 Nov: the clinic backend** (spec 002, `demo/clinic-backend/`). Bets 1 to 5. Josie, a GP, said on 7 Nov she
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

Done 9 Nov: **product management runs on four lists** (`docs/OPERATING_SYSTEM.md`, Product management): customers,
bets, work and the decisions waiting on you, each with one home. `tests/test_queue.py` checks the queue's shape, and
the brief checks the rest each morning.

## Next, in order

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
   each with what the agent may and must never do, hours, who it escalates to, hard fails and a gate). Write the
   scripted and red-team cases for the 15 unscored rows first (join, plan, check-in, questions, sending documents,
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
