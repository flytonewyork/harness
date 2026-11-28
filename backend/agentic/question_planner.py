"""Zero-LLM-call Ontology-Guided Active Question Planner (Option C2).

Implements evidence-backed iterative clinical inquiring inspired by MedKGI (2026) and
Ontology-Guided Differential Diagnosis (2026) directly over our declarative primary-care
ontology (`backend/ontology/` Murtagh PROMPT frames + openEHR Positive Exclusions):

- Priority 0 (Safety-First): Any `unasked_mandatory_findings` from `check_positive_exclusions()`
  — strictly blocks `attest_all_clear` until asked and explicitly excluded with verbatim quotes.
- Priority 1 (Discriminative Information Gain): Ranks unasked Murtagh PROMPT features by
  expected information gain (Shannon entropy over active serious/masquerade/probable hypotheses
  weighted by HK/AU jurisdiction epidemiology) with zero extra LLM calls.
- Priority 2 (Stopping Criterion): Stops questioning immediately on emergency/urgency-4 trigger,
  or once mandatory exclusions are cleared and marginal information gain drops below threshold.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .memory import CaseEvidenceState


@dataclass(frozen=True, slots=True)
class PlannedQuestion:
    concept_id: str
    priority_tier: str  # "mandatory_exclusion" | "serious_discriminator" | "masquerade_check" | "routine_clarification"
    score: float
    information_gain: float
    rationale: str
    prompt_cue_en: str
    prompt_cue_zh: str = ""
    discriminates_between: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "priority_tier": self.priority_tier,
            "score": round(self.score, 4),
            "information_gain": round(self.information_gain, 4),
            "rationale": self.rationale,
            "prompt_cue_en": self.prompt_cue_en,
            "prompt_cue_zh": self.prompt_cue_zh,
            "discriminates_between": list(self.discriminates_between),
        }


@dataclass(frozen=True, slots=True)
class QuestionPlan:
    rfe_ids: tuple[str, ...]
    jurisdiction: str
    can_attest_all_clear: bool
    max_urgency: int
    should_stop_questioning: bool
    stop_reason: str
    unasked_mandatory_findings: tuple[str, ...]
    present_findings: tuple[str, ...]
    explicitly_excluded_findings: tuple[str, ...]
    questions: tuple[PlannedQuestion, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rfe_ids": list(self.rfe_ids),
            "jurisdiction": self.jurisdiction,
            "can_attest_all_clear": self.can_attest_all_clear,
            "max_urgency": self.max_urgency,
            "should_stop_questioning": self.should_stop_questioning,
            "stop_reason": self.stop_reason,
            "unasked_mandatory_findings": list(self.unasked_mandatory_findings),
            "present_findings": list(self.present_findings),
            "explicitly_excluded_findings": list(self.explicitly_excluded_findings),
            "questions": [q.to_dict() for q in self.questions],
        }

    def to_prompt_block(self, lang: str = "en") -> str:
        """Render compact deterministic guidance for the conversational LLM."""
        lines = [
            f"ONTOLOGY_QUESTION_PLAN [jur={self.jurisdiction} | can_attest_all_clear={self.can_attest_all_clear} | "
            f"stop={self.should_stop_questioning} ({self.stop_reason})]:"
        ]
        if self.should_stop_questioning:
            if self.max_urgency >= 4:
                lines.append("- IMMEDIATE ESCALATION: stop questioning and route to emergency care.")
            else:
                lines.append("- INTAKE SUFFICIENT: stop asking redundant questions; summarize verified findings for GP.")
            return "\n".join(lines)
        for idx, q in enumerate(self.questions, start=1):
            cue = q.prompt_cue_zh if (lang.startswith("zh") and q.prompt_cue_zh) else q.prompt_cue_en
            lines.append(
                f"  {idx}. [{q.priority_tier}] target={q.concept_id} | cue=\"{cue}\" | why={q.rationale}"
            )
        return "\n".join(lines)


def _binary_entropy(p: float) -> float:
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def _resolve_concept_labels(ontology_graph: Any, concept_id: str, jurisdiction: str) -> tuple[str, str]:
    """Extract clean EN and ZH labels from an OntologyGraph concept if available."""
    c = ontology_graph.get_concept(concept_id) if hasattr(ontology_graph, "get_concept") else None
    if c is None:
        pretty = concept_id.split(".")[-1].replace("_", " ")
        return pretty, pretty
    label_en = getattr(c, "label_en", None) or getattr(c, "label", None)
    label_zh = getattr(c, "label_zh", None) or ""
    if hasattr(label_en, "resolve"):
        en_str = str(label_en.resolve(jurisdiction, "en"))
        zh_str = str(label_en.resolve(jurisdiction, "zh"))
        return en_str, zh_str
    en_str = str(label_en or concept_id.split(".")[-1].replace("_", " "))
    zh_str = str(label_zh or en_str)
    return en_str, zh_str


def _extract_hypotheses_from_frame(frame: Any) -> list[dict[str, Any]]:
    """Normalize Murtagh PROMPT frame hypotheses across PR #77 schema or adapter dicts."""
    tier_weights = {
        "serious_not_to_be_missed": 3.0,
        "red_flags": 3.5,
        "masquerades": 2.0,
        "pitfalls": 1.8,
        "probable_diagnoses": 1.0,
        "probable": 1.0,
    }
    out: list[dict[str, Any]] = []
    for attr, base_w in tier_weights.items():
        items = getattr(frame, attr, None)
        if items is None and isinstance(frame, dict):
            items = frame.get(attr)
        for item in items or ():
            if isinstance(item, str):
                out.append({
                    "id": item,
                    "tier": attr,
                    "weight": base_w,
                    "features": (item,),
                    "jurisdiction_weights": {},
                })
            elif isinstance(item, dict):
                cid = str(item.get("concept_id") or item.get("id") or "")
                feats = tuple(item.get("discriminating_features") or item.get("features") or ([cid] if cid else ()))
                out.append({
                    "id": cid,
                    "tier": attr,
                    "weight": float(item.get("weight", base_w)),
                    "features": feats,
                    "jurisdiction_weights": dict(item.get("jurisdiction_weights") or {}),
                })
            else:
                cid = str(getattr(item, "concept_id", None) or getattr(item, "id", "") or "")
                feats = tuple(
                    getattr(item, "discriminating_features", None)
                    or getattr(item, "features", None)
                    or ([cid] if cid else ())
                )
                jw = dict(getattr(item, "jurisdiction_weights", None) or {})
                out.append({
                    "id": cid,
                    "tier": attr,
                    "weight": float(getattr(item, "weight", base_w)),
                    "features": feats,
                    "jurisdiction_weights": jw,
                })
    return [h for h in out if h["id"]]


def plan_next_questions(
    ontology_graph: Any,
    case_state: CaseEvidenceState,
    *,
    lang: str = "en",
    max_questions: int = 2,
    max_turns: int = 6,
    min_ig_threshold: float = 0.15,
) -> QuestionPlan:
    """Deterministically rank the highest-value next clinical questions for `case_state`."""
    jur = (case_state.jurisdiction or getattr(ontology_graph, "jurisdiction", "HK") or "HK").upper()
    present_set = set(case_state.present_findings.keys())
    excluded_set = set(case_state.explicitly_excluded_findings.keys())
    asked_set = set(case_state.asked_concepts) | present_set | excluded_set

    rfe_ids = tuple(case_state.active_rfe_ids)
    can_attest_all_clear = True
    max_urgency = 0
    unasked_mandatory: list[str] = []
    frames: list[Any] = []

    for rfe_id in rfe_ids:
        frame = ontology_graph.get_frame_for_rfe(rfe_id) if hasattr(ontology_graph, "get_frame_for_rfe") else None
        if frame is None:
            continue
        frames.append(frame)
        if hasattr(ontology_graph, "check_positive_exclusions"):
            exc = ontology_graph.check_positive_exclusions(
                rfe_id,
                present_findings=present_set,
                explicitly_excluded_findings=excluded_set,
            )
            if not exc.can_attest_all_clear:
                can_attest_all_clear = False
            max_urgency = max(max_urgency, int(getattr(exc, "max_urgency", 0) or 0))
            for cid in getattr(exc, "unasked_mandatory_findings", ()) or ():
                if cid not in unasked_mandatory:
                    unasked_mandatory.append(cid)

    # Also check deterministic patient edges for urgency-4 emergency triggers
    if hasattr(ontology_graph, "evaluate_patient_edges"):
        active_cids = list(dict.fromkeys(list(rfe_ids) + list(present_set)))
        fired = ontology_graph.evaluate_patient_edges(
            active_concept_ids=active_cids,
            active_med_names=list(case_state.active_med_ids),
            facts=case_state.to_ontology_facts(),
            lang=lang,
        )
        for fe in fired:
            edge = getattr(fe, "edge", fe)
            urg = int(getattr(edge, "urgency_floor", 0) or 0)
            max_urgency = max(max_urgency, urg)

    # Priority 2A: Emergency present -> immediately stop questioning and escalate
    if max_urgency >= 4:
        return QuestionPlan(
            rfe_ids=rfe_ids,
            jurisdiction=jur,
            can_attest_all_clear=False,
            max_urgency=max_urgency,
            should_stop_questioning=True,
            stop_reason="emergency_escalation_triggered",
            unasked_mandatory_findings=tuple(unasked_mandatory),
            present_findings=tuple(sorted(present_set)),
            explicitly_excluded_findings=tuple(sorted(excluded_set)),
            questions=(),
        )

    candidates: dict[str, PlannedQuestion] = {}

    # Priority 0: Mandatory openEHR positive exclusions (NEVER skipped while unasked)
    for idx, cid in enumerate(unasked_mandatory):
        en_lbl, zh_lbl = _resolve_concept_labels(ontology_graph, cid, jur)
        score = 100.0 + max(0, 10 - idx)
        candidates[cid] = PlannedQuestion(
            concept_id=cid,
            priority_tier="mandatory_exclusion",
            score=score,
            information_gain=1.0,
            rationale=f"Mandatory safety positive-exclusion required before attesting all-clear ({jur}).",
            prompt_cue_en=f"Ask directly whether the patient has {en_lbl}.",
            prompt_cue_zh=f"直接詢問病人有冇出現{zh_lbl}。",
            discriminates_between=("red_flag_present", "red_flag_excluded"),
        )

    # Priority 1: Murtagh PROMPT discriminating features ranked by weighted expected information gain
    hypotheses: list[dict[str, Any]] = []
    for frame in frames:
        hypotheses.extend(_extract_hypotheses_from_frame(frame))

    # Filter out hypotheses already explicitly excluded
    active_hyps = [h for h in hypotheses if h["id"] not in excluded_set]
    total_w = 0.0
    weighted_hyps: list[tuple[dict[str, Any], float]] = []
    for h in active_hyps:
        jur_mult = float(h["jurisdiction_weights"].get(jur, 1.0))
        w = max(0.1, h["weight"] * jur_mult)
        weighted_hyps.append((h, w))
        total_w += w

    if total_w > 0.0:
        feature_to_hyps: dict[str, list[tuple[dict[str, Any], float]]] = {}
        for h, w in weighted_hyps:
            for feat in h["features"]:
                if feat in asked_set or feat in candidates:
                    continue
                feature_to_hyps.setdefault(feat, []).append((h, w))

        for feat, supporting in feature_to_hyps.items():
            w_pos = sum(w for _, w in supporting)
            p_pos = min(0.99, max(0.01, w_pos / total_w))
            ig = _binary_entropy(p_pos)
            # Boost serious-not-to-be-missed and masquerade discriminators
            max_tier_w = max(h["weight"] for h, _ in supporting)
            tier_label = (
                "serious_discriminator"
                if max_tier_w >= 3.0
                else ("masquerade_check" if max_tier_w >= 1.8 else "routine_clarification")
            )
            jur_boost = max(float(h["jurisdiction_weights"].get(jur, 1.0)) for h, _ in supporting)
            score = ig * max_tier_w * jur_boost * 10.0
            en_lbl, zh_lbl = _resolve_concept_labels(ontology_graph, feat, jur)
            disc_ids = tuple(dict.fromkeys(h["id"] for h, _ in supporting))
            candidates[feat] = PlannedQuestion(
                concept_id=feat,
                priority_tier=tier_label,
                score=score,
                information_gain=ig,
                rationale=(
                    f"High-yield {tier_label.replace('_', ' ')} (EIG={ig:.2f}, jur={jur}) "
                    f"for {', '.join(disc_ids[:3])}."
                ),
                prompt_cue_en=f"Ask clearly about {en_lbl} to narrow the differential.",
                prompt_cue_zh=f"詢問病人有冇{zh_lbl}以協助醫生分辨病因。",
                discriminates_between=disc_ids[:4],
            )

    ranked = sorted(candidates.values(), key=lambda q: (-q.score, q.concept_id))
    selected = tuple(ranked[: max(1, max_questions)])

    # Priority 2B: Stopping criterion
    if can_attest_all_clear:
        if not ranked:
            return QuestionPlan(
                rfe_ids=rfe_ids,
                jurisdiction=jur,
                can_attest_all_clear=True,
                max_urgency=max_urgency,
                should_stop_questioning=True,
                stop_reason="mandatory_exclusions_cleared_no_remaining_discriminators",
                unasked_mandatory_findings=(),
                present_findings=tuple(sorted(present_set)),
                explicitly_excluded_findings=tuple(sorted(excluded_set)),
                questions=(),
            )
        if case_state.turn_count >= max_turns:
            return QuestionPlan(
                rfe_ids=rfe_ids,
                jurisdiction=jur,
                can_attest_all_clear=True,
                max_urgency=max_urgency,
                should_stop_questioning=True,
                stop_reason="turn_budget_reached_exclusions_cleared",
                unasked_mandatory_findings=(),
                present_findings=tuple(sorted(present_set)),
                explicitly_excluded_findings=tuple(sorted(excluded_set)),
                questions=(),
            )
        if ranked[0].information_gain < min_ig_threshold and ranked[0].priority_tier == "routine_clarification":
            return QuestionPlan(
                rfe_ids=rfe_ids,
                jurisdiction=jur,
                can_attest_all_clear=True,
                max_urgency=max_urgency,
                should_stop_questioning=True,
                stop_reason="information_gain_saturated",
                unasked_mandatory_findings=(),
                present_findings=tuple(sorted(present_set)),
                explicitly_excluded_findings=tuple(sorted(excluded_set)),
                questions=(),
            )

    return QuestionPlan(
        rfe_ids=rfe_ids,
        jurisdiction=jur,
        can_attest_all_clear=can_attest_all_clear,
        max_urgency=max_urgency,
        should_stop_questioning=False,
        stop_reason="mandatory_exclusions_pending" if not can_attest_all_clear else "discriminating_questions_pending",
        unasked_mandatory_findings=tuple(unasked_mandatory),
        present_findings=tuple(sorted(present_set)),
        explicitly_excluded_findings=tuple(sorted(excluded_set)),
        questions=selected,
    )
