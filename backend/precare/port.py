"""What the precare agent may read and do at a clinic.

Any adapter that offers these methods can host the agent: LoopBench's simulated pre-visit clinic, and later a
clinic's booking system plus its WhatsApp line. Reads are the appointment, the patient's identifiers and the
conversation. Writes are a message to the patient on the number on file for the appointment, made only from fixed
catalogue templates (no free text), and the same four
moves as the engine port (`backend/engine/ports.py`): open an item, raise its urgency, raise an alarm, attest.
There is no close, dismiss, downgrade or reassure. Only a doctor closes an item.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

FAMILIES = ("previsit_brief",        # the doctor's summary of the intake; closed by the doctor at the visit
            "previsit_red_flag",     # an urgent symptom reported before the visit
            "previsit_question")     # something only the doctor can answer before the visit (medicines, results)


class PrecarePort(Protocol):
    now: datetime

    # reading
    def appointment(self, appt_id: str) -> dict: ...
    # {"id", "patient_id", "doctor_id", "start": iso, "visit_type": new_problem|chronic_review|health_check|procedure,
    #  "tests": [ordered tests, e.g. "fasting_glucose", "lipids"], "procedure": str | None}
    def patient(self, patient_id: str) -> dict: ...                  # {"id", "name_en", "name_zh", "dob": iso date}
    def doctor(self, doctor_id: str) -> dict: ...                    # {"id", "name_en", "name_zh"}
    def messages(self, appt_id: str) -> list[dict]: ...              # [{"id", "dir": in|out, "text", "at", "slot"}]

    # writing: a message on the appointment's number, made only from catalogue templates (texts.CATALOGUE) with
    # slot values the port takes from the booking (texts.values_for); then open or raise only
    def send(self, appt_id: str, templates: list[str], lang: str, slot: str | None = None) -> str: ...
    def open_item(self, patient_id: str, owner_id: str, family: str, reason: str, evidence: list[str],
                  urgency: int = 1, due: datetime | None = None, reasoning: list[dict] | None = None,
                  escalates: str | None = None) -> str: ...
    def raise_urgency(self, item_id: str, urgency: int, reason: str = "") -> None: ...
    def alarm(self, channel: str, reason: str) -> None: ...
    def attest_all_clear(self, scope: str = "all", note: str = "") -> None: ...
