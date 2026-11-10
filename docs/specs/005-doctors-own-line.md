# 005: The doctor's own line, live

**Problem.** On 10 Nov Thomas locked tmrw's direction: a care line that belongs to the doctor, not the clinic, paid
from care revenue rather than software spend (`docs/DECISIONS.md`, 10 Nov). Spec 004 built the care line's safety core
against a simulated clinic, in tester mode only. Nothing a real doctor or patient can use exists yet: there is no
doctor side, no plan from the doctor's own words, no payments and no hosting that stays up. Thomas and Jose want to
offer it to their own patients in about two weeks, and the first few messages have to make both of them say "oh".

**Change.** A live, multi-doctor care line on one shared WhatsApp number, hosted in Google Cloud's Hong Kong region.
Each doctor works from their own WhatsApp, with a web page for the long view. It is built on `backend/careline/`
(spec 004) and keeps all of its safety core.

## Decisions this spec builds on (Thomas, 10 Nov 2026)

| Question | Decision |
|---|---|
| First patients | Thomas's and Jose's own sessions, with each clinic's OK |
| Model on patient content | Google Vertex AI (Gemini), Hong Kong region (`asia-east2`) |
| Numbers | One shared number now; each doctor moves to their own number once Meta approves it |
| Doctor's surface | WhatsApp for everything day to day, plus a web page |
| Day-one flows | Voice note → written plan; check-ins and reminders; ask the doctor; paid remote follow-up |
| Pricing | Hybrid: free for the doctor; the patient pays a HK$30 tmrw service fee on paid follow-ups, on top of the doctor's own fee; a doctor subscription is a setting, off |
| Paid items | Repeat prescription; result review. Joining, the plan, check-ins, reminders and clinical questions are free |
| Payments | Stripe Connect: the doctor's fee goes to the doctor's account, and tmrw's fee is kept apart |
| Repeats | Collected at the clinic where the doctor sessions |
| Go-live gate | Live and paid from the first patient. Thomas accepts the regulatory risk; a lawyer's review is recommended but doesn't block |
| Launch | Thomas and Jose together, about two weeks out, after a rehearsal week with role-play patients |
| Brand | "Dr Thomas Hu's care line", with a small "by tmrw" |
| Codebase | Harness, on top of PR #26 |

## The first minutes

**Patient (target: plan in hand before they leave the room).**
1. At the end of the consult the doctor shows a QR card. The patient scans it and WhatsApp opens with
   "Hi Dr Hu [TH01]" already typed. They tap send.
2. "This is Dr Thomas Hu's care line, by tmrw." Two buttons: join or not now. Then their name and date of birth.
3. Within about two minutes their plan arrives in English and Chinese, in the doctor's words: what to take, what to
   watch for, what happens next and when. It ends with the doctor's hours and the safety line ("if it can't wait,
   call 999 or go to A&E").
4. Day 3: "How are you going since Tuesday?" with Better / Same / Worse.
5. Any question, any hour: an instant reply saying who answers and by when ("Dr Hu replies by 6 pm today").

**Doctor (target: first plan sent within 60 seconds of finishing the voice note).**
1. Thomas adds the doctor (`deploy/line.sh doctor`). The doctor's first message to the line gets a welcome with
   their QR card, a payouts link (Stripe, about 10 minutes) and the commands; fees and hours are on the web page.
2. When a patient joins, the doctor's chat shows "Chan Tai Man (b. 1964) joined at Clinic X. Reply with a voice
   note for their plan."
3. The doctor holds the mic for 20 seconds, in English, Cantonese or both. About 15 seconds later a draft plan
   comes back with Send / Edit / Skip. Every line in it is tied to words the doctor actually said.
4. Later: questions arrive sorted, each with a drafted reply (Send / Edit / I'll call). A repeat request arrives as
   "Approve HK$X / Needs a visit / Decline". The web page shows the day's earnings.

## How it works

| Part | File (new or changed) | What it does |
|---|---|---|
| Doctors and clinics | `agent.py`, `store.py` | Many doctors on one number. A doctor has a code, names, clinics, hours, fees and a Stripe account. Each clinic has hours, pickup windows, a contact, and whether consent is on file. A message's sender decides its role: a doctor's registered number is the doctor; anyone else is a patient. |
| Store | `store.py` | Memory in tests, SQLite on a laptop, Firestore live (serverless, `asia-east2`, no database to run). Collections: doctor, patient, wa, plan, item, handoff, payment, msg, ctx, dsession, token, seen, event. The record keeps every message and decision (RP-01); the audit trail (`event`) refuses message text, names and numbers. |
| Join | `agent.py` | The QR's pre-typed code picks the doctor. Then consent, name and date of birth. With no EMR, the doctor confirms the link from their chat. Only after that does anything from the record go out. |
| Doctor console | `agent.py` (`_doctor`) | The doctor's side of the same number: voice note → plan, buttons on every draft, typed replies to a patient (sent as the doctor's own words, still through `check_outbound`), `queue`, `hours`, `pause`. |
| Plan from a voice note | `drafting.py`, `models.py` | Vertex Gemini turns the audio into a transcript, then into a structured plan (medicines, what to watch for, next steps, check-in day, reminders), each item carrying the transcript words it came from. A plan item whose quote isn't word for word in the transcript is dropped and flagged, the `unsupported_claim` rule from LoopBench. The patient's text is built from fixed bilingual layouts around the doctor's items. The doctor approves every word before it is sent. |
| Model port | `models.py` | `VertexGemini` (region from settings, `asia-east2` by default) and `FakeModel` for tests and sim. Nothing in tests or CI calls a real model. |
| Ask the doctor | `agent.py`, `drafting.py` | A clinical question gets the fixed acknowledgement with a reply-by time in the doctor's hours, and goes to the doctor with a drafted reply grounded only in the approved plan and the thread. Red flags keep spec 004's fixed reply and alert. |
| Check-ins and reminders | `agent.py` (`tick`) | Due items come from the approved plan. Outside WhatsApp's 24-hour window they go out as approved utility templates. A Cloud Scheduler tick every minute sends what's due, in the doctor's hours only. |
| Paid follow-ups | `payments.py`, `agent.py` | Repeat: the patient picks a medicine from their approved plan; the doctor taps Approve / Needs a visit / Decline; only on Approve does the patient get a Stripe Checkout link (doctor's fee + HK$30); once paid, the patient picks a collection window in the clinic's hours and the doctor is told (the doctor arranges it with the clinic). Result review: the patient sends a photo or PDF; the line quotes the fee; once paid, the doctor gets the file and a draft explanation for their eyes only, then approves what the patient sees. No charge when the doctor says a visit is needed. |
| Stripe | `payments.py` | Connect Express accounts for doctors, onboarded by a link in their chat. Destination charges with `application_fee_amount` = HK$30, and a webhook for `checkout.session.completed`. The subscription switch is in settings, off. |
| Doctor web page | `static/doctor.html`, `api.py` | Opened by a one-time link sent to the doctor's WhatsApp. Today, queue, patients (thread, plan, payments), settings, earnings, and record export as a PDF for the doctor's own notes (record-keeping stays the doctor's duty). |
| Templates | `templates.py`, `python -m backend.line templates submit` | Utility templates in English and Chinese: check-in, reminder, "Dr X has replied", payment link, and the doctor's "new item" (the doctor is outside the 24-hour window too). Submitted by script, with status read back. |
| Hosting | `deploy/line.sh`, `deploy/line.Dockerfile` | Cloud Run (`asia-east2`, exactly one instance with CPU always on, so the webhook is never cold and messages are handled in order), Firestore, Secret Manager, Cloud Storage for media (deleted after 30 days by a lifecycle rule), Cloud Scheduler every minute. Deployed by Thomas from Cloud Shell with one script, so no cloud keys live in GitHub or in Claude sessions. |

**Modes.** `sim` (fakes for WhatsApp, the model and Stripe), `staging` (real WhatsApp test number, Stripe test mode,
role-play patients only), `live`. `live` still refuses to start without `CARELINE_I_HAVE_SIGN_OFF=yes`; Thomas gives
that sign-off as the doctor.

## Where real patient data may be

This is the first time tmrw holds real patient data, so the boundary is written down.
- **Only** in the live service's Google Cloud project (Cloud SQL, Cloud Storage, Vertex calls), in `asia-east2`.
- **Never** in git, tests, CI, logs, Slack, the morning brief, Claude Docs, artifacts or Claude sessions. Logs carry
  ids and event names, never message text, names or numbers. Claude sessions work on `sim` and `staging` only.
- Google's documentation says regional endpoints "don't guarantee data residency or in-region ML processing". Before
  launch, check Vertex's data-residency terms for `asia-east2`, and turn off any data caching or logging Google
  offers for these calls.
- tmrw processes data for the doctor (the data user, under the PDPO). The welcome message links a plain privacy
  notice, and every patient consents before anything else happens.

## Money

| Who pays | What | Goes to |
|---|---|---|
| Patient | The doctor's own fee for a repeat or a result review | The doctor's Stripe account |
| Patient | HK$30 tmrw service fee, shown on its own line | tmrw (the Connect application fee) |
| Doctor | Nothing during the pilot. Subscription switch off; its price and limits are settings | tmrw, once switched on |

Stripe's fees on a destination charge are paid by the platform, so tmrw nets about HK$16 on a HK$300 repeat (HK$330
charged). The doctor's dashboard shows what a subscription would have cost them each month, so its price can be
tested later without switching it on.

## Done when

- `python -m pytest -q` passes, including new cases for:
  - the doctor console;
  - plan faithfulness: synthetic transcripts in English, Cantonese and both mixed, with planted items that aren't in
    the transcript, which must be dropped;
  - joining with a doctor code;
  - the paid repeat and result review paths;
  - templates outside the 24-hour window;
  - hours.
- `python -m backend.careline.evals` reports zero hard fails on the old 63 cases plus the new ones.
- A rehearsal week in `staging`, with Thomas and Jose role-playing at least 10 patients each, has measured:
  - time from voice note to the patient receiving the plan (target under 2 minutes);
  - the share of plan drafts sent without edits.
- Before `live`: Meta has approved the templates, a test payment has been made and refunded, and each clinic has
  agreed in writing.

## Invariants touched

- **Authority line:** kept. The agent opens and drafts; only a doctor approves, sends clinical words, claims or
  closes.
- **Verbatim quotes:** extended to the plan. Every plan item must quote the doctor's transcript word for word.
- **No LoopBench import:** unchanged. LoopBench and the evals stay the gate.
- **Synthetic data:** still the rule for the repo and every Claude session. Real data lives only in the live project,
  as above.

## Where the build stands (10 Nov)

- Built: `backend/line/` with the simulator (`python -m backend.line sim`), 33 tests (`tests/test_line.py`,
  `tests/test_line_api.py`), the deploy script and `docs/LINE_RUNBOOK.md`.
- Proven only with fakes so far. The Vertex adapter is tested with Google's client faked. The first real-model
  drafts, in Cantonese especially, come in the rehearsal week.
- Not yet: Meta templates approved, a staging deployment, the texts approved by Thomas, the clinics' OK.

## Out of scope

- Each doctor's own number (next, once Meta approves display names).
- EMR or lab integrations; video calls; delivery of medicines.
- Languages other than English and Chinese (Cantonese); Mandarin goes to the doctor as typed.
- The doctor subscription switched on. On 10 Nov Thomas saw dozens of post-visit clinic tools competing on ads; in
  a market like that, subscription prices fall towards zero, so the patient's service fee is the model to test.
- Group or clinic accounts.
