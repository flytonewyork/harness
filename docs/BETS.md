# Bets

What has to be true for tmrw to reach product-market fit, written down with odds before it is tested, so results
move the odds instead of the story. Set on 8 Nov 2026, before the Josie meeting.

**The signal we're betting on.** After a month, the doctor won't give it back, and patients message again without
being prompted.

**Rules.**
- A prior is never edited once its test starts. New information before a test gets a new row with its own date.
- A stated yes counts as half a yes until it is done.
- Every result goes in the partner's `signal` issue and in the table below, with its Brier score:
  (prior − outcome)², where the outcome is 1 if the bet passed and 0 if not. 0.25 is a coin flip; lower is better.
- Thomas fills in his column before each test, so both sets of odds can be scored.

| # | Bet (pass line) | Claude | Thomas | Test · cost | Result | Brier (C / T) |
|---|---|---|---|---|---|---|
| 1 | Josie would charge close to her normal fee for a WhatsApp repeat, and would offer the line to her stable patients | 55% | | Ask her · 10 min, 8 Nov | | |
| 2 | At least 3 of 10 stable patients take a paid remote repeat | 50% | | Hand-run pilot · 1–2 months | | |
| 3 | At least 40% of patients asked in the room scan the QR code | 70% | | Hand-run pilot · 2 weeks | | |
| 4 | At least 25% of those who join message again, unprompted, within 30 days | 35% | | Hand-run pilot · 1 month | | |
| 5 | Chronic Co-Care is the best first market: Josie is a Co-Care family doctor and names its follow-up or admin work as a burden | 40% | | Ask her · 2 questions, 8 Nov | | |
| 6 | EC Healthcare agrees a written next step, with data access, by 26 Nov | 20% | | Phase 0 look-back (#5) · already running | | |
| 8 | At least 2 doctors besides Thomas use the line in a normal week by 1 Feb, without being chased (set 10 Nov) | 30% | | The live line (spec 005) · Jose from launch, then others | | |
| 9 | At least 10% of patients the doctor offers a monthly membership to subscribe within 14 days of the offer (set 10 Nov) | 30% | | The live line · offered one patient at a time; best tested in a self-pay setting (Adrian's clinic) | | |
| 10 | EC Healthcare agrees in writing, by 26 Nov, that Thomas may offer his care line to patients he sees in his own sessions at an EC practice (set 10 Nov) | 20% | | Ask Julie on 11 Nov, then a one-page scope (`docs/EC_MEETING_PREP.md`) · 1 hour | | |

Side bet on 4: at least 40% reply to a check-in, 65%. A reply to a prompt proves much less than coming back
unprompted.

**Together.** The care line with independent GPs needs bets 1 to 4 to pass. They're linked, so the odds are better
than multiplying them, but still about 1 in 10. EC by 26 Nov is about 1 in 5. Most of these will miss; the edge is
running them cheaply and quickly, and knowing in advance what each miss means.

**Order, by what we learn per hour.**
1. Today: bets 1 and 5. They're near 50/50, they decide a lot and they cost minutes.
2. Next: bets 2 and 3 in one pilot run by hand from Josie's own clinic WhatsApp, by her staff or Thomas, with the
   patients' consent, no AI and no data leaving her clinic.
3. Bet 4 comes out of the same pilot.

## 10 Nov: the live line decides the shape

The direction is locked (spec 005), and the live line is now the test for bets 2, 3, 4 and 8, run on Thomas's and
Jose's own patients instead of a hand-run pilot. Bet 1 changes shape: Thomas and Jose are the doctors, so the open
question is whether other doctors would charge (bet 8 tests whether they use it at all). Pass lines for the 1 Feb
review, all measured by the line itself:
- Bet 3: at least 40% of patients shown the QR code join.
- Bet 4: at least 25% of those who join message again, unprompted, within 30 days.
- Bet 2: at least 3 in 10 patients offered a paid repeat or result review pay for it.
- Bet 8: at least 2 doctors besides Thomas use it in a normal week.
- Bet 9: at least 10% of patients offered a membership subscribe within 14 days.

Why bet 9 exists: Hong Kong outpatient care is cheap and often bundled (insurer passes from HK$80 a visit, telco
video consults), and many working adults have outpatient cover. So paying per item out of pocket competes with a
covered or cheap visit, and bet 2 may miss in the mass market for that reason alone. Continuity with your own doctor
is the thing those bundles don't sell. A membership tests whether people will pay for that.

If they pass (bet 2 or bet 9, with 3 or 4): the doctor-owned line is the company; raise on it. If they don't: the 1 Feb review weighs joining Heidi
against a clinic-facing business, which the 10 Nov direction rules out for now.

**Bet 10 (20%).** It asks EC for no data and no access to its systems, and Julie has already raised the Taikoo
practice with Thomas. Against it: a listed clinic group will want its compliance team to look at any tool that
messages its patients, and that rarely finishes in two weeks; and whether Thomas takes sessions at EC isn't settled.
If it fails by 26 Nov: launch at the clinics that have said yes, and keep EC as a later site.

## Each bet

**1. The doctor gets paid (55%).** HK GPs are paid per visit, so care between visits is unpaid unless it replaces a
paid visit. Private clinics already run paid online consultations with delivery, but repeats done by message,
without video, sit uneasily with the Medical Council's duty of an adequate evaluation.
If it fails: the independent-GP care line has no revenue. Stop adding patient-facing features; turn to bet 6 or to
a membership the patient pays for.

**2. Patients pay for a remote repeat (50%).** The same price with no trip and no wait is a strong offer for working
adults; many older patients value the visit itself. Pick patients due a repeat inside the pilot window, or most
won't get the chance.
If it fails: ask the ones who said no whether it was the price, trust or habit before dropping repeats as the wedge.

**3. Patients join when the doctor asks (70%).** A personal ask from the doctor, with WhatsApp already on every phone,
makes scanning easy. It's the likeliest to pass and the least informative.
If it fails: change who asks (the front desk) and how (a printed card) once. Below 20% after that, the channel is
the problem.

**4. Patients come back on their own (35%).** This is the real PMF signal and the weakest bet: someone seen once for
an acute illness has little reason to return once they're better.
If it fails: the line works only when the clinic pushes. Narrow it to chronic patients (bet 5), who have a reason
to come back.

**5. Co-Care first (40%).** The scheme brings continuity and government money, but as far as we know it pays per
visit, not for work between visits, so the doctor's gain is unclear.
If it fails: chronic private patients outside the scheme, or EC's corporate patients.

**6. A clinic group pays for recovered visits (20% by 26 Nov).** Data deals rarely close in 18 days, and the call with
EC's CTO hasn't happened yet. About 45% that clinic groups pay for this at all.
If it fails by 26 Nov: it isn't a verdict. Set the next date and keep the look-back offer open.

**Not a bet, a gate:** whichever model may touch Hong Kong patient data must pass the task catalogue's gate
(`demo/clinic-backend/tasks.json`, `NOW.md`). It matters only once a bet above has passed.

## Questions for Josie today

1. If a stable patient could get their repeat on WhatsApp without coming in, would you charge for it? How much?
   (bet 1)
2. Would you offer that to your stable patients? Which drugs would you never do this way? (bet 1)
3. Are you a Co-Care family doctor? What does it cost you in follow-up and admin? (bet 5)
4. Would you try it for two weeks: you ask 30 patients the one question at the end of the visit, and we run the line
   by hand from your clinic's WhatsApp? (bets 2 to 4)

## Evidence behind the odds

- Patients message when they can. In the US, patient-authored portal messages rose 153% while office visits rose 17%
  (JAMA, June 2026, Epic data, as summarised by
  [Paubox](https://www.paubox.com/blog/what-the-surge-in-patient-portal-messages-means-for-secure-communication)).
  Billing for messages cut them by only 8.8% at Mayo Clinic
  ([HealthDay](https://www.healthday.com/healthpro-news/modest-decline-seen-in-portal-messaging-with-billing-implementation)).
  The cost lands on doctors: up to two and a half hours of extra after-hours work from portal messages
  ([TechTarget on Health Affairs](https://www.techtarget.com/patientengagement/news/366646955/Patient-portal-messages-yield-up-to-26-hours-of-after-hours-work)).
- Hong Kong care is episodic. In an older CUHK survey, 80% of people with a main doctor had seen another primary care
  provider in the past year ([Griffiths, 2011](https://www.cuhk.edu.hk/hkiaps/conference/event/PPT/GRIFFITHS%20Sian_0303.pdf)),
  and only about 60% of the population has a regular source of care
  ([HKMJ](https://www.hkmj.org/system/files/hkmj198368.pdf)). Patients choose a doctor for convenience; perceived
  clinical skill is what keeps them
  ([J Eval Clin Pract, 2010](https://scholars.hkmu.edu.hk/en/publications/how-do-patients-choose-their-doctors-for-primary-care-in-a-free-m/)).
- The Chronic Disease Co-Care Pilot Scheme passed 200,000 participants early and is being made permanent
  ([Government press release, 13 Mar 2026](https://www.info.gov.hk/gia/general/202603/13/P2026031300680p.htm)).
- Telemedicine in Hong Kong runs under the Medical Council's non-binding guidelines (December 2019); private clinics
  already deliver medicines after online consultations
  ([HK regulatory map](https://claude.ai/artifact/Y9X9VHrvWiMVGdiLbpjDdW)).
- Recovered return visits at a simulated solo clinic: HK$108k–144k a year (LoopBench, three runs).
