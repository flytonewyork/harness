"""Deterministic CJK-aware 5-slot <4,096-token Context Compiler with progressive skill disclosure & PHI gate.

Solves three real production failure modes in bilingual Hong Kong clinical agents:
1. Naive `len(s) // 3` token estimation severely undercounts Traditional Chinese / Cantonese
   (where CJK characters consume ~1.5–1.8 tokens each), causing silent context overflows.
2. Blindly injecting all clinic protocols, full discharge PDFs, and long transcripts bloats
   latency, dilutes safety instructions, and risks prompt injection drowning out clinical rules.
3. Fallback calls routed outside GCP `asia-east2` (Hong Kong) require deterministic HK PDPO
   `PHIScrubber` redaction before any payload leaves the region.

Every compiled `ContextSnapshot` guarantees:
- Hard ceiling `total_tokens <= 4096` (working slot budget `<= 3840` + `256` framing headroom)
- Slot 1 (System Prompt & Safety Floor) is NEVER truncated
- Priority-ordered deterministic pruning across Slots 2–5 with full auditability (`dropped_items`,
  `truncated_slots`, `subgraph_pruning_ratio`, `phi_scrubbed`, `context_hash`)
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.acute.security import PHIScrubber

from .memory import EPHEMERAL_PRIVATE_TIER, assert_emr_memory_isolation
from .prompts import PromptTemplate, fence_untrusted_text
from .skills import SkillRegistry, SkillSpec
from .tools import ToolSpec

HARD_TOKEN_CEILING = 4096
FRAMING_HEADROOM_TOKENS = 256
DEFAULT_WORKING_BUDGET = HARD_TOKEN_CEILING - FRAMING_HEADROOM_TOKENS  # 3840

# Default per-slot token budgets (sum = 3840 < 4096)
SLOT_BUDGETS: dict[str, int] = {
    "slot1_system_safety_floor": 520,
    "slot2_skills_and_graph": 760,
    "slot3_clinical_state_memory": 1120,
    "slot4_quotes_conversation": 840,
    "slot5_schema_tools_repair": 600,
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
_JSON_STRUCT_RE = re.compile(r'[\{\}\[\]":,]')


def estimate_tokens(value: Any) -> int:
    """CJK-aware deterministic token estimator for English + HK Traditional Chinese + JSON."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    punct_count = len(_JSON_STRUCT_RE.findall(text))
    remaining = max(0, len(text) - cjk_count - punct_count)
    # Empirical SentencePiece/BPE ratios: CJK ~1.6 tok/char, JSON struct ~0.55 tok/char, ASCII ~1/3.8
    est = cjk_count * 1.6 + punct_count * 0.55 + remaining / 3.8
    return max(1, int(est + 0.999))


def _truncate_text_to_tokens(text: str, max_tokens: int) -> tuple[str, bool]:
    """Deterministically truncate text to fit within `max_tokens` (CJK-safe, binary search)."""
    if max_tokens <= 0:
        return "", bool(text)
    if estimate_tokens(text) <= max_tokens:
        return text, False
    suffix = " …[truncated]"
    target = max(1, max_tokens - estimate_tokens(suffix))
    lo, hi = 0, len(text)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if estimate_tokens(text[:mid]) <= target:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return text[:best].rstrip() + suffix, True


def _scrub_nested(obj: Any, extra_names: list[str] | None = None) -> tuple[Any, list[str]]:
    """Recursively run HK PDPO PHIScrubber over strings in a nested structure."""
    redacted: set[str] = set()

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            clean, cats = PHIScrubber.scrub(v, extra_names=extra_names)
            redacted.update(cats)
            return clean
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, dict):
            return {k: _walk(val) for k, val in v.items()}
        return v

    return _walk(obj), sorted(redacted)


@dataclass
class CompiledSlot:
    name: str
    priority: int
    budget_tokens: int
    tokens_est: int
    truncated: bool
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.name,
            "priority": self.priority,
            "budget_tokens": self.budget_tokens,
            "tokens_est": self.tokens_est,
            "truncated": self.truncated,
        }


@dataclass
class ContextSnapshot:
    prompt_id: str
    prompt_version: str
    prompt_fingerprint: str
    region: str
    channel: str
    system_message: str
    user_message: str
    slots: list[CompiledSlot]
    total_tokens_est: int
    token_budget_ceiling: int
    within_budget: bool
    skills_loaded: list[dict[str, Any]]
    active_node_ids: list[str]
    subgraph_pruning_ratio: str
    dropped_items: list[str] = field(default_factory=list)
    truncated_slots: list[str] = field(default_factory=list)
    phi_scrubbed: bool = False
    redacted_categories: list[str] = field(default_factory=list)
    context_hash: str = ""

    def to_telemetry_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_fingerprint": self.prompt_fingerprint,
            "region": self.region,
            "channel": self.channel,
            "total_tokens_est": self.total_tokens_est,
            "token_budget_ceiling": self.token_budget_ceiling,
            "within_budget": self.within_budget,
            "active_node_ids": list(self.active_node_ids),
            "subgraph_pruning_ratio": self.subgraph_pruning_ratio,
            "skills_loaded": list(self.skills_loaded),
            "slots": [s.to_dict() for s in self.slots],
            "dropped_items": list(self.dropped_items),
            "truncated_slots": list(self.truncated_slots),
            "phi_scrubbed": self.phi_scrubbed,
            "redacted_categories": list(self.redacted_categories),
            "context_hash": self.context_hash,
        }


class ContextCompiler:
    """Compiles a strict <4,096-token 5-slot context window for any governed clinical agent turn."""

    def __init__(
        self,
        *,
        token_ceiling: int = HARD_TOKEN_CEILING,
        slot_budgets: dict[str, int] | None = None,
    ) -> None:
        self.token_ceiling = min(HARD_TOKEN_CEILING, max(1024, token_ceiling))
        self.working_budget = self.token_ceiling - FRAMING_HEADROOM_TOKENS
        self.slot_budgets = dict(slot_budgets or SLOT_BUDGETS)

    def compile(
        self,
        *,
        template: PromptTemplate,
        skills: list[SkillSpec],
        active_graph_nodes: list[dict[str, Any]],
        total_graph_nodes: int = 14,
        clinical_state: dict[str, Any] | None = None,
        memories: list[dict[str, Any]] | None = None,
        verified_quotes: list[dict[str, Any]] | None = None,
        conversation_turns: list[dict[str, Any]] | None = None,
        current_input: str = "",
        tool_specs: list[ToolSpec] | None = None,
        repair_diagnostics: dict[str, Any] | None = None,
        template_vars: dict[str, Any] | None = None,
        region: str = "asia-east2",
        channel: str = "workspace",
        patient_names_for_scrub: list[str] | None = None,
    ) -> ContextSnapshot:
        """Assemble, prune, fence, and PDPO-scrub a deterministic 5-slot context window."""
        dropped: list[str] = []
        truncated_slots: list[str] = []
        tvars = dict(template_vars or {})

        # Enforce hard EMR/patient isolation on memories passed into non-private channels
        safe_memories: list[dict[str, Any]] = []
        for m in memories or []:
            if channel != "private_sam" and isinstance(m, dict) and m.get("tier") == EPHEMERAL_PRIVATE_TIER:
                assert_emr_memory_isolation([m])
            safe_memories.append(m)

        # ---- Slot 1: System Prompt & Immutable Safety Floor (NEVER truncated) ----
        s1_budget = self.slot_budgets["slot1_system_safety_floor"]
        system_core = template.render_system(active_skills_block="", **tvars)
        s1_tokens = estimate_tokens(system_core)
        slot1 = CompiledSlot("slot1_system_safety_floor", 0, s1_budget, s1_tokens, False, system_core)

        # ---- Slot 2: Progressive Skills (1–3) & Pruned Graph Subgraph (2–4 nodes) ----
        s2_budget = self.slot_budgets["slot2_skills_and_graph"]
        pruned_nodes = list(active_graph_nodes[:4])
        if len(active_graph_nodes) > 4:
            dropped.append(f"graph_nodes_pruned:{len(active_graph_nodes) - 4}")

        compact_nodes = [
            {
                "id": n.get("id"),
                "title": n.get("title"),
                "guideline_ref": n.get("guideline_ref"),
                "safety_floor": bool(n.get("safety_floor")),
                "summary": str(n.get("summary") or "")[:180],
            }
            for n in pruned_nodes
        ]
        graph_block = json.dumps(compact_nodes, ensure_ascii=False, separators=(",", ":"))
        skills_block = SkillRegistry.render_skills_prompt_block(skills)
        s2_combined = f"{skills_block}\n\nGRAPH_NODES:{graph_block}".strip()
        s2_trunc = False
        if estimate_tokens(s2_combined) > s2_budget:
            # Preserve always-on skill & compact graph nodes; trim lower-priority skill details first
            skills_budget = max(120, s2_budget - estimate_tokens(graph_block) - 20)
            skills_block, s2_trunc = _truncate_text_to_tokens(skills_block, skills_budget)
            s2_combined = f"{skills_block}\n\nGRAPH_NODES:{graph_block}".strip()
            if estimate_tokens(s2_combined) > s2_budget:
                s2_combined, _ = _truncate_text_to_tokens(s2_combined, s2_budget)
                s2_trunc = True
            truncated_slots.append("slot2_skills_and_graph")
        slot2 = CompiledSlot(
            "slot2_skills_and_graph", 1, s2_budget, estimate_tokens(s2_combined), s2_trunc, s2_combined
        )

        # ---- Slot 3: Deterministic Clinical State & Top-K Longitudinal Memory ----
        s3_budget = self.slot_budgets["slot3_clinical_state_memory"]
        state_obj = dict(clinical_state or {})
        mem_kept: list[dict[str, Any]] = []
        for idx, m in enumerate(safe_memories):
            candidate = {"clinical_state": state_obj, "agent_memory": mem_kept + [m]}
            if estimate_tokens(candidate) <= s3_budget:
                mem_kept.append(m)
            else:
                dropped.append(f"memory_trimmed:{len(safe_memories) - idx}")
                break

        s3_payload = {"clinical_state": state_obj, "agent_memory": mem_kept}
        s3_text = json.dumps(s3_payload, ensure_ascii=False, separators=(",", ":"))
        s3_trunc = False
        if estimate_tokens(s3_text) > s3_budget:
            s3_text, s3_trunc = _truncate_text_to_tokens(s3_text, s3_budget)
            truncated_slots.append("slot3_clinical_state_memory")
        slot3 = CompiledSlot(
            "slot3_clinical_state_memory", 2, s3_budget, estimate_tokens(s3_text), s3_trunc, s3_text
        )

        # ---- Slot 4: Verbatim Quotes & Bounded Conversation Window (Fenced Untrusted Data) ----
        s4_budget = self.slot_budgets["slot4_quotes_conversation"]
        fenced_input = fence_untrusted_text(current_input, source="current_input") if current_input else ""
        input_tokens = estimate_tokens(fenced_input)

        # Trim current input only if it alone exceeds 60% of Slot 4
        s4_trunc = False
        max_input_tok = int(s4_budget * 0.6)
        if input_tokens > max_input_tok:
            clipped_raw, _ = _truncate_text_to_tokens(current_input, max_input_tok - 20)
            fenced_input = fence_untrusted_text(clipped_raw, source="current_input")
            s4_trunc = True

        remaining_s4 = max(0, s4_budget - estimate_tokens(fenced_input) - 24)
        kept_quotes: list[dict[str, Any]] = []
        for idx, q in enumerate(verified_quotes or []):
            q_json = json.dumps(q, ensure_ascii=False, separators=(",", ":"))
            q_tok = estimate_tokens(q_json)
            if q_tok <= remaining_s4:
                kept_quotes.append(q)
                remaining_s4 -= q_tok
            else:
                dropped.append(f"quotes_trimmed:{len(verified_quotes or []) - idx}")
                break

        # Keep most recent conversation turns first (oldest-first eviction)
        kept_turns_rev: list[dict[str, Any]] = []
        raw_turns = list(conversation_turns or [])
        for turn in reversed(raw_turns):
            t_compact = {
                "actor": turn.get("actor") or turn.get("role") or "user",
                "text": str(turn.get("text") or turn.get("content") or "")[:240],
            }
            t_tok = estimate_tokens(t_compact)
            if t_tok <= remaining_s4:
                kept_turns_rev.append(t_compact)
                remaining_s4 -= t_tok
            else:
                dropped.append(f"conversation_turns_trimmed:{len(raw_turns) - len(kept_turns_rev)}")
                break
        kept_turns = list(reversed(kept_turns_rev))

        s4_parts: list[str] = []
        if kept_quotes:
            s4_parts.append(fence_untrusted_text(json.dumps(kept_quotes, ensure_ascii=False), source="verified_quotes"))
        if kept_turns:
            s4_parts.append(fence_untrusted_text(json.dumps(kept_turns, ensure_ascii=False), source="conversation_history"))
        if fenced_input:
            s4_parts.append(fenced_input)
        s4_text = "\n\n".join(s4_parts)
        if estimate_tokens(s4_text) > s4_budget:
            s4_text, _ = _truncate_text_to_tokens(s4_text, s4_budget)
            s4_trunc = True
        if s4_trunc:
            truncated_slots.append("slot4_quotes_conversation")
        slot4 = CompiledSlot(
            "slot4_quotes_conversation", 3, s4_budget, estimate_tokens(s4_text), s4_trunc, s4_text
        )

        # ---- Slot 5: Task Schema, Permitted Tools & Optional 1-Retry Repair Diagnostics ----
        s5_budget = self.slot_budgets["slot5_schema_tools_repair"]
        s5_obj: dict[str, Any] = {}
        if tool_specs:
            s5_obj["permitted_tools"] = [
                {"name": t.name, "authority": t.authority.value, "required": (t.parameters or {}).get("required", [])}
                for t in tool_specs
            ]
        if repair_diagnostics:
            s5_obj["repair_diagnostics"] = repair_diagnostics
        elif template.output_schema:
            s5_obj["output_schema_required"] = template.output_schema.get("required", [])

        s5_text = json.dumps(s5_obj, ensure_ascii=False, separators=(",", ":")) if s5_obj else ""
        s5_trunc = False
        if estimate_tokens(s5_text) > s5_budget:
            s5_text, s5_trunc = _truncate_text_to_tokens(s5_text, s5_budget)
            truncated_slots.append("slot5_schema_tools_repair")
        slot5 = CompiledSlot(
            "slot5_schema_tools_repair", 4, s5_budget, estimate_tokens(s5_text), s5_trunc, s5_text
        )

        # ---- HK PDPO Off-Region PHI Boundary Gate ----
        phi_scrubbed = False
        redacted_cats: list[str] = []
        is_hk_resident = (region or "").strip().lower() == "asia-east2"
        if not is_hk_resident:
            s3_clean, c3 = PHIScrubber.scrub(slot3.content, extra_names=patient_names_for_scrub)
            s4_clean, c4 = PHIScrubber.scrub(slot4.content, extra_names=patient_names_for_scrub)
            slot3.content = s3_clean
            slot3.tokens_est = estimate_tokens(s3_clean)
            slot4.content = s4_clean
            slot4.tokens_est = estimate_tokens(s4_clean)
            phi_scrubbed = True
            redacted_cats = sorted(set(c3) | set(c4))

        # ---- Final Message Assembly & Hard <4,096 Ceiling Enforcement ----
        system_msg = template.render_system(active_skills_block=skills_block, **tvars)
        if repair_diagnostics:
            user_msg = template.render_user(
                prior_payload_json=json.dumps(repair_diagnostics.get("prior_payload", {}), ensure_ascii=False),
                violations_json=json.dumps(repair_diagnostics.get("violations", []), ensure_ascii=False),
                **tvars,
            )
        else:
            user_msg = template.render_user(
                graph_nodes_block=graph_block,
                clinical_state_block=slot3.content,
                untrusted_context_block=slot4.content,
                **tvars,
            )

        # Mathematical hard guarantee: total_tokens <= self.token_ceiling (4096)
        total_tokens = estimate_tokens(system_msg) + estimate_tokens(user_msg)
        if total_tokens > self.token_ceiling:
            # Trim user_msg to preserve system_msg (Slot 1 Safety Floor) intact
            allowed_user = max(128, self.token_ceiling - estimate_tokens(system_msg) - 8)
            user_msg, _ = _truncate_text_to_tokens(user_msg, allowed_user)
            if "slot4_quotes_conversation" not in truncated_slots:
                truncated_slots.append("slot4_quotes_conversation")
            total_tokens = estimate_tokens(system_msg) + estimate_tokens(user_msg)

        slots = [slot1, slot2, slot3, slot4, slot5]
        ctx_hash = hashlib.sha256(f"{system_msg}\n---\n{user_msg}".encode("utf-8")).hexdigest()[:16]
        active_ids = [str(n.get("id")) for n in pruned_nodes if n.get("id")]
        denom = max(total_graph_nodes, len(active_ids), 1)

        return ContextSnapshot(
            prompt_id=template.prompt_id,
            prompt_version=template.version,
            prompt_fingerprint=template.fingerprint(),
            region=region,
            channel=channel,
            system_message=system_msg,
            user_message=user_msg,
            slots=slots,
            total_tokens_est=total_tokens,
            token_budget_ceiling=self.token_ceiling,
            within_budget=total_tokens <= self.token_ceiling,
            skills_loaded=[sk.to_metadata() for sk in skills],
            active_node_ids=active_ids,
            subgraph_pruning_ratio=f"{len(active_ids)}/{denom} nodes injected",
            dropped_items=dropped,
            truncated_slots=sorted(set(truncated_slots)),
            phi_scrubbed=phi_scrubbed,
            redacted_categories=redacted_cats,
            context_hash=ctx_hash,
        )
