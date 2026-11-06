# 003: The care line, a doctor's WhatsApp with an AI care team behind it (draft, 6 Nov 2026)

**Status:** a candidate direction for the 26 Nov go/no-go, not a decision. It builds on spec 002 (doctor signs,
agent does) and on what the Apricot call showed (#15). **This week's goal:** make it concrete enough that Thomas
wants to take GP sessions and call-out jobs to test it.

## The ultimate vision (Thomas, 6 Nov)

> **It turns an independent GP into their own side hustle: something they can grow, see the impact of, and make
> money from.**

Three things the product has to deliver for every doctor:
- **Grow:** the QR handoff builds a panel, patients come back, and referrals follow.
- **See the impact:** a dashboard of patients followed, loops closed, patients who came back, and readings back on
  target.
- **Make money:** paid follow-up work and memberships, with the doctor as the merchant.

The analogy is Substack for writers: you own your audience, you can see it grow, and it pays you directly.

**Wording for the outside world:** say "your own practice, alongside your sessions". Patients, clinics and the
Medical Council's advertising rules all read "side hustle" badly, so that phrase stays the internal north star.

## The best case, in Thomas's words (6 Nov)

> It makes me actually want to do GP sessions, and to do more of them. It builds in parallel, takes the burden off
> the sessions and makes them something I look forward to. And it gives me a wholly owned piece I can grow, built on
> patient interactions, through a legit channel: a network of my own patients and doctors.

**The test of this spec:** every session Thomas works adds to something he owns. That's the patients on his line
(with consent), his library of approved answers and protocols, his record of loops closed, and the doctors using
the same line. It counts as a success even if it never becomes a venture.

**Measured weekly, from the first session:**
- "I'm looking forward to my next session", scored 1–10
- sessions booked per week
- minutes of admin after each session
- patients on the line
- loops closed
- patients who come back or ask for him
- doctors using the line

**What keeps it legit:**
- patient consent at the QR scan
- each clinic agrees, and its record gets everything written back
- no poaching: patients rebook with the clinic
- Medical Council and PDPO checks done before any AI touches real messages

## The winning pilot (6 Nov draft)

One doctor, Thomas, is the constant across four settings. Each setting tests a different part of the idea.
Partners join in waves, each once the previous wave's gate is met. The thresholds are proposals for Thomas to set.

| Wave | Setting | What it tests | Gate to the next wave |
|---|---|---|---|
| 0 · now to mid-Dec | His GP sessions (Virtus or others), doctor mode, **manual**. In parallel, the LoopBench care-line simulator | Does it make sessions something he looks forward to? Do patients use the line? | Weekly "looking forward" score ≥ 7 for 3 weeks; patients on the line and loops closed rising; zero safety incidents; admin minutes falling |
| 1 · from opening | The FPC with Adrian: **the full stack** (care line as front door, membership, AI-native record, senior medical director) | Will patients pay for continuity? Doctor minutes per patient per month; patient-facing AI, once the model and consent questions are settled | Paying members; under about 15 doctor-minutes per patient per month |
| 2 · on EC's timeline | EC's new practice: **clinic mode under EC's brand** (tmrw consulting; EC owns the patient data, tmrw keeps the software) | Write-back to an in-house system, patients kept within the group, value to an enterprise | Recalls ending in attendance, compared with EC's own baseline; write-back working |
| 3 · once traction shows | Partner clinics already approached: Virtus, Central Health, Amber Health (Apricot VITA as channel and data, API pending) | Whether it spreads: one doctor, a six-week pilot, their own numbers | Doctors keep using it after the pilot; one clinic converts to paid |

**What stays Thomas's (tmrw's) across every wave:**
- the software and the loop engine
- the protocol and answer library
- the doctor network
- his own line's patients, with their consent

Patient records stay with the clinic that saw the patient.

**Conflicts to manage up front:**
- tell both EC and Adrian about the other role, since the two clinics may overlap in services
- agree who owns which patient relationship in each setting
- check exclusivity and IP terms in the EC and FPC agreements
- time: two clinic openings, sessions and building can't all start at once, so sequence them by the partners'
  timelines

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
- **Episodic visits** (most GP visits are one-offs; Josie, #17): a safety-net card plus a day-3 "better or not?"
  check-in. The most common visit becomes the first touch of a relationship.
- **Medicines:** repeat prescriptions over the line, signed by the doctor, with the **price shown before the patient
  pays**. The patient collects, has it delivered where the rules allow, or asks for the prescription to fill at a
  pharmacy.
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

## Market sizing: decompiling the clinic (first cut, 6 Nov; ranges, with every assumption marked)

**1. The clinic layer that could be passed back (Hong Kong GP care):**
- **Consultations:** 14.2% of people saw a doctor in the 30 days before the survey (Thematic Household Survey, 2022–23). 80% of those saw a Western-medicine doctor, and the private sector gives about 68% of outpatient doctor consultations (Primary Healthcare Blueprint).
- **Assumptions:** 1.3–2 consultations per person who consulted, and about 70% of private consultations being GP visits. That gives about 6–9 million private GP consultations a year.
- **Revenue:** at the HK$400 median fee (HKMJ 2026), that's about **HK$2.5–3.6bn a year**.
- **The clinic's share:** about half (assumption), so **HK$1.2–1.8bn a year** goes on rent, staff, drug margin and operator profit.
- **What can be passed back:** if an AI back office and less rent remove a third to half of that (assumption), about **HK$0.4–0.9bn a year** could go to doctors (higher income) and patients (lower prices, no trip for follow-ups).
- **Drug margin sits inside the clinic's half.** Hong Kong GPs dispense their own medicines. In Josie's example
  (#17) a drug costs about $60 at a pharmacy, $80 to the clinic, and $120 to the patient: roughly double the pharmacy
  price. An independent doctor buying wholesale can keep a fair margin and still charge less. **Margin per visit is
  to be measured in the pilot.**

**2. New continuing-care demand (illustrative; care that doesn't happen today):**
- **Chronic:** 2.2m people have a chronic condition. If 10% take a membership at HK$100–150 a month, that's about HK$0.3–0.4bn.
- **Co-care:** the network targets 700k participants. If about 40% are in treatment, at up to HK$1,896 a year (subsidy plus co-pay), that's about HK$0.5bn flowing to family doctors.
- **Prevention and longevity:** 1–2% of adults at HK$500 a month is about HK$0.4–0.7bn.
- **Families coordinating care for elderly parents:** about HK$0.3bn.
- **Total:** about **HK$1.5–1.9bn a year**, growing with ageing (people aged 65+ rise from 1.5m to 2.5m by 2039).

**3. What tmrw could earn:**
- **Hong Kong alone:** subscriptions from 400–1,000 doctors at HK$1–3k a month is about **HK$5–36m a year (US$0.6–4.6m)**. A share of fees, if the Medical Council's rules allow one, could be several times that.
- **Read:** Hong Kong is a beachhead and the proof, not the venture.
- **The venture case needs:**
  - WhatsApp-heavy markets where patients pay doctors directly. Malaysia, for example, has about 10,000 private GP clinics, and over 70% say they are struggling to stay open (Malaysian Medical Association, May 2026), so "grow your practice" is the pitch there.
  - And/or a cut of the money flowing through doctors' practices, the way Shopify takes a cut of its merchants' sales.

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

## Medicines: the dispensing layer (from Josie, #17)

**What a locum across 6 Hong Kong practices sees:**
- GPs take home about 50% of fees.
- Most patients are one-off visits.
- A clinic needs little more than a doctor, one nurse and the drugs.
- Doctors make a good part of their money selling medicines, at about double the pharmacy price.

**What the product does with this:**
1. **Refills over the line.** The patient asks on WhatsApp. The agent checks that the medicine is stable and that
   monitoring is up to date (for example, kidney function on an ACE inhibitor), then drafts the refill. The doctor
   signs. Patients get recurring convenience, and the doctor gets recurring margin.
2. **Fair, upfront prices.** The doctor sets the price, the patient sees it before paying, and the patient can
   always ask for the prescription instead. Aim for fair and transparent, not cheapest: that builds trust without
   giving away the doctor's income.
3. **A procurement agent:**
   - compares wholesale prices across suppliers
   - reorders before stock runs out
   - flags stock nearing expiry
   - keeps the dispensing record
   This is back-office work the clinic does today (VITA has an inventory module for it).
4. **Partner pharmacies** for doctors without their own stock (locums, call-outs): the signed prescription goes to
   a pharmacy that delivers or holds it for collection. The Community Pharmacy Programme starts in four districts at
   the end of 2026.

**Guardrail:** prescribe for a clinical reason, never for margin. Every refill carries its indication and its
monitoring status, with verbatim evidence, the same as every other item.

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
- **Ten doctor conversations by 26 Nov** (1 so far: Josie, #17), each asking:
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

1. **Dispensing and delivery:**
   - Pharmacy and Poisons Ordinance (Cap. 138) rules on supplying, storing and labelling medicines
   - whether a doctor may deliver dispensed medicines to their own patients
   - the Medical Council's dispensing and labelling requirements
   - what's allowed for doctors without premises (locums, call-outs)
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
