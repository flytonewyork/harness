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
| `/workspace` | Clinician workspace: starts **blank** with scannable Patient QR, populates live as phone chats, 14 widgets, empty Management Plan, `/` orders, Confirm & Sign, **Under the Hood** X-Ray, **Reset Demo** |
| `/p/chat` (or `/p/qr/...`) | Pure mobile natural-language chat (empty → clinic typing bubble → welcome → AI chat + slide-over Patient Journeys sidebar + live doctor-signed MCHK PDF delivery) |
| `/emr` | Simulated clinic EMR + deep-dive harness inspector (tool calls, memory tiers, additive addenda, authority probe) |
| `/c` | Staff console: nurse prep, doctor sign-off, MCHK PDF receipt |
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

**Real phone without a deploy (temporary public HTTPS URL):**
```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080   # prints https://<random>.trycloudflare.com
```
Open `/workspace` **through that URL** on the laptop. Its blank-start QR uses `window.location.origin`, so it points
phones directly at `https://<random>.trycloudflare.com/p/chat` (also editable inline via `Edit host URL`). Both phone
and laptop pair SSE with a 2–2.5s poll fallback, so quick-tunnel buffering never blocks live sync. Stop the tunnel
after the demo.

## B. Pre-flight (T-15 min)

1. `curl -s localhost:8080/api/v1/health` → 200. Open `/workspace` on the laptop.
2. Click **`Reset Demo`** in `/workspace` (or `curl -X POST localhost:8080/api/v1/demo/live/reset`) so the laptop starts **completely blank** with the crisp QR code centered on screen.
3. Decide the model mode and say it out loud. With no Vertex/Gemini credentials the runtime is
   **deterministic** (that's how the eval report ran). With credentials it defaults to **Gemini 4 Neon**
   (`gemini-4-neon`, with automatic fallback to **Gemini 4 Hydrogen** `gemini-4-hydrogen`) via Vertex AI
   `asia-east2` or `GEMINI_API_KEY`. The safety floor is identical either way.
4. Physical phone ready with camera open to scan the QR code on the laptop screen.
5. Fallback if anything breaks: the static `demo/ec-care-line/index.html?walk=kwok`, then the grading report.

## C. Run-of-show (10 minutes, then stop)

**Act 1: Physical Phone QR Scan → Natural Chat → Live Workspace Populates (4 min)**
1. Laptop `/workspace` starts **blank** with the Patient QR code on screen.
2. Scan QR on physical phone → `/p/chat` opens **empty** → animated clinic typing bubble → warm introduction from Sam (Clinic Care Assistant) for **LAM Wing-Yan (32F)**. Immediately on the laptop, a live thread appears in `Running · Today` showing `LoopWatcher` + `CareAgent` working!
3. Message Sam naturally on the phone (English or Cantonese): sore throat + 38.2°C fever, asks *"can I take aspirin?"*, requests a 2-day sick note, asks about Ferritin 14.
4. Sam immediately warns against Aspirin (G6PD deficiency from chart) and prepares the summary. Watch the laptop promote the thread live into **`# my-work` (`Needs Decision`)** with Ferritin trend, safety alerts, atomic issues, and an **empty** `Management Plan`.
5. On laptop: test `/prescribe Aspirin` → deterministic **HTTP 422** from `rx_rules`. Then click `+` on Paracetamol + 2-day `/cert` → **Confirm & Sign Plan**. Show the 10-second Undo; let it lapse. Watch the **Doctor-Signed Care Plan + MCHK Sick Leave PDF button** arrive live in the phone chat!

**Act 2: Switch Patient Journeys from Phone Sidebar (3 min)**
1. Tap `☰` on the physical phone to open the **Patient Demo Journeys** sidebar and select **CHAN Tai Man (62M)** (Post-QEH AFib discharge). Phone clears to empty chat → typing bubble → welcome. A **second independent thread** spawns live on the laptop! Chat about Warfarin 5→3mg / statin bags → laptop mounts `MedDiff`, Python `CHA₂DS₂-VASc` / `HAS-BLED`, and QEH ECG.
2. Switch to **WONG Mei Ling (58F)** (Home BP): reply `128/78` → stays quietly in `Scheduled / Waiting` (zero doctor interruption, silent Sam gate!). Then reply `156/96 with headache` → wakes up live into `Needs Decision`!
3. Optional: switch to **CHEUNG Kwok-Wai (56M)** to show Cantonese hyperbole (`「咳到想死但冇氣促冇胸痛」` → NOT 999) vs true cardiac red flag (`「心口好痛、唞唔到氣」` → instant 999 exit).

**Act 3: Under the Hood & Full Reset (3 min)**
1. Click **`Under the Hood`** in the `/workspace` top bar to reveal the technical X-Ray drawer:
   - Live Agent Tool Stream (`LoopWatcher`, `SafetySentinel`, `ProtocolCDSAgent`, `PresentationCompiler` with authority tags)
   - Click **`Probe Authority Guard (BLOCKED)`** (`emr.write_signed_orders` → `BLOCKED`)
   - `<4,096`-token 5-slot Context Budget, 5/5 Presentation Linter, Practice Graph `v2.4`, Sole EMR SoT additive addenda (zero Sam chat leakage)
   - Optional: open `/emr` tab or `# clinic-team` (3-pt ID quarantine) / `@ Clinic Playbook` (Curator v2.4 → v2.5 diff).
2. Click **`Reset Demo`** → both laptop and phone reset cleanly back to blank.

**Close:** "Every output from our 20-case benchmark is in the grading report. Grade any one you like."

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
