"""Persistent Postgres Audit Sink for `ModelGateway` (WO #72 Stage 3 Permission Gate).

Persists every `ModelGateway` completion (and residency/budget denial) to the RLS-protected
`agent_runs` table without coupling `backend.gateway.gateway` directly to SQLAlchemy.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from backend.acute.db import AgentRun, Database, Tenant, new_id

logger = logging.getLogger(__name__)


def make_postgres_gateway_audit_sink(database: Database) -> Callable[[dict[str, Any]], None]:
    """Return a thread-safe `ModelGateway` audit sink that persists records to `agent_runs`."""

    def _sink(entry: dict[str, Any]) -> None:
        event = str(entry.get("event") or "")
        if event not in ("gateway_complete", "residency_denied", "budget_exceeded"):
            return
        tenant_id = str(entry.get("tenant_id") or "").strip()
        if not tenant_id:
            return
        try:
            with database.admin_session() as adm:
                if adm.get(Tenant, tenant_id) is None:
                    return
            with database.tenant_session(tenant_id) as s:
                val_errors = list(entry.get("validation_errors") or [])
                validators = [
                    {"check": "zero_retention", "passed": bool(entry.get("zero_retention_enforced", True))},
                    {"check": "zero_tools", "passed": not bool(entry.get("tools_enabled", False))},
                    {"check": "protocol_schema", "passed": len(val_errors) == 0, "errors": val_errors},
                ]
                run = AgentRun(
                    id=new_id("gwrun"),
                    tenant_id=tenant_id,
                    patient_id=entry.get("patient_id") or None,
                    encounter_id=entry.get("encounter_id") or None,
                    protocol_id=str(entry.get("protocol_id") or "ad_hoc")[:64],
                    protocol_version=str(entry.get("protocol_version") or "1.0.0")[:32],
                    model_id=str(entry.get("model_id") or "unknown")[:80],
                    provider=str(entry.get("provider") or "gateway")[:32],
                    region=str(entry.get("region") or "asia-east2")[:32],
                    prompt_hash=str(entry.get("protocol_hash") or "0" * 64)[:64],
                    redacted=bool(entry.get("redacted", False)),
                    input_hash=str(entry.get("input_hash") or "0" * 64)[:64],
                    output_json={
                        "source": "model_gateway_audit",
                        "event": event,
                        "cache_key": entry.get("cache_key"),
                        "from_cache": bool(entry.get("from_cache", False)),
                        "fallback_used": bool(entry.get("fallback_used", False)),
                        "redactions_applied": list(entry.get("redactions_applied") or []),
                        "attempts": int(entry.get("attempts") or 0),
                    },
                    validators_json=validators,
                    latency_ms=int(round(float(entry.get("latency_ms") or 0.0))),
                    input_tokens=int(entry.get("input_tokens") or 0),
                    output_tokens=int(entry.get("output_tokens") or 0),
                    cost_usd=float(entry.get("cost_usd") or 0.0),
                )
                s.add(run)
        except Exception as exc:
            logger.warning("Non-fatal Postgres ModelGateway audit persistence error: %s", exc)

    return _sink
