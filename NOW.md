# NOW

The one queue for harness and loopbench. Keep it under 2 KB. Last updated 12 Nov 2026.

**Focus until the 26 Nov go/no-go:** a written next step from EC Healthcare. Everything else is in `archive/` or Parked.

## In progress (three at most)

1. **EC discovery scope.** One-page scope for one EC clinic, no patient data (`docs/EC_DISCOVERY_SCOPE.md`).
   Fill the **[after Wed]** fields from the 11 Nov meeting with Julie (`docs/EC_MEETING_PREP.md`) and send by Thu 12 Nov.
2. **The EC demo.** `demo/ec-care-line/index.html`. For next week's meeting with EC's CEO, Julie and the head of AI:
   Ms Kwok's WhatsApp follow-up, one beat per screen, then the doctor's item (`?walk=kwok`). `?walk=1` is unchanged.
   `suite.html`: Dr Chan's one list across all six journeys, plus the evidence view for the head of AI (blind gap shown).
   Then: refresh the offline copy on the Mac mini and a timed dry run.
3. **The care line.** The doctor's WhatsApp line, harness #28 (spec 005), stacked on #26. The Mac mini service `com.tmrw.careline`
   was stopped 11 Nov and stays off until Thomas restarts it; it replied to numbers outside the tester list on 10 Nov.

## Waiting on you

- **The EC discovery's fee.** Default: a fixed fee you set in the scope; nobody names one at the meeting. By Thu 12 Nov.
- **Stale PRs.** harness #13, #16, #18 and loopbench #5 are superseded (#26 stays: #28 is stacked on it). Default: close them. By Fri 13 Nov.

## Next, after EC answers

1. Wire the product's platform checks into the EC look-back, with a parity test against LoopBench's.
2. Overnight page for critical results (labs post at any hour; the engine runs three times a day).

## Parked until the 1 Feb 2027 review

Apricot/VITA, Josie and the clinic-backend demo, landing pages and pricing, clinic-agent-evals, the `claude -p`
extraction run, LLM harness, capacity frontier, autonomy ladder, trials, bio, Heidi. Details: `archive/docs/`.
