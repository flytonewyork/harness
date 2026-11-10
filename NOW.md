# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 10 Nov 2026
(direction locked: the doctor's own line).

**Milestone: patients use the doctor's own line.** Done when Thomas's and Jose's real patients have joined, received
plans and paid for a follow-up through it. The pass lines that decide the shape at the 1 Feb review are in
`docs/BETS.md` (bets 2–4 and 8). The direction, locked 10 Nov: tmrw is the doctor's own care line, paid from care
between visits, not clinic software (`docs/DECISIONS.md`, spec 005).

## In progress (three at most)

1. **The doctor's own line, live** (spec 005, `backend/line/`; how to run it: `docs/LINE_RUNBOOK.md`). Built 10 Nov
   and running in the simulator (`python -m backend.line sim`). Built on PR #26: one shared number for many doctors, the
   doctor's WhatsApp console, the plan from a voice note (Vertex Gemini, Hong Kong), check-ins and reminders, ask the
   doctor, paid repeats and result reviews (Stripe Connect, HK$30 service fee), the doctor's web page, and the deploy
   script (Cloud Run `asia-east2`). Next: staging, then a rehearsal week with role-play patients, then launch with
   Thomas and Jose around 24 Nov. Thomas, in parallel: the accounts in
   `docs/LINE_RUNBOOK.md` (Meta, Google Cloud, Stripe), each clinic's written OK, Jose's details, and a lawyer's look at the fee structure (recommended, not
   a gate).
2. **EC Healthcare, Wed 11 Nov 4pm** (#23). Keep it light: ask which burden on EC's doctors to take away first and
   for a discovery that needs no patient data (PR #25). Build nothing more for EC.
3. **Josie** (#20). File what she agreed to on 8 Nov, then ask whether she'd be the third doctor on the line.

Done 6 Nov: **the operating system is locked in.** Its test was a task started in Slack coming back as a scored PR, and
#10 did (the seven checks in the product). What it set up: Slack with the Claude and GitHub apps; skills and a cloud
setup script in both repos; cloud sessions as the default; CI green with the scoreboard gate; the weekday brief; and
partner contacts as `signal` issues. How it works: `docs/OPERATING_SYSTEM.md`.

## Next, in order

1. Held-out red-team cases for the care line, written by someone other than the builder (spec 003 rows the live line
   uses: join, plan, check-in, questions, repeats, results).
2. Each doctor on their own WhatsApp number, once Meta approves display names.
3. The doctor subscription switched on, priced from what the dashboard's shadow price showed.
4. Doctor-minutes metric: review time per item, measured on the live line.
5. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model. Staged in
   `~/loopbench-data/extract` (4,064 dev documents, prompt `fu-extract-v2`). Thomas, in Terminal on the Mac mini:
   `claude`, then `/login`; then `cd ~/loopbench-data/extract && ./run_extraction.sh`.

## Parked until the 1 Feb 2027 review

- Clinic-operations and engine work, by the 10 Nov direction (spec 005); LoopBench stays the gate: the VITA profile
  update, clinic-agent-evals into git, the platform checks in the EC look-back driver, repeat dispensing in the world
  model, critical results out of hours (RT-07), the LoopBench realism bugs, the task catalogue as a clinic allowlist,
  the LLM harness traces, the capacity-frontier experiment, held-out scenarios and the clinical policy sign-off.
- The landing page's "for clinics" side and its pilot form.
- Cloud Doctor's autonomy ladder above rung 1, until the open decision in `docs/DECISIONS.md` is made.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval on a branch, then leave it. It isn't on the Mac mini; most likely
  `~/heidi_eval/clinical` on the MacBook Pro.
- New tools and frameworks.
