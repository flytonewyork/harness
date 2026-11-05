# Data request: Phase 0 follow-up look-back

**For:** Julie and EC Healthcare's IT lead · **From:** tmrw consulting · **Version:** 1, November 2026

## What this is for
A one-off look back over one EC clinic's past records to count the follow-ups the clinic owed that never happened, by kind: abnormal results nobody acted on, tests never resulted, referrals with no reply, positive stool tests (FIT) without a colonoscopy, abnormal mammograms, monitoring bloods after new medicines, planned reviews and hospital follow-ups. Nothing is sent to doctors or patients. It answers one question before anything is built: how many follow-ups slip through, of which kinds, and what they are worth in consultations.

## What EC gets back, within a week of access
- Counts of unclosed and late follow-ups per kind, per 1,000 patients.
- The list of keys for patients still owed a follow-up. EC can turn those keys back into patients to call. We cannot.
- Missed consultations × fee, as a range: HK$400–540, the median private GP consultation fee in a March 2025 survey (Hong Kong Med J 2026;32:354–6), and HK$316 for Co-care (HK$166 subsidy plus the HK$150 recommended co-payment).
- Data gaps, such as results never filed to a patient and HA referrals that only eHealth can confirm.

## Scope
- **Clinic:** one EC clinic, all doctors. More clinics can come later.
- **Dates:** every record dated 1 Jul 2025 to the extract date. Follow-ups are counted if they started 1 Jul 2025 to 30 Jun 2026, so nearly all of them fall due before the extract. The dates are a proposal; any 12-month window works. Follow-ups not yet due at the extract date are left out, not counted as missed.

## Privacy (PDPO)
- **No names, HKIDs, phone numbers, addresses or full dates of birth.** Birth year only.
- **Opaque keys.** EC makes every key with HMAC-SHA256, using a secret EC creates and keeps, applied to its own patient, doctor and record numbers (first 16 hex characters). The same patient gets the same key in every table.
- **Free text is optional (²).** It is sent only after EC's script removes names, ID numbers, phone numbers and addresses. Alternatively, the look-back runs on a machine inside EC's environment and no free text leaves EC. EC decides whether a language model may read free text, and in which setup. Without one, a rules-only reader is used.
- **Handling.** Encrypted transfer, used only for this analysis, deleted on completion. EC remains the data user.

## Tables
One CSV per table: UTF-8, ISO dates (`2026-03-01`) and times (`2026-03-01T14:30`), empty cells where something is unknown. Columns marked ² are de-identified free text (optional). Columns marked ³ are wanted if the system has them. The field-by-field definitions are in `backend/lookback/schema.py`.

| Table | Rows | Columns |
|---|---|---|
| `patients` | Everyone registered at the clinic during the window | `patient_key`, `birth_year`, `sex`, `registered_doctor_key`³, `ehealth_consent`³, `cdcc`³ |
| `doctors` | Every doctor who worked there during the window | `doctor_key`, `role`, `start_date`, `end_date` |
| `consultations` | Every consultation, including Co-care and voucher visits | `encounter_key`, `patient_key`, `doctor_key`, `datetime`, `visit_type`, `plan_text`² |
| `lab_orders` | Every laboratory request | `order_key`, `patient_key`, `doctor_key`, `datetime`, `lab`, `lab_no`, `tests` |
| `lab_results` | One row per test result that reached the clinic (interface, portal, fax or courier) | `report_key`, `patient_key`, `doctor_key`, `lab_no`, `reported_datetime`, `received_datetime`, `received_via`, `test`, `value`, `unit`, `ref_low`, `ref_high`, `flag`, `reviewed_datetime`³ |
| `referrals` | Every referral letter written | `referral_key`, `patient_key`, `doctor_key`, `datetime`, `destination`, `specialty`, `urgency`, `reply_received_datetime`³ |
| `documents` | Every incoming letter or report: specialist letters, imaging, discharge and A&E summaries, scanned results | `doc_key`, `patient_key`, `type`, `source`, `received_datetime`, `text`², `filed_datetime`³ |
| `prescriptions` | Every dispensing or prescription | `rx_key`, `patient_key`, `doctor_key`, `datetime`, `drug`, `new_start`³, `days_supplied`³ |
| `screening` | Programme results: CRCSP FIT, and Co-care screening | `screen_key`, `patient_key`, `doctor_key`, `programme`, `result_datetime`, `result` |
| `contacts`³ | Calls, messages and recall letters, if the system logs them | `contact_key`, `patient_key`, `staff_key`, `datetime`, `channel`, `purpose`² |
| `appointments`³ | Booked appointments, if there is a booking module | `appointment_key`, `patient_key`, `doctor_key`, `booked_for`, `created`, `status` |

Results that were never filed to a patient stay in, with an empty `patient_key` and their `lab_no`. The look-back links them back through the clinic's own request numbers.

## Questions for EC's IT lead
1. Which of these tables does the clinic system hold? Do lab results arrive as structured data or as scanned paper?
2. Can the system export the plan section of a consultation note separately from the rest of the note?
3. Is result sign-off (`reviewed_datetime`) recorded? Are calls to patients logged?
4. Where should the look-back run: on a machine in EC's environment, or on an encrypted copy outside it?

*Shareable copy: the Claude doc "Data request: Phase 0 follow-up look-back". Tested before the first request: the same look-back runs on a simulated export in exactly this format (LoopBench, synthetic patients only). There, it found 96–98% of the missed follow-ups the clinic's own records can show, and 97–98% of its flags were right.*
