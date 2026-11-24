"""Production Agentic Core for Clinic AI Harness: Skills, Prompts, Tools, Context & Memory.

Provides a standalone, strictly governed agentic runtime layer:
- `skills`: Clinical `SKILL.md` frontmatter validator & progressive-disclosure router.
- `prompts`: Versioned SHA-256 fingerprinted prompt catalog, XML injection fencing & output screens.
- `tools`: Typed tool registry, JSON-schema arg validator, asymmetric authority gate & multi-provider adapters.
- `context`: Deterministic CJK-aware 5-slot <4,096-token ContextCompiler + off-HK PDPO PHIScrubber boundary.
- `memory`: 3-tier clinical memory manager with OCR/CJK verbatim quote verification, superseding/contradiction
  tracking, HK PDPO lifecycle, EMR isolation guard, and PostgreSQL 15 FORCE RLS persistence.
- `runner`: End-to-end `GovernedAgentRunner` with pre-gate, bounded tool loop & 1-retry diagnostic self-repair.

Strict boundary invariant: NEVER imports `loopbench`.
"""
from .context import (
    HARD_TOKEN_CEILING,
    SLOT_BUDGETS,
    CompiledSlot,
    ContextCompiler,
    ContextSnapshot,
    estimate_tokens,
)
from .memory import (
    EPHEMERAL_PRIVATE_TIER,
    ClinicalMemoryManager,
    EMRIsolationError,
    InMemoryAgentMemoryStore,
    MemoryPolicyError,
    MemoryRecord,
    PostgresAgentMemoryStore,
    QuoteVerificationResult,
    assert_emr_memory_isolation,
    verify_memory_quote,
)
from .prompts import (
    CARE_LINE_PATIENT_TURN_V1,
    CLINICAL_PRESENTATION_SLOT_FILL_V1,
    DOCUMENT_COMMITMENT_AND_DIFF_V1,
    INSTRUCTION_PLAN_COMPILER_V1,
    PRESENTATION_SELF_REPAIR_V1,
    PROMPT_REGISTRY,
    SAM_PRIVATE_REGISTRAR_QA_V1,
    OutputScreenResult,
    PromptTemplate,
    detect_prompt_injection_signals,
    fence_untrusted_text,
    get_prompt_template,
    screen_agent_output,
)
from .runner import AgentRunTrace, GovernedAgentRunner, ModelTransport, ModelTurnResponse, export_agentic_manifest
from .skills import (
    SkillRegistry,
    SkillSpec,
    SkillTriggers,
    SkillValidationError,
    default_clinical_skills_dir,
    load_protocol_banned_claims,
    parse_skill_markdown,
)
from .tools import (
    ToolAuthority,
    ToolCallRequest,
    ToolExecutionResult,
    ToolRegistry,
    ToolSpec,
    ToolTurnGuard,
    build_default_clinic_tool_registry,
    validate_tool_args,
)
from .transports import (
    LiveHTTPModelTransport,
    ScriptedReplayTransport,
    acquire_vertex_adc_token,
    detect_provider_chain,
)

__all__ = [
    "HARD_TOKEN_CEILING",
    "SLOT_BUDGETS",
    "CompiledSlot",
    "ContextCompiler",
    "ContextSnapshot",
    "estimate_tokens",
    "EPHEMERAL_PRIVATE_TIER",
    "ClinicalMemoryManager",
    "EMRIsolationError",
    "InMemoryAgentMemoryStore",
    "MemoryPolicyError",
    "MemoryRecord",
    "PostgresAgentMemoryStore",
    "QuoteVerificationResult",
    "assert_emr_memory_isolation",
    "verify_memory_quote",
    "CARE_LINE_PATIENT_TURN_V1",
    "CLINICAL_PRESENTATION_SLOT_FILL_V1",
    "DOCUMENT_COMMITMENT_AND_DIFF_V1",
    "INSTRUCTION_PLAN_COMPILER_V1",
    "PRESENTATION_SELF_REPAIR_V1",
    "PROMPT_REGISTRY",
    "SAM_PRIVATE_REGISTRAR_QA_V1",
    "OutputScreenResult",
    "PromptTemplate",
    "detect_prompt_injection_signals",
    "fence_untrusted_text",
    "get_prompt_template",
    "screen_agent_output",
    "AgentRunTrace",
    "GovernedAgentRunner",
    "ModelTransport",
    "ModelTurnResponse",
    "export_agentic_manifest",
    "SkillRegistry",
    "SkillSpec",
    "SkillTriggers",
    "SkillValidationError",
    "default_clinical_skills_dir",
    "load_protocol_banned_claims",
    "parse_skill_markdown",
    "ToolAuthority",
    "ToolCallRequest",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolSpec",
    "ToolTurnGuard",
    "build_default_clinic_tool_registry",
    "validate_tool_args",
    "LiveHTTPModelTransport",
    "ScriptedReplayTransport",
    "acquire_vertex_adc_token",
    "detect_provider_chain",
]
