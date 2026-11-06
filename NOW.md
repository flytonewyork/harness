# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 6 Nov 2026.

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: EC Healthcare's Phase 0 look-back on their export, or an Amber Health pilot on Apricot VITA.

## In progress (three at most)

1. **Apricot call, 6 Nov.** Learn how VITA really works (`loopbench/docs/APRICOT_DEMO_PREP.md`). Studio polish for the
   call: merged 6 Nov (loopbench #2); republish Studio before the call. After the call: a signal note, then the VITA
   profile update below.
2. **EC Healthcare Phase 0 look-back.** The request is ready (`docs/EC_DATA_REQUEST.md`). Next: agree scope, fee and
   the data route.
3. **Lock in the operating system.** Done when a task started in Slack comes back as a scored PR. Done 5–6 Nov: Slack
   (`#thomas-claude`, `#signals`, `#ship`, `#daily`) with the Claude and GitHub apps connected; a `signal` label in
   both repos; both repos side by side on the Mac mini; every piece mapped in `docs/INVENTORY.md`; the Phase 1 plan in
   git; skills and a cloud setup script, so a session started from Slack, the web or the phone follows the same
   protocol (harness #2, loopbench #3); a cloud environment on claude.ai/code, so `@Claude` in Slack now runs Claude
   Code sessions (checked 6 Nov); the weekday morning brief, scheduled; and cloud sessions as the default, with the
   Mac mini kept for the jobs only it can do. Left for Thomas, because Claude sessions can't change a token's access:
   give the token in `REPOS_TOKEN` access to both repos, so the scoreboard runs and main goes green. See
   `docs/OPERATING_SYSTEM.md`, Install.

## Next, in order

1. Update the VITA profile from the 6 Nov call (`loopbench/systems/apricot_vita.yaml`); fields still marked unverified
   stay marked.
2. Put clinic-agent-evals into git (loopbench). The zip isn't on the Mac mini; it is most likely on the MacBook Pro
   (`docs/INVENTORY.md`). Porting its tool contract and 32 cases waits for the 26 Nov go decision
   (`docs/PHASE1_CLOSEOUT.md`, Deferred).
3. Run the seven platform checks inside the product on every item it opens: the observability real data needs,
   before the EC look-back runs on a real export.
4. Repeat dispensing in the LoopBench world model, so reasoning traces can cite current medicines.
5. Doctor-minutes metric: review time per item with and without verified evidence.
6. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model. First log the Mac
   mini's Claude CLI in (`claude`, then `/login`).
7. LLM harness producing traces against the same seven checks.
8. Capacity-frontier experiment.
9. Thomas: held-out scenarios (outside both repos) and sign-off of `loopbench/data/clinical_policy.yaml`.

## Parked until the 1 Feb 2027 review

- Cloud Doctor's autonomy ladder above rung 1, until the open decision in `docs/DECISIONS.md` is made.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval on a branch, then leave it. It isn't on the Mac mini; most likely
  `~/heidi_eval/clinical` on the MacBook Pro.
- New tools and frameworks.
