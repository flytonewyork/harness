"""Unified integration adapter contract & fault-injecting simulator base (Section 6 & 17).

Every external integration (WhatsApp BSP, Teleconsult Booking, Pharmacy Fulfilment, Flu Vaccination,
Corporate Billing) implements this contract so production adapters and LoopBench simulators are 100%
interchangeable and verified by identical contract tests.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


class AdapterError(RuntimeError):
    """Recoverable or terminal adapter failure."""

    def __init__(self, message: str, *, retryable: bool = True, code: str = "adapter_error"):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass(frozen=True)
class AdapterResult:
    ok: bool
    external_ref: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AdapterContract(Protocol):
    name: str
    capabilities: tuple[str, ...]

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool: ...
    def health(self) -> dict[str, Any]: ...


def verify_hmac_sha256(raw_body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 webhook signature verification (Meta/Twilio/Stripe compatible)."""
    if not signature or not secret:
        return False
    clean = signature.split("=", 1)[-1].strip()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(clean.lower(), expected.lower())


class FaultInjector:
    """Deterministic fault injection for simulated clinic CI tests (timeouts, 503s, duplicates)."""

    def __init__(self) -> None:
        self.fail_next: dict[str, int] = {}
        self.permanent_down: set[str] = set()
        self.seen_idempotency: dict[str, AdapterResult] = {}

    def inject_transient_failures(self, op: str, count: int) -> None:
        self.fail_next[op] = count

    def set_down(self, op: str, down: bool = True) -> None:
        if down:
            self.permanent_down.add(op)
        else:
            self.permanent_down.discard(op)

    def check(self, op: str, idempotency_key: str | None = None) -> AdapterResult | None:
        if idempotency_key and idempotency_key in self.seen_idempotency:
            return self.seen_idempotency[idempotency_key]
        if op in self.permanent_down:
            raise AdapterError(f"Simulated outage on {op}", retryable=True, code="service_unavailable")
        remaining = self.fail_next.get(op, 0)
        if remaining > 0:
            self.fail_next[op] = remaining - 1
            raise AdapterError(f"Simulated transient failure on {op}", retryable=True, code="transient_503")
        return None

    def record(self, idempotency_key: str | None, result: AdapterResult) -> AdapterResult:
        if idempotency_key:
            self.seen_idempotency[idempotency_key] = result
        return result
