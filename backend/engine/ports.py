"""The engine's view of a clinic: everything it may read, and the only things it may do.

Any adapter that offers these methods can host the engine: LoopBench's simulated clinic
(`loopbench.harness_api.ClinicAPI`), a replay of an EC export, and later EC's live system.

Writes are deliberately asymmetric. The engine can open an item, raise its urgency, raise an alarm about a
feed, or attest that nothing is outstanding. It can never close, dismiss, downgrade or reassure: only a doctor
closes an item. Every item carries a reasoning trace whose quotes the platform checks word for word.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

FAMILIES = ("abnormal_result", "test_never_resulted", "followup_recommended", "positive_screen",
            "referral", "drug_monitoring", "planned_review")


class ClinicPort(Protocol):
    now: datetime
    ServiceUnavailable: type[Exception]           # raised when a source (portal, eHealth) is down

    # reading
    def clinic(self) -> dict: ...
    def staff(self) -> list[dict]: ...
    def roster(self, day: date) -> dict: ...                       # {"open": bool, "closed_reason": str, "doctors": [...]}
    def patients(self) -> list[dict]: ...
    def find_patients(self, name: str | None = None, dob: str | None = None, hkid: str | None = None) -> list[dict]: ...
    def cms_since(self, cursor: int) -> tuple[list[dict], int]: ...
    def portal_since(self, source: str, cursor: int) -> tuple[list[dict], int]: ...
    def ehealth(self, patient_id: str) -> list[dict]: ...
    def chart(self, patient_id: str) -> list[dict]: ...
    def items(self, status: str | None = None) -> list[dict]: ...

    # writing: open or raise only
    def open_item(self, patient_id: str, owner_id: str, family: str, reason: str, evidence: list[str],
                  urgency: int = 1, due: datetime | None = None, reasoning: list[dict] | None = None,
                  escalates: str | None = None) -> str: ...
    def raise_urgency(self, item_id: str, urgency: int, reason: str = "") -> None: ...
    def alarm(self, channel: str, reason: str) -> None: ...
    def attest_all_clear(self, scope: str = "all", note: str = "") -> None: ...


Record = dict[str, Any]   # a record as the port returns it: id, kind, channel, patient_id, author, text, data, times
