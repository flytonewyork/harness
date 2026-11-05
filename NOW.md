# NOW

Read this at the start of every session and update it at the end, in the same PR. Last updated 5 Nov 2026.

**Milestone: first real-customer signal.** Done when a partner that holds patient data agrees a next step in
writing: EC Healthcare's Phase 0 look-back on their export, or an Amber Health pilot on Apricot VITA.

## In progress (three at most)

1. **Apricot call, 6 Nov.** Learn how VITA really works (`loopbench/docs/APRICOT_DEMO_PREP.md`). Studio polish for the
   call: loopbench PR #2 (review, merge, then republish Studio). After the call: a signal note, then the VITA profile
   update below.
2. **EC Healthcare Phase 0 look-back.** The request is ready (`docs/EC_DATA_REQUEST.md`). Next: agree scope, fee and
   the data route.
3. **Lock in the operating system.** Done 5 Nov: `#ship` created in Slack, `signal` label in both repos. Left for
   Thomas, because Claude sessions can't merge unreviewed work, set repository secrets or sign in to Slack apps:
   merge loopbench #1 then harness #1, add the `REPOS_TOKEN` secret, and connect the GitHub app in `#ship`
   (`docs/OPERATING_SYSTEM.md`, Install).

## Next, in order

1. Update the VITA profile from the 6 Nov call (`loopbench/systems/apricot_vita.yaml`); fields still marked unverified
   stay marked.
2. Put clinic-agent-evals into git (loopbench), then port its tool contract and 32 cases into LoopBench.
3. Run the seven platform checks inside the product on every item it opens: the observability real data needs,
   before the EC look-back runs on a real export.
4. Repeat dispensing in the LoopBench world model, so reasoning traces can cite current medicines.
5. Doctor-minutes metric: review time per item with and without verified evidence.
6. `claude -p` extraction run on the Mac mini, replayed in CI: agentic numbers with a real model.
7. LLM harness producing traces against the same seven checks.
8. Capacity-frontier experiment.
9. Thomas: held-out scenarios (outside both repos) and sign-off of `loopbench/data/clinical_policy.yaml`.

## Parked until the 1 Feb 2027 review

- Cloud Doctor's autonomy ladder above rung 1, until the open decision in `docs/DECISIONS.md` is made.
- Trial pre-screening and the clinical trials venture.
- Bio AI and the biotech projects.
- Heidi work: commit clinical_eval to heidi_eval, then leave it.
- New tools and frameworks.
