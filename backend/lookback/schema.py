"""The Phase 0 export format: what we ask EC for, table by table.

This is the single source of truth for the data request (docs/EC_DATA_REQUEST.md is written from it), for the
simulated export LoopBench writes, and for the look-back runner that reads either. One CSV per table, UTF-8,
ISO dates (YYYY-MM-DD) and datetimes (YYYY-MM-DDTHH:MM), empty cells for unknowns.

Keys are opaque. EC makes them with its own secret (HMAC-SHA256 of its internal IDs, first 16 hex characters),
so nobody outside EC can turn a key back into a person, and EC can still find the patient to call. No names,
HKIDs, phone numbers, addresses or full dates of birth are requested. Tier 2 columns hold free text and are only
sent once names and identifiers are removed (or the look-back runs inside EC's environment).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    meaning: str
    tier: int = 1          # 1 = structured, needed; 2 = de-identified free text, optional; 3 = if the system has it


T = Field
TABLES: dict[str, tuple[str, list[Field]]] = {
    "patients": ("Everyone registered at the clinic during the window.", [
        T("patient_key", "Opaque patient key (HMAC of EC's patient number)"),
        T("birth_year", "Year of birth only"),
        T("sex", "M / F"),
        T("registered_doctor_key", "Usual or registered doctor, if recorded", 3),
        T("ehealth_consent", "Y / N: gave the clinic eHealth sharing consent", 3),
        T("cdcc", "Y / N: Chronic Disease Co-Care participant", 3),
    ]),
    "doctors": ("Every doctor who worked at the clinic during the window.", [
        T("doctor_key", "Opaque doctor key"),
        T("role", "principal / GP / sessional / locum"),
        T("start_date", "First day at the clinic, if within the window"),
        T("end_date", "Last day at the clinic, if within the window"),
    ]),
    "consultations": ("Every consultation, including Co-care and voucher visits.", [
        T("encounter_key", "Opaque key"),
        T("patient_key", ""), T("doctor_key", ""),
        T("datetime", "Start of the consultation"),
        T("visit_type", "walk-in / booked review / Co-care screening / Co-care treatment / other"),
        T("plan_text", "The note's plan section (or whole note), names and identifiers removed", 2),
    ]),
    "lab_orders": ("Every laboratory request.", [
        T("order_key", ""), T("patient_key", ""), T("doctor_key", "Ordering doctor"),
        T("datetime", "When ordered"), T("lab", "Laboratory"), T("lab_no", "The lab's request number, if known"),
        T("tests", "Tests or panels requested, separated by ' / '"),
    ]),
    "lab_results": ("One row per test result that reached the clinic (interface, portal, fax or courier).", [
        T("report_key", "Same key for every row of one report"), T("patient_key", "Empty if never filed to a patient"),
        T("doctor_key", "Doctor the report is addressed to"), T("lab_no", ""),
        T("reported_datetime", "When the lab reported it"), T("received_datetime", "When it reached the clinic"),
        T("received_via", "interface / portal / fax / courier"),
        T("test", ""), T("value", ""), T("unit", ""), T("ref_low", ""), T("ref_high", ""), T("flag", "H / L / empty"),
        T("reviewed_datetime", "When a doctor signed it off, if the system records it", 3),
    ]),
    "referrals": ("Every referral letter written.", [
        T("referral_key", ""), T("patient_key", ""), T("doctor_key", ""), T("datetime", ""),
        T("destination", "HA / private / imaging / other"), T("specialty", ""), T("urgency", "urgent / semi-urgent / stable"),
        T("reply_received_datetime", "If the system links the reply", 3),
    ]),
    "documents": ("Every incoming document: specialist letters, imaging reports, discharge summaries, scanned results.", [
        T("doc_key", ""), T("patient_key", "Empty if never filed to a patient"),
        T("type", "specialist letter / imaging report / discharge summary / A&E summary / lab report / message / other"),
        T("source", "fax / courier / email / patient-carried / WhatsApp / other"),
        T("received_datetime", ""), T("filed_datetime", "When attached to the patient, if recorded", 3),
        T("text", "Document text, names and identifiers removed", 2),
    ]),
    "prescriptions": ("Every dispensing or prescription.", [
        T("rx_key", ""), T("patient_key", ""), T("doctor_key", ""), T("datetime", ""),
        T("drug", "Drug name and strength"), T("new_start", "Y / N: first time for this patient", 3),
        T("days_supplied", "", 3),
    ]),
    "screening": ("Programme results: Colorectal Cancer Screening Programme (FIT) and Co-care screening.", [
        T("screen_key", ""), T("patient_key", ""), T("doctor_key", ""), T("programme", "CRCSP FIT / CDCC"),
        T("result_datetime", ""), T("result", "POSITIVE / NEGATIVE / values"),
    ]),
    "contacts": ("Calls, messages and recall letters to patients, if the system logs them.", [
        T("contact_key", ""), T("patient_key", ""), T("staff_key", "Doctor or staff key"), T("datetime", ""),
        T("channel", "phone / SMS / WhatsApp / letter / other"),
        T("purpose", "Short free text, names and identifiers removed", 2),
    ]),
    "appointments": ("Booked appointments, if the system has a booking module.", [
        T("appointment_key", ""), T("patient_key", ""), T("doctor_key", ""), T("booked_for", ""), T("created", ""),
        T("status", "attended / no-show / cancelled / booked"),
    ]),
}


def columns(table: str) -> list[str]:
    return [f.name for f in TABLES[table][1]]
