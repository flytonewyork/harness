"""Production 3-tier clinical memory manager with verbatim quote verification, PDPO lifecycle & FORCE RLS store.

Enforces core architectural locks from docs/PRODUCT_DECISIONS.md:
1. Clinic EMR stays the sole legal/clinical Source of Truth. `emr_memory` is a read-only projection;
   `agent_memory_entries` stores longitudinal trajectories, verified quotes, and preferences separately.
2. Ephemeral Private Isolation: `ephemeral_private` (Doctor <-> Sam scratchpad) is HARD-BLOCKED from
   EMR writebacks, patient-facing portals, and cross-channel context assembly.
3. Verbatim Quote Verification: Every clinical episodic/longitudinal fact upsert MUST pass OCR/CJK-safe
   quote-span verification against the source corpus (fail-closed: fabricated quotes are rejected).
4. Longitudinal Trajectory & Contradiction Tracking: Updating an existing `(patient_id, topic)` preserves
   history via `superseded_by` links and flags `contradiction_detected` when active values conflict.
5. HK PDPO (Cap. 486) DPP1/DPP2/DPP3/DPP6: Consent-scope validation, topic-specific TTL / staleness,
   patient memory export (excluding private doctor scratchpad), and clean erasure without touching EMR.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from backend.acute.db import AgentMemoryEntry, Database, new_id, utcnow

VALID_PERSISTENT_TIERS = frozenset({"episodic", "longitudinal", "preference", "override_diff"})
EPHEMERAL_PRIVATE_TIER = "ephemeral_private"
VALID_CONSENT_SCOPES = frozenset({"ai_assisted_intake", "longitudinal_care", "clinic_practice_learning"})

# Topic-specific clinical staleness thresholds in hours (None = never goes stale)
TOPIC_STALENESS_HOURS: dict[str, int | None] = {
    "acute_symptoms": 48,
    "vitals": 24,
    "peak_temp": 24,
    "rat_result": 72,
    "sick_leave_request": 168,
    "home_bp": 336,           # 14 days
    "inr_result": 336,        # 14 days
    "pending_lab_followup": 2160,  # 90 days
    "serum_ferritin": 2160,
    "hba1c": 2160,
    "thyroid_tsh": 2160,
    "home_med_reconciliation": 720,  # 30 days
    "safety_alerts": None,    # allergies / G6PD never silently expire
    "allergy": None,
    "g6pd_status": None,
}

# Default PDPO retention windows (days) per memory tier
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "episodic": 180,
    "longitudinal": 365 * 3,
    "preference": 365 * 2,
    "override_diff": 365 * 3,
}

# Full-width HK Chinese punctuation & dash normalization table for OCR-safe quote matching
_PUNCT_MAP = str.maketrans({
    "：": ":",
    "，": ",",
    "。": ".",
    "（": "(",
    "）": ")",
    "；": ";",
    "！": "!",
    "？": "?",
    "–": "-",
    "—": "-",
    "－": "-",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "　": " ",
})
_WS_RE = re.compile(r"\s+")


class MemoryPolicyError(ValueError):
    """Raised on consent violations, unverified quotes, or invalid memory tiers."""


class EMRIsolationError(ValueError):
    """Raised if ephemeral private Sam notes are ever passed into EMR or patient-facing channels."""


@dataclass(frozen=True)
class QuoteVerificationResult:
    verified: bool
    normalized_quote: str
    start_char: int | None = None
    end_char: int | None = None
    reason: str = ""


_TIGHT_PUNCT = frozenset(":,;()-")


def _normalize_with_index_map(text: str) -> tuple[str, list[int]]:
    """Normalize punctuation + whitespace (including CJK full-width spacing) while preserving char offsets."""
    out_chars: list[str] = []
    idx_map: list[int] = []
    prev_space = False
    for idx, ch in enumerate(text or ""):
        mapped = ch.translate(_PUNCT_MAP).lower()
        if mapped.isspace():
            if not prev_space and out_chars and out_chars[-1] not in _TIGHT_PUNCT:
                out_chars.append(" ")
                idx_map.append(idx)
                prev_space = True
        else:
            if mapped in _TIGHT_PUNCT and out_chars and out_chars[-1] == " ":
                out_chars.pop()
                idx_map.pop()
            out_chars.append(mapped)
            idx_map.append(idx)
            prev_space = False
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        idx_map.pop()
    return "".join(out_chars), idx_map


def verify_memory_quote(quote: str, source_corpus: str | list[str]) -> QuoteVerificationResult:
    """Verify a quote appears verbatim (OCR/CJK-punctuation-tolerant) inside source_corpus."""
    q_clean = (quote or "").strip()
    if not q_clean:
        return QuoteVerificationResult(False, "", reason="Empty verbatim quote rejected.")
    q_norm, _ = _normalize_with_index_map(q_clean)
    if len(q_norm.replace(" ", "")) < 3:
        return QuoteVerificationResult(False, q_norm, reason="Quote too short (< 3 non-space chars) to ground clinical fact.")

    corpus_str = "\n".join(source_corpus) if isinstance(source_corpus, list) else (source_corpus or "")
    if not corpus_str.strip():
        return QuoteVerificationResult(False, q_norm, reason="Empty source corpus; cannot verify quote.")

    body_norm, idx_map = _normalize_with_index_map(corpus_str)
    pos = body_norm.find(q_norm)
    if pos < 0:
        return QuoteVerificationResult(
            False,
            q_norm,
            reason="Quote not found verbatim in source corpus (possible paraphrase or hallucination).",
        )
    start_char = idx_map[pos]
    end_pos = min(len(idx_map) - 1, pos + len(q_norm) - 1)
    end_char = idx_map[end_pos] + 1
    return QuoteVerificationResult(True, q_norm, start_char=start_char, end_char=end_char, reason="verified")


@dataclass
class MemoryRecord:
    id: str
    tenant_id: str
    patient_id: str
    tier: str
    topic: str
    summary_text: str
    source_doc_id: str | None
    source_quote: str
    consent_scope: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)

    @property
    def is_superseded(self) -> bool:
        return bool(self.payload.get("superseded_by"))

    @property
    def expires_at(self) -> datetime | None:
        raw = self.payload.get("expires_at")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def is_expired(self, now: datetime | None = None) -> bool:
        exp = self.expires_at
        if exp is None:
            return False
        cur = now or utcnow()
        return cur >= exp

    def staleness(self, now: datetime | None = None) -> str:
        ttl_hours = TOPIC_STALENESS_HOURS.get(self.topic, 720)
        if ttl_hours is None:
            return "fresh"
        cur = now or utcnow()
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        age_hours = max(0.0, (cur - created).total_seconds() / 3600.0)
        if age_hours >= ttl_hours:
            return "stale"
        if age_hours >= ttl_hours * 0.7:
            return "aging"
        return "fresh"

    def to_dict(self, *, now: datetime | None = None) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "patient_id": self.patient_id,
            "tier": self.tier,
            "topic": self.topic,
            "summary_text": self.summary_text,
            "source_doc_id": self.source_doc_id,
            "source_quote": self.source_quote,
            "consent_scope": self.consent_scope,
            "superseded_by": self.payload.get("superseded_by"),
            "contradiction_detected": bool(self.payload.get("contradiction_detected", False)),
            "staleness": self.staleness(now=now),
            "verified_span": self.payload.get("verified_span"),
            "created_at": self.created_at.isoformat(),
        }


class MemoryStoreProtocol(Protocol):
    def insert(self, rec: MemoryRecord) -> MemoryRecord: ...
    def list_by_patient(self, tenant_id: str, patient_id: str) -> list[MemoryRecord]: ...
    def mark_superseded(self, tenant_id: str, old_id: str, new_id_val: str, *, contradiction: bool = False) -> None: ...
    def delete_by_ids(self, tenant_id: str, ids: list[str]) -> int: ...


class InMemoryAgentMemoryStore:
    """Fast tenant-scoped in-memory store for offline tests and ephemeral sessions."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], MemoryRecord] = {}

    def insert(self, rec: MemoryRecord) -> MemoryRecord:
        self._rows[(rec.tenant_id, rec.id)] = copy.deepcopy(rec)
        return copy.deepcopy(rec)

    def list_by_patient(self, tenant_id: str, patient_id: str) -> list[MemoryRecord]:
        out = [
            copy.deepcopy(r)
            for (tid, _), r in self._rows.items()
            if tid == tenant_id and r.patient_id == patient_id
        ]
        out.sort(key=lambda r: r.created_at)
        return out

    def mark_superseded(self, tenant_id: str, old_id: str, new_id_val: str, *, contradiction: bool = False) -> None:
        row = self._rows.get((tenant_id, old_id))
        if row is not None:
            row.payload["superseded_by"] = new_id_val
            if contradiction:
                row.payload["contradiction_detected"] = True

    def delete_by_ids(self, tenant_id: str, ids: list[str]) -> int:
        removed = 0
        for mid in ids:
            if (tenant_id, mid) in self._rows:
                del self._rows[(tenant_id, mid)]
                removed += 1
        return removed


class PostgresAgentMemoryStore:
    """PostgreSQL 15 FORCE RLS-backed store reusing existing `agent_memory_entries` table."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _to_record(row: AgentMemoryEntry) -> MemoryRecord:
        return MemoryRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            patient_id=row.patient_id,
            tier=row.tier,
            topic=row.topic,
            summary_text=row.summary_text,
            source_doc_id=row.source_doc_id,
            source_quote=row.source_quote,
            consent_scope=row.consent_scope,
            payload=dict(row.payload_json or {}),
            created_at=row.created_at,
        )

    def insert(self, rec: MemoryRecord) -> MemoryRecord:
        with self.db.tenant_session(rec.tenant_id) as s:
            orm = AgentMemoryEntry(
                id=rec.id,
                tenant_id=rec.tenant_id,
                patient_id=rec.patient_id,
                tier=rec.tier,
                topic=rec.topic,
                summary_text=rec.summary_text,
                source_doc_id=rec.source_doc_id,
                source_quote=rec.source_quote,
                consent_scope=rec.consent_scope,
                payload_json=dict(rec.payload),
                created_at=rec.created_at,
            )
            s.add(orm)
            s.flush()
            return self._to_record(orm)

    def list_by_patient(self, tenant_id: str, patient_id: str) -> list[MemoryRecord]:
        with self.db.tenant_session(tenant_id) as s:
            rows = s.scalars(
                select(AgentMemoryEntry)
                .where(AgentMemoryEntry.patient_id == patient_id)
                .order_by(AgentMemoryEntry.created_at.asc())
            ).all()
            return [self._to_record(r) for r in rows]

    def mark_superseded(self, tenant_id: str, old_id: str, new_id_val: str, *, contradiction: bool = False) -> None:
        with self.db.tenant_session(tenant_id) as s:
            row = s.get(AgentMemoryEntry, old_id)
            if row is not None:
                payload = dict(row.payload_json or {})
                payload["superseded_by"] = new_id_val
                if contradiction:
                    payload["contradiction_detected"] = True
                row.payload_json = payload
                flag_modified(row, "payload_json")
                s.flush()

    def delete_by_ids(self, tenant_id: str, ids: list[str]) -> int:
        if not ids:
            return 0
        with self.db.tenant_session(tenant_id) as s:
            res = s.execute(
                delete(AgentMemoryEntry).where(AgentMemoryEntry.id.in_(ids))
            )
            return int(res.rowcount or 0)


def assert_emr_memory_isolation(
    entries_or_text: list[dict[str, Any]] | str,
    private_sam_messages: list[str] | None = None,
) -> None:
    """Fail-closed guard ensuring ephemeral private Sam chat NEVER leaks into EMR or patient surfaces."""
    if isinstance(entries_or_text, list):
        for item in entries_or_text:
            if isinstance(item, dict) and item.get("tier") == EPHEMERAL_PRIVATE_TIER:
                raise EMRIsolationError("Ephemeral private Doctor-Sam memory is strictly forbidden in EMR/patient payloads.")
        haystack = "\n".join(str(x) for x in entries_or_text).lower()
    else:
        haystack = (entries_or_text or "").lower()

    for secret_line in private_sam_messages or []:
        clean = secret_line.strip().lower()
        if len(clean) >= 12 and clean in haystack:
            raise EMRIsolationError(
                "Detected ephemeral private Doctor-Sam chat content inside EMR/patient writeback payload."
            )


class ClinicalMemoryManager:
    """High-level 3-tier clinical memory orchestrator (EMR cache + Verified Agent Memory + Ephemeral Sam)."""

    def __init__(self, store: MemoryStoreProtocol | None = None) -> None:
        self.store: MemoryStoreProtocol = store or InMemoryAgentMemoryStore()
        # Ephemeral RAM-only buffers (intentionally NOT written to PostgreSQL or Clinic EMR)
        self._emr_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._ephemeral_private_threads: dict[tuple[str, str], list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ Tier 1: Read-Only EMR Cache
    def hydrate_emr_projection(self, tenant_id: str, patient_id: str, emr_snapshot: dict[str, Any]) -> dict[str, Any]:
        proj = {
            **copy.deepcopy(emr_snapshot),
            "_source_of_truth": "clinic_emr_readonly",
            "_hydrated_at": utcnow().isoformat(),
        }
        self._emr_cache[(tenant_id, patient_id)] = proj
        return copy.deepcopy(proj)

    def get_emr_projection(self, tenant_id: str, patient_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._emr_cache.get((tenant_id, patient_id), {}))

    # ------------------------------------------------------------------ Tier 2: Persistent Agent Memory
    def upsert_verified_fact(
        self,
        *,
        tenant_id: str,
        patient_id: str,
        topic: str,
        summary_text: str,
        verbatim_quote: str,
        source_corpus: str | list[str],
        source_doc_id: str | None = None,
        tier: str = "episodic",
        consent_scope: str = "ai_assisted_intake",
        consent_granted: bool = True,
        retention_days: int | None = None,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Verify verbatim quote, check PDPO consent, supersede prior topic entry, and persist."""
        if not consent_granted:
            raise MemoryPolicyError("Cannot persist AgentMemoryEntry: HK PDPO consent not granted or withdrawn.")
        if consent_scope not in VALID_CONSENT_SCOPES:
            raise MemoryPolicyError(f"Invalid PDPO consent_scope '{consent_scope}'.")
        if tier == EPHEMERAL_PRIVATE_TIER:
            raise EMRIsolationError("Use append_ephemeral_private_turn() for private Sam chat; never persist to AgentMemory.")
        if tier not in VALID_PERSISTENT_TIERS:
            raise MemoryPolicyError(f"Invalid memory tier '{tier}'. Allowed: {sorted(VALID_PERSISTENT_TIERS)}")

        q_res = verify_memory_quote(verbatim_quote, source_corpus)
        if not q_res.verified:
            return {
                "stored": False,
                "verified": False,
                "reason": q_res.reason,
                "topic": topic,
            }

        cur_time = now or utcnow()
        ttl_days = retention_days if retention_days is not None else DEFAULT_RETENTION_DAYS.get(tier, 180)
        expires_at = cur_time + timedelta(days=max(1, ttl_days))

        existing = self.store.list_by_patient(tenant_id, patient_id)
        active_same_topic = [
            r for r in existing
            if r.topic == topic and not r.is_superseded and not r.is_expired(cur_time)
        ]

        # Idempotent dedup: identical topic + summary_text + quote returns existing entry cleanly
        for prev in active_same_topic:
            if prev.summary_text.strip().lower() == summary_text.strip().lower():
                return {
                    "stored": True,
                    "verified": True,
                    "deduplicated": True,
                    "contradiction_detected": False,
                    "entry": prev.to_dict(now=cur_time),
                }

        new_entry_id = new_id("mem")
        contradiction = False
        superseded_ids: list[str] = []
        for prev in active_same_topic:
            # Flag contradiction if another value on the same topic was logged within 72h with different summary
            prev_ts = prev.created_at if prev.created_at.tzinfo else prev.created_at.replace(tzinfo=timezone.utc)
            within_episode = abs((cur_time - prev_ts).total_seconds()) <= 72 * 3600
            if within_episode and prev.summary_text.strip().lower() != summary_text.strip().lower():
                contradiction = True
            self.store.mark_superseded(tenant_id, prev.id, new_entry_id, contradiction=contradiction)
            superseded_ids.append(prev.id)

        payload: dict[str, Any] = {
            **dict(metadata or {}),
            "verified_span": {"start_char": q_res.start_char, "end_char": q_res.end_char},
            "expires_at": expires_at.isoformat(),
            "supersedes": superseded_ids,
            "contradiction_detected": contradiction,
            "previous_summary": active_same_topic[-1].summary_text if active_same_topic else None,
        }
        rec = MemoryRecord(
            id=new_entry_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            tier=tier,
            topic=topic,
            summary_text=summary_text.strip(),
            source_doc_id=source_doc_id,
            source_quote=verbatim_quote.strip(),
            consent_scope=consent_scope,
            payload=payload,
            created_at=cur_time,
        )
        saved = self.store.insert(rec)
        return {
            "stored": True,
            "verified": True,
            "deduplicated": False,
            "contradiction_detected": contradiction,
            "superseded_ids": superseded_ids,
            "entry": saved.to_dict(now=cur_time),
        }

    def retrieve_active_memories(
        self,
        *,
        tenant_id: str,
        patient_id: str,
        topics: list[str] | None = None,
        include_stale: bool = True,
        limit: int = 8,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return active (non-superseded, non-expired) memories ranked by recency & safety priority."""
        cur_time = now or utcnow()
        rows = self.store.list_by_patient(tenant_id, patient_id)
        topic_set = set(topics) if topics else None
        active: list[MemoryRecord] = []
        for r in rows:
            if r.is_superseded or r.is_expired(cur_time):
                continue
            if topic_set is not None and r.topic not in topic_set:
                continue
            if not include_stale and r.staleness(cur_time) == "stale":
                continue
            active.append(r)

        def _rank(r: MemoryRecord) -> tuple[int, float]:
            is_safety = 1 if r.topic in ("safety_alerts", "allergy", "g6pd_status") else 0
            is_conflict = 1 if r.payload.get("contradiction_detected") else 0
            ts = (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)).timestamp()
            return (is_safety * 2 + is_conflict, ts)

        active.sort(key=_rank, reverse=True)
        return [r.to_dict(now=cur_time) for r in active[:limit]]

    # ------------------------------------------------------------------ Tier 3: Ephemeral Private Sam Thread
    def append_ephemeral_private_turn(
        self,
        *,
        tenant_id: str,
        patient_id: str,
        who: str,
        actor: str,
        text: str,
    ) -> list[dict[str, Any]]:
        """Append to RAM-only private Doctor <-> Sam scratchpad (NEVER persisted to Clinic EMR)."""
        key = (tenant_id, patient_id)
        thread = self._ephemeral_private_threads.setdefault(key, [])
        thread.append({
            "tier": EPHEMERAL_PRIVATE_TIER,
            "who": who,
            "actor": actor,
            "text": text,
            "created_at": utcnow().isoformat(),
            "emr_exportable": False,
        })
        if len(thread) > 20:
            self._ephemeral_private_threads[key] = thread[-20:]
        return copy.deepcopy(self._ephemeral_private_threads[key])

    def get_ephemeral_private_thread(self, tenant_id: str, patient_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._ephemeral_private_threads.get((tenant_id, patient_id), []))

    # ------------------------------------------------------------------ HK PDPO DPP2 / DPP6 Lifecycle
    def purge_expired(self, tenant_id: str, patient_id: str, *, now: datetime | None = None) -> int:
        cur_time = now or utcnow()
        expired_ids = [
            r.id for r in self.store.list_by_patient(tenant_id, patient_id)
            if r.is_expired(cur_time)
        ]
        return self.store.delete_by_ids(tenant_id, expired_ids)

    def export_patient_memory(self, tenant_id: str, patient_id: str) -> dict[str, Any]:
        """PDPO DPP6 Data Access export of AgentMemoryEntry (strictly excludes ephemeral private Sam chat)."""
        rows = self.store.list_by_patient(tenant_id, patient_id)
        exported = [r.to_dict() for r in rows if r.tier != EPHEMERAL_PRIVATE_TIER]
        return {
            "patient_id": patient_id,
            "tenant_id": tenant_id,
            "store": "agent_memory_entries",
            "excludes_ephemeral_private_chat": True,
            "total_entries": len(exported),
            "entries": exported,
        }

    def erase_patient_memory(self, tenant_id: str, patient_id: str) -> dict[str, Any]:
        """PDPO DPP2/DPP6 erasure of Agent Memory + RAM caches (Clinic EMR legal record stays untouched)."""
        rows = self.store.list_by_patient(tenant_id, patient_id)
        deleted = self.store.delete_by_ids(tenant_id, [r.id for r in rows])
        self._emr_cache.pop((tenant_id, patient_id), None)
        self._ephemeral_private_threads.pop((tenant_id, patient_id), None)
        return {
            "patient_id": patient_id,
            "deleted_agent_memory_entries": deleted,
            "clinic_emr_untouched": True,
        }
