# Head-of-AI demo: links, run-of-show, Q&A, codebase map

Branch `feat/live-demo-convergence` (harness `0127bcb`, loopbench `22a27a8`). PR #65 is the base this stacks on.
Synthetic patients only. Feature freeze: polish, don't build.

## A. Links

**Code and reports (GitHub)**
- PR #65 (base): https://github.com/flytonewyork/harness/pull/65
- Convergence branch: https://github.com/flytonewyork/harness/tree/feat/live-demo-convergence
- Agentic core branch: https://github.com/flytonewyork/harness/tree/feat/agentic-skills-prompts-memory
- LoopBench eval branch: https://github.com/flytonewyork/loopbench/tree/feat/live-demo-convergence
- Founder grading report: `docs/research/founder_grading_report.html` (open in a browser; `.md` twin alongside)
- Seam contract: `docs/research/SEAM_CONTRACT.md`. Agentic design: `docs/AGENTIC_ARCHITECTURE.md`

**Live surfaces** (one FastAPI process serving the built frontend; default `http://localhost:8080`)

| Path | What it shows |
| :--- | :--- |
| `/workspace` | Clinician workspace: 14 widgets, empty Management Plan, `/` orders, Confirm & Sign |
| `/emr` | Simulated clinic EMR + harness X-ray (tool calls, memory tiers, additive addenda, authority probe) |
| `/p/qr/pat_lam_urti_demo` | Patient phone view (LAM Wing-Yan), live-updated over SSE on sign-off |
| `/c` | Staff console: nurse prep, doctor sign-off, MCHK PDF receipt |
| `/demo` | 3-act resident-agent shell (PR #64) |
| `demo/ec-care-line/index.html?walk=kwok` | Static care-line walkthrough (offline-safe fallback) |

**No staging URL is deployed yet.** To get one: `bash deploy/deploy_gcp_asia_east2.sh` (Cloud Run
`clinic-ai-harness-staging`, `asia-east2`, Cloud SQL + Secret Manager; prints the URL at the end).

**Run locally**
```bash
docker run -d --name harness-postgres -p 5432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_DB=clinic_harness postgres:15-alpine
npm --prefix frontend run build
HARNESS_ENV=staging HARNESS_RUNTIME_MODE=demo DEMO_MODE=true DEMO_SHOW_OTP=true \
DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness \
DATABASE_ADMIN_URL=postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness \
uvicorn backend.acute.app:app_from_env --factory --host 0.0.0.0 --port 8080
```
For the phone QR, the phone must reach the host: use the laptop's LAN IP or the staging URL, not `localhost`.

## B. Pre-flight (T-15 min)

1. `curl -s localhost:8080/api/v1/health` → 200. Open all four surfaces in separate tabs.
2. Reset: `curl -X POST localhost:8080/api/v1/workspace/reset` and
   `curl -X POST localhost:8080/api/v1/emr/patient/pat_lam_urti_demo/reset-urti`.
3. Decide the model mode and say it out loud. With no Vertex/Gemini credentials the runtime is
   **deterministic** (that's how the eval report ran). With credentials it prefers Vertex `gemini-2.5-flash` in
   `asia-east2`. The safety floor is identical either way.
4. Phone on the same network, QR page open, screen lock off.
5. Fallback if anything breaks: the static `demo/ec-care-line/index.html?walk=kwok`, then the grading report.

## C. Run-of-show (10 minutes, then stop)

**Act 1: the workspace (4 min), `/workspace`**
1. Four seeded cases: CHAN (post-QEH AFib, warfarin 5→3 mg + statin switch), LAM Wing-Yan (URTI, G6PD,
   penicillin allergy, ferritin 14), WONG (home BP), LEE (borderline LDL).
2. Open CHAN: MedDiff shows the switch as stop+start, so no false duplicate-statin alarm. CHA2DS2-VASc and HAS-BLED are
   computed in Python, not by the model.
3. Point out the Management Plan is **empty**. The agent proposes; the doctor adds with `+` or `/`.
4. Open LAM. Type `/rx` and try aspirin → **422 from `rx_rules`** (G6PD). That's the deterministic floor.
5. Add a 2-day `/cert` (employer copy omits diagnosis) → **Confirm & Sign Plan** → MCHK PDF receipt.
6. Show the 10-second Undo; let it lapse.

**Act 2: the record and the phone (3 min), `/emr` + phone**
1. The phone updates live once the doctor signs.
2. In `/emr` the plan appears as an **additive addendum**. The private Sam chat is not in it, ever.
3. Run the authority probe: an agent tries `emr.write_signed_orders` → BLOCKED.

**Act 3: governance and X-ray (3 min)**
1. `# clinic-team`: Nurse Rachel resolves a document quarantined on a 2/3 ID match.
2. Curator: playbook v2.4 → v2.5 diff, then rollback.
3. Architecture X-ray: the context budget (about 2.5k of 4,096 tokens), nodes traversed, linter 5/5.

**Close:** "Every output from tonight's 20 cases is in the grading report. Grade any one you like."

## D. Head-of-AI Q&A

- **Why not let the LLM render the UI?** The model fills 5 slots in a compiler; output becomes one of 14 finite
  widgets; a 5-rule linter (`validate_case_presentation`) checks it; one bounded self-repair retry, then it fails
  closed. That makes the output testable and the eval meaningful.
- **What does the model actually decide?** Nothing clinical. Red flags (`triage.py`), Rx safety (`rx_rules.py`),
  ID matching (`verification.py`) and calculators are deterministic and run first. The model drafts and summarises.
  Agents can open items or raise urgency, never close or reassure; only doctors close.
- **Prompt injection?** Patient text goes inside nonce-fenced `<untrusted_data>`; EN and Cantonese jailbreak patterns
  are detected (`backend/agentic/prompts.py`); tools are behind a typed authority gate, so a hijacked prompt still
  can't sign or write orders. Case FG-16.
- **Cantonese?** Real colloquial and code-switched cases (FG-09/10/14/15), including the hyperbole trap
  「咳到想死但冇氣促冇胸痛」, which must **not** trigger 999.
- **Data residency / PDPO?** Default region `asia-east2`. Off-HK providers only behind `PHIScrubber`. FORCE row-level
  security with a non-superuser app role. EMR stays the record; writes are additive and only after doctor sign-off.
- **Memory?** Three tiers (session / case / practice), all RLS-scoped. Memory quotes are verified against source text
  before use, so no made-up quotes (`backend/agentic/memory.py`).
- **Prompts and skills?** 8 clinical `SKILL.md` files (`skills/clinical/`), progressively disclosed under a token
  budget; prompts versioned with SHA-256 fingerprints, so a trace records exactly which prompt ran.
- **How do you know it works?** LoopBench is a separate repo that only talks to the harness over HTTP; the harness
  never imports it. 20/20 cases, 79/79 assertions. **Be straight:** synthetic cases, written by us, run
  deterministically. It's a regression gate, not a clinical validation, and not a blind set.
- **EMR integration?** `emr_connector.py`: additive-only connector matrix (EC Healthcare, ClinicSolution X,
  Apricot/VITA) plus a phase-1 bridge. Real integration waits on the discovery with EC's IT.

**Don't claim:** that the care line is live for EC (it isn't), any measured time savings (LoopBench figures are
simulated), or live-LLM eval numbers (the report ran deterministic).

## E. Codebase map

```
harness/
  backend/acute/            the product runtime (FastAPI)
    app.py                  all routes: /workspace, /emr, /confirm, /undo, /demo/reset, SSE
    agent_harness.py        model runtime: Vertex asia-east2 → Gemini → Anthropic → OpenAI → deterministic
    workspace_engine.py     5-slot presentation compiler + 1-retry linter repair
    triage.py               Loop 1: deterministic red flags / 999
    verification.py         Loop 2: 3-point ID gate, quarantine
    rx_rules.py             Rx parser + CDS (G6PD, allergy, Cap. 134, DDI, renal) → 422
    certificates.py         MCHK sick-leave PDF (encrypted vault)
    terminology.py          codes, HK formulary, pathway terms
    practice_graph.py       8 pathways, calculators, preventive rules
    curator.py              playbook versions, diff fingerprint, rollback
    emr_connector.py, adapters.py   additive EMR writeback + dispatch
    live_bus.py             SSE push to patient phone
    workflows.py            outbox_events, undo, grace windows, nudges
  backend/agentic/          skills, prompts, tool gate, context compiler, memory, runner
  skills/clinical/*/SKILL.md 8 clinical skills
  frontend/src/v2/workspace/ ClinicWorkspaceApp.tsx + workspace.css (warm paper)
  docs/                     PRODUCT_DECISIONS, CLINICAL_UI_DESIGN_SYSTEM, AGENT_HANDOFF, research/
  deploy/                   Cloud Run asia-east2 scripts
loopbench/
  src/loopbench/founder_eval.py, scenarios/founder_eval/cases_20.yaml   the 20-case suite
```
