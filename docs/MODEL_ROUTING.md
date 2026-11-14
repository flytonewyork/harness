# Which model does which job

12 Nov 2026. The engine reads with keyword rules first. A model reader (`backend/extract/reader.py`) can be layered on
top. It is **union only**: it can add an item or raise urgency, and it can never remove or lower anything. Every field
it returns needs the document's exact words, or it is dropped before a journey sees it. Model ids and prices below come
from the claude-api skill (prices cached 3 Nov 2026), not from memory.

## Starting assignment (chosen before measurement; the Mac mini run decides)

| Job | Where | Model | Effort | Why |
|---|---|---|---|---|
| Urgency screening of a patient's own message (care-line replies, side effects, precare red flags) | `extract/triage.py` (followup); precare screens | `claude-opus-5-5` | medium (its default) | Safety-critical recall. Few messages per clinic-day, so about 1-4 US cents each. Thinking is always on in Opus 5.5. |
| Reading clinic documents into typed fields with quotes (insurer letters, discharge summaries, specialist letters) | insurance `TASK`; follow-on, post-checkup and medication when they get one | `claude-sonnet-5-5` | low | Structured extraction. The quote check catches invented fields. Higher volume. |
| Comparison arm on every task | all | `claude-haiku-5-5` | default | About 1/20 of Sonnet's price. If it matches on fresh blind sets, it becomes the production candidate for document reading. |
| Drafting to patients | none | none | | Patient-facing text stays fixed templates by design. No model writes to a patient. |
| Drafting for the doctor (an insurer report, a letter reply), if it is ever added | not built | `claude-opus-5-5` | | Shown to the doctor only and never sent automatically. That needs its own spec and Thomas's go-ahead. |

**Rule for the final choice:** use the cheapest model whose first run on a fresh blind set passes at least as many
cases as the best model, misses no red flag that the best model catches, and has no more quotes dropped. Which model
may touch EC data (residency, contract) is still open. Nothing here is wired to real patient data.

## What keeps a model run honest

- **Pinned ids.** `--model sonnet` is stored as `claude-sonnet-5-5`, because an alias moves when a new model ships.
  Each replay file holds one model.
- **Prompt fingerprint in every cache key.** `Task.fingerprint()` hashes the instructions, the schema and the quoted
  fields. If the prompt is edited without a version bump, every cached answer misses and the journey falls back to
  its keyword reader. A trace always says who read the document: `followup-keyword-v1`, `model …`, or `… fell back`.
- **Clean calls.** Each call uses a replacement system prompt, no tools, no MCP, no slash commands and no saved
  session. It runs from an empty folder, so no repository CLAUDE.md reaches the model. The document is data, never an
  instruction.
- **The screening schema cannot reassure.** It has fields for `red_flag` and `worse` only. There is no "fine" field.
- **Blind sets (loopbench `scripts/blind.py`).** Freeze the reader, then write the set with `frozen: <id>` in its
  header, then run it once and keep the result. `firstrun` refuses a set with no freeze. It refuses a reader that
  changed since its freeze. It refuses a model on a set written before that model's prompt existed. It refuses to redo
  a first run. CI fails a PR that edits a set after its first run, or that changes or deletes a first-run file.
  The older blind sets predate the model prompts. They are still replayed, but they are reported as not blind for
  the model.

## What union-only cannot fix

The model can only raise. So if the keyword reader pages on a negated symptom ("冇再氣促，胸口都唔翳喇", "the wheeze
has gone"), the model cannot stop that false page. Followup blind v2 shows both of these cases (keyword first run
5/14). That fix belongs in the keyword reader: write a new version, freeze it, then write a new blind set.

## The Mac mini run (needs Thomas's go-ahead; nothing below has been run)

On the Mac mini, with `~/harness` and `~/loopbench` on the branches of this PR (pull first):

```bash
cd ~/loopbench
bash scripts/model_run.sh estimate     # no calls: counts and a cautious API-price estimate per model
bash scripts/model_run.sh run          # opus, sonnet, haiku on every followup message (resumable; stops on a login error, or past $10 per file)
bash scripts/model_run.sh score        # no calls: replays the answers, writes model first runs on blind v2
git add results/journeys && git commit -m "Model reader first run (followup)" && git push
```

`JOURNEYS="followup insurance"` adds insurance once its PR is on the same branch, and it needs a blind set written
after its own freeze. `MODELS=haiku` runs a single model, and `MAX_USD=5` lowers the stop.

**Expected cost:** the estimate assumes 1,500 output tokens per call, thinking included, so it is cautious. Followup
is 33 messages: Opus $1.16, Sonnet $0.58, Haiku $0.03. Insurance is 97 documents: Opus $3.52, Sonnet $1.76, Haiku $0.09.
Both journeys on all three models come to **under $7 at API prices**. On the Mac mini's `claude` login the run counts
against the plan's usage instead of being billed per token. Expect 10-20 minutes at 4 workers.
