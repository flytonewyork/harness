# 002: Product direction, locked 6 Nov 2026

**One line.** A persistent clinic agent that follows up and closes loops for doctors.

**The picture.** A clinic sits inside a bubble. Everything that crosses the bubble (lab results, imaging, hospital
letters and discharge summaries, eHealth records, referral replies, patient messages, appointments, prescriptions,
programme dates like CDCC reviews and screening) is fed to one agent that never sleeps. The agent turns each thing
that needs doing into a loop. It owns every loop until it ends, and puts each decision in front of the doctor who
owns it, with the work already done.

## Problem: where doctors' time and patients' safety go

Thomas's own read is that he has little insight yet into what consumes doctors' time, so this is the outside
evidence. Almost all of it is from the US and UK. Hong Kong private practice is smaller, uses less EHR and more
paper, fax and WhatsApp, and has no gatekeeping, so every partner contact still has to confirm it (see "Open").

**Time doctors spend now** (the work we can hand back):
- About half the office day is EHR and desk work, against 27% face to face, plus 1–2 hours more each night
  (Sinsky 2016, 57 doctors in four specialties).
- Family doctors spend 5.9 h per weekday in the EHR, inside an 11.4 h workday. 24% of that EHR time is the inbox
  and 44% is clerical work (Arndt 2017, 142 family doctors).
- Primary care doctors get about 77 inbox notifications a day, a fifth of them test results (Murphy 2016).
- UK GPs report more and more time keying in and chasing hospital correspondence (Pulse survey, 2014, 453 GPs, self-selected).

**Loops that drop silently** (the harm we can prevent):
- 6.8–62% of lab results and 1.0–35.7% of imaging results go without follow-up in ambulatory care (Callen 2012,
  systematic review of 19 studies).
- 41% of patients leave hospital with results still pending; 9% of those results are potentially actionable, and
  doctors were often unaware of them (Roy 2005).
- The discharge summary had reached the GP by the first follow-up visit in only 12–34% of cases, and 65% of
  summaries left out pending tests (Kripalani 2007).

**Work that never gets done** (the capacity we can add):
- Delivering all recommended adult primary care would take 26.7 h a day for an average panel, 14.1 h of it
  preventive. With team-based care it falls to 9.3 h (Porter 2022).

**Why it pays in Hong Kong now:**
- Under Chronic Disease Co-Care (CDCC), family doctors earn incentives when participants' blood glucose and blood
  pressure reach target, and give long-term follow-up. It passed 200,000 enrolled and became a regular scheme in
  March 2026. The Co-care Network (909 family doctors as of 23 Sep 2026) is set to grow into more chronic diseases,
  women's health, cancer screening and vaccination (LCQ16, 7 Oct 2026; HKUMed, 12 Oct 2026).
- So follow-up and recall work is becoming paid work for private GPs, and the burden grows with each programme.
- Partners agree: every one of the five logged so far points at doctors' workload (EC #5, Amber #6, Apricot #7,
  Adrian #8, Virtus #11). Virtus is well over capacity. None has named the specific burden yet.

## Change: what the agent does

**Which loops.** Anything a GP would reasonably be expected to follow up in a practice. Two groups:

| Group | Loops | Today |
|---|---|---|
| A. Results and hospital follow-ups | abnormal results, tests that never came back, follow-ups recommended in letters and reports, discharge summaries and pending results, HA appointments, referrals, drug monitoring | Built: the 7 `FAMILIES` in LoopBench and the engine |
| B. Recalls and prevention | planned reviews, CDCC reviews and targets, screening due, vaccination due, lapsed chronic patients | `planned_review` and `positive_screen` built. Loops that fall due by schedule with no document to trigger them are new: a new family, scored in LoopBench first |

**The authority model: doctor signs, agent does.** (Decided by Thomas, 6 Nov. It closes the open question in
`docs/DECISIONS.md`.)
1. **Find.** The agent opens an item for every loop, with its reasoning and verbatim evidence. It never waits to be asked.
2. **Prepare.** It does the legwork: gathers the evidence, checks eHealth, and drafts the next action (a recall
   message, a booking, a referral or a repeat-test request).
3. **Sign.** The doctor approves, edits or rejects with one tap. Nothing reaches a patient or another provider
   without that approval.
4. **Do and watch.** The agent carries out the approved action and keeps watching until the loop really ends
   (patient attended, result back, letter answered). If it stalls, the agent raises it on day 7 and copies the
   doctor on duty on day 14.
5. **Close.** Only the doctor closes an item. The agent may say "evidence that this loop ended" but never closes,
   dismisses, downgrades or reassures on its own.

**What "100% accuracy" means.** We can't promise that a model's clinical judgment is always right, so the product
doesn't ask it to be. These are the guarantees we can build and test:
- **No silent drops.** Every loop is either open with an owner or closed by a doctor. Measured as missed loops in
  LoopBench and in the Phase 0 look-back.
- **Every claim checks out.** Every quote is verbatim and every citation is something the agent could see. A
  made-up quote is a hard fail (the seven platform checks, spec 001).
- **No unsupervised decisions.** Every clinical decision and every outbound action carries a doctor's signature.
- **Precision is measured.** The doctor's decision on each item (done, not needed, duplicate) is read back as the
  review signal.

## Done when (the eval comes first)
- **LoopBench** scores both groups: missed harm-weighted loops per 1,000 patient-years, made-up quotes (0), hard fails
  (0), and doctor-minutes per item.
- **Group B** has its own families and scenarios in the world model before the engine is built for them.
- **Demos:** by the end of next week (13 Nov), one per group, each on a simulated clinic year:
  - **Demo A (results and hospital):** for EC Healthcare and Amber Health (on Apricot VITA).
  - **Demo B (recalls and CDCC):** for Virtus Healthcare and Adrian's clinic.
  - Each demo shows the full find → prepare → sign → do → watch → close cycle on the doctor's list.
- **Partners:** each partner names its top burden in its own words, filed as a `signal` issue. The demos are re-cut
  to match.

## Invariants touched
- **Authority line.** Kept and sharpened. The port still only opens, raises, alarms and attests all-clear. Drafted
  actions ride on an item. Carrying one out needs a doctor-side approval (a doctor endpoint, as closing is today),
  and carrying it out never closes the item. Adding the "prepare" and "do" steps to the port needs its own spec.
- Verbatim quotes, rules floor, no LoopBench imports, synthetic data: unchanged.

## Out of scope
- The agent closing loops or acting without a signature. This waits for the autonomy ladder decision, parked until
  the 1 Feb 2027 review.
- Writing to a clinic system (VITA or EC's own) before a partner agrees how. Until then, demos run on simulated
  clinics.
- Scribing and documentation during the consult. Ambient scribes such as Heidi already cover it; we start where the
  consult ends.

## Open (ask at every partner contact)
- Which of these loops costs their doctors the most time, and which have hurt patients when they dropped? Their words, filed as a signal.
- Who does this work today (doctor, nurse, front desk), and how many minutes does it take?
- For CDCC clinics: how do they track reviews and targets today?

## Sources
- Sinsky C et al. Allocation of physician time in ambulatory practice. *Ann Intern Med* 2016. doi:10.7326/M16-0961
- Arndt BG et al. Tethered to the EHR. *Ann Fam Med* 2017;15(5):419–426. doi:10.1370/afm.2121
- Murphy DR et al. *JAMA Intern Med* 2016, inbox notifications ([summary](https://www.beckershospitalreview.com/healthcare-information-technology/pcps-receive-nearly-77-ehr-notifications-a-day-study-finds.html))
- Callen JL et al. Failure to follow-up test results for ambulatory patients. *J Gen Intern Med* 2012;27(10):1334–1348
- Roy CL et al. Test results that return after hospital discharge. *Ann Intern Med* 2005 ([summary](https://news.harvard.edu/gazette/story/2005/07/home-from-the-hospital-almost-half-of-patients-are-discharged-with-test-results-still-pending))
- Kripalani S et al. Deficits in communication between hospital physicians and primary care physicians. *JAMA* 2007;297:831–841
- Porter J et al. Revisiting the time needed to provide adult primary care. *J Gen Intern Med* 2022. doi:10.1007/s11606-022-07707-x
- Pulse GP survey, 2014: [80% of GPs spend more time inputting information from secondary care](https://www.pulsetoday.co.uk/news/working-life/80-of-gps-spend-more-time-inputting-information-from-secondary-care/)
- LegCo LCQ16, 7 Oct 2026: [Development of primary healthcare services](https://www.info.gov.hk/gia/general/202610/07/P2026100700411.htm)
- HKUMed, 12 Oct 2026: [CDCC evaluation](https://lead.hku.hk/en/article/2026/10/hkumed-finds-chronic-disease-co-care-pilot-scheme-highly-effective-in-chronic-disease-control-40-of-screened-participants-diagnosed-with-chronic-conditions/)
