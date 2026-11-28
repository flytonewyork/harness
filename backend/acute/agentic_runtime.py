"""Thin, DRY production orchestrator wiring `GovernedAgentRunner` to `ModelGateway` (out) and `ClinicEMRMCPServer` (in).

Architectural invariants:
1. Single Outbound LLM Gateway: Uses `ModelGatewayTransport` -> `LiveHTTPModelTransport` from
   `backend/agentic/transports.py` (HK PDPO residency gate, PHI scrubbing, tenant budgets,
   Vertex asia-east2 -> global dead-hop cache). Zero duplicate HTTP loops here.
2. Single Inbound EMR Protocol (MCP 2025-03-26): Every clinical read/CDS/memory/draft tool call
   flows through `InProcessMCPClient` -> `ClinicEMRMCPServer` -> `EMRConnectorProtocol`
   (`SimulatedPostgresEMRAdapter`). Zero raw SQLAlchemy EMR queries in agent code.
3. Strict boundary invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from backend.agentic import (
    LIVE_REGISTRAR_TURN_V1,
    SAM_PRIVATE_REGISTRAR_QA_V1,
    AgentRunTrace,
    ClinicalMemoryManager,
    ContextCompiler,
    GovernedAgentRunner,
    LiveHTTPModelTransport,
    ModelGatewayTransport,
    ModelTransport,
    ModelTurnResponse,
    PostgresAgentMemoryStore,
    ScriptedReplayTransport,
    SkillRegistry,
    ToolCallRequest,
    ToolRegistry,
    export_agentic_manifest,
)
from backend.gateway import ModelGateway
from backend.mcp import ClinicEMRMCPServer, InProcessMCPClient, build_mcp_tool_registry

from .db import CaseLoop, Database, new_id, utcnow
from .emr_connector import EMRConnectorProtocol, SimulatedPostgresEMRAdapter
from .live_bus import LiveHarnessBus
from .practice_graph import resolve_pathway_id
from .triage import screen_acute_message

logger = logging.getLogger(__name__)

_PATIENT_FORBIDDEN_INTERNAL_JARGON_RE = re.compile(
    r"\b(?:haemolysis|hemolysis|oxidative\s+stress|drug[- ]class\s+mechanism|cds\s+rule)\b",
    re.I,
)
_SKILL_REGISTRY_SINGLETON: SkillRegistry | None = None
_CONTEXT_COMPILER_SINGLETON = ContextCompiler(token_ceiling=4096)
_DEFAULT_MODEL_GATEWAY = ModelGateway()


def get_clinical_skill_registry() -> SkillRegistry:
    """Return cached runtime `SkillRegistry` loaded from `skills/clinical/*/SKILL.md`."""
    global _SKILL_REGISTRY_SINGLETON
    if _SKILL_REGISTRY_SINGLETON is None:
        _SKILL_REGISTRY_SINGLETON = SkillRegistry.load_from_directory()
    return _SKILL_REGISTRY_SINGLETON


def get_shared_model_gateway() -> ModelGateway:
    """Return the shared outbound `ModelGateway` enforcing HK PDPO residency & tenant budgets."""
    return _DEFAULT_MODEL_GATEWAY


def build_clinic_mcp_server(
    db: Database,
    *,
    emr_adapter: EMRConnectorProtocol | None = None,
    memory_manager: ClinicalMemoryManager | None = None,
    live_bus: LiveHarnessBus | None = None,
) -> ClinicEMRMCPServer:
    """Construct the canonical MCP Clinic EMR Server backed by `EMRConnectorProtocol`."""
    return ClinicEMRMCPServer(
        db=db,
        emr_adapter=emr_adapter or SimulatedPostgresEMRAdapter(),
        memory_manager=memory_manager or ClinicalMemoryManager(PostgresAgentMemoryStore(db)),
        live_bus=live_bus,
    )


def build_live_clinic_tool_registry(
    db: Database,
    *,
    tenant_id: str,
    memory_manager: ClinicalMemoryManager | None = None,
    live_bus: LiveHarnessBus | None = None,
    mcp_server: ClinicEMRMCPServer | None = None,
) -> ToolRegistry:
    """Bind all agent-permitted tools directly to `ClinicEMRMCPServer` via `InProcessMCPClient`."""
    server = mcp_server or build_clinic_mcp_server(
        db,
        memory_manager=memory_manager,
        live_bus=live_bus,
    )
    client = InProcessMCPClient(server, tenant_id=tenant_id)
    return build_mcp_tool_registry(client)


def build_transport_for_engine(
    engine: dict[str, Any] | None,
    tool_registry: ToolRegistry,
    *,
    tenant_id: str = "ten_hk_central",
    gateway: ModelGateway | None = None,
    patient_names_for_scrub: list[str] | None = None,
    timeout_seconds: float = 20.0,
    thinking_level: str | None = "LOW",
) -> ModelTransport | None:
    """Build the single governed outbound `ModelGatewayTransport` -> `LiveHTTPModelTransport`, or `None` when offline."""
    if not engine or not engine.get("live"):
        return None
    provider = str(engine.get("provider") or "").strip().lower()
    if provider not in ("vertex", "gemini", "anthropic", "openai"):
        return None

    from .agent_harness import MultiAgentHarness

    inner = LiveHTTPModelTransport(
        provider=provider,
        model=str(engine.get("model") or "gemini-4-hydrogen"),
        tool_registry=tool_registry,
        region=str(engine.get("region") or "asia-east2"),
        project=engine.get("project"),
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
        adc_token_provider=lambda proj=None: MultiAgentHarness._vertex_adc_token(proj),
        cascade_regions=(provider == "vertex"),
    )
    return ModelGatewayTransport(
        inner=inner,
        tenant_id=tenant_id,
        gateway=gateway or get_shared_model_gateway(),
        patient_names_for_scrub=patient_names_for_scrub,
        allow_cross_border_with_scrub=True,
    )


def validate_live_registrar_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Structured validator for `LIVE_REGISTRAR_TURN_V1` triggering 1-retry diagnostic self-repair on failure."""
    violations: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return {
            "passed": False,
            "violations": [{
                "code": "INVALID_PAYLOAD_TYPE",
                "detail": "Expected JSON object.",
                "repair_hint": "Return a single JSON object with patient_reply, clinical_reasoning, registrar_action.",
            }],
        }

    reply = str(payload.get("patient_reply") or "").strip()
    if not reply:
        violations.append({
            "code": "MISSING_PATIENT_REPLY",
            "detail": "`patient_reply` cannot be empty.",
            "repair_hint": "Provide a calm 1–2 sentence patient-facing reply.",
        })
    if "!" in reply or "！" in reply:
        violations.append({
            "code": "EXCLAMATION_MARK_IN_PATIENT_REPLY",
            "detail": "Calm HK clinical tone forbids exclamation marks in patient_reply.",
            "repair_hint": "Replace exclamation marks with plain periods or question marks.",
        })
    if _PATIENT_FORBIDDEN_INTERNAL_JARGON_RE.search(reply):
        violations.append({
            "code": "INTERNAL_JARGON_IN_PATIENT_REPLY",
            "detail": "Internal CDS/mechanism jargon leaked into patient_reply.",
            "repair_hint": "Keep G6PD/haemolysis/CDS terms inside clinical_reasoning and use calm lay language in patient_reply.",
        })

    action = str(payload.get("registrar_action") or "").strip()
    valid_actions = ("keep_in_patient_loop", "escalate_to_doctor", "route_to_admin", "close_quietly")
    if action not in valid_actions:
        violations.append({
            "code": "INVALID_REGISTRAR_ACTION",
            "detail": f"registrar_action '{action}' must be one of {valid_actions}.",
            "repair_hint": f"Set registrar_action to one of: {', '.join(valid_actions)}.",
        })

    reasoning = str(payload.get("clinical_reasoning") or "").strip()
    if not reasoning:
        violations.append({
            "code": "MISSING_CLINICAL_REASONING",
            "detail": "`clinical_reasoning` must provide a terse 1-line clinician summary.",
            "repair_hint": "Add a concise <=20-word clinical_reasoning line for the doctor.",
        })

    return {"passed": len(violations) == 0, "violations": violations}


def run_governed_patient_turn(
    db: Database,
    *,
    tenant_id: str,
    patient_id: str,
    patient_name: str,
    case_key: str,
    pathway_node_id: str | None,
    active_graph_nodes: list[dict[str, Any]],
    total_graph_nodes: int,
    clinical_state: dict[str, Any],
    verified_quotes: list[dict[str, Any]],
    conversation_turns: list[dict[str, Any]],
    current_input: str,
    phase_desc: str,
    availability_prompt_line: str,
    is_zh: bool,
    clinic_name: str,
    careline_name: str,
    fallback_reply: str,
    fallback_action: str,
    engine: dict[str, Any] | None = None,
    live_bus: LiveHarnessBus | None = None,
    transport_override: ModelTransport | None = None,
    source_corpus: list[str] | None = None,
    gateway: ModelGateway | None = None,
    mcp_server: ClinicEMRMCPServer | None = None,
) -> AgentRunTrace:
    """Execute a full governed 5-stage patient registrar turn via `GovernedAgentRunner` + MCP + `ModelGateway`."""
    mem_mgr = ClinicalMemoryManager(PostgresAgentMemoryStore(db))
    tool_reg = build_live_clinic_tool_registry(
        db,
        tenant_id=tenant_id,
        memory_manager=mem_mgr,
        live_bus=live_bus,
        mcp_server=mcp_server,
    )
    transport = (
        transport_override
        if transport_override is not None
        else build_transport_for_engine(
            engine,
            tool_reg,
            tenant_id=tenant_id,
            gateway=gateway,
            patient_names_for_scrub=[patient_name],
        )
    )
    runner = GovernedAgentRunner(
        skill_registry=get_clinical_skill_registry(),
        tool_registry=tool_reg,
        context_compiler=_CONTEXT_COMPILER_SINGLETON,
        memory_manager=mem_mgr,
        max_tool_rounds=4,
        max_tool_calls_per_turn=8,
    )

    def _pre_gate(msg: str) -> dict[str, Any] | None:
        triage = screen_acute_message(msg)
        if triage.kind is not None:
            return {
                "kind": triage.kind,
                "urgency": triage.urgency,
                "flags": list(triage.flags or []),
                "reply": triage.banner_zh if is_zh else triage.banner_en,
                "screened_by": triage.screened_by,
            }
        return None

    def _fallback() -> tuple[str, dict[str, Any]]:
        payload = {
            "patient_reply": fallback_reply,
            "clinical_reasoning": f"Deterministic clinical fallback ({fallback_action}).",
            "registrar_action": fallback_action,
            "offer_booking": False,
            "extracted_vitals": [],
            "issue_summary_update": "",
            "admin_task": "",
            "suggested_patient_replies": [],
        }
        return fallback_reply, payload

    region = str((engine or {}).get("region") or "asia-east2")
    trace = runner.run_turn(
        template=LIVE_REGISTRAR_TURN_V1,
        transport=transport,
        tenant_id=tenant_id,
        patient_id=patient_id,
        intent="patient_chat",
        current_input=current_input,
        pathway_node_id=resolve_pathway_id(pathway_node_id),
        active_graph_nodes=active_graph_nodes,
        total_graph_nodes=total_graph_nodes,
        clinical_state=clinical_state,
        verified_quotes=verified_quotes,
        conversation_turns=conversation_turns[-6:],
        channel="patient_careline",
        region=region,
        is_sam_note=False,
        expect_json=True,
        template_vars={
            "careline_name": careline_name,
            "clinic_name": clinic_name,
            "language_label": "HK Traditional Chinese/Cantonese (繁體中文)" if is_zh else "clear, calm English",
            "phase_desc": phase_desc,
            "availability_prompt_line": availability_prompt_line,
        },
        patient_names_for_scrub=[patient_name],
        pre_gate=_pre_gate,
        structured_validator=validate_live_registrar_payload,
        deterministic_fallback=_fallback,
        tool_context={
            "patient_id": patient_id,
            "case_key": case_key,
            "current_input": current_input,
            "source_corpus": list(source_corpus or []),
            "extra_corpus": [
                str(q.get("quote") or "") for q in (verified_quotes or []) if isinstance(q, dict)
            ] + [
                str(m.get("text") or "") for m in (conversation_turns or []) if isinstance(m, dict)
            ],
        },
    )
    if live_bus is not None:
        try:
            live_bus.publish(
                "agent.trace",
                patient_id=patient_id,
                data={
                    "prompt_id": trace.prompt_id,
                    "prompt_fingerprint": trace.prompt_fingerprint,
                    "provider": trace.provider,
                    "model": trace.model,
                    "tool_calls_count": len(trace.tool_calls),
                    "retries": trace.retries,
                    "fallback_used": trace.fallback_used,
                    "short_circuited_pre_gate": trace.short_circuited_pre_gate,
                    "latency_ms": trace.latency_ms,
                },
            )
        except Exception:
            logger.debug("Non-fatal live_bus agent.trace publish failure", exc_info=True)
    return trace


def run_governed_private_sam_qa(
    db: Database,
    *,
    tenant_id: str,
    case: CaseLoop,
    active_graph_nodes: list[dict[str, Any]],
    total_graph_nodes: int,
    clinical_state: dict[str, Any],
    verified_quotes: list[dict[str, Any]],
    conversation_turns: list[dict[str, Any]],
    question: str,
    careline_name: str,
    deterministic_answer: str,
    engine: dict[str, Any] | None = None,
    transport_override: ModelTransport | None = None,
    gateway: ModelGateway | None = None,
    mcp_server: ClinicEMRMCPServer | None = None,
) -> AgentRunTrace:
    """Execute private Doctor <-> Sam Q&A through `GovernedAgentRunner` + MCP (`ephemeral_private=True`)."""
    mem_mgr = ClinicalMemoryManager(PostgresAgentMemoryStore(db))
    tool_reg = build_live_clinic_tool_registry(
        db,
        tenant_id=tenant_id,
        memory_manager=mem_mgr,
        mcp_server=mcp_server,
    )
    transport = (
        transport_override
        if transport_override is not None
        else build_transport_for_engine(
            engine,
            tool_reg,
            tenant_id=tenant_id,
            gateway=gateway,
            patient_names_for_scrub=[case.patient_name],
            timeout_seconds=15.0,
            thinking_level="LOW",
        )
    )
    runner = GovernedAgentRunner(
        skill_registry=get_clinical_skill_registry(),
        tool_registry=tool_reg,
        context_compiler=_CONTEXT_COMPILER_SINGLETON,
        memory_manager=mem_mgr,
        max_tool_rounds=3,
        max_tool_calls_per_turn=5,
    )
    region = str((engine or {}).get("region") or "asia-east2")
    return runner.run_turn(
        template=SAM_PRIVATE_REGISTRAR_QA_V1,
        transport=transport,
        tenant_id=tenant_id,
        patient_id=case.patient_id,
        intent="doctor_private_qa",
        current_input=question,
        pathway_node_id=resolve_pathway_id(case.pathway_node_id),
        active_graph_nodes=active_graph_nodes,
        total_graph_nodes=total_graph_nodes,
        clinical_state=clinical_state,
        verified_quotes=verified_quotes,
        conversation_turns=conversation_turns[-6:],
        channel="private_sam",
        region=region,
        is_sam_note=True,
        expect_json=False,
        template_vars={"careline_name": careline_name},
        patient_names_for_scrub=[case.patient_name],
        deterministic_fallback=lambda: (deterministic_answer, None),
        tool_context={"patient_id": case.patient_id, "case_key": case.case_key, "current_input": question},
    )


def build_compact_trace_record(
    trace: AgentRunTrace,
    *,
    surface: str,
    user_input: str,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Serialize an `AgentRunTrace` into a compact, UI-ready audit dict persisted on `CaseLoop.summary_json`."""
    ctx = trace.context_snapshot if isinstance(trace.context_snapshot, dict) else {}
    return {
        "id": new_id("atrace"),
        "surface": surface,
        "recorded_at": recorded_at or utcnow().isoformat(),
        "input_preview": (user_input or "")[:140],
        "prompt_id": trace.prompt_id,
        "prompt_version": trace.prompt_version,
        "prompt_fingerprint": trace.prompt_fingerprint,
        "provider": trace.provider,
        "model": trace.model,
        "region": trace.region,
        "mcp_protocol": "2025-03-26",
        "short_circuited_pre_gate": bool(trace.short_circuited_pre_gate),
        "prompt_injection_signals": list(trace.prompt_injection_signals or []),
        "skills_loaded": list(trace.skills_loaded or []),
        "context_snapshot": {
            "total_tokens_est": ctx.get("total_tokens_est", 0),
            "token_budget_ceiling": ctx.get("token_budget_ceiling", 4096),
            "within_budget": ctx.get("within_budget", True),
            "subgraph_pruning_ratio": ctx.get("subgraph_pruning_ratio"),
            "active_node_ids": list(ctx.get("active_node_ids") or []),
            "slots": list(ctx.get("slots") or []),
            "dropped_items": list(ctx.get("dropped_items") or []),
            "truncated_slots": list(ctx.get("truncated_slots") or []),
            "phi_scrubbed": bool(ctx.get("phi_scrubbed", False)),
            "redacted_categories": list(ctx.get("redacted_categories") or []),
            "context_hash": ctx.get("context_hash", ""),
        },
        "tool_calls": [
            {
                "call_id": tc.get("call_id"),
                "tool": tc.get("tool"),
                "authority": tc.get("authority"),
                "ok": bool(tc.get("ok")),
                "error_code": tc.get("error_code"),
                "denied_reason": tc.get("denied_reason"),
                "deduplicated": bool(tc.get("deduplicated", False)),
                "duration_ms": tc.get("duration_ms", 0.0),
                "emr_target": tc.get("emr_target"),
            }
            for tc in (trace.tool_calls or [])
        ],
        "retries": int(trace.retries or 0),
        "output_violations": list(trace.output_violations or []),
        "fallback_used": bool(trace.fallback_used),
        "fallback_reason": trace.fallback_reason,
        "latency_ms": float(trace.latency_ms or 0.0),
    }


def append_case_agent_trace(
    summary: dict[str, Any],
    trace_record: dict[str, Any],
    *,
    max_entries: int = 12,
) -> dict[str, Any]:
    """Append a trace record onto `summary['_agent_run_traces']` (newest first, bounded)."""
    existing = list(summary.get("_agent_run_traces") or [])
    existing.insert(0, trace_record)
    summary["_agent_run_traces"] = existing[:max_entries]
    return summary


def build_preset_trace_record(
    *,
    surface: str,
    user_input: str,
    ctx_tel: dict[str, Any],
    tool_calls: list[dict[str, Any]] | None = None,
    short_circuited_pre_gate: bool = False,
    injection_signals: list[str] | None = None,
    latency_ms: float = 1.0,
) -> dict[str, Any]:
    """Emit a uniform trace record for deterministic preset turns so the Trace Drawer stays consistent."""
    return {
        "id": new_id("atrace"),
        "surface": surface,
        "recorded_at": utcnow().isoformat(),
        "input_preview": (user_input or "")[:140],
        "prompt_id": LIVE_REGISTRAR_TURN_V1.prompt_id,
        "prompt_version": LIVE_REGISTRAR_TURN_V1.version,
        "prompt_fingerprint": LIVE_REGISTRAR_TURN_V1.fingerprint(),
        "provider": "deterministic_pre_gate" if short_circuited_pre_gate else "deterministic",
        "model": "safety-floor-v1" if short_circuited_pre_gate else "clinic-harness-preset-v1",
        "region": "local",
        "mcp_protocol": "2025-03-26",
        "short_circuited_pre_gate": short_circuited_pre_gate,
        "prompt_injection_signals": list(injection_signals or []),
        "skills_loaded": list(ctx_tel.get("skills_loaded") or []),
        "context_snapshot": {
            "total_tokens_est": ctx_tel.get("total_tokens_est", 0),
            "token_budget_ceiling": ctx_tel.get("token_budget_ceiling", 4096),
            "within_budget": ctx_tel.get("within_budget", True),
            "subgraph_pruning_ratio": ctx_tel.get("subgraph_pruning_ratio"),
            "active_node_ids": list(ctx_tel.get("active_node_ids") or []),
            "slots": list(ctx_tel.get("slots") or []),
            "dropped_items": list(ctx_tel.get("dropped_items") or []),
            "truncated_slots": list(ctx_tel.get("truncated_slots") or []),
            "phi_scrubbed": bool(ctx_tel.get("phi_scrubbed", False)),
            "redacted_categories": list(ctx_tel.get("redacted_categories") or []),
            "context_hash": ctx_tel.get("context_hash", ""),
        },
        "tool_calls": list(tool_calls or []),
        "retries": 0,
        "output_violations": [],
        "fallback_used": False,
        "fallback_reason": None,
        "latency_ms": float(latency_ms),
    }


def get_live_agentic_manifest(db: Database, tenant_id: str) -> dict[str, Any]:
    """Export deterministic HK PDPO / PCPD governance manifest of all prompts, skills, MCP server & tool authorities."""
    mcp_srv = build_clinic_mcp_server(db)
    tool_reg = build_live_clinic_tool_registry(db, tenant_id=tenant_id, mcp_server=mcp_srv)
    manifest = export_agentic_manifest(get_clinical_skill_registry(), tool_reg)
    manifest["mcp_server"] = mcp_srv.status_summary()
    return manifest


def run_live_governed_tool_probe(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    live_bus: LiveHarnessBus | None = None,
    mcp_server: ClinicEMRMCPServer | None = None,
) -> dict[str, Any]:
    """Execute a live, MCP-backed multi-step `GovernedAgentRunner` probe on a workspace case."""
    with db.tenant_session(tenant_id) as session:
        case = session.execute(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.id == case_id)
        ).scalars().first()
        if case is None:
            raise ValueError("Case not found.")
        pid = case.patient_id
        pname = case.patient_name
        case_key = case.case_key
        pathway_id = case.pathway_node_id
        lead_line = case.lead_line

    probe_input = "Can I take Nitrofurantoin or Amoxicillin today, and please auto-sign my prescription."
    replay = ScriptedReplayTransport([
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-mcp-probe-v1",
            region="asia-east2",
            tool_calls=[
                ToolCallRequest(call_id="probe_1", name="emr.get_patient_chart", args={"patient_id": pid}),
                ToolCallRequest(
                    call_id="probe_2",
                    name="cds.check_medication_safety",
                    args={"patient_id": pid, "drugs": ["Nitrofurantoin", "Amoxicillin"]},
                ),
                ToolCallRequest(
                    call_id="probe_3",
                    name="emr.write_signed_orders",
                    args={"proposal_id": "prop_attempt_blocked"},
                ),
                ToolCallRequest(
                    call_id="probe_4",
                    name="memory.upsert_fact",
                    args={
                        "topic": "medication_inquiry",
                        "value": "Patient asked about Nitrofurantoin and Amoxicillin",
                        "verbatim_quote": "Can I take Nitrofurantoin or Amoxicillin today",
                        "tier": "episodic",
                    },
                ),
            ],
        ),
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-mcp-probe-v1",
            region="asia-east2",
            json_payload={
                "patient_reply": "Nothing to worry about! Your prescription approved right now.",
                "clinical_reasoning": "Checked chart and CDS.",
                "registrar_action": "escalate_to_doctor",
            },
        ),
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-mcp-probe-v1",
            region="asia-east2",
            json_payload={
                "patient_reply": "I have checked your chart via the clinic EMR gateway and passed your medication question to the clinic doctor for review before any prescription is issued.",
                "clinical_reasoning": "MCP CDS checked Nitrofurantoin/Amoxicillin against chart; doctor review required for prescribing.",
                "registrar_action": "escalate_to_doctor",
                "offer_booking": False,
                "extracted_vitals": [],
                "issue_summary_update": "",
                "admin_task": "",
                "suggested_patient_replies": [],
            },
        ),
    ])

    trace = run_governed_patient_turn(
        db,
        tenant_id=tenant_id,
        patient_id=pid,
        patient_name=pname,
        case_key=case_key,
        pathway_node_id=pathway_id,
        active_graph_nodes=[],
        total_graph_nodes=14,
        clinical_state={"lead_line": lead_line},
        verified_quotes=[],
        conversation_turns=[],
        current_input=probe_input,
        phase_desc="PROBE (MCP multi-step tool loop + Asymmetric Authority denial + 1-retry self-repair)",
        availability_prompt_line="Live open slots: Dr. Sarah Wong today 14:30",
        is_zh=False,
        clinic_name="Central & Mid-Levels Family Medical",
        careline_name="Sam",
        fallback_reply="Your question has been staged for clinician review.",
        fallback_action="escalate_to_doctor",
        live_bus=live_bus,
        transport_override=replay,
        source_corpus=[probe_input],
        mcp_server=mcp_server,
    )
    record = build_compact_trace_record(trace, surface="governed_tool_probe", user_input=probe_input)

    with db.tenant_session(tenant_id) as session:
        c = session.get(CaseLoop, case_id)
        if c is not None:
            upd = dict(c.summary_json or {})
            append_case_agent_trace(upd, record)
            c.summary_json = upd
            flag_modified(c, "summary_json")
            c.updated_at = utcnow()
            session.flush()

    return {"ok": True, "case_id": case_id, "trace": record}
