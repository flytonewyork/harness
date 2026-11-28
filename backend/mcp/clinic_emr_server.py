"""Spec-compliant JSON-RPC 2.0 MCP Server (`2025-03-26`) exposing the Simulated Clinic EMR & Clinical Tools.

Design invariants:
1. Sole EMR Adapter Boundary: All patient chart, lab/document, and immunization reads delegate strictly
   to `EMRConnectorProtocol` (`SimulatedPostgresEMRAdapter`) — zero raw SQLAlchemy EMR queries here.
2. Dual-Layer Asymmetric Authority: Tools marked `BLOCKED` or `SIGNED_WRITE` (e.g., `emr.write_signed_orders`)
   are unconditionally rejected server-side with JSON-RPC error `-32003` (`AUTHORITY_DENIED`) when invoked
   by any non-clinician (`actor_role != "clinician"`).
3. Canonical Tool Schema Single Source of Truth: `tools/list` derives directly from
   `backend.agentic.tools.build_default_clinic_tool_registry` — zero duplicated tool definitions.
4. Supports both zero-overhead in-process dispatch (`InProcessMCPClient`) and Streamable HTTP JSON-RPC 2.0
   (`POST /mcp/clinic-emr`) with per-tenant HK PDPO scoping.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from backend.acute.db import Database, new_id, utcnow
from backend.acute.emr_connector import EMRConnectorProtocol, SimulatedPostgresEMRAdapter
from backend.acute.live_bus import LiveHarnessBus
from backend.acute.practice_graph import evaluate_preventive_care
from backend.acute.rx_rules import check_prescription_safety
from backend.acute.scheduling import build_clinic_availability
from backend.acute.triage import screen_acute_message
from backend.agentic.memory import ClinicalMemoryManager, PostgresAgentMemoryStore
from backend.agentic.tools import (
    AGENT_PERMITTED_AUTHORITIES,
    ToolAuthority,
    build_default_clinic_tool_registry,
    validate_tool_args,
)

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_SERVER_NAME = "clinic-emr-mcp"
MCP_SERVER_VERSION = "1.0.0"

# Standard JSON-RPC 2.0 + clinical authority error codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
MCP_AUTHORITY_DENIED_CODE = -32003
MCP_RESOURCE_NOT_FOUND_CODE = -32004

_RESOURCE_URI_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("patient_chart", re.compile(r"^emr://patients/([^/]+)/chart$")),
    ("patient_records", re.compile(r"^emr://patients/([^/]+)/records$")),
    ("patient_preventive", re.compile(r"^emr://patients/([^/]+)/preventive-gaps$")),
    ("clinic_availability", re.compile(r"^emr://clinic/availability/([^/]+)$")),
)


def _estimate_age(dob_str: str | None) -> int:
    if not dob_str:
        return 35
    try:
        dob = date.fromisoformat(str(dob_str)[:10])
        today = date.today()
        return max(0, today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))
    except ValueError:
        return 35


class ClinicEMRMCPServer:
    """MCP JSON-RPC 2.0 Server wrapping `EMRConnectorProtocol`, HK CDS rules, Scheduling & Clinical Memory."""

    def __init__(
        self,
        *,
        db: Database,
        emr_adapter: EMRConnectorProtocol | None = None,
        memory_manager: ClinicalMemoryManager | None = None,
        live_bus: LiveHarnessBus | None = None,
    ) -> None:
        self.db = db
        self.emr: EMRConnectorProtocol = emr_adapter or SimulatedPostgresEMRAdapter()
        self.memory = memory_manager or ClinicalMemoryManager(PostgresAgentMemoryStore(db))
        self.live_bus = live_bus
        self._schema_registry = build_default_clinic_tool_registry()
        self.call_count: int = 0
        self.denied_count: int = 0

    def status_summary(self) -> dict[str, Any]:
        specs = self._schema_registry.list_specs(include_blocked=True)
        return {
            "server_name": MCP_SERVER_NAME,
            "server_version": MCP_SERVER_VERSION,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "endpoint": "/mcp/clinic-emr",
            "transports": ["in_process", "streamable_http_jsonrpc"],
            "emr_adapter": self.emr.connector_name,
            "emr_tier": self.emr.connector_tier,
            "tools_total": len(specs),
            "tools_permitted_for_ai": sum(1 for s in specs if s.authority in AGENT_PERMITTED_AUTHORITIES),
            "tools_blocked_for_ai": [s.name for s in specs if s.authority not in AGENT_PERMITTED_AUTHORITIES],
            "resource_templates": [r["uriTemplate"] for r in self._list_resource_templates()],
            "calls_served": self.call_count,
            "authority_denials": self.denied_count,
        }

    @staticmethod
    def _list_resource_templates() -> list[dict[str, Any]]:
        return [
            {
                "uriTemplate": "emr://patients/{patient_id}/chart",
                "name": "Patient EMR Chart Summary",
                "description": "Read-only demographics, allergies, G6PD status, active medications, and eGFR from Clinic EMR.",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "emr://patients/{patient_id}/records",
                "name": "Patient Clinical Records & Open Loops",
                "description": "Recent verified documents, observations, prescriptions, attachments, and active case loops.",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "emr://patients/{patient_id}/preventive-gaps",
                "name": "Patient Preventive Care Gaps",
                "description": "Deterministic HK CHP preventive triggers and vaccination eligibility.",
                "mimeType": "application/json",
            },
            {
                "uriTemplate": "emr://clinic/availability/{service}",
                "name": "Clinic Live Slot Availability",
                "description": "Real-time open GP and Nurse Rachel slots by service type.",
                "mimeType": "application/json",
            },
        ]

    def _emit_sse(self, patient_id: str, *, tool: str, authority: str, emr_target: str, summary: str) -> None:
        if self.live_bus is None or not patient_id:
            return
        try:
            self.live_bus.append_tool_call(
                patient_id,
                {
                    "agent": "MCP::clinic-emr",
                    "tool": tool,
                    "authority": authority,
                    "emr_target": emr_target,
                    "summary": summary,
                },
            )
        except Exception:
            logger.debug("Non-fatal MCP SSE emit failure for %s", tool, exc_info=True)

    # ------------------------------------------------------------------ JSON-RPC 2.0 Dispatcher
    def handle_jsonrpc(
        self,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        actor_role: str = "assistant",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a single MCP JSON-RPC 2.0 request and return a spec-compliant JSON-RPC 2.0 response."""
        req_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            return self._err(req_id, JSONRPC_INVALID_REQUEST, "Invalid JSON-RPC 2.0 request envelope.")

        method = payload["method"]
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        ctx = dict(runtime_context or {})

        if method == "initialize":
            return self._ok(
                req_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": MCP_SERVER_VERSION,
                        "emrAdapter": self.emr.connector_name,
                    },
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                },
            )

        if method == "ping":
            return self._ok(req_id, {})

        if method == "tools/list":
            include_blocked = bool(params.get("include_blocked", True))
            specs = self._schema_registry.list_specs(include_blocked=include_blocked)
            return self._ok(
                req_id,
                {
                    "tools": [
                        {
                            "name": s.name,
                            "description": s.description,
                            "inputSchema": s.parameters,
                            "_meta": {
                                "authority": s.authority.value,
                                "idempotent": s.idempotent,
                                "emr_target": s.emr_target,
                                "ai_permitted": s.authority in AGENT_PERMITTED_AUTHORITIES,
                            },
                        }
                        for s in specs
                    ]
                },
            )

        if method == "resources/list":
            return self._ok(
                req_id,
                {
                    "resources": [],
                    "resourceTemplates": self._list_resource_templates(),
                },
            )

        if method == "resources/read":
            uri = str(params.get("uri") or "").strip()
            if not uri:
                return self._err(req_id, JSONRPC_INVALID_PARAMS, "Missing required param 'uri'.")
            return self._handle_resource_read(req_id, uri=uri, tenant_id=tenant_id, ctx=ctx)

        if method == "tools/call":
            raw_name = str(params.get("name") or "").strip()
            args = params.get("arguments")
            if args is None:
                args = {}
            if not raw_name or not isinstance(args, dict):
                return self._err(req_id, JSONRPC_INVALID_PARAMS, "tools/call requires 'name' (str) and 'arguments' (object).")
            return self._handle_tool_call(
                req_id,
                tool_name=raw_name,
                arguments=args,
                tenant_id=tenant_id,
                actor_role=actor_role,
                ctx=ctx,
            )

        return self._err(req_id, JSONRPC_METHOD_NOT_FOUND, f"Unknown MCP method '{method}'.")

    # ------------------------------------------------------------------ Resources
    def _handle_resource_read(
        self,
        req_id: Any,
        *,
        uri: str,
        tenant_id: str,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        for kind, pat in _RESOURCE_URI_PATTERNS:
            m = pat.match(uri)
            if not m:
                continue
            target = m.group(1)
            if kind == "patient_chart":
                data = self._exec_get_patient_chart({"patient_id": target}, tenant_id=tenant_id, ctx=ctx)
            elif kind == "patient_records":
                data = self._exec_get_clinical_records({"patient_id": target}, tenant_id=tenant_id, ctx=ctx)
            elif kind == "patient_preventive":
                data = self._exec_check_preventive_gaps({"patient_id": target}, tenant_id=tenant_id, ctx=ctx)
            else:
                data = self._exec_get_available_slots({"service": target}, tenant_id=tenant_id, ctx=ctx)
            return self._ok(
                req_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(data, ensure_ascii=False),
                        }
                    ]
                },
            )
        return self._err(req_id, MCP_RESOURCE_NOT_FOUND_CODE, f"Unknown Clinic EMR resource URI: '{uri}'.")

    # ------------------------------------------------------------------ Tools
    def _handle_tool_call(
        self,
        req_id: Any,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        actor_role: str,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        canon_name = self._schema_registry.resolve_name(tool_name)
        spec = self._schema_registry.get(canon_name)
        if spec is None:
            return self._err(req_id, JSONRPC_METHOD_NOT_FOUND, f"Unknown MCP tool '{tool_name}'.")

        # Server-side Asymmetric Authority enforcement (second defense layer)
        if actor_role != "clinician" and spec.authority not in AGENT_PERMITTED_AUTHORITIES:
            self.denied_count += 1
            reason = (
                f"DENIED (MCP Server Asymmetric Authority): actor_role='{actor_role}' cannot execute "
                f"'{spec.name}' [authority={spec.authority.value}]. Requires HK MCHK doctor sign-off."
            )
            pid = str(arguments.get("patient_id") or ctx.get("patient_id") or "")
            self._emit_sse(
                pid,
                tool=spec.name,
                authority=ToolAuthority.BLOCKED.value,
                emr_target=spec.emr_target or "authority_boundary",
                summary=reason,
            )
            return self._err(
                req_id,
                MCP_AUTHORITY_DENIED_CODE,
                reason,
                data={"error_code": "AUTHORITY_DENIED", "tool": spec.name, "authority": spec.authority.value},
            )

        arg_errors = validate_tool_args(spec, arguments)
        if arg_errors:
            return self._err(
                req_id,
                JSONRPC_INVALID_PARAMS,
                "MCP tool argument validation failed.",
                data={"validation_errors": arg_errors},
            )

        self.call_count += 1
        dispatch_map = {
            "emr.get_patient_chart": self._exec_get_patient_chart,
            "emr.get_clinical_records": self._exec_get_clinical_records,
            "emr.check_preventive_gaps": self._exec_check_preventive_gaps,
            "clinic.get_available_slots": self._exec_get_available_slots,
            "clinic.book_slot": self._exec_book_slot,
            "safety.screen_red_flags": self._exec_screen_red_flags,
            "memory.upsert_fact": self._exec_upsert_fact,
            "cds.check_medication_safety": self._exec_check_medication_safety,
            "harness.stage_staff_proposal": self._exec_stage_staff_proposal,
        }
        fn = dispatch_map.get(spec.name)
        if fn is None:
            return self._err(req_id, JSONRPC_INTERNAL_ERROR, f"No MCP handler bound for '{spec.name}'.")

        try:
            result = fn(arguments, tenant_id=tenant_id, ctx=ctx)
        except Exception as exc:
            logger.warning("MCP tool '%s' execution error: %s", spec.name, exc, exc_info=True)
            return self._err(req_id, JSONRPC_INTERNAL_ERROR, f"Tool execution error: {type(exc).__name__}: {exc}")

        return self._ok(
            req_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": False,
                "_meta": {
                    "protocol": MCP_PROTOCOL_VERSION,
                    "server": MCP_SERVER_NAME,
                    "emr_adapter": self.emr.connector_name,
                    "authority": spec.authority.value,
                },
            },
        )

    # ------------------------------------------------------------------ Adapter-Backed Tool Implementations
    def _exec_get_patient_chart(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        summary = self.emr.read_patient_summary(self.db, tenant_id, pid)
        if summary is None:
            return {"found": False, "patient_id": pid}
        out = {
            "found": True,
            "patient_id": summary.get("patient_id", pid),
            "full_name": summary.get("name_en", ""),
            "name_zh": summary.get("name_zh", ""),
            "dob": summary.get("dob", ""),
            "age": _estimate_age(summary.get("dob")),
            "sex": summary.get("sex", "U"),
            "allergies": list(summary.get("allergies") or []),
            "g6pd_deficient": bool(summary.get("g6pd_deficient", False)),
            "egfr": summary.get("egfr"),
            "weight_kg": summary.get("weight_kg"),
            "current_meds": list(summary.get("active_meds") or []),
            "corporate_id": summary.get("corporate_id"),
            "campaign_id": summary.get("campaign_id"),
            "source_of_truth": "clinic_emr_readonly",
            "mcp_adapter": self.emr.connector_name,
        }
        self._emit_sse(
            pid,
            tool="emr.get_patient_chart",
            authority="READ",
            emr_target="patient_chart",
            summary=f"MCP READ chart for {out['full_name']} (G6PD={out['g6pd_deficient']}, allergies={len(out['allergies'])}, eGFR={out['egfr']})",
        )
        return out

    def _exec_get_clinical_records(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        out = self.emr.read_clinical_records(self.db, tenant_id, pid)
        self._emit_sse(
            pid,
            tool="emr.get_clinical_records",
            authority="READ",
            emr_target="documents",
            summary=f"MCP READ {len(out.get('documents', []))} docs, {len(out.get('attachments', []))} attachments, {len(out.get('recent_observations', []))} obs",
        )
        return out

    def _exec_check_preventive_gaps(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        summary = self.emr.read_patient_summary(self.db, tenant_id, pid)
        if summary is None:
            return {"patient_id": pid, "gaps": []}
        imms = self.emr.read_immunizations(self.db, tenant_id, pid)
        completed = [str(i.get("cvx_code") or i.get("vaccine_display") or "").lower() for i in imms]
        profile = {
            "age": _estimate_age(summary.get("dob")),
            "sex": summary.get("sex", "U"),
            "conditions": [],
            "febrile": bool(ctx.get("febrile", False)),
            "corporate_plan": str(summary.get("corporate_id") or ""),
            "completed_preventive_ids": completed,
        }
        gaps = evaluate_preventive_care(profile)
        self._emit_sse(
            pid,
            tool="emr.check_preventive_gaps",
            authority="READ",
            emr_target="open_loops",
            summary=f"MCP evaluated preventive gaps -> {len(gaps)} active trigger(s)",
        )
        return {"patient_id": pid, "gaps": gaps[:6]}

    def _exec_get_available_slots(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        service = str(args.get("service") or "teleconsult")
        pid = str(ctx.get("patient_id") or "")
        include_nurse = service in ("flu_vaccine", "nurse_slot", "blood_draw")
        with self.db.tenant_session(tenant_id) as session:
            roster = build_clinic_availability(
                session,
                tenant_id,
                for_case_key=ctx.get("case_key"),
                include_preventive_nurse=include_nurse,
            )
        compact_slots: list[dict[str, Any]] = []
        for doc in roster[:4]:
            for day_blk in (doc.get("days") or [])[:2]:
                for sl in (day_blk.get("slots") or []):
                    if sl.get("status") == "open":
                        compact_slots.append({
                            "slot_id": f"{doc.get('id')}_{day_blk.get('day_key')}_{sl.get('time')}",
                            "provider": doc.get("name"),
                            "role": doc.get("role"),
                            "day": day_blk.get("label_en"),
                            "time": sl.get("time"),
                            "service": service,
                        })
                        if len(compact_slots) >= 6:
                            break
                if len(compact_slots) >= 6:
                    break
        self._emit_sse(
            pid,
            tool="clinic.get_available_slots",
            authority="READ",
            emr_target="schedule",
            summary=f"MCP found {len(compact_slots)} open {service} slot(s)",
        )
        return {"service": service, "open_slots": compact_slots}

    def _exec_book_slot(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        slot_id = str(args.get("slot_id") or "")
        self._emit_sse(
            pid,
            tool="clinic.book_slot",
            authority="DRAFT",
            emr_target="schedule",
            summary=f"MCP staged non-clinical booking slot {slot_id}",
        )
        return {
            "booked": True,
            "status": "staged_confirmation",
            "patient_id": pid,
            "slot_id": slot_id,
            "recorded_at": utcnow().isoformat(),
        }

    def _exec_screen_red_flags(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        msg = str(args.get("message") or "")
        temp_c = args.get("temp_c")
        if temp_c is not None and f"{temp_c}" not in msg:
            msg = f"{msg} (temperature {temp_c}C)".strip()
        res = screen_acute_message(msg)
        pid = str(ctx.get("patient_id") or "")
        self._emit_sse(
            pid,
            tool="safety.screen_red_flags",
            authority="CDS",
            emr_target="safety_sentinel",
            summary=f"MCP union-only triage -> urgency={res.urgency}, kind={res.kind or 'none'}",
        )
        return {
            "kind": res.kind,
            "urgency": res.urgency,
            "flags": list(res.flags or []),
            "screened_by": res.screened_by,
        }

    def _exec_upsert_fact(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(ctx.get("patient_id") or "")
        topic = str(args.get("topic") or args.get("key") or "clinical_fact").strip()
        value = str(args.get("value") or "").strip()
        quote = str(args.get("verbatim_quote") or "").strip()
        tier = str(args.get("tier") or "episodic").strip()
        corpus: list[str] = []
        for src in (ctx.get("source_corpus") or []):
            if str(src or "").strip():
                corpus.append(str(src))
        if ctx.get("current_input"):
            corpus.append(str(ctx["current_input"]))
        for extra in (ctx.get("extra_corpus") or []):
            if str(extra or "").strip():
                corpus.append(str(extra))
        res = self.memory.upsert_verified_fact(
            tenant_id=tenant_id,
            patient_id=pid,
            topic=topic,
            summary_text=value,
            verbatim_quote=quote,
            source_corpus=corpus,
            source_doc_id=str(args.get("source") or "patient_chat"),
            tier=tier,
            consent_scope="ai_assisted_intake",
            consent_granted=True,
        )
        self._emit_sse(
            pid,
            tool="memory.upsert_fact",
            authority="MEMORY",
            emr_target="memory",
            summary=(
                f"MCP stored verified fact [{topic}]"
                if res.get("stored")
                else f"MCP REJECTED unverified quote for [{topic}]: {res.get('reason', 'quote mismatch')}"
            ),
        )
        return res

    def _exec_check_medication_safety(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        drugs = [str(d).strip() for d in (args.get("drugs") or []) if str(d).strip()]
        dx_icd10 = args.get("diagnosis_icd10")
        summary = self.emr.read_patient_summary(self.db, tenant_id, pid) or {}
        profile = {
            "dob": summary.get("dob") or "1990-01-01",
            "weight_kg": summary.get("weight_kg"),
            "age": _estimate_age(summary.get("dob")),
            "sex": summary.get("sex", "F"),
            "allergies": list(summary.get("allergies") or []),
            "g6pd_deficient": bool(summary.get("g6pd_deficient", False)),
            "egfr": summary.get("egfr"),
            "current_meds": list(summary.get("active_meds") or []),
            "conditions": [],
        }
        items = [{"drug": d, "dose_mg": 300 if d.lower() == "aspirin" else 500, "freq": "BD"} for d in drugs]
        report = check_prescription_safety(profile, items, diagnosis_icd10=dx_icd10).to_dict()
        self._emit_sse(
            pid,
            tool="cds.check_medication_safety",
            authority="CDS",
            emr_target="cds_rules",
            summary=f"MCP CDS checked {', '.join(drugs)} -> status={report.get('status')} ({len(report.get('findings') or [])} findings)",
        )
        return report

    def _exec_stage_staff_proposal(self, args: dict[str, Any], *, tenant_id: str, ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        staged_id = new_id("draft_prop")
        self._emit_sse(
            pid,
            tool="harness.stage_staff_proposal",
            authority="DRAFT",
            emr_target="proposal_queue",
            summary=f"MCP staged unsigned draft packet {staged_id} for Nurse -> Doctor sign-off",
        )
        return {
            "staged": True,
            "proposal_id": staged_id,
            "authority": "DRAFT",
            "requires_mchk_doctor_signoff": True,
            "patient_id": pid,
            "symptoms": list(args.get("symptoms") or []),
            "sick_leave_days_requested": args.get("sick_leave_days"),
        }

    # ------------------------------------------------------------------ JSON-RPC Helpers
    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _err(req_id: Any, code: int, message: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
        err_obj: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err_obj["data"] = data
        return {"jsonrpc": "2.0", "id": req_id, "error": err_obj}
