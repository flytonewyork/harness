"""Typed tool-call registry, JSON-schema arg validator, asymmetric authority gate & multi-provider adapters.

Hard production invariants enforced on every tool call:
1. Asymmetric Authority Gate: Tools marked `BLOCKED` or `SIGNED_WRITE` are unconditionally DENIED
   when invoked by any AI agent — before the handler is ever reached.
2. Progressive Skill Allowlist Intersection: A turn may ONLY invoke tools permitted by the
   currently active `SKILL.md` selection.
3. Strict Runtime JSON-Schema Validation: Validates required args, types, enums, numeric bounds,
   and unknown fields — returning crisp self-repair errors instead of crashing.
4. Hallucinated Tool Recovery & Loop Circuit Breaker: Resolves Gemini underscore names back to
   canonical dot-names, deduplicates repeated identical idempotent calls, and caps calls per turn.
5. Native Multi-Provider Translation: Emits Vertex/Gemini `functionDeclarations`, Anthropic `tools`,
   and OpenAI `tools`, and parses provider tool-call payloads into a single `ToolCallRequest`.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ToolAuthority(str, Enum):
    READ = "READ"
    MEMORY = "MEMORY"
    CDS = "CDS"
    DRAFT = "DRAFT"
    BLOCKED = "BLOCKED"
    SIGNED_WRITE = "SIGNED_WRITE"


AGENT_PERMITTED_AUTHORITIES = frozenset({
    ToolAuthority.READ,
    ToolAuthority.MEMORY,
    ToolAuthority.CDS,
    ToolAuthority.DRAFT,
})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    authority: ToolAuthority
    description: str
    parameters: dict[str, Any]
    idempotent: bool = True
    requires_verified_quote: bool = False
    emr_target: str | None = None
    handler: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None

    @property
    def gemini_name(self) -> str:
        return self.name.replace(".", "_")

    def to_schema_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "authority": self.authority.value,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class ToolCallRequest:
    call_id: str
    name: str
    args: dict[str, Any]
    provider_raw_name: str = ""


@dataclass(frozen=True)
class ToolExecutionResult:
    call_id: str
    tool_name: str
    authority: str
    ok: bool
    result: dict[str, Any]
    error_code: str | None = None
    denied_reason: str | None = None
    deduplicated: bool = False
    duration_ms: float = 0.0
    emr_target: str | None = None

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool_name,
            "authority": self.authority,
            "ok": self.ok,
            "error_code": self.error_code,
            "denied_reason": self.denied_reason,
            "deduplicated": self.deduplicated,
            "duration_ms": self.duration_ms,
            "emr_target": self.emr_target,
            "result": self.result,
        }


def _check_json_type(val: Any, expected: str | list[str]) -> bool:
    types = [expected] if isinstance(expected, str) else list(expected)
    for t in types:
        if t == "null" and val is None:
            return True
        if t == "boolean" and isinstance(val, bool):
            return True
        if t == "integer" and isinstance(val, int) and not isinstance(val, bool):
            return True
        if t == "number" and isinstance(val, (int, float)) and not isinstance(val, bool):
            return True
        if t == "string" and isinstance(val, str):
            return True
        if t == "array" and isinstance(val, list):
            return True
        if t == "object" and isinstance(val, dict):
            return True
    return False


def validate_tool_args(spec: ToolSpec, args: Any) -> list[str]:
    """Validate tool arguments against a JSON Schema subset with actionable error messages."""
    if not isinstance(args, dict):
        return ["Tool arguments must be a JSON object."]
    if "_malformed_json" in args:
        return [f"Malformed JSON in tool arguments: {str(args['_malformed_json'])[:120]}"]

    schema = spec.parameters or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    errors: list[str] = []

    for req_key in required:
        if req_key not in args or args[req_key] is None or args[req_key] == "":
            errors.append(f"Missing required argument '{req_key}'.")

    if schema.get("additionalProperties") is False:
        unknown = sorted(set(args.keys()) - set(props.keys()))
        if unknown:
            errors.append(f"Unexpected argument(s): {', '.join(unknown)}.")

    for k, v in args.items():
        if k not in props:
            continue
        p = props[k]
        expected_type = p.get("type")
        if expected_type and not _check_json_type(v, expected_type):
            errors.append(f"Argument '{k}' expected type '{expected_type}', got '{type(v).__name__}'.")
            continue
        if "enum" in p and v not in p["enum"]:
            errors.append(f"Argument '{k}' must be one of {p['enum']}, got '{v}'.")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in p and v < p["minimum"]:
                errors.append(f"Argument '{k}' ({v}) is below minimum {p['minimum']}.")
            if "maximum" in p and v > p["maximum"]:
                errors.append(f"Argument '{k}' ({v}) exceeds maximum {p['maximum']}.")
        if isinstance(v, list) and "items" in p and isinstance(p["items"], dict):
            item_type = p["items"].get("type")
            if item_type:
                for idx, item in enumerate(v):
                    if not _check_json_type(item, item_type):
                        errors.append(f"Argument '{k}[{idx}]' expected type '{item_type}'.")
    return errors


class ToolTurnGuard:
    """Per-turn execution guard enforcing call budgets, loop circuit breaking, and dedup cache."""

    def __init__(self, *, max_calls_per_turn: int = 8, max_identical_repeats: int = 2) -> None:
        self.max_calls_per_turn = max_calls_per_turn
        self.max_identical_repeats = max_identical_repeats
        self.total_calls = 0
        self._repeat_counts: dict[str, int] = {}
        self._idempotent_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def fingerprint(tool_name: str, args: dict[str, Any]) -> str:
        blob = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def check_budget(self) -> str | None:
        if self.total_calls >= self.max_calls_per_turn:
            return f"Tool call budget exceeded ({self.max_calls_per_turn} calls/turn). Synthesize final response now."
        return None

    def record_call(self, key: str) -> int:
        self.total_calls += 1
        self._repeat_counts[key] = self._repeat_counts.get(key, 0) + 1
        return self._repeat_counts[key]

    def get_cached(self, key: str) -> dict[str, Any] | None:
        return self._idempotent_cache.get(key)

    def put_cached(self, key: str, result: dict[str, Any]) -> None:
        self._idempotent_cache[key] = result


class ToolRegistry:
    """Governed registry for clinical agent tools with strict authority enforcement & multi-provider schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._gemini_to_canonical: dict[str, str] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec
        self._gemini_to_canonical[spec.gemini_name] = spec.name

    def resolve_name(self, raw_name: str) -> str:
        clean = (raw_name or "").strip()
        if clean in self._tools:
            return clean
        return self._gemini_to_canonical.get(clean, clean)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(self.resolve_name(name))

    def list_specs(self, allowed_names: set[str] | None = None, *, include_blocked: bool = False) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for spec in self._tools.values():
            if not include_blocked and spec.authority not in AGENT_PERMITTED_AUTHORITIES:
                continue
            if allowed_names is not None and spec.name not in allowed_names:
                continue
            out.append(spec)
        return sorted(out, key=lambda s: s.name)

    # ------------------------------------------------------------------ Multi-Provider Tool Schemas
    def to_vertex_gemini_tools(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        decls = [
            {
                "name": s.gemini_name,
                "description": f"[{s.authority.value}] {s.description}",
                "parameters": s.parameters,
            }
            for s in self.list_specs(allowed_names)
        ]
        return [{"functionDeclarations": decls}] if decls else []

    def to_anthropic_tools(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "name": s.gemini_name,
                "description": f"[{s.authority.value}] {s.description}",
                "input_schema": s.parameters,
            }
            for s in self.list_specs(allowed_names)
        ]

    def to_openai_tools(self, allowed_names: set[str] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": s.gemini_name,
                    "description": f"[{s.authority.value}] {s.description}",
                    "parameters": s.parameters,
                },
            }
            for s in self.list_specs(allowed_names)
        ]

    def parse_provider_tool_calls(self, provider: str, raw_response: dict[str, Any]) -> list[ToolCallRequest]:
        """Normalize native tool calls from Vertex/Gemini, Anthropic, or OpenAI into ToolCallRequest list."""
        calls: list[ToolCallRequest] = []
        p = (provider or "").lower()

        if p in ("vertex", "gemini"):
            cands = raw_response.get("candidates") or []
            parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
            for idx, part in enumerate(parts):
                if "functionCall" in part:
                    fc = part["functionCall"]
                    raw_nm = str(fc.get("name") or "")
                    calls.append(ToolCallRequest(
                        call_id=f"gem_call_{idx}",
                        name=self.resolve_name(raw_nm),
                        args=dict(fc.get("args") or {}),
                        provider_raw_name=raw_nm,
                    ))

        elif p == "anthropic":
            for idx, block in enumerate(raw_response.get("content") or []):
                if block.get("type") == "tool_use":
                    raw_nm = str(block.get("name") or "")
                    calls.append(ToolCallRequest(
                        call_id=str(block.get("id") or f"anth_call_{idx}"),
                        name=self.resolve_name(raw_nm),
                        args=dict(block.get("input") or {}),
                        provider_raw_name=raw_nm,
                    ))

        elif p == "openai":
            choices = raw_response.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            for idx, tc in enumerate(msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                raw_nm = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except ValueError:
                    parsed = {"_malformed_json": str(raw_args)}
                calls.append(ToolCallRequest(
                    call_id=str(tc.get("id") or f"oai_call_{idx}"),
                    name=self.resolve_name(raw_nm),
                    args=parsed if isinstance(parsed, dict) else {},
                    provider_raw_name=raw_nm,
                ))

        return calls

    # ------------------------------------------------------------------ Governed Execution Gate
    def execute(
        self,
        request: ToolCallRequest,
        *,
        allowed_tools: set[str] | None = None,
        actor_role: str = "assistant",
        guard: ToolTurnGuard | None = None,
        context: dict[str, Any] | None = None,
    ) -> ToolExecutionResult:
        """Validate, authorize, deduplicate, and execute a tool call fail-closed."""
        t0 = time.perf_counter()
        canon_name = self.resolve_name(request.name)
        spec = self._tools.get(canon_name)

        # 1. Unknown / hallucinated tool recovery
        if spec is None:
            permitted = [s.name for s in self.list_specs(allowed_tools)]
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=canon_name,
                authority="UNKNOWN",
                ok=False,
                error_code="UNKNOWN_TOOL",
                result={
                    "error": f"Unknown tool '{canon_name}'.",
                    "permitted_tools": permitted,
                },
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # 2. Asymmetric Authority Hard Floor (agents NEVER invoke BLOCKED or SIGNED_WRITE)
        if actor_role != "clinician" and spec.authority not in AGENT_PERMITTED_AUTHORITIES:
            reason = (
                f"DENIED (Asymmetric Authority): AI agent cannot execute '{spec.name}' "
                f"[authority={spec.authority.value}]. Requires HK MCHK registered doctor sign-off."
            )
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=spec.name,
                authority=spec.authority.value,
                ok=False,
                error_code="AUTHORITY_DENIED",
                denied_reason=reason,
                emr_target=spec.emr_target,
                result={"allowed": False, "error": reason},
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # 3. Progressive Skill Allowlist Intersection
        if allowed_tools is not None and spec.name not in allowed_tools:
            reason = f"Tool '{spec.name}' is not enabled by the active clinical skills for this turn."
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=spec.name,
                authority=spec.authority.value,
                ok=False,
                error_code="TOOL_NOT_IN_ACTIVE_SKILLS",
                denied_reason=reason,
                emr_target=spec.emr_target,
                result={"allowed": False, "error": reason, "allowed_tools": sorted(allowed_tools)},
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # 4. Strict Runtime JSON-Schema Argument Validation
        arg_errors = validate_tool_args(spec, request.args)
        if arg_errors:
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=spec.name,
                authority=spec.authority.value,
                ok=False,
                error_code="INVALID_TOOL_ARGS",
                emr_target=spec.emr_target,
                result={"error": "Tool argument validation failed.", "validation_errors": arg_errors},
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # 5. Per-turn call budget & loop circuit breaker / idempotent deduplication
        if guard is not None:
            budget_err = guard.check_budget()
            if budget_err:
                return ToolExecutionResult(
                    call_id=request.call_id,
                    tool_name=spec.name,
                    authority=spec.authority.value,
                    ok=False,
                    error_code="TOOL_BUDGET_EXCEEDED",
                    emr_target=spec.emr_target,
                    result={"error": budget_err},
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                )
            fp = guard.fingerprint(spec.name, request.args)
            repeats = guard.record_call(fp)
            if spec.idempotent and repeats > 1:
                cached = guard.get_cached(fp)
                if cached is not None:
                    return ToolExecutionResult(
                        call_id=request.call_id,
                        tool_name=spec.name,
                        authority=spec.authority.value,
                        ok=True,
                        deduplicated=True,
                        emr_target=spec.emr_target,
                        result={**cached, "_deduplicated": True},
                        duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                    )
            if repeats > guard.max_identical_repeats:
                return ToolExecutionResult(
                    call_id=request.call_id,
                    tool_name=spec.name,
                    authority=spec.authority.value,
                    ok=False,
                    error_code="LOOP_CIRCUIT_BREAKER",
                    emr_target=spec.emr_target,
                    result={"error": f"Repeated identical call to '{spec.name}' halted by loop circuit breaker."},
                    duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                )

        if spec.handler is None:
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=spec.name,
                authority=spec.authority.value,
                ok=False,
                error_code="NO_HANDLER_BOUND",
                emr_target=spec.emr_target,
                result={"error": f"No runtime handler bound for tool '{spec.name}'."},
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        try:
            out = spec.handler(dict(request.args), dict(context or {}))
        except Exception as exc:
            return ToolExecutionResult(
                call_id=request.call_id,
                tool_name=spec.name,
                authority=spec.authority.value,
                ok=False,
                error_code="TOOL_EXECUTION_ERROR",
                emr_target=spec.emr_target,
                result={"error": f"Tool execution failed cleanly: {type(exc).__name__}: {exc}"},
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        if guard is not None and spec.idempotent:
            guard.put_cached(guard.fingerprint(spec.name, request.args), out)

        return ToolExecutionResult(
            call_id=request.call_id,
            tool_name=spec.name,
            authority=spec.authority.value,
            ok=True,
            emr_target=spec.emr_target,
            result=out,
            duration_ms=round((time.perf_counter() - t0) * 1000, 2),
        )


def build_default_clinic_tool_registry(
    handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] | None = None,
) -> ToolRegistry:
    """Build the canonical clinic tool registry matching agent_harness.py + workspace contracts."""
    h = handlers or {}
    reg = ToolRegistry()
    specs = [
        ToolSpec(
            name="emr.get_patient_chart",
            authority=ToolAuthority.READ,
            description="Read patient demographics, allergies, G6PD status, active medications, eGFR, and employer plan from Clinic EMR.",
            parameters={
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="patient_chart",
            handler=h.get("emr.get_patient_chart"),
        ),
        ToolSpec(
            name="emr.get_clinical_records",
            authority=ToolAuthority.READ,
            description="Retrieve recent lab reports, GP consult notes, observations, and open follow-up loops from Clinic EMR.",
            parameters={
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="documents",
            handler=h.get("emr.get_clinical_records"),
        ),
        ToolSpec(
            name="emr.check_preventive_gaps",
            authority=ToolAuthority.READ,
            description="Evaluate seasonal flu vaccination eligibility, corporate coverage, and open preventive gaps.",
            parameters={
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="open_loops",
            handler=h.get("emr.check_preventive_gaps"),
        ),
        ToolSpec(
            name="clinic.get_available_slots",
            authority=ToolAuthority.READ,
            description="Query live bookable nurse vaccination, blood draw, and clinic follow-up slots.",
            parameters={
                "type": "object",
                "properties": {"service": {"type": "string", "enum": ["flu_vaccine", "teleconsult", "nurse_slot", "blood_draw"]}},
                "required": ["service"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="schedule",
            handler=h.get("clinic.get_available_slots"),
        ),
        ToolSpec(
            name="clinic.book_slot",
            authority=ToolAuthority.DRAFT,
            description="Book a non-clinical vaccination or follow-up slot in the clinic schedule.",
            parameters={
                "type": "object",
                "properties": {"patient_id": {"type": "string"}, "slot_id": {"type": "string"}},
                "required": ["patient_id", "slot_id"],
                "additionalProperties": False,
            },
            idempotent=False,
            emr_target="schedule",
            handler=h.get("clinic.book_slot"),
        ),
        ToolSpec(
            name="safety.screen_red_flags",
            authority=ToolAuthority.CDS,
            description="Run deterministic bilingual HK 999 Medical and Mental-Health crisis screen (union-only, zero LLM bypass).",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}, "temp_c": {"type": "number", "minimum": 30.0, "maximum": 45.0}},
                "required": ["message"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="safety_sentinel",
            handler=h.get("safety.screen_red_flags"),
        ),
        ToolSpec(
            name="memory.upsert_fact",
            authority=ToolAuthority.MEMORY,
            description="Persist a structured clinical fact into Agent Memory ONLY with a verified verbatim quote.",
            parameters={
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "verbatim_quote": {"type": "string"},
                    "source": {"type": "string"},
                    "tier": {"type": "string", "enum": ["episodic", "longitudinal", "preference"]},
                },
                "required": ["value", "verbatim_quote"],
                "additionalProperties": False,
            },
            idempotent=False,
            requires_verified_quote=True,
            emr_target="memory",
            handler=h.get("memory.upsert_fact"),
        ),
        ToolSpec(
            name="cds.check_medication_safety",
            authority=ToolAuthority.CDS,
            description="Evaluate proposed or patient-asked drugs against HK Cap. 134, G6PD, allergy, DDI, and renal eGFR rules.",
            parameters={
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "drugs": {"type": "array", "items": {"type": "string"}},
                    "diagnosis_icd10": {"type": "string"},
                },
                "required": ["patient_id", "drugs"],
                "additionalProperties": False,
            },
            idempotent=True,
            emr_target="cds_rules",
            handler=h.get("cds.check_medication_safety"),
        ),
        ToolSpec(
            name="harness.stage_staff_proposal",
            authority=ToolAuthority.DRAFT,
            description="Stage a structured clinical review packet (draft orders + MCHK sick-leave draft) for Nurse -> Doctor sign-off.",
            parameters={
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "symptoms": {"type": "array", "items": {"type": "string"}},
                    "onset": {"type": "string"},
                    "temp_c": {"type": "number", "minimum": 34.0, "maximum": 43.0},
                    "rat_result": {"type": "string"},
                    "sick_leave_days": {"type": "integer", "minimum": 1, "maximum": 7},
                    "patient_message": {"type": "string"},
                },
                "required": ["patient_id"],
                "additionalProperties": False,
            },
            idempotent=False,
            emr_target="proposal_queue",
            handler=h.get("harness.stage_staff_proposal"),
        ),
        ToolSpec(
            name="emr.write_signed_orders",
            authority=ToolAuthority.BLOCKED,
            description="Doctor-only MCHK signature write-back to EMR. Strictly DENIED if invoked by an AI agent.",
            parameters={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
                "additionalProperties": False,
            },
            idempotent=False,
            emr_target="authority_boundary",
            handler=h.get("emr.write_signed_orders"),
        ),
    ]
    for sp in specs:
        reg.register(sp)
    return reg
