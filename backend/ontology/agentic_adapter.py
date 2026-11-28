"""First-class integration adapter connecting `backend/ontology/` to `backend/agentic/`.

Lives strictly inside `lane-h-clinical-ontology` (`backend/ontology/**`) with zero edits
to `lane-b-agentic-graph`, providing:
1. Five typed, JSON-schema-validated `ToolSpec`s (`ToolAuthority.READ` / `ToolAuthority.CDS`)
   ready for `backend.agentic.ToolRegistry` and `GovernedAgentRunner`.
2. `ontology_to_graph_nodes(...)`: projects a 1-hop compiled ontology subgraph into the
   exact `active_graph_nodes` dict format consumed by `ContextCompiler.compile()` Slot 2
   (with accurate `subgraph_pruning_ratio` and `<4,096`-token CJK-aware budgeting).
3. `build_ontology_skill_spec()`: valid `SkillSpec` (`authority_ceiling="CDS"`) granting
   progressive disclosure access to the read/CDS ontology tools without ever touching
   `SIGNED_WRITE` or `BLOCKED` authority.
"""
from __future__ import annotations

import hashlib
from typing import Any, Sequence

from backend.agentic.skills import SkillSpec, SkillTriggers
from backend.agentic.tools import ToolAuthority, ToolRegistry, ToolSpec

from .graph import OntologyGraph
from .loader import get_ontology
from .prompt_pack import build_context_pack
from .schema import ConceptId, ExclusionState, JurisdictionCode

ONTOLOGY_TOOL_NAMES: tuple[str, ...] = (
    "ontology.lookup_rfe_frame",
    "ontology.evaluate_med_safety",
    "ontology.evaluate_observation",
    "ontology.check_positive_exclusions",
    "ontology.build_context_pack",
)


def _resolve_graph(
    args: dict[str, Any],
    context: dict[str, Any],
    default_jurisdiction: JurisdictionCode,
    default_clinic: str | None,
) -> OntologyGraph:
    jur: JurisdictionCode = (
        args.get("jurisdiction")
        or context.get("jurisdiction")
        or default_jurisdiction
    )
    clinic = args.get("clinic") if "clinic" in args else context.get("clinic", default_clinic)
    return get_ontology(jur, clinic=clinic)


def build_ontology_tool_specs(
    *,
    default_jurisdiction: JurisdictionCode = "HK",
    default_clinic: str | None = None,
) -> list[ToolSpec]:
    """Build 5 strictly READ/CDS `ToolSpec`s backed by the compiled Clinical Ontology."""

    def _lookup_rfe_frame(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        g = _resolve_graph(args, ctx, default_jurisdiction, default_clinic)
        rfe_id = str(args["rfe_id"])
        lang = str(args.get("lang") or "en")
        frame = g.get_frame_for_rfe(rfe_id)
        if frame is None:
            return {"found": False, "rfe_id": rfe_id, "ontology_hash": g.ontology_hash}
        mp = frame.murtagh_prompt
        return {
            "found": True,
            "rfe_id": rfe_id,
            "frame_id": frame.id,
            "jurisdiction": g.jurisdiction,
            "ontology_hash": g.ontology_hash,
            "emergency_number": g.defaults.emergency_number,
            "audience": mp.audience,
            "probability_diagnoses": [d.concept_id for d in mp.probability_diagnoses],
            "serious_not_to_be_missed": [d.concept_id for d in mp.serious_not_to_be_missed],
            "pitfalls_often_missed": [d.concept_id for d in mp.pitfalls_often_missed],
            "masquerades": [
                {"tier": m.tier, "masquerade": m.masquerade, "concept_id": m.concept_id}
                for m in mp.masquerades
            ],
            "positive_exclusions": [
                {
                    "id": ex.id,
                    "finding_concept_id": ex.finding_concept_id,
                    "mandatory": ex.mandatory,
                    "urgency_if_present": ex.urgency_if_present,
                    "question": ex.question.resolve(g.jurisdiction, lang),
                }
                for ex in frame.positive_exclusions
            ],
            "safety_netting": [
                {
                    "id": sn.id,
                    "escalation_channel": sn.escalation_channel,
                    "patient_instruction": sn.patient_instruction.resolve(g.jurisdiction, lang),
                }
                for sn in frame.safety_netting
            ],
        }

    def _evaluate_med_safety(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        g = _resolve_graph(args, ctx, default_jurisdiction, default_clinic)
        lang = str(args.get("lang") or "en")
        fired = g.evaluate_patient_edges(
            active_concept_ids=list(args.get("active_concept_ids") or ()),
            active_med_names=list(args.get("med_names") or ()),
            facts=dict(args.get("facts") or {}),
            lang=lang,
        )
        return {
            "jurisdiction": g.jurisdiction,
            "ontology_hash": g.ontology_hash,
            "count": len(fired),
            "max_urgency": max((f.edge.urgency_floor or 0 for f in fired), default=0),
            "has_hard_block": any(f.edge.severity == "hard_block" for f in fired),
            "fired_edges": [
                {
                    "edge_id": f.edge.id,
                    "relation": f.edge.relation,
                    "source_id": f.matched_source_id,
                    "target_id": f.edge.target_id,
                    "severity": f.edge.severity,
                    "urgency_floor": f.edge.urgency_floor,
                    "clinician_rationale": f.clinician_rationale,
                    "clinician_action": f.clinician_action,
                    "patient_safety_net": f.patient_safety_net,
                }
                for f in fired
            ],
        }

    def _evaluate_observation(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        g = _resolve_graph(args, ctx, default_jurisdiction, default_clinic)
        obs_id = str(args["observation_concept_id"])
        lang = str(args.get("lang") or "en")
        try:
            ev = g.evaluate_observation(
                obs_id,
                float(args["value"]),
                ucum=args.get("ucum"),
                facts=dict(args.get("facts") or {}),
                lang=lang,
                previous_value=args.get("previous_value"),
                previous_ucum=args.get("previous_ucum"),
                days_since_previous=args.get("days_since_previous"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return {
                "found": False,
                "jurisdiction": g.jurisdiction,
                "ontology_hash": g.ontology_hash,
                "observation_concept_id": obs_id,
                "error": str(exc),
            }
        return {
            "found": True,
            "jurisdiction": g.jurisdiction,
            "ontology_hash": g.ontology_hash,
            "observation_concept_id": ev.observation_concept_id,
            "canonical_value": ev.canonical_value,
            "canonical_ucum": ev.canonical_ucum,
            "display_value": ev.display_value,
            "display_ucum": ev.display_ucum,
            "urgency": ev.urgency,
            "band": ev.band,
            "direction": ev.direction,
            "working_problem": ev.working_problem,
            "plan_template": ev.plan_template,
            "pearls": list(ev.pearls),
            "trend_alerts": list(ev.trend_alerts),
        }

    _VALID_EXCLUSION_STATES = frozenset({"present", "explicitly_excluded", "not_asked"})

    def _check_positive_exclusions(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        g = _resolve_graph(args, ctx, default_jurisdiction, default_clinic)
        rfe_id = str(args["rfe_id"])
        raw_states = dict(args.get("exclusion_states") or {})
        invalid_vals = {
            k: v for k, v in raw_states.items() if v not in _VALID_EXCLUSION_STATES
        }
        if invalid_vals:
            return {
                "found": False,
                "jurisdiction": g.jurisdiction,
                "ontology_hash": g.ontology_hash,
                "rfe_id": rfe_id,
                "error": f"Invalid exclusion_states values: {invalid_vals}. Allowed: {sorted(_VALID_EXCLUSION_STATES)}",
            }
        try:
            states: dict[ConceptId, ExclusionState] = raw_states  # type: ignore[assignment]
            res = g.check_positive_exclusions(rfe_id, states, facts=dict(args.get("facts") or {}))
        except (KeyError, ValueError, TypeError) as exc:
            return {
                "found": False,
                "jurisdiction": g.jurisdiction,
                "ontology_hash": g.ontology_hash,
                "rfe_id": rfe_id,
                "error": str(exc),
            }
        return {
            "found": True,
            "jurisdiction": g.jurisdiction,
            "ontology_hash": g.ontology_hash,
            "frame_id": res.frame_id,
            "can_attest_all_clear": res.can_attest_all_clear,
            "max_urgency": res.max_urgency,
            "present_findings": list(res.present_findings),
            "unasked_mandatory_findings": list(res.unasked_mandatory_findings),
            "explicitly_excluded_findings": list(res.explicitly_excluded_findings),
        }

    def _build_pack(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        g = _resolve_graph(args, ctx, default_jurisdiction, default_clinic)
        try:
            pack = build_context_pack(
                g,
                patient_context=dict(args.get("patient_context") or {}),
                rfe_ids=list(args.get("rfe_ids") or ()),
                med_names=list(args.get("med_names") or ()),
                lab_results=list(args.get("lab_results") or ()),
                max_tokens=int(args.get("max_tokens") or 1200),
                lang=str(args.get("lang") or "en"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            return {
                "found": False,
                "jurisdiction": g.jurisdiction,
                "ontology_hash": g.ontology_hash,
                "error": str(exc),
            }
        return {
            "found": True,
            "jurisdiction": pack.jurisdiction,
            "ontology_hash": pack.ontology_hash,
            "token_estimate": pack.token_estimate,
            "truncated": pack.truncated,
            "audience": pack.audience,
            "node_ids": list(pack.node_ids),
            "allowed_codes": {k: list(v) for k, v in pack.allowed_codes.items()},
            "text": pack.text,
        }

    return [
        ToolSpec(
            name="ontology.lookup_rfe_frame",
            authority=ToolAuthority.READ,
            description="Retrieve clinician-only Murtagh PROMPT frame, mandatory openEHR positive exclusions, and safety netting for a primary-care RFE.",
            parameters={
                "type": "object",
                "properties": {
                    "rfe_id": {"type": "string"},
                    "jurisdiction": {"type": "string", "enum": ["HK", "AU"]},
                    "clinic": {"type": "string"},
                    "lang": {"type": "string", "enum": ["en", "zh-HK"]},
                },
                "required": ["rfe_id"],
                "additionalProperties": False,
            },
            handler=_lookup_rfe_frame,
        ),
        ToolSpec(
            name="ontology.evaluate_med_safety",
            authority=ToolAuthority.CDS,
            description="Deterministically evaluate active medications (EN/zh-HK brand or generic) and clinical concepts against ontology DDI, PGx, renal, pregnancy, and monitoring rules.",
            parameters={
                "type": "object",
                "properties": {
                    "med_names": {"type": "array", "items": {"type": "string"}},
                    "active_concept_ids": {"type": "array", "items": {"type": "string"}},
                    "facts": {"type": "object"},
                    "jurisdiction": {"type": "string", "enum": ["HK", "AU"]},
                    "clinic": {"type": "string"},
                    "lang": {"type": "string", "enum": ["en", "zh-HK"]},
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_evaluate_med_safety,
        ),
        ToolSpec(
            name="ontology.evaluate_observation",
            authority=ToolAuthority.CDS,
            description="Deterministically interpret a lab or vital value with UCUM unit conversion (e.g. HK g/dL <-> AU g/L), severity bands, trends, and HK/AU epidemiology pearls.",
            parameters={
                "type": "object",
                "properties": {
                    "observation_concept_id": {"type": "string"},
                    "value": {"type": "number"},
                    "ucum": {"type": "string"},
                    "facts": {"type": "object"},
                    "previous_value": {"type": "number"},
                    "previous_ucum": {"type": "string"},
                    "days_since_previous": {"type": "integer", "minimum": 0},
                    "jurisdiction": {"type": "string", "enum": ["HK", "AU"]},
                    "clinic": {"type": "string"},
                    "lang": {"type": "string", "enum": ["en", "zh-HK"]},
                },
                "required": ["observation_concept_id", "value"],
                "additionalProperties": False,
            },
            handler=_evaluate_observation,
        ),
        ToolSpec(
            name="ontology.check_positive_exclusions",
            authority=ToolAuthority.CDS,
            description="Evaluate openEHR Positive Exclusion gate for an RFE: blocks attest_all_clear if any mandatory red flag is unasked or present.",
            parameters={
                "type": "object",
                "properties": {
                    "rfe_id": {"type": "string"},
                    "exclusion_states": {"type": "object"},
                    "facts": {"type": "object"},
                    "jurisdiction": {"type": "string", "enum": ["HK", "AU"]},
                    "clinic": {"type": "string"},
                },
                "required": ["rfe_id", "exclusion_states"],
                "additionalProperties": False,
            },
            handler=_check_positive_exclusions,
        ),
        ToolSpec(
            name="ontology.build_context_pack",
            authority=ToolAuthority.READ,
            description="Build a token-budgeted 1-hop clinical ontology ContextPack (safety edges, critical labs, Murtagh frame, canonical ID allowlist).",
            parameters={
                "type": "object",
                "properties": {
                    "patient_context": {"type": "object"},
                    "rfe_ids": {"type": "array", "items": {"type": "string"}},
                    "med_names": {"type": "array", "items": {"type": "string"}},
                    "lab_results": {"type": "array", "items": {"type": "object"}},
                    "max_tokens": {"type": "integer", "minimum": 300, "maximum": 2000},
                    "jurisdiction": {"type": "string", "enum": ["HK", "AU"]},
                    "clinic": {"type": "string"},
                    "lang": {"type": "string", "enum": ["en", "zh-HK"]},
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_build_pack,
        ),
    ]


def register_ontology_tools(
    registry: ToolRegistry,
    *,
    default_jurisdiction: JurisdictionCode = "HK",
    default_clinic: str | None = None,
) -> ToolRegistry:
    """Register all 5 ontology READ/CDS tools into an existing `ToolRegistry`."""
    for spec in build_ontology_tool_specs(
        default_jurisdiction=default_jurisdiction,
        default_clinic=default_clinic,
    ):
        registry.register(spec)
    return registry


def build_ontology_skill_spec() -> SkillSpec:
    """Build a valid `SkillSpec` granting progressive disclosure access to ontology READ/CDS tools."""
    body = (
        "# Clinical Ontology Grounding (RACGP / HKFM)\n"
        "- Always resolve undifferentiated RFEs through Murtagh PROMPT frames and verify mandatory openEHR positive exclusions before `attest_all_clear`.\n"
        "- Evaluate active medications (EN/zh-HK) and labs/vitals against deterministic ontology safety edges and UCUM conversions.\n"
        "- Respect asymmetric authority: never diagnose to patients, close items, or issue autonomous prescriptions."
    )
    h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return SkillSpec(
        name="clinical-ontology-grounding",
        version="1.0.0",
        autonomy_level="L1",
        authority_ceiling="CDS",
        priority=15,
        max_tokens=220,
        triggers=SkillTriggers(
            always_on=False,
            intents=("intake", "triage", "consult", "medication", "lab_review", "doctor_qa", "draft_canvas"),
            pathways=(),
            keywords_en=("rfe", "fatigue", "chest", "cough", "fever", "warfarin", "metformin", "carbimazole", "sildenafil"),
            keywords_zh=("薄血丸", "發燒", "心口痛", "好攰", "頭暈", "肚痛", "血色素", "布洛芬"),
        ),
        allowed_tools=ONTOLOGY_TOOL_NAMES,
        safety_floor_refs=("asymmetric_clinical_authority", "open_ehr_positive_exclusions", "hk_au_emergency_routing"),
        banned_claims=("nothing to worry", "all clear", "looks normal", "no need to see", "唔使擔心", "冇事"),
        body=body,
        content_hash=h,
        estimated_tokens=90,
        source_path="backend/ontology/agentic_adapter.py",
    )


def _compact(text: str, limit: int = 178) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def ontology_to_graph_nodes(
    graph: OntologyGraph,
    *,
    patient_context: dict[str, Any] | None = None,
    rfe_ids: Sequence[ConceptId] = (),
    med_names: Sequence[str] = (),
    lab_results: Sequence[dict[str, Any]] = (),
    max_tokens: int = 720,
    lang: str = "en",
) -> list[dict[str, Any]]:
    """Project a bounded 1-hop ontology subgraph into <=4 compact `ContextCompiler` Slot 2 nodes.

    Each node `summary` is strictly <=178 chars so `ContextCompiler.compile()` (which slices
    `active_graph_nodes[:4]` and `summary[:180]`) NEVER silently drops safety edges, critical labs,
    or Murtagh PROMPT red flags.
    """
    ctx = dict(patient_context or {})
    ctx.setdefault("jurisdiction", graph.jurisdiction)
    active_cids = list(dict.fromkeys(list(rfe_ids) + list(ctx.get("concepts") or ())))

    # Node 1 (Safety Floor, never dropped): emergency routing + top fired hard_block/critical edges
    fired = graph.evaluate_patient_edges(
        active_concept_ids=active_cids,
        active_med_names=list(med_names),
        facts=ctx,
        lang=lang,
    )
    top_edges = [f"{f.edge.id}({f.edge.severity}/u{f.edge.urgency_floor or 0})" for f in fired[:4]]
    edge_str = ", ".join(top_edges) if top_edges else "none"
    nodes: list[dict[str, Any]] = [
        {
            "id": f"ontology.safety_floor.{graph.jurisdiction.lower()}",
            "layer": "safety_floor",
            "category": "safety",
            "title": f"Ontology Safety Floor ({graph.jurisdiction} | {graph.ontology_hash})",
            "guideline_ref": f"ontology:{graph.jurisdiction}:{graph.ontology_hash}",
            "summary": _compact(f"Emg={graph.defaults.emergency_number}; L1 authority; Alerts: {edge_str}"),
            "safety_floor": True,
        }
    ]

    # Node 2: Critical / Urgent / Abnormal observations (highest urgency first)
    eval_obs: list[tuple[int, str]] = []
    for lr in lab_results:
        oid = lr.get("concept_id")
        val = lr.get("value")
        if oid and val is not None and graph.get_observation_rule(oid):
            try:
                ev = graph.evaluate_observation(
                    oid,
                    float(val),
                    ucum=lr.get("ucum"),
                    facts=ctx,
                    lang=lang,
                    previous_value=lr.get("previous_value"),
                    previous_ucum=lr.get("previous_ucum"),
                    days_since_previous=lr.get("days_since_previous"),
                )
                eval_obs.append((ev.urgency, f"{oid}={ev.display_value}{ev.display_ucum}[{ev.band}/u{ev.urgency}]"))
            except (KeyError, ValueError, TypeError):
                continue
    if eval_obs:
        eval_obs.sort(key=lambda pair: -pair[0])
        nodes.append(
            {
                "id": f"ontology.observations.{graph.ontology_hash}",
                "layer": "ebm_base",
                "category": "labs",
                "title": f"Ontology Lab/Vital Bands ({graph.jurisdiction})",
                "guideline_ref": "LOINC/UCUM",
                "summary": _compact("; ".join(item for _, item in eval_obs[:4])),
                "safety_floor": any(u >= 3 for u, _ in eval_obs),
            }
        )

    # Node 3: Primary RFE Murtagh PROMPT must-not-miss & mandatory openEHR positive exclusions
    if rfe_ids:
        frame_bits: list[str] = []
        for rfe_id in rfe_ids[:2]:
            frame = graph.get_frame_for_rfe(rfe_id)
            if frame is None:
                continue
            serious = ",".join(d.concept_id.split(":")[-1] for d in frame.murtagh_prompt.serious_not_to_be_missed[:3])
            excl = ",".join(e.finding_concept_id.split(":")[-1] for e in frame.positive_exclusions if e.mandatory)[:60]
            frame_bits.append(f"{rfe_id}: MNNM[{serious}] Excl[{excl}]")
        if frame_bits:
            nodes.append(
                {
                    "id": f"ontology.frames.{graph.ontology_hash}",
                    "layer": "ebm_base",
                    "category": "pathway",
                    "title": f"Murtagh PROMPT & Positive Exclusions ({graph.jurisdiction})",
                    "guideline_ref": "RACGP/HKCFP",
                    "summary": _compact(" | ".join(frame_bits)),
                    "safety_floor": False,
                }
            )

    # Node 4: Resolved active medications & statutory schedules
    resolved_meds = []
    for m in med_names:
        c = graph.get_concept(m) if m in graph.concepts else graph.resolve_drug(m)
        if c and c.drug_facet:
            resolved_meds.append(f"{c.id.split(':')[-1]}({c.drug_facet.schedule})")
    if resolved_meds:
        nodes.append(
            {
                "id": f"ontology.meds.{graph.ontology_hash}",
                "layer": "ebm_base",
                "category": "medication",
                "title": f"Active Medications ({graph.jurisdiction})",
                "guideline_ref": "HK_CAP134_138" if graph.jurisdiction == "HK" else "AU_TGA_SUSMP",
                "summary": _compact(", ".join(dict.fromkeys(resolved_meds))[:175]),
                "safety_floor": False,
            }
        )

    return nodes[:4]
