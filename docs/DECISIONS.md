# Decisions

One line per decision, newest first, so no session has to decide it again. Change one only with a new row that says
why. Open questions sit at the top until they're decided.

## Open

| Raised | Question | Options | Recommendation |
|---|---|---|---|
| 5 Nov 2026 | Who may close a loop? The harness says only a doctor, ever. The Cloud Doctor spec's autonomy ladder lets a task class "act within its class" at rung 3, and its fixed test says "close the safe ones". | (a) Doctor-only closing, always. (b) Classes may close loops once they reach rung 3. | (a): keep closing doctor-only. Let the ladder govern outbound actions (drafted messages, recall bookings), which never close a loop by themselves. Decide before the ladder is built. |

## Decided

| Date | Decision | Why | Source |
|---|---|---|---|
| 6 Nov 2026 | Cloud sessions are the default place work runs (`tmrw` environment, both repos); the Mac mini only for `claude -p` model runs and rebuilds from the raw data | Thomas asked for it. Cloud sessions start from any device, set themselves up and end in a PR; the Mac mini holds the logged-in CLI and the raw data kept outside git | `docs/OPERATING_SYSTEM.md`, Where work runs |
| 6 Nov 2026 | The protocol travels with the repos: skills (`ship`, `signal`, `park`, `brief`, `review`) and a cloud setup script in each repo | A session started from Slack, the web or the phone begins with one repo and no context, so the steps can't live in a chat | `CLAUDE.md`, `.claude/skills/` |
| 6 Nov 2026 | Slack follows Anthropic's `@Claude` patterns, in a light version for a Max plan: `#thomas-claude`, `#signals`, `#ship`, `#daily` | Thomas asked for Slack set up the way a Claude-native org runs it, to learn and copy the workflow; Claude Tag itself needs a Team or Enterprise plan | `docs/OPERATING_SYSTEM.md`, Slack |
| 6 Nov 2026 | `docs/INVENTORY.md` is the map of everything; anything outside the two repos is a live view, a partner document, retired or parked | The 6 Nov audit found the work spread over 24 artifacts, 4 Project docs, two zips and three machines | `docs/INVENTORY.md` |
| 5 Nov 2026 | Operating system v2: GitHub is the only home, `NOW.md` is the one queue, the LoopBench scoreboard gates every PR | Work was being rebuilt in each chat; the 28 Oct OS was a document nothing enforced | `docs/OPERATING_SYSTEM.md` |
| 5 Nov 2026 | Every item carries a reasoning trace with verbatim quotes, checked by the platform | Agent work is reviewed like a pull request; a made-up quote is a hard fail | `loopbench/docs/PLAN.md` |
| 5 Nov 2026 | Asymmetric authority: the agent opens or raises; only a doctor closes, dismisses or downgrades | Switching it off must leave the clinic less safe, and it must never be unsafe itself | `CLAUDE.md` |
| 5 Nov 2026 | Rules v1.0 is the frozen floor; behaviour changes go behind new versions | Results stay comparable across releases | `CLAUDE.md` |
| 5 Nov 2026 | The test bed comes first; the product plugs into it and never imports it | "Evals first", made literal | `loopbench/docs/PLAN.md` |
| 5 Nov 2026 | Model choice is pluggable and outputs are cached by prompt, model and document | Hong Kong isn't on Anthropic's supported-countries list; LoopBench is how a permitted model gets proven | `loopbench/docs/PLAN.md` |
| 5 Nov 2026 | heidi_eval stays the Heidi work sample; tmrw IP lives in harness and loopbench | IP doesn't live in a repo named for, and shown to, a prospective employer | `loopbench/docs/PLAN.md` |
| 3 Nov 2026 | North Star: a business signal from real customers, through tmrw consulting | Replaces getting hired by Heidi | Thomas |
| 1 Nov 2026 | Direction page: anything that tempts a change of direction goes to Parked until the 1 Feb 2027 review | Stop re-deciding direction every week | Thomas |
| Standing | Synthetic data only, in repos, chats, Slack and frontier models | Patient-data rights are the main blocker in Hong Kong | `CLAUDE.md` |
