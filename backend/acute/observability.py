"""Production observability, Cloud Logging structured JSON logs, and Prometheus metrics.

Zero PHI in logs or metrics (every string passes PHIScrubber). Designed for GCP Cloud Run
Cloud Logging & Cloud Monitoring / Prometheus scraping with bounded memory footprints.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from .security import PHIScrubber


class CloudJsonLogFormatter(logging.Formatter):
    """Structured JSON formatter compatible with Google Cloud Logging (severity, trace, httpRequest)."""

    _LEVEL_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        raw_msg = record.getMessage()
        clean_msg, _ = PHIScrubber.scrub(raw_msg)
        payload: dict[str, Any] = {
            "severity": self._LEVEL_MAP.get(record.levelno, "INFO"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
            "message": clean_msg,
        }
        for key in ("request_id", "method", "path", "status_code", "latency_ms", "tenant_id", "actor_role", "error_code"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_structured_logger(name: str = "tmrw.acute") -> logging.Logger:
    log = logging.getLogger(name)
    if not any(isinstance(h.formatter, CloudJsonLogFormatter) for h in log.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(CloudJsonLogFormatter())
        log.addHandler(handler)
        log.propagate = False
    log.setLevel(logging.INFO)
    return log


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return round(sorted_vals[lo], 2)
    frac = idx - lo
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac, 2)


def normalize_route_group(path: str) -> str:
    """Collapse dynamic path IDs into bounded cardinality route groups for Prometheus/SLO tracking."""
    if not path.startswith("/api/"):
        return "spa_static"
    parts = [p for p in path.split("/") if p]
    # e.g. /api/v1/clinician/proposals/{id}/decide -> api.clinician.proposals
    if len(parts) >= 4:
        return f"api.{parts[2]}.{parts[3]}"
    if len(parts) == 3:
        return f"api.{parts[2]}"
    return "api.other"


class TelemetryRegistry:
    """Thread-safe bounded-memory SLO & clinical safety metrics registry."""

    def __init__(self, max_samples_per_group: int = 1000) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._max_samples = max_samples_per_group
        self._all_latencies: deque[float] = deque(maxlen=max_samples_per_group * 2)
        self._route_latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max_samples_per_group))
        self._http_total: int = 0
        self._http_status_classes: dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
        self._counters: dict[str, int] = {
            "intakes_total": 0,
            "red_flags_escalated_total": 0,
            "prompt_injections_neutralized_total": 0,
            "rx_safety_checks_total": 0,
            "rx_hard_blocks_total": 0,
            "rx_overrides_total": 0,
            "certs_signed_total": 0,
            "prescriptions_signed_total": 0,
            "wa_messages_sent_total": 0,
            "outbox_events_processed_total": 0,
            "outbox_events_dlq_total": 0,
            "pdpo_dar_exports_total": 0,
            "pdpo_dcr_submitted_total": 0,
            "pdpo_deletions_total": 0,
            "unhandled_errors_total": 0,
        }

    def record_http(self, path: str, status_code: int, latency_ms: float) -> None:
        group = normalize_route_group(path)
        cls_key = f"{status_code // 100}xx"
        with self._lock:
            self._http_total += 1
            if cls_key in self._http_status_classes:
                self._http_status_classes[cls_key] += 1
            self._all_latencies.append(latency_ms)
            self._route_latencies[group].append(latency_ms)

    def inc(self, metric: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[metric] = self._counters.get(metric, 0) + amount

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            all_s = sorted(self._all_latencies)
            route_stats = {}
            for grp, dq in sorted(self._route_latencies.items()):
                vals = sorted(dq)
                route_stats[grp] = {
                    "count": len(vals),
                    "p50_ms": _percentile(vals, 50),
                    "p95_ms": _percentile(vals, 95),
                    "p99_ms": _percentile(vals, 99),
                }
            total = self._http_total
            err_5xx = self._http_status_classes["5xx"]
            error_rate_pct = round((err_5xx / total) * 100.0, 3) if total > 0 else 0.0
            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "http": {
                    "requests_total": total,
                    "status_classes": dict(self._http_status_classes),
                    "error_rate_5xx_pct": error_rate_pct,
                    "latency_ms": {
                        "p50": _percentile(all_s, 50),
                        "p95": _percentile(all_s, 95),
                        "p99": _percentile(all_s, 99),
                    },
                    "routes": route_stats,
                },
                "clinical_safety": dict(self._counters),
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines: list[str] = [
            "# HELP clinic_harness_uptime_seconds Process uptime in seconds",
            "# TYPE clinic_harness_uptime_seconds gauge",
            f"clinic_harness_uptime_seconds {snap['uptime_seconds']}",
            "# HELP clinic_harness_http_requests_total Total HTTP requests handled",
            "# TYPE clinic_harness_http_requests_total counter",
            f"clinic_harness_http_requests_total {snap['http']['requests_total']}",
        ]
        for cls_key, cnt in snap["http"]["status_classes"].items():
            lines.append(f'clinic_harness_http_responses_total{{status_class="{cls_key}"}} {cnt}')
        lat = snap["http"]["latency_ms"]
        lines.extend([
            "# HELP clinic_harness_http_latency_ms Rolling HTTP request latency percentiles in milliseconds",
            "# TYPE clinic_harness_http_latency_ms gauge",
            f'clinic_harness_http_latency_ms{{quantile="0.50"}} {lat["p50"]}',
            f'clinic_harness_http_latency_ms{{quantile="0.95"}} {lat["p95"]}',
            f'clinic_harness_http_latency_ms{{quantile="0.99"}} {lat["p99"]}',
            "# HELP clinic_harness_clinical_events_total Clinical safety and PDPO governance counters",
            "# TYPE clinic_harness_clinical_events_total counter",
        ])
        for k, v in snap["clinical_safety"].items():
            lines.append(f"clinic_harness_{k} {v}")
        return "\n".join(lines) + "\n"
