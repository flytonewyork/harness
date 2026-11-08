# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 8 Nov 2026.

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: EC Healthcare's Phase 0 look-back on their export, or an Amber Health pilot on Apricot VITA.

Toward the 26 Nov go/no-go, 4 partner conversations are logged: EC (#5), Amber (#6), Adrian (#8) and Virtus (#11).
Today's Apricot call (#7) makes 5. All of them point at doctors' workload, but no partner has yet named the specific
burden, so that is the question for every next contact.

## In progress (three at most)

1. **Apricot call, 6 Nov.** Learn how VITA really works (`loopbench/docs/APRICOT_DEMO_PREP.md`). Studio polish for the
   call: merged 6 Nov (loopbench #2); republish Studio before the call. After the call: a signal note, then the VITA
   profile update below.
2. **EC Healthcare Phase 0 look-back.** The request is ready (`docs/EC_DATA_REQUEST.md`). Julie is arranging a call
   with EC's CTO (#5). Use it to find the burdens in EC's own system, then agree the Phase 0 scope, fee and data route.

3. **Josie demo, 8 Nov: the refill desk** (spec 002, `demo/refill/`). Josie, a GP, said on 7 Nov she might be
   interested in a clinic running the agentic model. The demo shows three WhatsApp refill requests on a Sunday at a
   shut clinic: routine (about 20 seconds to sign), Mr Chan's held refill (the engine's critical potassium, quotes
   checked by a test) and a red flag answered with 999. It ends with the ask: what would she need to see to put ten
   of her own refill patients through it? After the meeting: a `signal` issue, then decide whether refills become
   the next build.

Done 6 Nov: **the operating system is locked in.** Its test was a task started in Slack coming back as a scored PR, and
#10 did (the seven checks in the product). What it set up: Slack with the Claude and GitHub apps; skills and a cloud
setup script in both repos; cloud sessions as the default; CI green with the scoreboard gate; the weekday brief; and
partner contacts as `signal` issues. How it works: `docs/OPERATING_SYSTEM.md`.

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
4. Repeat dispensing in the LoopBench world model, so reasoning traces can cite current medicines. The refill desk
   demo shows why: for Mr Chan the engine says "No relevant medicines dispensed at this clinic" while his note names
   lisinopril and spironolactone.
5. Critical results out of hours. The engine runs at 08:30, 13:30 and 19:30, so Mr Chan's potassium of 6.6, posted
   at 19:40 on a Saturday, waited 13 hours. A 24/7 care line needs an overnight check for criticals; score it in
   LoopBench as time from posting to page.
6. LoopBench realism bug: seed 1 refers a 28-year-old man (FUNG Tai Man) to gynaecology. Fix the referral
   generator's sex check.
7. Doctor-minutes metric: review time per item with and without verified evidence.
8. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model. First log the Mac
   mini's Claude CLI in (`claude`, then `/login`). Scored honestly since 6 Nov: a document the model never answered
   counts as missed, not as the rules floor's answer (#12, loopbench #4).
9. LLM harness producing traces against the same seven checks.
10. Capacity-frontier experiment.
11. Thomas: held-out scenarios (outside both repos) and sign-off of `loopbench/data/clinical_policy.yaml`.

## Parked until the 1 Feb 2027 review

- Cloud Doctor's autonomy ladder above rung 1, until the open decision in `docs/DECISIONS.md` is made.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval on a branch, then leave it. It isn't on the Mac mini; most likely
  `~/heidi_eval/clinical` on the MacBook Pro.
- New tools and frameworks.
