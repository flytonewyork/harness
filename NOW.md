# NOW

The one queue for harness and loopbench. Keep it under 2 KB. Last updated 24 Nov 2026.

**Focus until 26 Nov go/no-go:** written next step from EC Healthcare. **Feature freeze:** hone the working live demo (Tracks 1–4).

## In progress (three at most)

1. **EC discovery scope.** One-page scope for one EC clinic, no patient data (`docs/EC_DISCOVERY_SCOPE.md`).
   Fill the **[after Wed]** fields from the 11 Nov meeting (`docs/EC_MEETING_PREP.md`, `docs/EC_JULIE_CHEATSHEET.md`).
2. **Hone working EC & Head-of-AI live demo (PR #65 / `feat/live-demo-convergence`).** Specs: `docs/PRODUCT_DECISIONS.md`,
   `docs/AGENT_HANDOFF.md` (§7), `docs/HEAD_OF_AI_DEMO_PREP.md`. Live: `/workspace` (blank QR), `/p/chat`, `/emr`, `/c`.
3. **The care line.** The doctor's WhatsApp line, harness #28 (spec 005), stacked on #26. Service `com.tmrw.careline`
   stays off until Thomas restarts it.

## Waiting on you

- **The EC discovery's fee.** Default: a fixed fee you set in the scope; nobody names one at the meeting. By Thu 12 Nov.
- **Stale PRs.** harness #13, #16, #18 and loopbench #5 are superseded (#26 stays: #28 is stacked on it). Default: close them. By Fri 13 Nov.
- **Mac mini readers.** Grok 4.7 extra high, insurance blind v4. Default: no second model. By Mon 16 Nov.

## Next, after EC answers

1. Wire the product's platform checks into the EC look-back, with a parity test against LoopBench's.
2. Overnight page for critical results (labs post at any hour; the engine runs three times a day).

## Parked until the 1 Feb 2027 review

Apricot/VITA, Josie and the clinic-backend demo, landing pages and pricing, clinic-agent-evals, the `claude -p`
extraction run, LLM harness, capacity frontier, autonomy ladder, trials, bio, Heidi. Details: `archive/docs/`.
