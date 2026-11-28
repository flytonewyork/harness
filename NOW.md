# NOW

The one queue for harness and loopbench. Keep it under 2 KB. Last updated 28 Nov 2026.

**Focus until the 26 Nov go/no-go:** a written next step from EC Healthcare. Everything else is in `archive/` or Parked.

## In progress (three at most)

1. **EC discovery & Head of AI pilot brief.** Scope (`docs/EC_DISCOVERY_SCOPE.md`), 2-week design-partner pilot brief (`docs/EC_HEAD_OF_AI_PILOT_BRIEF.md`), and A4 architecture diagram (`docs/EC_ARCHITECTURE_ONEPAGER.html`).
2. **The EC demo (`/c` & `/ec`) & Stage 1 AI hardening.** Freeze-safe GI cross-referral (`prop_lam_gi_referral`), unified `SpO2 < 94%` companion floor, `ModelGateway` schema/fencing/validators, real `AgentRun`/`followup_48h_v1` wiring, and SRE log-injection guard. Gate: `scripts/check.sh`.
3. **The care line.** The doctor's WhatsApp line, harness #28 (spec 005), stacked on #26. Service `com.tmrw.careline`
   stays off until Thomas restarts it.

## Waiting on you

- **The EC discovery's fee.** Default: a fixed fee you set in the scope; nobody names one at the meeting. By Thu 12 Nov.
- **Stale PRs.** harness #13, #16, #18 and loopbench #5 are superseded (#26 stays: #28 is stacked on it). Default: close them. By Fri 13 Nov.
- **Mac mini readers.** Grok 4.7 extra high, insurance blind v4. Default: no second model. By Mon 16 Nov.

## Next, after EC answers

1. Wire the product's platform checks into the EC look-back, with a parity test against LoopBench's.
2. Overnight page for critical results (labs post at any hour; the engine runs three times a day).
3. Stage 2–3 AI architecture: shared `backend/safety/` kernel, unified `ModelGateway`, R4 `AuditEvent`/`Provenance`, SMART scopes, read/draft-only MCP gate.

## Parked until the 1 Feb 2027 review

Apricot/VITA, Josie and the clinic-backend demo, landing pages and pricing, clinic-agent-evals, the `claude -p`
extraction run, LLM harness, capacity frontier, autonomy ladder, trials, bio, Heidi. Details: `archive/docs/`.
