# 004: The WhatsApp care line, after the visit

**Problem.** The landing page (v3, v4) and the clinic backend demo promise a WhatsApp care line after the visit: the
plan in writing, bookings, "is my result back?", repeat prescriptions and a day-3 check-in. Spec 003 wrote what the
agents may and must never do, as 54 features with gates, but nothing ran: no agent, no eval cases for the 15
unscored rows, no message-side checks, and the QR code on the page opened nothing. On 9 Nov Thomas asked for a working
prototype on his WhatsApp Business number, plugged into the simulated clinic so it can later be swapped for Apricot's
or EC's endpoints, with the eval set on a dashboard.

**Change.** `backend/careline/`, one package, run with `python -m backend.careline`:

| Part | File | What it does |
|---|---|---|
| Agent | `agent.py` | One message in, replies out. Order for every message: red flag or crisis (fixed reply plus an alert to the named doctor), stop, join (consent, then name and date of birth, linked only on exactly one match), identity check before anything from the chart, then the menu option or intent's fixed steps. Anything clinical goes to the doctor word for word, with an owner and a reply-by time. |
| Texts | `texts.py` | Every word sent to a patient, in English and Cantonese. The agent sends only these, filled with facts, so it can't give advice of its own. Clinical words reach a patient only inside a reply the doctor approved: the written plan, and the approved test preparation. |
| Safety | `safety.py` | Red-flag and crisis lists (English and Cantonese; they over-trigger on purpose), clinical-question detection, and `check_outbound`, the message-side hard fails from spec 003. Every reply is checked before it is sent; a failure blocks it and hands it to a person. |
| Clinic | `clinic.py` | `CarePort`, the care line's narrow view of an EMR. `SimClinic` reads LoopBench's product feed (harness-run/1) through the product's adapter, frozen at Sun 13 Jun 2027 09:30 (the demo's moment). `PlaybackClinic` records every EMR call and answer, and replays them with no clinic behind it. `ApricotClinic` and `ECClinic` are the slots for real endpoints, and they raise until a data route is agreed. |
| Queue | `store.py` | Hand-offs (owner, reply-by, the chain they move along when unclaimed), drafts for the doctor, and the record of every message, check and decision (RP-01). The agent opens; only a person, by id from the clinic's settings, claims, replies, approves or closes. |
| Hours | `settings.py`, `clinic_settings.json` | Opening hours, holidays, the named doctor, who owns what, reply-by minutes. Reply-by counts only open minutes, so nothing is promised at 3 am (HE-01, HE-02). Red flags count from now. |
| Intent | `intents.py` | Rules in English and Cantonese by default. A model (`claude-api`, or `claude-cli` on the Mac mini) only picks one label for a message the rules couldn't place. It is cached by prompt, model and text, and a miss falls back to the rules under the rules' name. The model never writes to a patient. |
| WhatsApp | `whatsapp.py`, `api.py` | Cloud API webhook (verify token; `X-Hub-Signature-256` checked; unsigned webhooks refused outside sim). It sends text, reply buttons and list messages. A tape of every tester conversation feeds `replay.py`. |
| Dashboard | `static/dashboard.html` at `/careline` | A patient's phone to test with (synthetic personas from the chart), the people's queue (approve the plan, claim, reply, close), the record, the evals by feature and gate, and setup with the QR code. |
| Join page | `static/join.html` at `/join` | The QR code and wa.me link for the website, with the 999 line. |
| Evals | `evals/careline/cases.json`, `evals.py` | 63 cases: 36 scripted, 14 red-team, 9 fixed-reply and 4 refusal. Each is tied to a feature in `evals/agent_features.csv`. The runner re-checks every message the agent sent instead of trusting the agent's own checks, then adds the whole-conversation checks: missed red flag, dropped message, false promise, wrong patient, no consent, authority. It reports each feature's gate. |

**Modes.** `sim`: nothing leaves the machine. `tester`: the real WhatsApp number, but only the numbers in
`CARELINE_TESTERS` reach the agent. Anyone else who scans the QR code gets one fixed notice ("a test, not a medical
service; for an emergency call 999"), and nothing they write is kept or sent to a model. `live`: refused unless
`CARELINE_I_HAVE_SIGN_OFF=yes`, which needs the gates in spec 003 signed off and the open decision on real patient
data (`docs/DECISIONS.md`) made.

**Done when.**
- `tests/test_careline.py` passes. It runs every case, and two mutation tests show the suite fails when the agent is
  wrong: a missing fixed reply, and advice in a template.
- `python -m backend.careline.evals` reports zero hard fails.
- The dashboard runs the post-visit flow against the simulated clinic.
- The signed webhook path answers.
- A recorded conversation replays unchanged.

**Where the gates stand (9 Nov).**
- 63/63 cases pass with 0 hard fails, on 34 of the 54 features.
- `G-fixed` (HE-03, HE-04) and `G-refuse` (NV-01 to NV-06) are met by cases. RT-04, RT-05 and RT-06 meet
  `G-loop` in LoopBench, as before.
- Every `G-critical`, `G-high` and `G-standard` row with cases now needs shadow conversations: 0 of 300, 200 or 100.
  So nothing clinical is live, and every clinical step still waits for a person.
- The suite was written alongside the agent, so passing it is necessary, not sufficient. Held-out red-team cases
  written by someone else are the next proof.

**Invariants touched.**
- Authority line: kept. The agent can open a hand-off or a draft; closing, approving and claiming need a person, and
  clinical hand-offs need a doctor (tested).
- Verbatim quotes: hand-offs quote the patient word for word, and the written plan is the doctor's own lines from the
  consult note, sent only as approved.
- No LoopBench import: the simulated clinic is read from the run file. Rules v1.0 is untouched.
- Synthetic data only: the chart is LoopBench's synthetic clinic. Tester mode keeps nothing from non-testers. Testers
  role-play synthetic patients (join as CHAN Tai Man, 23/09/1964) and must not send their own health information.

**Out of scope.**
- Live use with real patients.
- Template messages for clinic-started conversations outside the 24-hour window. The day-3 check-in is sent as a
  session message, so in tester mode it only reaches a tester who wrote in the last 24 hours. An approved template
  comes before any real check-in.
- Media download.
- Mandarin and other languages: they go to staff.
- The proxy scope for family members (AC-03): every proxy request goes to staff for now.
- FD-02, FD-04, FD-07, FD-09, RT-02 booking with the lab, RT-07 and RT-09, AH-02 and AH-03, CR-01 to CR-05, HE-06
  beyond the check-in, and RP-03.

**Sources.**
- WhatsApp Cloud API webhooks and signature (`X-Hub-Signature-256`, HMAC-SHA256 with the app secret).
- Interactive reply buttons (up to 3, titles up to 20 characters) and list messages (up to 10 rows, titles up to 24).
  Meta's developer documentation, as cited in spec 003.
