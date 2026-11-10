# The doctor's own line: setup, rehearsal and launch

How to take spec 005 from this repo to Thomas's and Jose's patients. Everything runs in Google Cloud's Hong Kong
region; real patient data never leaves it (`docs/DECISIONS.md`, 10 Nov). Steps marked **Thomas** need his accounts.

## 0. Try it now, on a laptop (5 minutes)

```bash
pip install -e ".[dev,careline]"
python -m backend.line sim          # then open http://localhost:8080/sim
```

The simulator shows two phones, one patient and one doctor, with fakes behind them. It needs no accounts and
nothing leaves the machine. Type `Hi Dr Hu [TH01]` on the patient's phone, join, then "dictate" a voice note on the
doctor's phone and tap Send. The fake model copies your words as they are; the real model (Vertex) translates them
and lays them out.

## 1. Accounts (Thomas, this week)

| What | Where | Done when |
|---|---|---|
| **Meta: a staging number** | Meta app dashboard → WhatsApp → API setup: Meta's free test number | You can send a test message to your own phone |
| **Meta: the live number** | Add a real number that isn't on any WhatsApp app; display name like "tmrw care". Business verification of TMRW CONSULTING LIMITED | Display name approved |
| **Meta: a permanent token** | Business settings → System users → add one with `whatsapp_business_messaging` and `whatsapp_business_management`; generate a token | Token saved somewhere safe; it goes into Secret Manager in step 2 |
| **Meta: templates** | `WA_TOKEN=… WA_WABA_ID=… python -m backend.line templates submit`, then `… templates status` | All five show APPROVED (minutes to 48 hours) |
| **Google Cloud** | A project with billing on (e.g. `tmrw-care-line`). In Vertex AI → Model Garden, check that Gemini 2.5 Flash is offered in `asia-east2` (Hong Kong); if not, use `asia-southeast1` and set `VERTEX_REGION` | Model available in the region |
| **Vertex data terms** | Read Google's "Vertex AI data residency" and "zero data retention" pages for the region; turn off any abuse-monitoring logging or caching you're allowed to | You're satisfied with where inputs are processed |
| **Stripe** | Dashboard → Connect → get started as a platform (marketplace) in Hong Kong. Test keys for staging, live keys for live | A test Express account can be created |
| **Lawyer (recommended)** | A Hong Kong lawyer reads the privacy notice (`/privacy`), the HK$30 patient service fee and the Medical Council's code on fee-sharing and telemedicine | Their comments are in a `signal` or decision row |
| **Clinics** | Each clinic where you or Jose do sessions agrees in writing that you may offer your line to patients you see there, and that repeats can be collected there | An email from each clinic |

## 2. Staging (Thomas, in Cloud Shell, about 30 minutes)

```bash
git clone https://github.com/flytonewyork/harness && cd harness
gcloud config set project <project-id>
deploy/line.sh setup staging        # pastes: WhatsApp token and app secret, Stripe test secret; the rest are generated
deploy/line.sh deploy staging       # asks for the test number, its phone number id and your testers' numbers
deploy/line.sh scheduler staging
deploy/line.sh urls staging         # paste the webhook URL and verify token into Meta; the Stripe URL into Stripe
deploy/line.sh secret staging STRIPE_WEBHOOK_SECRET   # Stripe's webhook signing secret; then deploy again
deploy/line.sh doctor staging       # you, then Jose: code TH01 / JS01, your own WhatsApp numbers, clinic, fees
```

Open `<url>/healthz`, then `<url>/j/TH01`. Send "hi" from your own WhatsApp to the line. You get the doctor
welcome and a payouts link (Stripe test mode).

## 3. Rehearsal week (Thomas and Jose, with friends role-playing synthetic patients)

Staging only answers numbers on the tester list, so add each friend's number. Friends play made-up patients. They
must never send their own health information.

For each doctor, do at least 10 role-played visits, covering:
1. join from the printed QR card;
2. a voice-note plan in English, one in Cantonese and one mixed;
3. a question in hours and one at night;
4. a red-flag message ("chest pain");
5. STOP, then JOIN;
6. a repeat that's approved and paid with Stripe's test card 4242 4242 4242 4242, then picked up;
7. a result review sent as a photo;
8. the day-3 check-in: run the scheduler, or wait.

Measure:
- time from the end of the voice note to the patient getting the plan (target under 2 minutes);
- the share of drafts sent without edits;
- anything the model said that you didn't.

File what you find as issues, and any partner reaction as a `signal`.

Approve the wording: read every text in `backend/line/texts.py` (English and Chinese), change what you'd say
differently, and set `APPROVED_BY` to your name and the date.

## 4. Launch (around 24 Nov)

- [ ] Templates approved on the live number.
- [ ] A live test payment of HK$1 + HK$30, refunded.
- [ ] Clinics' written OK on file.
- [ ] Texts approved (`APPROVED_BY`).
- [ ] `deploy/line.sh setup live`, `deploy live` (you type `yes` as the doctor signing off), `scheduler live`,
      `urls live`, `doctor live` for you and Jose.
- [ ] Print the QR cards (`<url>/j/TH01`, `/j/JS01`) and put one on the consult desk.

## Every day

- **The line messages you.** Joins, questions, repeats and drafts arrive on your own WhatsApp. If you haven't
  written to the line in 24 hours, the first item comes as a "new items, tap to see" template.
- **After a consult:** reply to the "joined" message with a voice note, check the draft, tap Send.
- **Commands:** `QUEUE` (what's waiting), `WEB` (a sign-in link for the web page: patients, history, fees, payouts,
  record export), `QR`, `PAUSE 3` (away 3 days: patients are told you're away and to call 999 or see another doctor),
  `RESUME`.
- **Record-keeping is still yours.** Export a patient's line record from the web page into your clinical notes.

## If something goes wrong

- **The model fails or is slow:** the line tells you and you can type the plan instead. Questions still reach you
  without a draft.
- **A message is blocked by a check:** you get "Blocked a message to X", and nothing was sent. Reply yourself.
- **Stop everything:** in Meta's app dashboard (WhatsApp → Configuration), unsubscribe the webhook from `messages`.
  The line stops receiving; patients' messages wait in WhatsApp until it's back.
- **A patient asks for their data or to be deleted:** ask Claude in a session to write the export or deletion
  script, but run it yourself in Cloud Shell. Claude sessions never read the live data.
