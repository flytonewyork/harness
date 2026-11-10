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
2. "You've reached Dr Thomas Hu's care line, an automated service by tmrw." Two buttons: join or not now. Then their
   name and date of birth.
3. Within about two minutes their plan arrives in English and Chinese, in the doctor's words: medicines, what to
   watch for, next steps. It ends with the doctor's hours and the safety line ("if it can't wait, call 999 or go to
   A&E").
4. Day 3: "It's 3 days since your visit with Dr Thomas Hu. How are you feeling?" with Better / About the same / Worse.
5. Any question, any hour: an instant reply saying who answers and by when ("Dr Thomas Hu will read this and reply
   by today 15:15").

**Doctor (target: first plan sent within 60 seconds of finishing the voice note).**
1. Thomas adds the doctor (`deploy/line.sh doctor`). The doctor's first message to the line gets a welcome with
   their QR card, a payouts link (Stripe, about 10 minutes) and the commands. Fees, the membership price and one QR
   card per clinic are on the web page.
2. When a patient joins, the doctor's chat shows "CHAN Tai Man (b. 1964) just joined your line at Clinic X. Reply to
   this message with a voice note or text for their plan."
3. The doctor holds the mic for 20 seconds, in English, Cantonese or both. The line answers "Got it. Drafting…",
   then a draft plan with Send / Edit / Skip. Every line in it is tied to words the doctor said, and the draft lists
   the check-in and reminders that Send will schedule.
4. Later: questions arrive with a drafted reply (Send draft / I'll write / I'll call). A repeat request arrives as
   Approve / Needs a visit / Decline. The web page shows the month's earnings and members.

## How it works

| Part | File | What it does |
|---|---|---|
| Doctors and clinics | `agent.py`, `store.py` | Many doctors on one number. A doctor has a code, names, their own WhatsApp number, hours, fees, a membership price, a Stripe account and up to nine clinics (name, address, phone, optional hours, and a switch that turns charging off for that clinic's patients). A message's sender decides its side: a registered doctor's number is the doctor; anyone else is a patient. |
| Store | `store.py` | Memory in tests, SQLite on a laptop, Firestore live (serverless, `asia-east2`, no database to run). Collections: doctor, patient, wa, plan, item, handoff, payment, msg, ctx, dsession, token, seen, event. The record keeps every message and decision (RP-01); the audit trail (`event`) refuses message text, names and numbers. |
| Join | `agent.py` | The QR's pre-typed code picks the doctor, and the clinic (`[TH01.2]` is the doctor's second clinic). Then consent, name and date of birth. There is no EMR to match against, so the doctor is the check: the "joined" message shows the name and birth year with a "Not my patient" button, which tells the person and closes the line to that number. Nothing clinical reaches a patient until the doctor sends it. Someone who says no, or STOP half-way, leaves nothing behind. |
| Returning patients | `agent.py` | A patient who scans the card again at a later visit, at any of the doctor's clinics, is "back": the doctor is told, and the next note becomes a new plan. Medicines from every plan stay available for repeats. This is what "the line follows the doctor" means in the product. |
| Doctor console | `agent.py` (`_doctor`) | The doctor's side of the same number. A voice note or text becomes a plan when the patient is new or back; otherwise it is a message to that patient, shown back for Send first. A typed swipe-reply to an open question goes out at once, word for word, labelled as written by the doctor. With no swipe-reply and more than one patient waiting, the line asks who it is for. Commands: QUEUE, WEB, QR, PAUSE, RESUME, HELP. |
| Plan from a voice note | `drafting.py`, `models.py` | Vertex Gemini turns the audio into a transcript, then into items (medicines, what to watch for, next steps, check-in day, reminders), each carrying the transcript words it came from. An item whose quote isn't in the transcript is dropped and shown to the doctor (`unsupported_claim`, LoopBench's rule). So is one with a number the doctor didn't say; the check reads digits, Chinese numerals and English number words. The patient's text is fixed bilingual headings around the doctor's items. Editing a plan sends the doctor's typed text exactly and drops the plan's reminders. |
| Model port | `models.py` | `VertexGemini` (region and model from settings, `asia-east2` by default) and `FakeModel` for tests and sim. Nothing in tests or CI calls a real model. |
| Ask the doctor | `agent.py`, `drafting.py` | A question gets the fixed acknowledgement with a reply-by time in the doctor's hours and goes to the doctor, with a draft reply written only from the approved plan. A draft with a number the plan doesn't have is dropped. Red flags and crisis messages keep spec 004's fixed replies and alert the doctor with the patient's number. |
| Check-ins and reminders | `agent.py` (`tick`) | Due items come from the approved plan. Cloud Scheduler ticks every minute and sends what's due in the daytime (09:00–20:00). Outside WhatsApp's 24-hour window they go out as approved utility templates. A reminder that names a sensitive test is replaced by "a next step in your plan is due", so it never shows on a lock screen. |
| Sending | `agent.py` (`deliver`, `_guard`, `_send`), `transport.py` | Every patient message passes the outbound checks (spec 003's message-side hard fails, plus a hash check that approved text is exactly what the doctor approved). Messages are split to WhatsApp's limits. Outside the 24-hour window, a message without a template waits behind a "tap to read" template. A failed send is recorded and the doctor is told. |
| Paid follow-ups | `payments.py`, `agent.py` | Repeat: the patient picks a medicine from their plans; the doctor taps Approve / Needs a visit / Decline; only on Approve does the patient get a Stripe Checkout link (doctor's fee + HK$30); once paid, the patient picks a collection window in the clinic's hours and the doctor is told (the doctor arranges it with the clinic). Result review: the patient sends a photo or PDF; the line quotes the fee; once paid, the doctor opens the file from the web page and answers by voice note or text. The model never reads the report. No charge when the doctor says a visit is needed. |
| Membership | `payments.py`, `agent.py` | Below, under Money. |
| Stripe | `payments.py` | Connect Express accounts for doctors, onboarded by a link in their chat. Destination charges: `application_fee_amount` HK$30 per item, `application_fee_percent` on memberships. Webhook events: `checkout.session.completed`, `customer.subscription.deleted`, `account.updated`. |
| Doctor web page | `static/doctor.html`, `api.py` | Opened by a one-time link sent to the doctor's WhatsApp; the link is used up by pressing Sign in, not by opening it. This month's numbers, the queue, patients with their thread and reports, a printable record for the doctor's own notes (record-keeping stays the doctor's duty), fees, membership, QR cards and payouts. A doctor sees only their own patients. |
| Templates | `templates.py`, `python -m backend.line templates submit` | Five utility templates, in English and Chinese: check-in, reminder, "has a message for you", payment link, and the doctor's "new items" (the doctor is outside the 24-hour window too). |
| Hosting | `deploy/line.sh`, `deploy/line.Dockerfile` | Cloud Run (`asia-east2`, exactly one instance with CPU always on, so the webhook is never cold and messages are handled in order), Firestore, Secret Manager, Cloud Storage for media (deleted after 30 days by a lifecycle rule), Cloud Scheduler every minute. Deployed by Thomas from Cloud Shell with one script, so no cloud keys live in GitHub or in Claude sessions. |
| Simulator | `static/sim.html`, `api.py` (`add_sim`) | `python -m backend.line sim`: a patient's phone and the doctor's phone on one page, with fakes for WhatsApp, the model and Stripe. Sim mode only. |

**Modes.** `sim` (fakes for WhatsApp, the model and Stripe), `staging` (real WhatsApp test number, Stripe test mode,
role-play patients only), `live`. `live` still refuses to start without `CARELINE_I_HAVE_SIGN_OFF=yes`; Thomas gives
that sign-off as the doctor.

## Where real patient data may be

This is the first time tmrw holds real patient data, so the boundary is written down.
- **Only** in the live service's Google Cloud project (Firestore, Cloud Storage, Vertex calls), in `asia-east2`.
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
| Patient (member) | The doctor's monthly membership price, plus tmrw's 10% on its own line; repeats and result reviews then carry no per-item charge | The doctor's Stripe account; tmrw's share as the Connect application fee |
| Doctor | Nothing during the pilot. Subscription switch off; its price and limits are settings | tmrw, once switched on |

**The membership (bet 9).** The doctor sets a monthly price on the web page (0 means off). After a plan is sent, the
doctor's confirmation carries an "Offer membership" button, and the web page has one per patient. Each patient is
offered once, by the doctor's own choice; the line never offers it by itself, and "No thanks" is a button. Joining is
a Stripe subscription (Checkout in subscription mode, destination charge, tmrw's share as the application fee
percentage). Members' repeats and result reviews go straight through with no per-item charge. CANCEL MEMBERSHIP
cancels the Stripe subscription; a cancellation in Stripe ends it too (`customer.subscription.deleted`).

Stripe's fees on a destination charge are paid by the platform, so tmrw nets about HK$16 on a HK$300 repeat (HK$330
charged). The doctor's dashboard shows what a subscription would have cost them each month, so its price can be
tested later without switching it on.

## Done when

- `python -m pytest -q` passes. The line's own cases are in `tests/test_line.py` and `tests/test_line_api.py`:
  - joining, declining, STOP, "Not my patient", returning patients, one number on two doctors' lines;
  - plan faithfulness: planted items and doses the doctor never said are dropped; Cantonese numerals and spoken
    English numbers are read; an edit goes out word for word;
  - questions in and out of hours, red flags and crisis messages before and after joining;
  - paid repeats and result reviews, the membership, Stripe's signature and retries;
  - the 24-hour window, templates, WhatsApp's length limits, a failed send;
  - the outbound checks, the audit trail's refusal of patient text, sign-in, and what a doctor may see.
- Spec 004's 63 eval cases still pass with zero hard fails (`tests/test_careline.py`). The line's cases are tests,
  not yet rows on the evals dashboard by feature id: that port, and held-out red-team cases written by someone other
  than the builder, are `NOW.md` Next 1.
- A rehearsal week in `staging`, with Thomas and Jose role-playing at least 10 patients each, has measured:
  - time from voice note to the patient receiving the plan (target under 2 minutes);
  - the share of plan drafts sent without edits.
- Before `live`: Meta has approved the templates, a test payment has been made and refunded, Thomas has approved
  every text, and each clinic has agreed in writing.

## Invariants touched

- **Authority line:** kept. The agent opens and drafts; only a doctor approves, sends clinical words, claims or
  closes.
- **Verbatim quotes:** extended to the plan. Every plan item must quote the doctor's transcript word for word.
- **No LoopBench import:** unchanged. LoopBench and the evals stay the gate.
- **Synthetic data:** still the rule for the repo and every Claude session. Real data lives only in the live project,
  as above.

## Where the build stands (10 Nov)

- Built and reviewed: `backend/line/`, 52 tests, the simulator, the deploy script and `docs/LINE_RUNBOOK.md`.
- Proven only with fakes so far. The Vertex adapter is tested with Google's client faked, and the deploy script has
  not been run. The first real-model drafts, in Cantonese especially, and the first real WhatsApp and Stripe calls
  come in staging. Expect small fixes there.
- Not yet: Meta templates approved, a staging deployment, the texts approved by Thomas, the clinics' OK.
- Known limits, accepted for the pilot:
  - one message is handled at a time, so a draft in progress (about 15 seconds) holds up other messages briefly;
  - identity is the doctor's own check of name and birth year, not a match against a record;
  - a patient on two doctors' lines talks to whichever doctor wrote to them last, until they scan a card again;
  - the doctor arranges collection with the clinic; the line only tells the doctor the window the patient chose;
  - Mandarin and other languages reach the doctor as typed.

## Out of scope

- Each doctor's own number (next, once Meta approves display names).
- EMR or lab integrations; video calls; delivery of medicines.
- Languages other than English and Chinese (Cantonese); Mandarin goes to the doctor as typed.
- The doctor subscription switched on. On 10 Nov Thomas saw dozens of post-visit clinic tools competing on ads; in
  a market like that, subscription prices fall towards zero, so the patient's service fee is the model to test.
- Group or clinic accounts.
