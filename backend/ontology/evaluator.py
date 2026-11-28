"""Pure-Python deterministic evaluators for WhenPredicate AST, ObservationRules,
and openEHR Positive Exclusion checklists (zero eval(), 100% deterministic).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .schema import (
    AllOf,
    AnyOf,
    ConceptId,
    ExclusionState,
    FactCondition,
    JurisdictionCode,
    Not,
    ObservationRule,
    PresentationFrame,
    WhenPredicate,
)


@dataclass(frozen=True)
class ObservationEvalResult:
    observation_concept_id: ConceptId
    input_value: float
    input_ucum: str
    canonical_value: float
    canonical_ucum: str
    display_value: float
    display_ucum: str
    urgency: int               # 0=normal, 1=abnormal_routine, 2=significant, 3=urgent, 4=critical
    band: str                  # "normal" | "abnormal" | "significant" | "urgent" | "critical"
    direction: str | None      # "H" | "L" | None
    reference_low: float | None
    reference_high: float | None
    working_problem: str | None
    plan_template: str | None
    pearls: tuple[str, ...]
    trend_alerts: tuple[str, ...]


@dataclass(frozen=True)
class ExclusionCheckResult:
    frame_id: str
    can_attest_all_clear: bool
    max_urgency: int
    present_findings: tuple[ConceptId, ...]
    unasked_mandatory_findings: tuple[ConceptId, ...]
    explicitly_excluded_findings: tuple[ConceptId, ...]


def evaluate_predicate(
    predicate: WhenPredicate | None,
    facts: dict[str, Any],
    *,
    is_a: Callable[[str, str], bool] | None = None,
) -> bool:
    """Evaluate a typed WhenPredicate AST against a structured patient/context dict."""
    if predicate is None:
        return True
    if isinstance(predicate, AllOf):
        return all(evaluate_predicate(p, facts, is_a=is_a) for p in predicate.all)
    if isinstance(predicate, AnyOf):
        return any(evaluate_predicate(p, facts, is_a=is_a) for p in predicate.any)
    if isinstance(predicate, Not):
        return not evaluate_predicate(predicate.not_, facts, is_a=is_a)
    if isinstance(predicate, FactCondition):
        return _eval_fact(predicate, facts, is_a=is_a)
    raise TypeError(f"Unsupported predicate node: {type(predicate)!r}")


def _eval_fact(
    cond: FactCondition,
    facts: dict[str, Any],
    *,
    is_a: Callable[[str, str], bool] | None = None,
) -> bool:
    # Extract value from facts
    if cond.fact == "obs_value":
        obs_map = facts.get("obs_values") or {}
        actual = obs_map.get(cond.concept_id) if cond.concept_id else None
    elif cond.fact == "on_med":
        actual = facts.get("on_meds") or facts.get("meds") or ()
    elif cond.fact == "has_concept":
        actual = facts.get("concepts") or ()
    elif cond.fact == "allergy_group":
        actual = facts.get("allergy_groups") or ()
    else:
        actual = facts.get(cond.fact)

    if cond.op == "exists":
        return actual is not None and actual != () and actual != [] and actual != ""
    if cond.op == "missing":
        return actual is None or actual == () or actual == [] or actual == ""

    if actual is None:
        if cond.op == "ne":
            return cond.value is not None
        if cond.op == "not_in":
            return True
        return False

    if cond.op == "is_a":
        target = str(cond.value)
        items = [actual] if isinstance(actual, str) else list(actual)
        if is_a is not None:
            return any(is_a(str(item), target) for item in items)
        return any(str(item) == target for item in items)

    if cond.op == "eq":
        return actual == cond.value
    if cond.op == "ne":
        return actual != cond.value
    if cond.op == "lt":
        return float(actual) < float(cond.value)
    if cond.op == "le":
        return float(actual) <= float(cond.value)
    if cond.op == "gt":
        return float(actual) > float(cond.value)
    if cond.op == "ge":
        return float(actual) >= float(cond.value)
    if cond.op == "in":
        if isinstance(actual, (list, tuple, set, frozenset)):
            return any(item in cond.value for item in actual)
        return actual in cond.value
    if cond.op == "not_in":
        if isinstance(actual, (list, tuple, set, frozenset)):
            return all(item not in cond.value for item in actual)
        return actual not in cond.value
    raise ValueError(f"Unknown operator: {cond.op}")


def convert_to_canonical(rule: ObservationRule, value: float, ucum: str | None) -> tuple[float, str]:
    """Convert an observation value to the rule's canonical UCUM unit."""
    if not ucum or ucum == rule.canonical_ucum:
        return value, rule.canonical_ucum
    for conv in rule.unit_conversions:
        if conv.from_ucum == ucum:
            return (value * conv.factor) + conv.offset, rule.canonical_ucum
    raise ValueError(
        f"No UnitConversion defined on {rule.id} from '{ucum}' to canonical '{rule.canonical_ucum}'"
    )


def convert_to_display(rule: ObservationRule, canonical_value: float) -> tuple[float, str]:
    """Convert a canonical observation value to the rule's display_ucum (e.g. HK g/dL vs AU g/L)."""
    target_unit = rule.display_ucum or rule.canonical_ucum
    if target_unit == rule.canonical_ucum:
        return canonical_value, rule.canonical_ucum
    for conv in rule.unit_conversions:
        if conv.from_ucum == target_unit:
            # Inverse of canonical = (input * factor) + offset
            return (canonical_value - conv.offset) / conv.factor, target_unit
    return canonical_value, rule.canonical_ucum


def evaluate_observation(
    rule: ObservationRule,
    value: float,
    *,
    ucum: str | None = None,
    facts: dict[str, Any] | None = None,
    jurisdiction: JurisdictionCode = "HK",
    lang: str = "en",
    previous_value: float | None = None,
    previous_ucum: str | None = None,
    days_since_previous: int | None = None,
    is_a: Callable[[str, str], bool] | None = None,
) -> ObservationEvalResult:
    """Deterministically interpret a lab/vital value using unit conversion,
    contextual reference intervals, nested severity bands, trends, and pearls.
    """
    ctx = dict(facts or {})
    ctx.setdefault("jurisdiction", jurisdiction)

    input_unit = ucum or rule.canonical_ucum
    canonical_val, canonical_unit = convert_to_canonical(rule, value, input_unit)
    display_val, display_unit = convert_to_display(rule, canonical_val)

    # Pick highest-priority matching reference interval
    matching_ris = [
        ri for ri in rule.reference_intervals
        if evaluate_predicate(ri.when, ctx, is_a=is_a)
    ]
    best_ri = None
    ref_low: float | None = None
    ref_high: float | None = None
    if matching_ris:
        best_ri = max(matching_ris, key=lambda r: r.priority)
        ref_low, ref_high = best_ri.low, best_ri.high

    direction: str | None = None
    if canonical_unit in ("{Ordinal}", "{presence}"):
        if canonical_val >= 1.0:
            direction = "positive"
    else:
        if ref_high is not None and canonical_val > ref_high:
            direction = "H"
        elif ref_low is not None and canonical_val < ref_low:
            direction = "L"

    # Evaluate nested severity bands (strictly highest wins), but do not let
    # global adult bands override a matched contextual physiological interval
    # (e.g. paediatric phosphate, neonatal reticulocytes/bilirubin, pregnant AFP).
    contextual_normal = (
        canonical_unit not in ("{Ordinal}", "{presence}")
        and best_ri is not None
        and best_ri.when is not None
        and direction is None
    )
    urgency = 0
    band = "normal"
    sb = rule.severity_bands
    if contextual_normal:
        pass
    elif sb.critical and sb.critical.matches(canonical_val):
        urgency, band = 4, "critical"
        if direction is None:
            direction = "L" if (sb.critical.low_threshold() is not None and canonical_val <= sb.critical.low_threshold()) else "H"
    elif sb.urgent and sb.urgent.matches(canonical_val):
        urgency, band = 3, "urgent"
        if direction is None:
            direction = "L" if (sb.urgent.low_threshold() is not None and canonical_val <= sb.urgent.low_threshold()) else "H"
    elif sb.significant and sb.significant.matches(canonical_val):
        urgency, band = 2, "significant"
        if direction is None:
            direction = "L" if (sb.significant.low_threshold() is not None and canonical_val <= sb.significant.low_threshold()) else "H"
    elif direction is not None:
        urgency, band = 1, "abnormal"

    # Evaluate trend rules if prior value provided
    trend_alerts: list[str] = []
    if previous_value is not None and days_since_previous is not None:
        prev_canonical, _ = convert_to_canonical(rule, previous_value, previous_ucum or input_unit)
        delta = canonical_val - prev_canonical
        delta_pct = (abs(delta) / abs(prev_canonical) * 100.0) if prev_canonical != 0 else 0.0
        for tr in rule.trend_rules:
            if days_since_previous > tr.window_days:
                continue
            if not evaluate_predicate(tr.when, ctx, is_a=is_a):
                continue
            dir_ok = (
                (tr.direction == "rise" and delta > 0)
                or (tr.direction == "drop" and delta < 0)
                or (tr.direction == "any_change" and delta != 0)
            )
            if not dir_ok:
                continue
            hit_abs = tr.delta_abs is not None and abs(delta) >= tr.delta_abs
            hit_pct = tr.delta_pct is not None and delta_pct >= tr.delta_pct
            if hit_abs or hit_pct:
                urgency = max(urgency, tr.urgency_floor)
                if band == "normal":
                    band = {1: "abnormal", 2: "significant", 3: "urgent", 4: "critical"}[urgency]
                trend_alerts.append(tr.clinician_rationale.resolve(jurisdiction, lang))

    # Resolve clinician-only working problem & plan
    working_problem: str | None = None
    plan_template: str | None = None
    if direction and direction in rule.direction_templates:
        tpl = rule.direction_templates[direction]  # type: ignore[index]
        working_problem = tpl.working_problem.resolve(jurisdiction, lang)
        plan_template = tpl.plan_template.resolve(jurisdiction, lang)

    # Resolve matching epidemiology pearls
    pearls = tuple(
        p.clinician_pearl.resolve(jurisdiction, lang)
        for p in rule.epidemiology_pearls
        if evaluate_predicate(p.when, ctx, is_a=is_a)
    )

    return ObservationEvalResult(
        observation_concept_id=rule.observation_concept_id,
        input_value=value,
        input_ucum=input_unit,
        canonical_value=round(canonical_val, 4),
        canonical_ucum=canonical_unit,
        display_value=round(display_val, 4),
        display_ucum=display_unit,
        urgency=urgency,
        band=band,
        direction=direction,
        reference_low=ref_low,
        reference_high=ref_high,
        working_problem=working_problem,
        plan_template=plan_template,
        pearls=pearls,
        trend_alerts=tuple(trend_alerts),
    )


def evaluate_positive_exclusions(
    frame: PresentationFrame,
    exclusion_states: dict[ConceptId, ExclusionState],
    *,
    facts: dict[str, Any] | None = None,
    is_a: Callable[[str, str], bool] | None = None,
) -> ExclusionCheckResult:
    """Enforce openEHR Positive Exclusion: any mandatory red flag in 'not_asked'
    blocks `attest_all_clear` so silent omissions never reassure a patient.
    """
    ctx = dict(facts or {})
    present: list[ConceptId] = []
    unasked_mandatory: list[ConceptId] = []
    excluded: list[ConceptId] = []
    max_urgency = 0

    for item in frame.positive_exclusions:
        if not evaluate_predicate(item.when, ctx, is_a=is_a):
            continue
        state = exclusion_states.get(item.finding_concept_id, "not_asked")
        if state == "present":
            present.append(item.finding_concept_id)
            max_urgency = max(max_urgency, item.urgency_if_present)
        elif state == "explicitly_excluded":
            excluded.append(item.finding_concept_id)
        else:
            if item.mandatory:
                unasked_mandatory.append(item.finding_concept_id)

    can_attest = len(present) == 0 and len(unasked_mandatory) == 0
    return ExclusionCheckResult(
        frame_id=frame.id,
        can_attest_all_clear=can_attest,
        max_urgency=max_urgency,
        present_findings=tuple(present),
        unasked_mandatory_findings=tuple(unasked_mandatory),
        explicitly_excluded_findings=tuple(excluded),
    )
