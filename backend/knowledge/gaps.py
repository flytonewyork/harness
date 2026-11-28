"""Non-blocking runtime KnowledgeGapLog and zero-LLM-call gap emitters (WO #62).

Clinical Safety & Privacy Invariants:
1. Non-blocking observability only: never modifies engine decisions, item urgency, or port authority.
2. Zero-PHI boundary (HK PDPO / EC environment): every string field on `KnowledgeGap` passes
   through `PHIScrubber.scrub()` BEFORE construction and serialization. `detail` is bounded to
   <= 200 characters and `ts` is truncated to minute precision.
3. Opaque keys only: never stores patient names, HKIDs, phone numbers, DOBs, or emails.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..acute.security import PHIScrubber

GAP_KINDS: frozenset[str] = frozenset({
    "unmapped_test",
    "unmapped_drug",
    "quote_dropped",
    "reader_fallback",
    "parser_miss",
    "verify_warn",
    "doctor_not_relevant",
    "doctor_already_done",
    "fact_rejected",
})

# Supplemental HKID regexes to catch parenthesized check-digits cleanly before PHIScrubber runs
_HKID_PAREN_RE = re.compile(r"\b[A-Z]{1,2}\d{6}\([0-9A]\)", re.I)
_HKID_MASKED_RE = re.compile(r"\b[A-Z]{1,2}\d{3}XXX\([0-9AX]\)", re.I)
MAX_DETAIL_CHARS = 200
MAX_PATTERN_CHARS = 120


def scrub_text(
    value: Any,
    *,
    extra_names: Iterable[str] | None = None,
    max_len: int | None = None,
) -> tuple[str, list[str]]:
    """Scrub PHI from any string value using PHIScrubber plus parenthesized/masked HKID guards."""
    text = " ".join(str(value or "").split())
    redactions: list[str] = []
    if _HKID_PAREN_RE.search(text):
        text = _HKID_PAREN_RE.sub("[REDACTED_HKID]", text)
        redactions.append("hkid")
    if _HKID_MASKED_RE.search(text):
        text = _HKID_MASKED_RE.sub("[REDACTED_HKID]", text)
        redactions.append("hkid")
    names = [str(n) for n in extra_names] if extra_names else None
    cleaned, base_redactions = PHIScrubber.scrub(text, extra_names=names)
    all_redactions = sorted(set(redactions) | set(base_redactions))
    if max_len is not None and len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned, all_redactions


def _minute_iso(ts: datetime | str | None = None) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat(timespec="minutes")
    if isinstance(ts, datetime):
        return ts.isoformat(timespec="minutes")
    raw = str(ts).strip()
    try:
        return datetime.fromisoformat(raw).isoformat(timespec="minutes")
    except ValueError:
        return datetime.now(timezone.utc).isoformat(timespec="minutes")


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    """One PHI-scrubbed runtime knowledge-gap observation."""

    ts: str              # ISO timestamp (minute precision)
    clinic_id: str
    kind: str            # one of GAP_KINDS
    journey: str         # "core" | "medication" | "insurance" | "followup" | "followon" | "post_checkup" | "lookback"
    family: str          # item family or fact kind
    pattern: str         # PHI-scrubbed token, test name, drug token, or check name
    record_kind: str     # "consult_note" | "lab_report" | "insurer_letter" | ...
    channel: str         # "cms" | "portal" | "fax" | "careline" | ...
    detail: str          # short PHI-scrubbed context (<= 200 chars)

    def __post_init__(self) -> None:
        if self.kind not in GAP_KINDS:
            raise ValueError(f"Unknown KnowledgeGap kind {self.kind!r}; expected one of {sorted(GAP_KINDS)}")
        # Enforce PHI scrubbing unconditionally on every field even if constructed directly
        for fld, limit in (
            ("clinic_id", 64),
            ("journey", 64),
            ("family", 64),
            ("pattern", MAX_PATTERN_CHARS),
            ("record_kind", 64),
            ("channel", 64),
            ("detail", MAX_DETAIL_CHARS),
        ):
            clean, _ = scrub_text(getattr(self, fld), max_len=limit)
            object.__setattr__(self, fld, clean)
        object.__setattr__(self, "ts", _minute_iso(self.ts))

    @classmethod
    def create(
        cls,
        *,
        clinic_id: str,
        kind: str,
        journey: str,
        family: str,
        pattern: str,
        record_kind: str = "",
        channel: str = "",
        detail: str = "",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> "KnowledgeGap":
        """Construct a KnowledgeGap after scrubbing all fields (including caller-known patient names)."""
        names = list(extra_names) if extra_names else None
        p_clean, _ = scrub_text(pattern, extra_names=names, max_len=MAX_PATTERN_CHARS)
        d_clean, _ = scrub_text(detail, extra_names=names, max_len=MAX_DETAIL_CHARS)
        return cls(
            ts=_minute_iso(ts),
            clinic_id=clinic_id,
            kind=kind,
            journey=journey,
            family=family,
            pattern=p_clean,
            record_kind=record_kind,
            channel=channel,
            detail=d_clean,
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeGap":
        return cls(
            ts=str(data.get("ts") or ""),
            clinic_id=str(data.get("clinic_id") or "default"),
            kind=str(data["kind"]),
            journey=str(data.get("journey") or "core"),
            family=str(data.get("family") or ""),
            pattern=str(data.get("pattern") or ""),
            record_kind=str(data.get("record_kind") or ""),
            channel=str(data.get("channel") or ""),
            detail=str(data.get("detail") or ""),
        )


class KnowledgeGapLog:
    """Thread-safe, append-only, non-blocking observability sink for KnowledgeGap records."""

    def __init__(
        self,
        clinic_id: str = "default",
        path: str | Path | None = None,
        *,
        strict: bool = False,
    ) -> None:
        self.clinic_id = str(clinic_id or "default")
        self.path = Path(path) if path is not None else None
        self.strict = strict
        self._lock = threading.Lock()
        self._events: list[KnowledgeGap] = []
        self._dropped_errors: int = 0

    @property
    def events(self) -> tuple[KnowledgeGap, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def dropped_errors(self) -> int:
        with self._lock:
            return self._dropped_errors

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def record(self, gap: KnowledgeGap) -> bool:
        """Append a pre-built KnowledgeGap. Never raises into engine callers unless strict=True."""
        try:
            line = gap.to_json()
            with self._lock:
                self._events.append(gap)
                if self.path is not None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
            return True
        except Exception:
            with self._lock:
                self._dropped_errors += 1
            if self.strict:
                raise
            return False

    def emit(
        self,
        *,
        kind: str,
        journey: str,
        family: str,
        pattern: str,
        record_kind: str = "",
        channel: str = "",
        detail: str = "",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> KnowledgeGap | None:
        """Build a PHI-scrubbed KnowledgeGap and record it non-blockingly."""
        try:
            gap = KnowledgeGap.create(
                clinic_id=self.clinic_id,
                kind=kind,
                journey=journey,
                family=family,
                pattern=pattern,
                record_kind=record_kind,
                channel=channel,
                detail=detail,
                ts=ts,
                extra_names=extra_names,
            )
            self.record(gap)
            return gap
        except Exception:
            with self._lock:
                self._dropped_errors += 1
            if self.strict:
                raise
            return None

    def write_jsonl(self, path: str | Path) -> Path:
        """Write all buffered gaps to a JSONL file (e.g. beside look-back outputs)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            lines = [g.to_json() for g in self._events]
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return out

    def summary(self) -> dict[str, Any]:
        return summarize_gaps(self.events)


class GapEmitters:
    """Five initial zero-LLM-call gap emitters specified in WO #62.

    Attached beside VerifyingClinic, Store, Extractor/Reader, and Lookback without modifying
    their clinical decisions or asymmetric authority.
    """

    def __init__(self, log: KnowledgeGapLog) -> None:
        self.log = log

    # 1. Unmapped entities
    def emit_unmapped_test(
        self,
        what: str,
        *,
        journey: str = "core",
        record_kind: str = "consult_note",
        channel: str = "cms",
        ts: datetime | str | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="unmapped_test",
            journey=journey,
            family="committed_test",
            pattern=what,
            record_kind=record_kind,
            channel=channel,
            detail=f"tests_for returned [] for commitment phrase: {what}",
            ts=ts,
        )

    def emit_unmapped_drug(
        self,
        drug_token: str,
        *,
        journey: str = "medication",
        family: str = "medication",
        record_kind: str = "consult_note",
        channel: str = "cms",
        ts: datetime | str | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="unmapped_drug",
            journey=journey,
            family=family,
            pattern=drug_token,
            record_kind=record_kind,
            channel=channel,
            detail=f"drug token not mapped to a known formulary class: {drug_token}",
            ts=ts,
        )

    # 2. Reader / extractor degradation
    def emit_quote_dropped(
        self,
        field_name: str,
        *,
        task_name: str,
        journey: str,
        record_kind: str = "",
        channel: str = "",
        ts: datetime | str | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="quote_dropped",
            journey=journey,
            family=task_name,
            pattern=f"{task_name}.{field_name}",
            record_kind=record_kind,
            channel=channel,
            detail=f"reader.verify dropped non-verbatim quote for field {field_name} in task {task_name}",
            ts=ts,
        )

    def emit_reader_fallback(
        self,
        task_name: str,
        *,
        journey: str,
        reason: str = "replay_miss",
        record_kind: str = "",
        channel: str = "",
        ts: datetime | str | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="reader_fallback",
            journey=journey,
            family=task_name,
            pattern=task_name,
            record_kind=record_kind,
            channel=channel,
            detail=f"reader fell back to rules floor ({reason})",
            ts=ts,
        )

    # 3. Parser misses
    def emit_parser_miss(
        self,
        *,
        journey: str = "insurance",
        family: str = "insurance_paperwork",
        pattern: str = "no_ask_or_deadline",
        record_kind: str = "insurer_letter",
        channel: str = "fax",
        detail: str = "insurer document arrived with no parsed ask or deadline",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="parser_miss",
            journey=journey,
            family=family,
            pattern=pattern,
            record_kind=record_kind,
            channel=channel,
            detail=detail,
            ts=ts,
            extra_names=extra_names,
        )

    # 4. Platform check warnings/failures from VerifyingClinic
    def emit_verify_checks(
        self,
        checks: Iterable[dict[str, Any]],
        *,
        journey: str = "core",
        family: str = "",
        record_kind: str = "",
        channel: str = "cms",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> list[KnowledgeGap]:
        emitted: list[KnowledgeGap] = []
        for chk in checks:
            status = str(chk.get("status") or "").lower()
            if status in ("warn", "fail"):
                check_name = str(chk.get("check") or chk.get("name") or "unknown")
                msg = str(chk.get("detail") or chk.get("reason") or status)
                gap = self.log.emit(
                    kind="verify_warn",
                    journey=journey,
                    family=family or check_name,
                    pattern=f"{check_name}:{status}",
                    record_kind=record_kind,
                    channel=channel,
                    detail=msg,
                    ts=ts,
                    extra_names=extra_names,
                )
                if gap is not None:
                    emitted.append(gap)
        return emitted

    # 5. Doctor friction traces (Store.doctor_close / Store.doctor_fact)
    def emit_doctor_close(
        self,
        resolution: str,
        *,
        family: str,
        journey: str = "core",
        note: str = "",
        record_kind: str = "",
        channel: str = "cms",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> KnowledgeGap | None:
        res = str(resolution or "").strip().lower()
        if res == "already_done":
            kind = "doctor_already_done"
        elif res == "not_relevant":
            kind = "doctor_not_relevant"
        else:
            return None
        return self.log.emit(
            kind=kind,
            journey=journey,
            family=family,
            pattern=note or family,
            record_kind=record_kind,
            channel=channel,
            detail=note or f"doctor closed item as {res}",
            ts=ts,
            extra_names=extra_names,
        )

    def emit_doctor_fact_reject(
        self,
        *,
        fact_kind: str,
        pattern: str,
        note: str = "",
        journey: str = "core",
        record_kind: str = "",
        channel: str = "cms",
        ts: datetime | str | None = None,
        extra_names: Iterable[str] | None = None,
    ) -> KnowledgeGap | None:
        return self.log.emit(
            kind="fact_rejected",
            journey=journey,
            family=fact_kind,
            pattern=pattern,
            record_kind=record_kind,
            channel=channel,
            detail=note or f"doctor rejected {fact_kind} fact",
            ts=ts,
            extra_names=extra_names,
        )


def summarize_gaps(gaps: Iterable[KnowledgeGap]) -> dict[str, Any]:
    """Produce a PHI-free aggregate summary of recorded KnowledgeGap events."""
    items = list(gaps)
    by_kind = Counter(g.kind for g in items)
    by_journey = Counter(g.journey for g in items)
    top_patterns = Counter((g.kind, g.pattern) for g in items).most_common(10)
    return {
        "total": len(items),
        "by_kind": dict(sorted(by_kind.items())),
        "by_journey": dict(sorted(by_journey.items())),
        "top_patterns": [
            {"kind": k, "pattern": p, "count": c}
            for (k, p), c in top_patterns
        ],
    }
