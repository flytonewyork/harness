# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 6 Nov 2026.

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: EC Healthcare's Phase 0 look-back on their export, or an Amber Health pilot on Apricot VITA.

**Product (locked 6 Nov):** a persistent clinic agent that follows up and closes loops for doctors. The doctor signs,
the agent does. Spec: `docs/specs/002-product-direction.md`.

Toward the 26 Nov go/no-go, 4 partner conversations are logged: EC (#5), Amber (#6), Adrian (#8) and Virtus (#11).
The Apricot call on 6 Nov (#7, notes in #15) makes 5. All of them point at doctors' workload, but no partner has yet named the specific
burden, so that is the question for every next contact.

## In progress (three at most)

1. **This week (to 13 Nov): the care line, made real enough that Thomas wants to take GP sessions** (spec 003,
   a candidate for the 26 Nov go/no-go). Deliverables:
   - a clickable prototype of the patient's WhatsApp and the doctor's inbox (synthetic)
   - the session kit: a bilingual plan card with QR code, the promise diary, and a WhatsApp Business setup
   - the care-line simulator and the sign-off trainer in LoopBench
   - the self-service catalogue and its evals (spec 004, 7 Nov): 37 reasons people come in, each with a protocol,
     red flags and a doctor's sign-off. The first slice of 8 tasks passes at 100% in LoopBench. Next: a held-out set
     written blind by Thomas or Josie, then shadow mode in sessions
   - ten doctor conversations about giving patients their WhatsApp (1 so far: Josie, locum GP, #17)
   Success: Thomas books GP or call-out sessions to test it. The winning pilot runs in waves (spec 003):
   0. his sessions, done manually
   1. the FPC with Adrian, the full stack
   2. EC's new practice, under EC's brand
   3. partner clinics: Virtus, Central Health, Amber
   **Working direction from 6 Nov evening (Thomas):** "going down this path, seems like a reasonable approach".
   The formal go/no-go stays on 26 Nov. **First up on Monday 9 Nov:**
   - book 2–3 GP sessions for the next fortnight
   - ask Adrian and EC for their opening timelines
   - ask Josie (#17) to be the second pilot doctor
   - send notes for a Central Health signal
   - Claude builds the session kit: a plan card with QR code, the promise diary and scoreboard, and a WhatsApp
     Business setup. The scoreboard tracks the 90-day measures in `docs/analysis/ai-native-clinic-investment.md`
   Still waiting on: Apricot's API docs and technical contact (#15), and the Heidi call by 18 Nov.
2. **EC Healthcare Phase 0 look-back.** The request is ready (`docs/EC_DATA_REQUEST.md`). Julie is arranging a call
   with EC's CTO (#5). Use it to find the burdens in EC's own system, then agree the Phase 0 scope, fee and data route.
3. **Two demos by 13 Nov (spec 002; may be re-cut around the care line after 26 Nov).** First, each partner names its top burden in its own words (a `signal`
   issue). Demo A covers results and hospital follow-ups, for EC and Amber. Demo B covers recalls and CDCC, for
   Virtus and Adrian. Each shows find → prepare → sign → do → watch → close on a simulated clinic year. Group B's
   new loop families are scored in LoopBench before the engine is built for them.

Done 6 Nov: **the operating system is locked in.** Its test was a task started in Slack coming back as a scored PR, and
#10 did (the seven checks in the product). What it set up: Slack with the Claude and GitHub apps; skills and a cloud
setup script in both repos; cloud sessions as the default; CI green with the scoreboard gate; the weekday brief; and
partner contacts as `signal` issues. How it works: `docs/OPERATING_SYSTEM.md`.

Done 6 Nov: **Apricot call** (signal #15). VITA already has most of the clinic-side follow-up pieces; eHealth
is upload only; Apricot's API docs are pending. Thomas is less bullish on a follow-up add-on for clinics, and the
care line (spec 003) came out of it.

Done 6 Nov: **investment analysis of an AI-native clinic** (`docs/analysis/ai-native-clinic-investment.md`, numbers
from `docs/analysis/clinic_model.py`, living version as a Claude Doc). The best unit economics of the six models
compared, if patients pay between visits; network effects are weak; the base case passes HK$100m a year around month
36 with a peak cash need of about HK$3m. It recommends a split by line: 50% of visits, 70% of the doctor's line.

Done 6 Nov: **product direction locked** (spec 002). It covers two groups of loops, A (results and hospital) and
B (recalls and prevention, CDCC included). The authority model is "doctor signs, agent does", which closes the
open question in `docs/DECISIONS.md`.

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
4. Repeat dispensing in the LoopBench world model, so reasoning traces can cite current medicines.
5. Doctor-minutes metric: review time per item with and without verified evidence.
6. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model. First log the Mac
   mini's Claude CLI in (`claude`, then `/login`). Scored honestly since 6 Nov: a document the model never answered
   counts as missed, not as the rules floor's answer (#12, loopbench #4).
7. LLM harness producing traces against the same seven checks.
8. Capacity-frontier experiment.
9. Thomas: held-out scenarios (outside both repos) and sign-off of `loopbench/data/clinical_policy.yaml`.

## Parked until the 1 Feb 2027 review

- Cloud Doctor's autonomy ladder above rung 1: the agent acting without a doctor's signature. Spec 002 keeps every
  action signed.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval on a branch, then leave it. It isn't on the Mac mini; most likely
  `~/heidi_eval/clinical` on the MacBook Pro.
- New tools and frameworks.
