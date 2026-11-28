"""Progressive 1-hop Subgraph ContextPack Builder for LLM Agents.

Prevents context-window bloat by never dumping the full clinical ontology into
prompts. Instead, deterministically compiles a token-budgeted (<1500 tok) 1-hop
subgraph grounded in the patient's active RFEs, medications, labs, and
jurisdiction (`HK` | `AU`), with:
- Tier 0 (Safety Floor, never truncated): emergency/crisis routing + active
  high-risk edges + jurisdiction regulatory floor.
- Tier 1 (Murtagh PROMPT Frame): clinician-only differentials, red flags,
  pitfalls, masquerades, and mandatory openEHR positive exclusions.
- Tier 2 (Medication 1-Hop Subgraph): schedules, DDIs, renal/PGx/monitoring edges.
- Tier 3 (Abnormal Lab 1-Hop Subgraph): unit-converted severity band, working
  problem, and HK/AU epidemiology pearls.
- Tier 4 (Closed Canonical ID Allowlist): constrains LLM structured output to
  real ontology concept IDs for deterministic verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .graph import OntologyGraph
from .schema import ConceptId


def estimate_tokens(text: str) -> int:
    """Same conservative token estimator used in gateway.py & extract/reader.py."""
    return max(1, len(text.encode("utf-8")) // 3)


@dataclass(frozen=True)
class ContextPack:
    text: str
    node_ids: tuple[ConceptId, ...]
    allowed_codes: dict[str, tuple[str, ...]]
    token_estimate: int
    ontology_hash: str
    jurisdiction: str
    audience: str
    truncated: bool


def build_context_pack(
    graph: OntologyGraph,
    *,
    patient_context: dict[str, Any] | None = None,
    rfe_ids: Sequence[ConceptId] = (),
    med_names: Sequence[str] = (),
    lab_results: Sequence[dict[str, Any]] = (),
    max_tokens: int = 1500,
    lang: str = "en",
) -> ContextPack:
    """Build a deterministic, token-budgeted clinician-facing ContextPack for LLM agents."""
    ctx = dict(patient_context or {})
    ctx.setdefault("jurisdiction", graph.jurisdiction)
    jur = graph.jurisdiction

    # Resolve medications to canonical IDs
    resolved_meds = []
    for m in med_names:
        c = graph.get_concept(m) if m in graph.concepts else graph.resolve_drug(m)
        if c and c not in resolved_meds:
            resolved_meds.append(c)

    # Evaluate abnormal/provided lab results
    eval_labs = []
    for lr in lab_results:
        obs_id = lr.get("concept_id")
        val = lr.get("value")
        if obs_id and val is not None and graph.get_observation_rule(obs_id):
            ev = graph.evaluate_observation(
                obs_id,
                float(val),
                ucum=lr.get("ucum"),
                facts=ctx,
                lang=lang,
                previous_value=lr.get("previous_value"),
                previous_ucum=lr.get("previous_ucum"),
                days_since_previous=lr.get("days_since_previous"),
            )
            eval_labs.append(ev)

    # Populate obs_values in ctx for predicate evaluation
    obs_map = dict(ctx.get("obs_values") or {})
    for ev in eval_labs:
        obs_map[ev.observation_concept_id] = ev.canonical_value
    ctx["obs_values"] = obs_map

    active_concepts: list[ConceptId] = list(
        dict.fromkeys(list(rfe_ids) + [c.id for c in resolved_meds] + [ev.observation_concept_id for ev in eval_labs])
    )
    fired_edges = graph.evaluate_patient_edges(
        active_concept_ids=active_concepts,
        active_med_names=[c.id for c in resolved_meds],
        facts=ctx,
        lang=lang,
    )

    included_nodes: list[ConceptId] = list(active_concepts)
    sections: list[str] = []

    # --- Tier 0: Safety Floor & Jurisdiction Routing (never truncated) ---
    d = graph.defaults
    t0_lines = [
        f"## CLINICAL ONTOLOGY CONTEXT [{jur} | hash={graph.ontology_hash} | clinician_only]",
        f"- Emergency: {d.emergency_number} ({d.emergency_dept_label.resolve(jur, lang)})",
        f"- Crisis hotlines: {', '.join(f'{k}={v}' for k, v in sorted(d.crisis_hotlines.items()))}",
        "- Authority: AI may open_item/raise_urgency/alarm/attest_all_clear only; NEVER diagnose to patient, close, or reassure.",
    ]
    if fired_edges:
        t0_lines.append("- Active Deterministic Safety Edges:")
        for fe in fired_edges[:8]:
            e = fe.edge
            urg = f"u{e.urgency_floor}" if e.urgency_floor else "advisory"
            t0_lines.append(
                f"  * [{e.severity.upper()}/{urg}] {e.id} ({fe.matched_source_id} -> {e.target_id}): {fe.clinician_rationale}"
            )
            if fe.clinician_action:
                t0_lines.append(f"    Action: {fe.clinician_action}")
            for nid in (fe.matched_source_id, e.target_id):
                if nid not in included_nodes and graph.has_concept(nid):
                    included_nodes.append(nid)
    tier0 = "\n".join(t0_lines)
    sections.append(tier0)

    truncated = False

    def _try_append(block: str) -> bool:
        nonlocal truncated
        if not block.strip():
            return True
        candidate = "\n\n".join(sections + [block])
        if estimate_tokens(candidate) <= max_tokens:
            sections.append(block)
            return True
        truncated = True
        return False

    # --- Tier 1: Murtagh PROMPT + Positive Exclusions ---
    t1_lines: list[str] = []
    for rid in rfe_ids[:3]:
        frame = graph.get_frame_for_rfe(rid)
        if frame is None:
            continue
        rfe_c = graph.get_concept(rid)
        title = rfe_c.display.resolve(jur, lang) if rfe_c else rid
        mp = frame.murtagh_prompt
        probs = ", ".join(item.concept_id for item in mp.probability_diagnoses)
        serious = ", ".join(item.concept_id for item in mp.serious_not_to_be_missed)
        pitfalls = ", ".join(item.concept_id for item in mp.pitfalls_often_missed[:5])
        masqs = ", ".join(f"{m.masquerade}({m.concept_id})" for m in mp.masquerades[:7])
        excls = ", ".join(
            f"{ex.finding_concept_id}[{'M' if ex.mandatory else 'O'}:u{ex.urgency_if_present}]"
            for ex in frame.positive_exclusions
        )
        t1_lines.append(f"### RFE Frame: {title} ({rid})")
        t1_lines.append(f"- Probability: {probs}")
        t1_lines.append(f"- Must-Not-Miss Red Flags: {serious}")
        if pitfalls:
            t1_lines.append(f"- Often-Missed Pitfalls: {pitfalls}")
        if masqs:
            t1_lines.append(f"- Masquerades: {masqs}")
        t1_lines.append(f"- Positive Exclusions (unasked mandatory blocks attest_all_clear): {excls}")

        for item in (*mp.probability_diagnoses, *mp.serious_not_to_be_missed, *mp.pitfalls_often_missed):
            if item.concept_id not in included_nodes:
                included_nodes.append(item.concept_id)
        for ex in frame.positive_exclusions:
            if ex.finding_concept_id not in included_nodes:
                included_nodes.append(ex.finding_concept_id)
    if t1_lines:
        _try_append("\n".join(t1_lines))

    # --- Tier 2: Active Medication 1-Hop Subgraph ---
    if resolved_meds:
        t2_lines = ["### Active Medications (1-Hop Ontology)"]
        for mc in resolved_meds:
            df = mc.drug_facet
            flags = []
            if df:
                flags.append(f"sched={df.schedule}")
                if df.high_risk:
                    flags.append("HIGH_RISK")
                if df.no_gap:
                    flags.append("NO_GAP")
                if df.telehealth_blocked:
                    flags.append("TELEHEALTH_BLOCKED")
                if df.g6pd_risk:
                    flags.append("G6PD_RISK")
                if df.pregnancy_caution != "none":
                    flags.append(f"PREG={df.pregnancy_caution}")
            line = f"- {mc.id} ({mc.display.resolve(jur, lang)}): {', '.join(flags)}"
            if df and df.dosing_pearl:
                line += f" | Pearl: {df.dosing_pearl.resolve(jur, lang)}"
            t2_lines.append(line)
        _try_append("\n".join(t2_lines))

    # --- Tier 3: Lab / Vital 1-Hop Subgraph ---
    if eval_labs:
        t3_lines = ["### Observations & Epidemiology Pearls"]
        for ev in eval_labs:
            t3_lines.append(
                f"- {ev.observation_concept_id}: {ev.display_value:g} {ev.display_ucum} "
                f"(band={ev.band}, u{ev.urgency}, dir={ev.direction or 'N'})"
            )
            if ev.working_problem:
                t3_lines.append(f"  Working problem: {ev.working_problem} | Plan: {ev.plan_template}")
            for pearl in ev.pearls[:3]:
                t3_lines.append(f"  Pearl: {pearl}")
            for tr in ev.trend_alerts:
                t3_lines.append(f"  Trend: {tr}")
        _try_append("\n".join(t3_lines))

    # --- Tier 4: Closed Canonical ID Allowlist ---
    unique_nodes = tuple(dict.fromkeys(included_nodes))
    snomed_codes = tuple(
        sorted({
            graph.concepts[nid].bindings.snomed_gps.code
            for nid in unique_nodes
            if nid in graph.concepts and graph.concepts[nid].bindings.snomed_gps
        })
    )
    loinc_codes = tuple(
        sorted({
            graph.concepts[nid].bindings.loinc.code
            for nid in unique_nodes
            if nid in graph.concepts and graph.concepts[nid].bindings.loinc
        })
    )
    t4 = "### Allowed Canonical IDs\n- " + ", ".join(unique_nodes)
    _try_append(t4)

    final_text = "\n\n".join(sections)
    return ContextPack(
        text=final_text,
        node_ids=unique_nodes,
        allowed_codes={
            "concept_id": unique_nodes,
            "snomed_gps": snomed_codes,
            "loinc": loinc_codes,
        },
        token_estimate=estimate_tokens(final_text),
        ontology_hash=graph.ontology_hash,
        jurisdiction=jur,
        audience="clinician_only",
        truncated=truncated,
    )
