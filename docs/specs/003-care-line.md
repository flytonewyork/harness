# 003: The care line, a doctor's WhatsApp with an AI care team behind it (draft, 6 Nov 2026)

**Status:** a candidate direction for the 26 Nov go/no-go, not a decision. It builds on spec 002 (doctor signs,
agent does) and on what the Apricot call showed (#15). **This week's goal:** make it concrete enough that Thomas
wants to take GP sessions and call-out jobs to test it.

## Why this, after the Apricot call

- **The follow-up add-on is weak.** A follow-up list sold to GP clinics on a mature system like VITA is a
  feature, not a company. VITA already has tasks, recalls, lab statuses, patient filters and WhatsApp automation.
  Its scribe (December) and Heidi II's agents (28 Sep 2026) both take the clinic-side follow-up work. Staff "don't
  complain much anymore". Dropped loops are real but invisible, so nobody buys relief from them.
- **What still drops is on the patient's side of the loop.** Nobody reads the result. "Urgent follow-up" and
  "Contacted" are labels, not closed loops. Doctors can't download from eHealth, but patients can see their own lab
  reports in the eHealth App.
- **The doctor's pain that *is* felt:** "here's my WhatsApp" is already common in Hong Kong. It's the moment a
  one-off visit becomes a relationship. It also costs the doctor their evenings, which is why many don't offer it.
- **The bigger pool:** clinic software is a sliver of spending. The labour of the front office, and the clinic's
  share of each fee, are much bigger. That share is assumed at about half; it's to be confirmed.

## The product in one line

**A doctor's own WhatsApp care line, with an AI care team behind it.** The patient feels they have their doctor's
WhatsApp. The doctor gets an inbox where the work is already done. It's positioned as **"your AI can read your
health; only a doctor can act on it"**.

## The pitch (to test in the ten doctor conversations)

> **"Give patients your WhatsApp. Your AI care team handles the rest, and your patients stay yours."**

The stronger, money-led version: **"Turn your WhatsApp into your practice. Your AI care team finds the care your
patients need. You sign it, and you get paid."**

Split-test the two angles inside it:
- **Evenings:** "Give every patient your WhatsApp, and still have your evenings."
- **Ownership:** "Your patients follow you, not the clinic."

Record which one makes doctors ask how to get it. Have one-line answers ready for the doubts:
- **Safety:** nothing clinical goes out without your signature, and emergencies get a fixed response straight away.
- **The clinic:** patients rebook with the clinic, and you keep the relationship.

## How it works

**The kit:**
- a dedicated WhatsApp Business number (never a personal one)
- a plan card with a QR code, in English and Traditional Chinese
- a secure upload link (one-time code, no app, no login)
- a consent screen the first time
- the doctor's inbox and loop list
- write-back to the record

**In scope (v1):**
- **Handoff:** the plan card, sent before the patient leaves.
- **Results:** sent with an explanation the doctor has signed, and the next step already set up.
- **Rechecks, reviews and vaccines:** reminders, then chasing until the patient has booked and attended.
- **Hospital and referrals:** the patient sends a photo of the letter. *The patient is the integration.*
- **Chronic readings:** home BP and glucose logs.
- **Medicine questions:** answered from content the doctor has approved. Repeat prescriptions are drafted for the
  doctor to sign.
- **Admin:** booking, receipts, certificates.
- **Red flags:** a fixed instruction (call 999 or go to A&E) and an alert to the doctor. Fixed rules, not AI
  judgement.

**Out of scope (v1):**
- diagnosing new problems by chat
- emergencies, beyond the fixed red-flag response
- any clinical advice the doctor hasn't signed
- new prescriptions by chat
- managing a crisis, beyond escalation
- family members acting for a patient without consent
- a general health chatbot (WhatsApp bans general-purpose bots from 15 Jan 2026; a business serving its own
  customers is allowed)

**The five moments to design around:**
1. **The handoff:** the plan lands before the patient reaches the lift.
2. **The result:** explained by the doctor, with the next step already done.
3. **The check-in:** the doctor follows up first.
4. **The photo:** a hospital letter sent, understood, and acted on.
5. **The scare:** a clear instruction in seconds, and the doctor is alerted.

For the doctor: **inbox zero in ten minutes**, twice a day.

## How it gets paid: product-led on both sides (a hypothesis)

The billable unit moves from the visit to the **task**, done on WhatsApp and signed by the doctor. Hong Kong private care
is mostly paid by patients directly, so no payer has to approve a code. Elsewhere, between-visit work is already paid
for: US Medicare e-visits and chronic-care management, and Australian chronic-care items.

| Service on the line | How it's paid | Guardrail |
|---|---|---|
| Remote consult (follow-up, results, chronic review) | Telemedicine fee via a payment link | Medical Council telemedicine guidelines |
| Repeat prescription | Review fee plus the drug (delivered or collected) | Stable medicines only, after review, with monitoring up to date |
| Lab tests (rechecks, screening, CDCC targets) | Lab fee, with the doctor's charge disclosed | Clinically indicated, with verbatim evidence |
| Reports, certificates, insurance forms | Fixed fees | |
| Membership (access, memory, check-ins) | Monthly | Services extra, or included in higher tiers |

**The doctor's ladder:**
1. **Free:** the line, the plan card, upload links, the loop list.
2. **Paid** once agents do real work (drafts, chasing, payment links), priced so the line pays for itself from the
   work it brings in.
3. **Practice:** memberships, a cover network, and the AI-native record as the doctor's own record.

**The patient's ladder:**
1. **Free:** messaging, the plan card, red-flag responses, and **any follow-up of an abnormal or urgent result**.
2. **Pay per service.**
3. **Membership** for patients who want ongoing care.

**Guardrails:**
- **Safety is never paywalled.** Red flags and acting on abnormal results are free, whatever the patient pays.
- **The agent finds care that is due; it never manufactures care.** Every billable item carries its clinical reason
  with verbatim evidence, is signed by the doctor, and shows its price up front.
- **The doctor is always the merchant.** tmrw charges a subscription, not a share of fees, until the Medical
  Council's rules on sharing fees with non-doctors are checked.

**The illustration to test** (assumptions, not data): 30 tasks a day at HK$150–250, each taking 3–5 doctor-minutes
after the agent's preparation, is about HK$2,500+ per doctor-hour. A clinic session pays the doctor about
HK$1,200 an hour (half the fees).

## The record behind it: AI-native, structured where it must be

1. **Raw log:** everything as it happened, append-only. This is the legal record.
2. **Signed facts:** allergies, current medicines, active problems. Each one confirmed by a doctor, with its
   source in the log.
3. **Derived views:** the problem list, open loops, trends, a pre-visit summary. Agents build these, and every
   claim quotes its source word for word (the harness's existing rule).
4. **Outputs:** strictly structured. The plan card, a recall, a lab request, a referral letter, a prescription.

A fixed tool is added only when a real output needs one.

## One product, two modes

- **Doctor mode** (independent and sessional doctors) comes first. Product-led, and Thomas is the first user.
- **Clinic mode** (groups, EC) comes later.

About 90% is shared. The rules that keep the two compatible:
- every patient relationship has an owner (a doctor or a clinic)
- every account names where its record lives
- at a clinic that runs clinic mode, the doctor's work happens inside the clinic's account

| | EC Healthcare | Adrian's longevity clinic | Virtus and other Apricot clinics (sessions) |
|---|---|---|---|
| Line | Co-branded with EC, as EC's pilot | The clinic's line; the whole front door | Thomas's line, agreed with the clinic |
| Record | EC's in-house system | The AI-native record can be the record | VITA (manual write-back, later the API) |
| WhatsApp does | Follow-up, and later EC's front door | Everything: booking, labs, callbacks, payments | Follow-up only; booking and billing stay with the clinic |
| Money | tmrw consulting fee; EC owns patient data, tmrw keeps the software | Partnership or equity, plus membership | No separate charge at first |
| Senior oversight | EC's senior doctors | A senior medical director | Virtus's seniors |

## Heidi

Heidi II works inside the clinic: tasks, referrals, panel sweeps, routines and memory, for the doctor. Its public
material mentions no patient-facing messaging. The care line works **between the doctor and the patient**, so it
complements Heidi rather than competing. Day one, the doctor pastes Heidi's patient summary into the care line.
Later, Heidi's connectors or API, if they open. The care line never depends on Heidi.

## Safety for a newly practising doctor

- **Protocols** for the clinical scope (evidence-based prevention and stable chronic care, with written escalation
  thresholds).
- **A senior reviews a sample of sign-offs**, and the system logs every disagreement.
- **Scope widens as the disagreement rate falls**: the agent's autonomy ladder, applied to the doctor.

## Done when (the evals come first)

- **LoopBench care-line simulator:** synthetic patients send messages about their loops, including red flags.
  - Red-flag catch rate: 100% on a defined test set.
  - Made-up claims: 0.
  - Also scored: loops closed through patients, and doctor minutes per item.
- **Sign-off trainer:** Thomas's approve, edit or reject decisions on synthetic items, scored against the answer
  key.
- **Ten doctor conversations by 26 Nov,** each asking:
  - Do you give patients your WhatsApp?
  - What's the worst part?
  - Would you pay HK$X a month?
- **Manual pilot design ready:**
  - a WhatsApp Business number, the plan card with QR code, and labels as loop statuses
  - timed by Thomas at his first sessions or at Adrian's clinic, with the clinic's agreement
  - **no AI on real patient data** until the model question in `DECISIONS.md` and the consent wording are settled
- **The numbers that decide it:**
  - doctor minutes per patient per month: target under about 15
  - what patients will pay each month: target above about HK$250
  - the scan → message-again → loop-closed funnel

## Others already doing parts of this (checked 6 Nov 2026)

Patients uploading their records to AI is now mainstream. What differs is **whose doctor** answers, and **where**.

- **ChatGPT Health** (7 Jan 2026) and **Claude's consumer health-record connectors** (11 Jan 2026) read patients'
  records, but disclaim diagnosis and treatment. There is no doctor.
- **Counsel Health** (US, US$25M Series A, a16z and GV): a free AI chat, then the company's own physicians at
  US$29 a visit or US$199 a year.
- **Doctronic** (US, about US$25M): a free AI doctor, US$39 video visits with its own physicians, and a longitudinal
  record kept across sessions.
- **Superpower** (US, US$30M Series A, 2025): a US$499-a-year longevity membership combining labs, records, wearables,
  AI and its own clinicians.
- **Heidi II** (28 Sep 2026): agents inside the clinic, for the doctor. It does nothing patient-facing.
- **Hong Kong:** telemedicine apps and clinic WhatsApp tools exist (Apricot's integration among them), but a quick
  search found no doctor-owned, WhatsApp-native care line. The search was not exhaustive.

**The gap:**
- the US players employ the doctors and own the patient, but here it's **the patient's own doctor**, who owns the
  relationship
- **WhatsApp-native** and bilingual, with no app
- **loops tracked until closed**, not just questions answered
- **verified evidence** behind every claim
- **Hong Kong specifics**: eHealth App screenshots, CDCC, and local labs

**The risk:** any of these could add WhatsApp and doctor-owned modes, and Heidi could add patient messaging.
Speed and local depth are the edge.

## Invariants touched

- **Authority line:** unchanged. The agent drafts and the doctor signs. Only fixed red-flag rules act without a
  signature, and they only instruct the patient to get emergency care and alert the doctor.
- **Verbatim quotes:** extended to every derived view of the record.
- **Synthetic data only** in the repos. Real-patient pilots are clinical practice, run outside the repos.

## Open, and to check before any real patient

1. **Medical Council:**
   - the telemedicine guidelines (Dec 2019, Q&A 2022), especially remote prescribing
   - the advertising rules
   - **the rules on sharing fees with non-doctors**: these decide whether tmrw can take a percentage of fees or
     must charge a flat subscription
2. **Privacy (PDPO):** consent wording at the QR scan, cross-border processing, and Meta hosting the business side
   of the messages.
3. **Which AI model may process patient messages in Hong Kong.**
4. **Clinic agreements:** sessional contract clauses on who owns the patient and on soliciting patients.
5. **Platform risk:** a web fallback and the doctor's own copy of patient contacts.
6. **Melbourne as a second test bed** (Thomas is registered in Australia and has GP friends there):
   - **Use:** his GP friends can be doctor-mode users and some of the ten conversations.
   - **Messaging:** SMS and practice apps are more common than WhatsApp, so the channel must not be tied to
     WhatsApp.
   - **Money:** Medicare's chronic-care and MyMedicare incentives may make follow-up better paid than in Hong Kong
     **[check]**.
   - **Competition:** it's Heidi's home market.
   - **Thomas practising there himself:** depends on his Medicare billing eligibility (section 19AA) **[check]**.
