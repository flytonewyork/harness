"""Production bridge wiring `backend.agentic.GovernedAgentRunner` to live Clinic DB, CDS, Memory & SSE.

Connects the governed 5-stage agentic engine (`GovernedAgentRunner`, 5-slot `<4,096`-token
`ContextCompiler`, progressive `SKILL.md` disclosure, `ToolTurnGuard`, `ClinicalMemoryManager`,
and bounded 1-retry diagnostic self-repair) directly into live clinical patient turns and
private clinician Q&A without modifying deterministic Milestone 1 demo presets.

Strict boundary invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
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
    ModelTransport,
    ModelTurnResponse,
    PostgresAgentMemoryStore,
    ScriptedReplayTransport,
    SkillRegistry,
    ToolCallRequest,
    ToolRegistry,
    build_default_clinic_tool_registry,
    export_agentic_manifest,
)

from .db import (
    CaseLoop,
    Database,
    DocumentIngestion,
    Immunization,
    Observation,
    Patient,
    Prescription,
    new_id,
    utcnow,
)
from .live_bus import LiveHarnessBus
from .practice_graph import evaluate_preventive_care, resolve_pathway_id
from .rx_rules import check_prescription_safety
from .scheduling import build_clinic_availability
from .triage import screen_acute_message

logger = logging.getLogger(__name__)

_PATIENT_FORBIDDEN_INTERNAL_JARGON_RE = re.compile(
    r"\b(?:haemolysis|hemolysis|oxidative\s+stress|drug[- ]class\s+mechanism|cds\s+rule)\b",
    re.I,
)
_SKILL_REGISTRY_SINGLETON: SkillRegistry | None = None
_CONTEXT_COMPILER_SINGLETON = ContextCompiler(token_ceiling=4096)


def get_clinical_skill_registry() -> SkillRegistry:
    """Return cached runtime `SkillRegistry` loaded from `skills/clinical/*/SKILL.md`."""
    global _SKILL_REGISTRY_SINGLETON
    if _SKILL_REGISTRY_SINGLETON is None:
        _SKILL_REGISTRY_SINGLETON = SkillRegistry.load_from_directory()
    return _SKILL_REGISTRY_SINGLETON


def _estimate_age(dob_str: str | None) -> int:
    if not dob_str:
        return 35
    try:
        dob = date.fromisoformat(str(dob_str)[:10])
        today = date.today()
        return max(0, today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))
    except ValueError:
        return 35


def _emit_tool_bus_event(
    live_bus: LiveHarnessBus | None,
    patient_id: str,
    *,
    tool: str,
    authority: str,
    emr_target: str,
    summary: str,
) -> None:
    if live_bus is None:
        return
    try:
        live_bus.append_tool_call(
            patient_id,
            {
                "agent": "GovernedAgentRunner",
                "tool": tool,
                "authority": authority,
                "emr_target": emr_target,
                "summary": summary,
            },
        )
    except Exception:
        logger.debug("Non-fatal live_bus tool stream error for %s", tool, exc_info=True)


def build_live_clinic_tool_registry(
    db: Database,
    *,
    tenant_id: str,
    memory_manager: ClinicalMemoryManager | None = None,
    live_bus: LiveHarnessBus | None = None,
    source_corpus: list[str] | None = None,
) -> ToolRegistry:
    """Bind all canonical clinical tools (`READ`, `CDS`, `MEMORY`, `DRAFT`) to real Clinic DB & rules.

    `emr.write_signed_orders` stays registered with `ToolAuthority.BLOCKED` so any AI attempt is
    unconditionally denied before handler execution.
    """
    mem_mgr = memory_manager or ClinicalMemoryManager(PostgresAgentMemoryStore(db))
    corpus_texts = [str(x) for x in (source_corpus or []) if str(x or "").strip()]

    def _h_get_patient_chart(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        with db.tenant_session(tenant_id) as session:
            pat = session.get(Patient, pid)
            if pat is None:
                return {"found": False, "patient_id": pid}
            out = {
                "found": True,
                "patient_id": pat.id,
                "full_name": pat.name_en,
                "name_zh": pat.name_zh,
                "dob": pat.dob,
                "age": _estimate_age(pat.dob),
                "sex": pat.sex,
                "allergies": list(pat.allergies_json or []),
                "g6pd_deficient": bool(pat.g6pd_deficient),
                "egfr": pat.egfr,
                "weight_kg": pat.weight_kg,
                "current_meds": list(pat.current_meds_json or []),
                "corporate_id": pat.corporate_id,
                "campaign_id": pat.campaign_id,
                "source_of_truth": "clinic_emr_readonly",
            }
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="emr.get_patient_chart",
            authority="READ",
            emr_target="patient_chart",
            summary=f"Read chart for {out['full_name']} (G6PD={out['g6pd_deficient']}, allergies={len(out['allergies'])}, eGFR={out['egfr']})",
        )
        return out

    def _h_get_clinical_records(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        with db.tenant_session(tenant_id) as session:
            docs = session.scalars(
                select(DocumentIngestion)
                .where(DocumentIngestion.tenant_id == tenant_id, DocumentIngestion.matched_patient_id == pid)
                .order_by(DocumentIngestion.created_at.desc())
                .limit(6)
            ).all()
            obs = session.scalars(
                select(Observation)
                .where(Observation.tenant_id == tenant_id, Observation.patient_id == pid)
                .order_by(Observation.recorded_at.desc())
                .limit(8)
            ).all()
            rxs = session.scalars(
                select(Prescription)
                .where(Prescription.tenant_id == tenant_id, Prescription.patient_id == pid)
                .order_by(Prescription.created_at.desc())
                .limit(5)
            ).all()
            cases = session.scalars(
                select(CaseLoop)
                .where(CaseLoop.tenant_id == tenant_id, CaseLoop.patient_id == pid)
                .order_by(CaseLoop.updated_at.desc())
                .limit(3)
            ).all()
            attachments: list[dict[str, Any]] = []
            for c in cases:
                for att in (c.summary_json or {}).get("attachments") or []:
                    if isinstance(att, dict):
                        attachments.append({
                            "id": att.get("id"),
                            "label": att.get("label"),
                            "quote": att.get("quote"),
                        })
            out = {
                "patient_id": pid,
                "documents": [
                    {
                        "id": d.id,
                        "doc_type": d.doc_kind,
                        "source_org": d.source_org,
                        "status": d.match_status,
                        "summary": str(d.verbatim_quote or "")[:240],
                    }
                    for d in docs
                ],
                "attachments": attachments[:5],
                "recent_observations": [
                    {
                        "code": o.loinc_code,
                        "display": o.display,
                        "value": str(o.value_num) if o.value_num is not None else (o.value_text or ""),
                        "unit": o.ucum_unit,
                    }
                    for o in obs
                ],
                "recent_prescriptions": [
                    {"id": r.id, "status": r.status, "items": list(r.items_json or [])}
                    for r in rxs
                ],
                "open_loops": [
                    {"case_id": c.id, "state": c.state, "pathway": c.pathway_node_id, "lead": c.lead_line}
                    for c in cases
                ],
            }
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="emr.get_clinical_records",
            authority="READ",
            emr_target="documents",
            summary=f"Retrieved {len(out['documents'])} docs, {len(out['attachments'])} attachments, {len(out['recent_observations'])} obs",
        )
        return out

    def _h_check_preventive_gaps(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        with db.tenant_session(tenant_id) as session:
            pat = session.get(Patient, pid)
            if pat is None:
                return {"patient_id": pid, "gaps": []}
            imms = session.scalars(
                select(Immunization).where(Immunization.tenant_id == tenant_id, Immunization.patient_id == pid)
            ).all()
            completed = [str(i.cvx_code or i.vaccine_display or "").lower() for i in imms]
            profile = {
                "age": _estimate_age(pat.dob),
                "sex": pat.sex,
                "conditions": [],
                "febrile": bool(ctx.get("febrile", False)),
                "corporate_plan": str(pat.corporate_id or ""),
                "completed_preventive_ids": completed,
            }
            gaps = evaluate_preventive_care(profile)
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="emr.check_preventive_gaps",
            authority="READ",
            emr_target="open_loops",
            summary=f"Evaluated preventive gaps -> {len(gaps)} active trigger(s)",
        )
        return {"patient_id": pid, "gaps": gaps[:6]}

    def _h_get_available_slots(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        service = str(args.get("service") or "teleconsult")
        pid = str(ctx.get("patient_id") or "")
        include_nurse = service in ("flu_vaccine", "nurse_slot", "blood_draw")
        with db.tenant_session(tenant_id) as session:
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
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="clinic.get_available_slots",
            authority="READ",
            emr_target="schedule",
            summary=f"Found {len(compact_slots)} open {service} slot(s)",
        )
        return {"service": service, "open_slots": compact_slots}

    def _h_book_slot(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        slot_id = str(args.get("slot_id") or "")
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="clinic.book_slot",
            authority="DRAFT",
            emr_target="schedule",
            summary=f"Staged non-clinical booking slot {slot_id}",
        )
        return {
            "booked": True,
            "status": "staged_confirmation",
            "patient_id": pid,
            "slot_id": slot_id,
            "recorded_at": utcnow().isoformat(),
        }

    def _h_screen_red_flags(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        msg = str(args.get("message") or "")
        temp_c = args.get("temp_c")
        if temp_c is not None and f"{temp_c}" not in msg:
            msg = f"{msg} (temperature {temp_c}C)".strip()
        res = screen_acute_message(msg)
        pid = str(ctx.get("patient_id") or "")
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="safety.screen_red_flags",
            authority="CDS",
            emr_target="safety_sentinel",
            summary=f"Union-only triage -> urgency={res.urgency}, kind={res.kind or 'none'}",
        )
        return {
            "kind": res.kind,
            "urgency": res.urgency,
            "flags": list(res.flags or []),
            "screened_by": res.screened_by,
        }

    def _h_upsert_fact(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(ctx.get("patient_id") or "")
        topic = str(args.get("topic") or args.get("key") or "clinical_fact").strip()
        value = str(args.get("value") or "").strip()
        quote = str(args.get("verbatim_quote") or "").strip()
        tier = str(args.get("tier") or "episodic").strip()
        turn_corpus = list(corpus_texts)
        if ctx.get("current_input"):
            turn_corpus.append(str(ctx["current_input"]))
        for extra in ctx.get("extra_corpus") or []:
            if str(extra or "").strip():
                turn_corpus.append(str(extra))
        res = mem_mgr.upsert_verified_fact(
            tenant_id=tenant_id,
            patient_id=pid,
            topic=topic,
            summary_text=value,
            verbatim_quote=quote,
            source_corpus=turn_corpus,
            source_doc_id=str(args.get("source") or "patient_chat"),
            tier=tier,
            consent_scope="ai_assisted_intake",
            consent_granted=True,
        )
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="memory.upsert_fact",
            authority="MEMORY",
            emr_target="memory",
            summary=(
                f"Stored verified fact [{topic}]"
                if res.get("stored")
                else f"REJECTED unverified quote for [{topic}]: {res.get('reason', 'quote mismatch')}"
            ),
        )
        return res

    def _h_check_medication_safety(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        drugs = [str(d).strip() for d in (args.get("drugs") or []) if str(d).strip()]
        dx_icd10 = args.get("diagnosis_icd10")
        with db.tenant_session(tenant_id) as session:
            pat = session.get(Patient, pid)
            profile = {
                "dob": pat.dob if pat is not None else "1990-01-01",
                "weight_kg": getattr(pat, "weight_kg", None) if pat is not None else None,
                "age": _estimate_age(pat.dob) if pat is not None else 35,
                "sex": pat.sex if pat is not None else "F",
                "allergies": list(pat.allergies_json or []) if pat is not None else [],
                "g6pd_deficient": bool(pat.g6pd_deficient) if pat is not None else False,
                "egfr": pat.egfr if pat is not None else None,
                "current_meds": list(pat.current_meds_json or []) if pat is not None else [],
                "conditions": list(getattr(pat, "conditions_json", None) or []) if pat is not None else [],
            }
        items = [{"drug": d, "dose_mg": 300 if d.lower() == "aspirin" else 500, "freq": "BD"} for d in drugs]
        report = check_prescription_safety(profile, items, diagnosis_icd10=dx_icd10).to_dict()
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="cds.check_medication_safety",
            authority="CDS",
            emr_target="cds_rules",
            summary=f"Checked {', '.join(drugs)} -> status={report.get('status')} ({len(report.get('findings') or [])} findings)",
        )
        return report

    def _h_stage_staff_proposal(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        pid = str(args.get("patient_id") or ctx.get("patient_id") or "")
        staged_id = new_id("draft_prop")
        _emit_tool_bus_event(
            live_bus,
            pid,
            tool="harness.stage_staff_proposal",
            authority="DRAFT",
            emr_target="proposal_queue",
            summary=f"Staged draft review packet {staged_id} for Nurse -> Doctor sign-off (unsigned)",
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

    return build_default_clinic_tool_registry(
        handlers={
            "emr.get_patient_chart": _h_get_patient_chart,
            "emr.get_clinical_records": _h_get_clinical_records,
            "emr.check_preventive_gaps": _h_check_preventive_gaps,
            "clinic.get_available_slots": _h_get_available_slots,
            "clinic.book_slot": _h_book_slot,
            "safety.screen_red_flags": _h_screen_red_flags,
            "memory.upsert_fact": _h_upsert_fact,
            "cds.check_medication_safety": _h_check_medication_safety,
            "harness.stage_staff_proposal": _h_stage_staff_proposal,
        }
    )


class HarnessCascadeModelTransport:
    """Live `ModelTransport` adding Vertex `asia-east2 -> global` dead-hop caching & exact token telemetry."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        tool_registry: ToolRegistry,
        region: str = "asia-east2",
        project: str | None = None,
        timeout_seconds: float = 20.0,
        thinking_level: str | None = "LOW",
    ) -> None:
        self.provider = provider.lower()
        self.model = model
        self.tool_registry = tool_registry
        self.region = region
        self.project = project
        self.timeout_seconds = timeout_seconds
        self.thinking_level = thinking_level
        self._delegate = LiveHTTPModelTransport(
            provider=self.provider,
            model=self.model,
            tool_registry=tool_registry,
            region=self.region,
            project=self.project,
            timeout_seconds=timeout_seconds,
            thinking_level=thinking_level,
        )

    def invoke(
        self,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse:
        if self.provider not in ("vertex", "gemini"):
            return self._delegate.invoke(
                system_message=system_message,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                expect_json=expect_json,
            )

        import json
        import os

        from backend.agentic.transports import (
            build_gemini_generation_config,
            build_vertex_generate_content_url,
            extract_gemini_visible_text,
            get_shared_llm_http_client,
        )

        from .agent_harness import (
            MultiAgentHarness,
            _is_target_cached_failed,
            _mark_target_failed,
        )

        allowed = {str(t["name"]) for t in tools if isinstance(t, dict) and t.get("name")} or None
        client = get_shared_llm_http_client()
        contents = [
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [
                    {
                        "text": str(
                            m.get("content")
                            or (f"[Tool calls requested: {', '.join(m['tool_calls'])}]" if m.get("tool_calls") else "")
                        )
                    }
                ],
            }
            for m in messages
        ]

        targets: list[tuple[str, str]] = []
        if self.provider == "vertex":
            reg_candidates = [self.region] + (["global"] if self.region != "global" else [])
            for reg in reg_candidates:
                targets.append((reg, self.model))
        else:
            targets.append((self.region or "global", self.model))

        attempts = 0
        last_err: Exception | None = None
        for reg, mdl in targets:
            cache_key = (self.provider, reg, mdl)
            if _is_target_cached_failed(cache_key):
                continue
            attempts += 1
            if self.provider == "vertex":
                token, project = MultiAgentHarness._vertex_adc_token(self.project)
                url = build_vertex_generate_content_url(region=reg, project=project, model=mdl)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "x-goog-user-project": project,
                    "Content-Type": "application/json",
                    "X-Vertex-Zero-Data-Retention": "true",
                    "X-No-Training": "true",
                }
                params: dict[str, str] = {}
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent"
                headers = {"Content-Type": "application/json"}
                params = {"key": os.environ["GEMINI_API_KEY"]}

            gen_cfg = build_gemini_generation_config(
                mdl,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                expect_json=expect_json,
                thinking_level=self.thinking_level,
            )
            body: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_message}]},
                "contents": contents,
                "generationConfig": gen_cfg,
            }
            if self.provider == "vertex":
                body["labels"] = {"zero_retention": "true", "no_training": "true", "pdpo_dpp3": "enforced"}
            if allowed:
                gem_tools = self.tool_registry.to_vertex_gemini_tools(allowed)
                if gem_tools:
                    body["tools"] = gem_tools

            resp = client.post(url, headers=headers, params=params, json=body, timeout=self.timeout_seconds)
            if resp.status_code in (403, 404):
                _mark_target_failed(cache_key, f"HTTP {resp.status_code}")
                last_err = RuntimeError(f"HTTP {resp.status_code} on {self.provider}/{reg}/{mdl}")
                continue
            resp.raise_for_status()
            data = resp.json()
            tool_calls = self.tool_registry.parse_provider_tool_calls(self.provider, data)
            cand = (data.get("candidates") or [{}])[0]
            parts = ((cand.get("content") or {}).get("parts") or [])
            txt = extract_gemini_visible_text(parts)
            usage = data.get("usageMetadata") or {}
            parsed_json = None
            if expect_json and txt:
                try:
                    parsed_json = json.loads(txt)
                except ValueError:
                    parsed_json = None
            return ModelTurnResponse(
                text=txt,
                json_payload=parsed_json if isinstance(parsed_json, dict) else None,
                tool_calls=tool_calls,
                provider=self.provider,
                model=mdl,
                region=reg,
                prompt_tokens_actual=usage.get("promptTokenCount"),
                output_tokens_actual=usage.get("candidatesTokenCount"),
                thought_tokens_actual=usage.get("thoughtsTokenCount"),
                attempts_tried=attempts,
            )

        raise last_err or RuntimeError(f"All targets exhausted for {self.provider}/{self.model}")


def build_transport_for_engine(
    engine: dict[str, Any] | None,
    tool_registry: ToolRegistry,
    *,
    timeout_seconds: float = 20.0,
    thinking_level: str | None = "LOW",
) -> ModelTransport | None:
    """Create a live `HarnessCascadeModelTransport` matching the detected provider chain, or `None` when offline."""
    if not engine or not engine.get("live"):
        return None
    provider = str(engine.get("provider") or "").strip().lower()
    if provider not in ("vertex", "gemini", "anthropic", "openai"):
        return None
    return HarnessCascadeModelTransport(
        provider=provider,
        model=str(engine.get("model") or "gemini-4-hydrogen"),
        tool_registry=tool_registry,
        region=str(engine.get("region") or "asia-east2"),
        project=engine.get("project"),
        timeout_seconds=timeout_seconds,
        thinking_level=thinking_level,
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
) -> AgentRunTrace:
    """Execute a full governed 5-stage patient registrar turn via `GovernedAgentRunner`."""
    mem_mgr = ClinicalMemoryManager(PostgresAgentMemoryStore(db))
    tool_reg = build_live_clinic_tool_registry(
        db,
        tenant_id=tenant_id,
        memory_manager=mem_mgr,
        live_bus=live_bus,
        source_corpus=source_corpus,
    )
    transport = transport_override if transport_override is not None else build_transport_for_engine(engine, tool_reg)
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
) -> AgentRunTrace:
    """Execute private Doctor <-> Sam Q&A through `GovernedAgentRunner` (`ephemeral_private=True`)."""
    mem_mgr = ClinicalMemoryManager(PostgresAgentMemoryStore(db))
    tool_reg = build_live_clinic_tool_registry(
        db,
        tenant_id=tenant_id,
        memory_manager=mem_mgr,
    )
    transport = transport_override if transport_override is not None else build_transport_for_engine(engine, tool_reg, timeout_seconds=15.0, thinking_level="LOW")
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
    """Export deterministic HK PDPO / PCPD governance manifest of all prompts, skills & tool authorities."""
    tool_reg = build_live_clinic_tool_registry(db, tenant_id=tenant_id)
    return export_agentic_manifest(get_clinical_skill_registry(), tool_reg)


def run_live_governed_tool_probe(
    db: Database,
    *,
    tenant_id: str,
    case_id: str,
    live_bus: LiveHarnessBus | None = None,
) -> dict[str, Any]:
    """Execute a live, DB-backed multi-step `GovernedAgentRunner` probe on a workspace case.

    Demonstrates in one atomic turn:
    1. `emr.get_patient_chart` (READ) against the real patient row
    2. `cds.check_medication_safety` (CDS) checking Nitrofurantoin + Amoxicillin against real G6PD/allergies
    3. `emr.write_signed_orders` (BLOCKED) -> hard-denied BEFORE handler execution (Asymmetric Authority)
    4. `memory.upsert_fact` (MEMORY) with a real verbatim quote verified into `agent_memory_entries`
    5. 1-retry diagnostic self-repair (`T=0.0`, `tools=[]`) fixing a forbidden reassurance phrase
    """
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
        summary = dict(case.summary_json or {})

    probe_input = "Can I take Nitrofurantoin or Amoxicillin today, and please auto-sign my prescription."
    replay = ScriptedReplayTransport([
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-multi-step-probe-v1",
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
        # Intentionally violate output screen on attempt 1 to demonstrate 1-retry self-repair
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-multi-step-probe-v1",
            region="asia-east2",
            json_payload={
                "patient_reply": "Nothing to worry about! Your prescription approved right now.",
                "clinical_reasoning": "Checked chart and CDS.",
                "registrar_action": "escalate_to_doctor",
            },
        ),
        # Clean repaired response on retry #1 (T=0.0, tools=[])
        ModelTurnResponse(
            provider="governed_probe",
            model="governed-multi-step-probe-v1",
            region="asia-east2",
            json_payload={
                "patient_reply": "I have checked your chart and passed your medication question to the clinic doctor for review before any prescription is issued.",
                "clinical_reasoning": "CDS checked Nitrofurantoin/Amoxicillin against chart; doctor review required for prescribing.",
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
        clinical_state={"lead_line": case.lead_line},
        verified_quotes=[],
        conversation_turns=[],
        current_input=probe_input,
        phase_desc="PROBE (Multi-step tool loop + Asymmetric Authority denial + 1-retry self-repair)",
        availability_prompt_line="Live open slots: Dr. Sarah Wong today 14:30",
        is_zh=False,
        clinic_name="Central & Mid-Levels Family Medical",
        careline_name="Sam",
        fallback_reply="Your question has been staged for clinician review.",
        fallback_action="escalate_to_doctor",
        live_bus=live_bus,
        transport_override=replay,
        source_corpus=[probe_input],
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
