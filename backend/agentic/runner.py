"""Governed end-to-end Agentic Turn Runner tying Skills, Prompts, Tools, Context & Memory together.

Execution pipeline (Deterministic First, Bounded LLM Second):
1. Pre-Gate: Deterministic red-flag / prompt-injection screening runs BEFORE any LLM call.
   Emergency red flags short-circuit immediately (0 LLM tokens consumed).
2. Progressive Skill Disclosure: Select always-on safety skill + 1–3 relevant `SKILL.md` files.
3. Memory Hydration & 5-Slot `<4,096`-Token Context Compilation (with off-HK PDPO PHI scrubbing).
4. Bounded Multi-Step Tool-Call Loop: Schema-validated, authority-gated, deduplicated, circuit-broken.
5. Output Guard + Bounded 1-Retry Diagnostic Self-Repair: Runs post-generation screen + optional
   structured validator (e.g. `validate_case_presentation`); on failure performs AT MOST 1 diagnostic
   repair retry, then falls back deterministically so unsafe/slop outputs never reach the UI or patient.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .context import ContextCompiler, ContextSnapshot, estimate_tokens
from .memory import CaseEvidenceState, ClinicalMemoryManager
from .question_planner import QuestionPlan, plan_next_questions
from .symbolic_verifier import SymbolicVerificationResult, verify_turn_against_ontology
from .prompts import (
    PRESENTATION_SELF_REPAIR_V1,
    PROMPT_REGISTRY,
    PromptTemplate,
    detect_prompt_injection_signals,
    screen_agent_output,
)
from .skills import SkillRegistry, SkillSpec, load_protocol_banned_claims
from .tools import ToolCallRequest, ToolExecutionResult, ToolRegistry, ToolTurnGuard


@dataclass
class ModelTurnResponse:
    text: str = ""
    json_payload: dict[str, Any] | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    provider: str = "deterministic"
    model: str = "deterministic-fallback"
    region: str = "asia-east2"
    prompt_tokens_actual: int | None = None
    output_tokens_actual: int | None = None
    thought_tokens_actual: int | None = None
    attempts_tried: int = 1


class ModelTransport(Protocol):
    def invoke(
        self,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse: ...


@dataclass
class AgentRunTrace:
    prompt_id: str
    prompt_version: str
    prompt_fingerprint: str
    provider: str
    model: str
    region: str
    short_circuited_pre_gate: bool
    prompt_injection_signals: list[str]
    skills_loaded: list[dict[str, Any]]
    context_snapshot: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    retries: int
    output_violations: list[dict[str, Any]]
    fallback_used: bool
    fallback_reason: str | None
    final_text: str
    final_json: dict[str, Any] | None
    latency_ms: float
    prompt_tokens_actual: int | None = None
    output_tokens_actual: int | None = None
    thought_tokens_actual: int | None = None
    attempts_tried: int = 1
    question_plan: dict[str, Any] | None = None
    symbolic_verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "provider": self.provider,
            "model": self.model,
            "region": self.region,
            "short_circuited_pre_gate": self.short_circuited_pre_gate,
            "prompt_injection_signals": list(self.prompt_injection_signals),
            "skills_loaded": list(self.skills_loaded),
            "context_snapshot": dict(self.context_snapshot),
            "tool_calls": list(self.tool_calls),
            "retries": self.retries,
            "output_violations": list(self.output_violations),
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
            "final_text": self.final_text,
            "final_json": self.final_json,
            "latency_ms": self.latency_ms,
            "prompt_tokens_actual": self.prompt_tokens_actual,
            "output_tokens_actual": self.output_tokens_actual,
            "thought_tokens_actual": self.thought_tokens_actual,
            "attempts_tried": self.attempts_tried,
        }
        if self.question_plan is not None:
            out["question_plan"] = dict(self.question_plan)
        if self.symbolic_verification is not None:
            out["symbolic_verification"] = dict(self.symbolic_verification)
        return out


class GovernedAgentRunner:
    """Provider-agnostic clinical agent runner enforcing skills, authority, memory & 1-retry self-repair."""

    def __init__(
        self,
        *,
        skill_registry: SkillRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        context_compiler: ContextCompiler | None = None,
        memory_manager: ClinicalMemoryManager | None = None,
        max_tool_rounds: int = 4,
        max_tool_calls_per_turn: int = 8,
    ) -> None:
        self.skills = skill_registry or SkillRegistry.load_from_directory()
        self.tools = tool_registry or ToolRegistry()
        self.compiler = context_compiler or ContextCompiler()
        self.memory = memory_manager or ClinicalMemoryManager()
        self.max_tool_rounds = max_tool_rounds
        self.max_tool_calls_per_turn = max_tool_calls_per_turn

    def run_turn(
        self,
        *,
        template: PromptTemplate,
        transport: ModelTransport | None,
        tenant_id: str,
        patient_id: str,
        intent: str,
        current_input: str,
        pathway_node_id: str | None = None,
        active_graph_nodes: list[dict[str, Any]] | None = None,
        total_graph_nodes: int = 14,
        clinical_state: dict[str, Any] | None = None,
        verified_quotes: list[dict[str, Any]] | None = None,
        conversation_turns: list[dict[str, Any]] | None = None,
        has_document: bool = False,
        channel: str = "workspace",
        region: str = "asia-east2",
        is_sam_note: bool = False,
        expect_json: bool = False,
        template_vars: dict[str, Any] | None = None,
        patient_names_for_scrub: list[str] | None = None,
        pre_gate: Callable[[str], dict[str, Any] | None] | None = None,
        structured_validator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        deterministic_fallback: Callable[[], tuple[str, dict[str, Any] | None]] | None = None,
        tool_context: dict[str, Any] | None = None,
        case_key: str | None = None,
        case_evidence_state: CaseEvidenceState | None = None,
        clinic_knowledge: Any = None,
        ontology_graph: Any = None,
        ontology_context_pack: Any = None,
        enable_question_planner: bool = True,
        enable_symbolic_veto: bool = True,
        selective_gating: bool = True,
        kernel_urgency: int = 0,
        candidate_entities: list[str] | None = None,
        gap_log: Any = None,
    ) -> AgentRunTrace:
        """Execute a full governed clinical agent turn with bounded tool loop, C1-C3 ontology stack & 1-retry self-repair."""
        t0 = time.perf_counter()
        injection_signals = detect_prompt_injection_signals(current_input)

        # 1. Deterministic Pre-Gate (zero LLM calls on emergency red flags)
        if pre_gate is not None:
            gate_hit = pre_gate(current_input)
            if gate_hit is not None:
                return AgentRunTrace(
                    prompt_id=template.prompt_id,
                    prompt_version=template.version,
                    prompt_fingerprint=template.fingerprint(),
                    provider="deterministic_pre_gate",
                    model="safety-floor-v1",
                    region=region,
                    short_circuited_pre_gate=True,
                    prompt_injection_signals=injection_signals,
                    skills_loaded=[],
                    context_snapshot={"total_tokens_est": 0, "token_budget_ceiling": self.compiler.token_ceiling, "within_budget": True},
                    tool_calls=[],
                    retries=0,
                    output_violations=[],
                    fallback_used=False,
                    fallback_reason=None,
                    final_text=str(gate_hit.get("reply") or ""),
                    final_json=gate_hit,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                )

        # 2. Progressive Skill Disclosure
        active_skills: list[SkillSpec] = self.skills.select_skills(
            intent=intent,
            pathway_node_id=pathway_node_id,
            message_text=current_input,
            has_document=has_document,
        )
        allowed_tools = self.skills.effective_allowed_tools(active_skills)
        permitted_specs = self.tools.list_specs(allowed_tools)
        protocol_banned = load_protocol_banned_claims() if channel != "private_sam" else []
        banned_claims = list(dict.fromkeys(
            list(template.banned_output_patterns)
            + self.skills.combined_banned_claims(active_skills)
            + protocol_banned
        ))

        # 3. C1 Per-Case Layered Memory + C2 Symbolic Question Planner + Selective <4,096-Token Context
        eff_case_key = case_key or (case_evidence_state.case_key if case_evidence_state is not None else None)
        eff_case_state = case_evidence_state
        if eff_case_state is None and eff_case_key is not None:
            eff_case_state = self.memory.get_case_state(tenant_id, patient_id, eff_case_key)

        active_memories = self.memory.retrieve_active_memories(
            tenant_id=tenant_id,
            patient_id=patient_id,
            case_key=eff_case_key,
            limit=8,
        )

        q_plan: QuestionPlan | None = None
        if enable_question_planner and ontology_graph is not None and eff_case_state is not None:
            q_lang = str((template_vars or {}).get("language") or "en")
            q_plan = plan_next_questions(ontology_graph, eff_case_state, lang=q_lang)

        snapshot: ContextSnapshot = self.compiler.compile(
            template=template,
            skills=active_skills,
            active_graph_nodes=active_graph_nodes or [],
            total_graph_nodes=total_graph_nodes,
            clinical_state=clinical_state,
            memories=active_memories,
            verified_quotes=verified_quotes,
            conversation_turns=conversation_turns,
            current_input=current_input,
            tool_specs=permitted_specs,
            template_vars=template_vars,
            region=region,
            channel=channel,
            patient_names_for_scrub=patient_names_for_scrub,
            clinic_knowledge=clinic_knowledge,
            ontology_context_pack=ontology_context_pack,
            case_evidence_state=eff_case_state,
            question_plan=q_plan,
            intent=intent,
            selective_gating=selective_gating,
        )

        # Offline / no-transport safe fallback
        if transport is None:
            fb_text, fb_json = deterministic_fallback() if deterministic_fallback else ("", None)
            return AgentRunTrace(
                prompt_id=template.prompt_id,
                prompt_version=template.version,
                prompt_fingerprint=template.fingerprint(),
                provider="deterministic",
                model="deterministic-fallback",
                region=region,
                short_circuited_pre_gate=False,
                prompt_injection_signals=injection_signals,
                skills_loaded=snapshot.skills_loaded,
                context_snapshot=snapshot.to_telemetry_dict(),
                tool_calls=[],
                retries=0,
                output_violations=[],
                fallback_used=True,
                fallback_reason="no_live_transport_configured",
                final_text=fb_text,
                final_json=fb_json,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                question_plan=q_plan.to_dict() if q_plan is not None else None,
            )

        # 4. Bounded Multi-Step Tool-Calling Loop
        guard = ToolTurnGuard(max_calls_per_turn=self.max_tool_calls_per_turn)
        messages: list[dict[str, Any]] = [{"role": "user", "content": snapshot.user_message}]
        tool_traces: list[dict[str, Any]] = []
        provider_tools = [s.to_schema_dict() for s in permitted_specs]

        last_resp = ModelTurnResponse(region=region)
        for _round in range(self.max_tool_rounds):
            last_resp = transport.invoke(
                system_message=snapshot.system_message,
                messages=messages,
                tools=provider_tools,
                temperature=template.temperature,
                max_output_tokens=template.max_output_tokens,
                expect_json=expect_json,
            )
            if not last_resp.tool_calls:
                break
            round_results: list[dict[str, Any]] = []
            for tc in last_resp.tool_calls:
                exec_res: ToolExecutionResult = self.tools.execute(
                    tc,
                    allowed_tools=allowed_tools,
                    actor_role="assistant",
                    guard=guard,
                    context=tool_context,
                )
                tool_traces.append(exec_res.to_trace_dict())
                round_results.append({
                    "call_id": exec_res.call_id,
                    "tool": exec_res.tool_name,
                    "ok": exec_res.ok,
                    "result": _bound_tool_result(exec_res.result, max_tokens=600),
                })
            messages.append({"role": "assistant", "tool_calls": [tc.name for tc in last_resp.tool_calls]})
            messages.append({"role": "tool", "content": json.dumps(round_results, ensure_ascii=False)})

        # 5. Post-Generation Validation + C3 Risk-Stratified F4 Symbolic Veto + Bounded 1-Retry Repair
        final_text, final_json, violations = self._validate_response(
            last_resp,
            expect_json=expect_json,
            banned_claims=banned_claims,
            channel=channel,
            is_sam_note=is_sam_note,
            structured_validator=structured_validator,
        )
        sym_res: SymbolicVerificationResult | None = None
        if enable_symbolic_veto and ontology_graph is not None and eff_case_state is not None:
            sym_res = verify_turn_against_ontology(
                final_text,
                final_json,
                ontology_graph=ontology_graph,
                case_state=eff_case_state,
                context_pack=ontology_context_pack,
                kernel_urgency=kernel_urgency,
                channel=channel,
                candidate_entities=candidate_entities,
                gap_log=gap_log,
            )
            if sym_res.vetoed:
                violations = list(violations) + sym_res.to_violation_strings()

        retries = 0
        fallback_used = False
        fallback_reason: str | None = None

        final_resp = last_resp
        total_attempts = int(getattr(last_resp, "attempts_tried", 1) or 1)
        if violations:
            retries = 1
            repair_payload = final_json if final_json is not None else {"text": final_text}
            if expect_json:
                repair_tpl = (
                    PRESENTATION_SELF_REPAIR_V1
                    if template.task_kind == "workspace_compile"
                    else PROMPT_REGISTRY.get("generic_json_self_repair", PRESENTATION_SELF_REPAIR_V1)
                )
            else:
                repair_tpl = template
            repair_ctx = self.compiler.compile(
                template=repair_tpl,
                skills=active_skills,
                active_graph_nodes=active_graph_nodes or [],
                total_graph_nodes=total_graph_nodes,
                clinical_state=clinical_state,
                memories=active_memories,
                current_input=current_input,
                repair_diagnostics={"prior_payload": repair_payload, "violations": violations},
                template_vars=template_vars,
                region=region,
                channel=channel,
                patient_names_for_scrub=patient_names_for_scrub,
                clinic_knowledge=clinic_knowledge,
                ontology_context_pack=ontology_context_pack,
                case_evidence_state=eff_case_state,
                question_plan=q_plan,
                intent=intent,
                selective_gating=selective_gating,
            )
            repair_resp = transport.invoke(
                system_message=repair_ctx.system_message,
                messages=[{"role": "user", "content": repair_ctx.user_message}],
                tools=[],
                temperature=0.0,
                max_output_tokens=template.max_output_tokens,
                expect_json=expect_json,
            )
            final_resp = repair_resp
            total_attempts += int(getattr(repair_resp, "attempts_tried", 1) or 1)
            final_text, final_json, violations = self._validate_response(
                repair_resp,
                expect_json=expect_json,
                banned_claims=banned_claims,
                channel=channel,
                is_sam_note=is_sam_note,
                structured_validator=structured_validator,
            )
            if enable_symbolic_veto and ontology_graph is not None and eff_case_state is not None:
                sym_res = verify_turn_against_ontology(
                    final_text,
                    final_json,
                    ontology_graph=ontology_graph,
                    case_state=eff_case_state,
                    context_pack=ontology_context_pack,
                    kernel_urgency=kernel_urgency,
                    channel=channel,
                )
                if sym_res.vetoed:
                    violations = list(violations) + sym_res.to_violation_strings()
            if violations:
                fallback_used = True
                fallback_reason = "repair_retry_still_failed_validation"
                if deterministic_fallback is not None:
                    final_text, final_json = deterministic_fallback()

        return AgentRunTrace(
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            prompt_fingerprint=template.fingerprint(),
            provider=final_resp.provider,
            model=final_resp.model,
            region=final_resp.region or region,
            short_circuited_pre_gate=False,
            prompt_injection_signals=injection_signals,
            skills_loaded=snapshot.skills_loaded,
            context_snapshot=snapshot.to_telemetry_dict(),
            tool_calls=tool_traces,
            retries=retries,
            output_violations=violations,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            final_text=final_text,
            final_json=final_json,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            prompt_tokens_actual=final_resp.prompt_tokens_actual,
            output_tokens_actual=final_resp.output_tokens_actual,
            thought_tokens_actual=final_resp.thought_tokens_actual,
            attempts_tried=total_attempts,
            question_plan=q_plan.to_dict() if q_plan is not None else None,
            symbolic_verification=sym_res.to_telemetry_dict() if sym_res is not None else None,
        )

    @staticmethod
    def _validate_response(
        resp: ModelTurnResponse,
        *,
        expect_json: bool,
        banned_claims: list[str],
        channel: str,
        is_sam_note: bool,
        structured_validator: Callable[[dict[str, Any]], dict[str, Any]] | None,
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        violations: list[dict[str, Any]] = []
        parsed_json = resp.json_payload
        text = (resp.text or "").strip()

        if expect_json and parsed_json is None:
            try:
                parsed_json = json.loads(text) if text else None
            except ValueError:
                parsed_json = None
            if not isinstance(parsed_json, dict):
                violations.append({
                    "code": "INVALID_JSON_OUTPUT",
                    "detail": "Expected a valid JSON object matching output_schema.",
                    "repair_hint": "Return a single valid JSON object with no surrounding markdown or commentary.",
                })
                return text, None, violations

        screen_blob = json.dumps(parsed_json, ensure_ascii=False) if parsed_json is not None else text
        screen_res = screen_agent_output(
            screen_blob,
            banned_claims=banned_claims,
            channel=channel,
            is_sam_note=is_sam_note,
        )
        violations.extend(screen_res.violations)

        if parsed_json is not None and structured_validator is not None:
            lint = structured_validator(parsed_json)
            if not lint.get("passed", True):
                for v in lint.get("violations") or []:
                    violations.append(dict(v))

        return text, parsed_json, violations


def _bound_tool_result(result: dict[str, Any], *, max_tokens: int = 600) -> dict[str, Any]:
    """Prevent oversized tool outputs from blowing out context budgets inside a multi-step loop."""
    if estimate_tokens(result) <= max_tokens:
        return result
    raw = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    max_chars = max(120, int(max_tokens * 2.4))
    return {
        "_truncated": True,
        "_original_tokens_est": estimate_tokens(result),
        "excerpt": raw[:max_chars] + " …[truncated]",
    }


def export_agentic_manifest(
    skill_registry: SkillRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Export deterministic HK PDPO / PCPD governance manifest of all prompts, skills & tool authorities."""
    skills = skill_registry or SkillRegistry.load_from_directory()
    tools = tool_registry
    return {
        "prompts": {
            pid: {
                "version": tpl.version,
                "task_kind": tpl.task_kind,
                "fingerprint": tpl.fingerprint(),
                "temperature": tpl.temperature,
                "max_output_tokens": tpl.max_output_tokens,
            }
            for pid, tpl in sorted(PROMPT_REGISTRY.items())
        },
        "skills": [sk.to_metadata() for sk in skills.all()],
        "tools": (
            [
                {"name": s.name, "authority": s.authority.value, "idempotent": s.idempotent}
                for s in tools.list_specs(include_blocked=True)
            ]
            if tools is not None
            else []
        ),
        "protocol_banned_claims_count": len(load_protocol_banned_claims()),
    }
