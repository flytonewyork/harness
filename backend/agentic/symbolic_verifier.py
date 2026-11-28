"""Risk-Stratified F4 Neuro-Symbolic Verifier & Veto Gate (Option C3).

Implements the highest-authority neuro-symbolic integration pattern identified in
Gorenshtein et al. (Mount Sinai / Harvard PRISMA review, Feb 2026, PMC12935018):
- Deterministic (<10ms CPU, zero LLM calls) symbolic post-generation verification over
  `OntologyGraph` + `CaseEvidenceState` + `ContextPack.allowed_codes`.
- Enforces 5 hard clinical safety contracts:
  1. Positive Exclusion Veto: blocks premature `attest_all_clear` / reassurance when
     `unasked_mandatory_findings` remain.
  2. Urgency Floor Veto: blocks urgency downgrades below `max(kernel, fired_edges, exclusions)`
     and requires jurisdiction-correct emergency number (`999` HK / `000` AU) on u4 patient turns.
  3. Closed Concept/Code Allowlist Veto: blocks fabricated ontology concept IDs or clinical codes.
  4. Critical/High Safety Edge Coverage Veto: blocks clinician briefs that omit active critical/high
     DDI, G6PD, pregnancy, renal, or UCUM-converted critical lab edges.
  5. Entity-Linking Miss Telemetry: logs unresolved clinical tokens into WO #62 `KnowledgeGapLog`
     (PHI-scrubbed, non-blocking) to continuously close Knowledge Lag & Entity-Linking Errors.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .memory import CaseEvidenceState

_PREMATURE_REASSURANCE_RE = re.compile(
    r"\b(?:all\s+clear|nothing\s+to\s+worry\s+about|completely\s+fine|definitely\s+(?:benign|harmless|not\s+serious)|"
    r"no\s+need\s+to\s+see\s+a\s+doctor)\b|完全無事|絕對無事|放心唔使睇醫生",
    re.I,
)
_EMERGENCY_NUMBER_BY_JUR: dict[str, str] = {"HK": "999", "AU": "000"}


@dataclass(frozen=True, slots=True)
class SymbolicViolation:
    code: str
    severity: str  # "hard_veto" | "warning"
    detail: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "remediation": self.remediation,
        }

    def to_diagnostic_string(self) -> str:
        return f"[{self.code}] {self.detail} -> {self.remediation}"


@dataclass(frozen=True, slots=True)
class SymbolicVerificationResult:
    passed: bool
    vetoed: bool
    violations: tuple[SymbolicViolation, ...] = field(default_factory=tuple)
    required_urgency_floor: int = 0
    can_attest_all_clear: bool = True
    missing_critical_edge_ids: tuple[str, ...] = field(default_factory=tuple)
    invalid_codes: tuple[str, ...] = field(default_factory=tuple)
    unmapped_entities: tuple[str, ...] = field(default_factory=tuple)
    latency_ms: float = 0.0

    def to_violation_strings(self) -> list[str]:
        return [v.to_diagnostic_string() for v in self.violations if v.severity == "hard_veto"]

    def to_telemetry_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "vetoed": self.vetoed,
            "required_urgency_floor": self.required_urgency_floor,
            "can_attest_all_clear": self.can_attest_all_clear,
            "violation_codes": [v.code for v in self.violations],
            "missing_critical_edge_ids": list(self.missing_critical_edge_ids),
            "invalid_codes": list(self.invalid_codes),
            "unmapped_entities": list(self.unmapped_entities),
            "latency_ms": self.latency_ms,
        }


def _collect_candidate_codes(candidate_json: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Extract emitted `(namespace, code)` pairs from structured LLM output for closed-allowlist validation."""
    if not isinstance(candidate_json, dict):
        return []
    found: list[tuple[str, str]] = []
    for key in ("concept_ids", "ontology_concept_ids", "cited_concept_ids"):
        for cid in candidate_json.get(key) or ():
            if isinstance(cid, str) and cid.strip():
                found.append(("concept", cid.strip()))
    codes_map = candidate_json.get("codes") or candidate_json.get("ontology_codes")
    if isinstance(codes_map, dict):
        for sys_name, vals in codes_map.items():
            for code in (vals if isinstance(vals, (list, tuple)) else [vals]):
                if isinstance(code, str) and code.strip():
                    found.append((str(sys_name).strip().lower(), code.strip()))
    return found


def _flatten_candidate_haystack(candidate_text: str, candidate_json: dict[str, Any] | None) -> str:
    parts = [candidate_text or ""]
    if isinstance(candidate_json, dict):
        import json

        parts.append(json.dumps(candidate_json, ensure_ascii=False))
    return "\n".join(parts).lower()


def verify_turn_against_ontology(
    candidate_text: str,
    candidate_json: dict[str, Any] | None = None,
    *,
    ontology_graph: Any = None,
    case_state: CaseEvidenceState | None = None,
    context_pack: Any = None,
    kernel_urgency: int = 0,
    channel: str = "workspace",
    candidate_entities: Iterable[str] | None = None,
    gap_log: Any = None,
) -> SymbolicVerificationResult:
    """Run deterministic F4 symbolic verification against OntologyGraph & CaseEvidenceState.

    Zero LLM calls, <10ms CPU. Returns `vetoed=True` if any hard clinical contract is violated.
    """
    t0 = time.perf_counter()
    if ontology_graph is None or case_state is None:
        return SymbolicVerificationResult(
            passed=True,
            vetoed=False,
            required_urgency_floor=int(kernel_urgency or 0),
            latency_ms=round((time.perf_counter() - t0) * 1000, 3),
        )

    jur = (case_state.jurisdiction or getattr(ontology_graph, "jurisdiction", "HK") or "HK").upper()
    violations: list[SymbolicViolation] = []
    haystack = _flatten_candidate_haystack(candidate_text, candidate_json)
    present_set = set(case_state.present_findings.keys())
    excluded_set = set(case_state.explicitly_excluded_findings.keys())

    # ---- 1. Evaluate Positive Exclusions across active RFEs ----
    can_attest_all_clear = True
    unasked_mandatory: list[str] = []
    exclusion_max_urgency = 0
    for rfe_id in case_state.active_rfe_ids:
        frame = ontology_graph.get_frame_for_rfe(rfe_id) if hasattr(ontology_graph, "get_frame_for_rfe") else None
        if frame is None or not hasattr(ontology_graph, "check_positive_exclusions"):
            continue
        exc = ontology_graph.check_positive_exclusions(
            rfe_id,
            present_findings=present_set,
            explicitly_excluded_findings=excluded_set,
        )
        if not exc.can_attest_all_clear:
            can_attest_all_clear = False
        exclusion_max_urgency = max(exclusion_max_urgency, int(getattr(exc, "max_urgency", 0) or 0))
        for cid in getattr(exc, "unasked_mandatory_findings", ()) or ():
            if cid not in unasked_mandatory:
                unasked_mandatory.append(cid)

    # Check premature attest_all_clear or reassuring closure
    claims_all_clear = bool(isinstance(candidate_json, dict) and candidate_json.get("attest_all_clear") is True)
    if not can_attest_all_clear and (claims_all_clear or _PREMATURE_REASSURANCE_RE.search(candidate_text or "")):
        violations.append(
            SymbolicViolation(
                code="PREMATURE_ALL_CLEAR",
                severity="hard_veto",
                detail=(
                    f"Cannot attest_all_clear or reassure while mandatory exclusions remain unasked: "
                    f"{unasked_mandatory[:5]}"
                ),
                remediation=(
                    f"Ask and explicitly exclude mandatory findings {unasked_mandatory[:3]} or escalate to clinician; "
                    "set attest_all_clear=false."
                ),
            )
        )

    # ---- 2. Evaluate Deterministic Patient Safety Edges & Observations ----
    active_cids = list(dict.fromkeys(list(case_state.active_rfe_ids) + list(present_set)))
    facts = case_state.to_ontology_facts()
    fired_edges = (
        ontology_graph.evaluate_patient_edges(
            active_concept_ids=active_cids,
            active_med_names=list(case_state.active_med_ids),
            facts=facts,
            lang="en",
        )
        if hasattr(ontology_graph, "evaluate_patient_edges")
        else []
    )

    edge_max_urgency = 0
    critical_high_edges: list[Any] = []
    for fe in fired_edges:
        edge = getattr(fe, "edge", fe)
        urg = int(getattr(edge, "urgency_floor", 0) or 0)
        edge_max_urgency = max(edge_max_urgency, urg)
        sev = str(getattr(edge, "severity", "") or "").lower()
        if sev in ("critical", "high", "hard_block") or urg >= 3:
            critical_high_edges.append(fe)

    obs_max_urgency = 0
    critical_obs: list[Any] = []
    if hasattr(ontology_graph, "evaluate_observation"):
        for lr in case_state.lab_results:
            obs_id = lr.get("concept_id")
            val = lr.get("value")
            if obs_id and val is not None and ontology_graph.get_observation_rule(obs_id):
                ev = ontology_graph.evaluate_observation(
                    obs_id,
                    float(val),
                    ucum=lr.get("ucum"),
                    facts=facts,
                    lang="en",
                    previous_value=lr.get("previous_value"),
                    previous_ucum=lr.get("previous_ucum"),
                    days_since_previous=lr.get("days_since_previous"),
                )
                obs_max_urgency = max(obs_max_urgency, int(ev.urgency))
                if int(ev.urgency) >= 3:
                    critical_obs.append(ev)

    required_floor = max(int(kernel_urgency or 0), exclusion_max_urgency, edge_max_urgency, obs_max_urgency)

    # Urgency floor veto
    if isinstance(candidate_json, dict) and candidate_json.get("urgency") is not None:
        try:
            cand_urg = int(candidate_json["urgency"])
        except (TypeError, ValueError):
            cand_urg = 0
        if cand_urg < required_floor:
            violations.append(
                SymbolicViolation(
                    code="URGENCY_FLOOR_VIOLATION",
                    severity="hard_veto",
                    detail=f"Candidate urgency {cand_urg} is below deterministic ontology/safety floor {required_floor}.",
                    remediation=f"Raise urgency to at least {required_floor} (asymmetric authority never downgrades).",
                )
            )

    # Jurisdiction emergency number check on patient-facing u4 escalations (and wrong-number guard)
    expected_emg = _EMERGENCY_NUMBER_BY_JUR.get(jur, "999")
    other_emgs = [num for j, num in _EMERGENCY_NUMBER_BY_JUR.items() if j != jur]
    is_patient_channel = channel.lower() in ("patient", "careline", "whatsapp", "patient_chat", "intake")
    if is_patient_channel:
        if required_floor >= 4 and expected_emg not in (candidate_text or ""):
            violations.append(
                SymbolicViolation(
                    code="MISSING_EMERGENCY_ROUTING",
                    severity="hard_veto",
                    detail=f"Urgency-4 emergency turn in jurisdiction {jur} did not direct patient to {expected_emg}.",
                    remediation=f"Include immediate emergency routing ({expected_emg} / A&E) for {jur}.",
                )
            )
        for wrong_num in other_emgs:
            if re.search(rf"\bcall\s+{re.escape(wrong_num)}\b|\bdial\s+{re.escape(wrong_num)}\b|致電\s*{re.escape(wrong_num)}", candidate_text or "", re.I):
                violations.append(
                    SymbolicViolation(
                        code="JURISDICTION_EMERGENCY_MISMATCH",
                        severity="hard_veto",
                        detail=f"Referenced wrong-jurisdiction emergency number '{wrong_num}' for jurisdiction {jur}.",
                        remediation=f"Use {jur} emergency number '{expected_emg}'.",
                    )
                )

    # ---- 3. Closed Concept / Code Allowlist Gate ----
    invalid_codes: list[str] = []
    allowed_codes = dict(getattr(context_pack, "allowed_codes", None) or {})
    allowed_concept_set = set(allowed_codes.get("concepts", ())) | set(getattr(context_pack, "node_ids", ()) or ())
    for ns, code in _collect_candidate_codes(candidate_json):
        valid = False
        if ns == "concept":
            if allowed_concept_set:
                valid = code in allowed_concept_set or (
                    hasattr(ontology_graph, "has_concept") and ontology_graph.has_concept(code)
                )
            elif hasattr(ontology_graph, "has_concept"):
                valid = ontology_graph.has_concept(code)
        else:
            ns_allowed = set(allowed_codes.get(ns, ()))
            if ns_allowed:
                valid = code in ns_allowed
            elif hasattr(ontology_graph, "validate_code"):
                valid = ontology_graph.validate_code(ns, code)
        if not valid:
            invalid_codes.append(f"{ns}:{code}")

    if invalid_codes:
        violations.append(
            SymbolicViolation(
                code="FABRICATED_ONTOLOGY_CODE",
                severity="hard_veto",
                detail=f"Emitted clinical concept/code(s) outside verified ontology allowlist: {invalid_codes}",
                remediation="Use only verified ontology concept IDs/codes from ContextPack.allowed_codes.",
            )
        )

    # ---- 4. Critical/High Safety Edge & Critical Lab Coverage (Clinician-Facing Surfaces) ----
    missing_edge_ids: list[str] = []
    is_clinician_surface = channel.lower() in ("workspace", "clinician", "emr", "doctor_brief")
    if is_clinician_surface:
        for fe in critical_high_edges:
            edge = getattr(fe, "edge", fe)
            eid = str(getattr(edge, "id", ""))
            src = str(getattr(fe, "matched_source_id", getattr(edge, "source_id", "")))
            tgt = str(getattr(edge, "target_id", ""))
            tokens_to_match = [
                tok.lower()
                for tok in (eid, src.split(".")[-1].replace("_", " "), tgt.split(".")[-1].replace("_", " "))
                if tok and len(tok) >= 3
            ]
            # Also match rationale keywords (e.g. g6pd, warfarin, inr, nitrofurantoin, acs)
            rat = str(getattr(fe, "clinician_rationale", "") or "").lower()
            for kw in ("g6pd", "warfarin", "inr", "nitrofurantoin", "pregnancy", "renal", "egfr", "bleed", "acs", "hypoxia", "sepsis", "haemolysis", "hemolysis"):
                if kw in rat:
                    tokens_to_match.append(kw)
            if tokens_to_match and not any(tok in haystack for tok in tokens_to_match):
                missing_edge_ids.append(eid or f"{src}->{tgt}")

        for ev in critical_obs:
            oid = str(ev.observation_concept_id)
            obs_short = oid.split(".")[-1].replace("_", " ").lower()
            if obs_short not in haystack and oid.lower() not in haystack and ev.band.lower() not in haystack:
                missing_edge_ids.append(f"obs:{oid}:{ev.band}")

        if missing_edge_ids:
            violations.append(
                SymbolicViolation(
                    code="MISSED_CRITICAL_SAFETY_EDGE",
                    severity="hard_veto",
                    detail=f"Clinician brief omitted active critical/high ontology safety edge(s): {missing_edge_ids}",
                    remediation="Explicitly surface every active critical/high DDI, G6PD, renal, or critical lab alert with rationale.",
                )
            )

    # ---- 5. Entity-Linking Miss Telemetry -> WO #62 KnowledgeGapLog ----
    unmapped: list[str] = []
    if candidate_entities:
        for token in candidate_entities:
            clean = str(token or "").strip()
            if not clean:
                continue
            resolved = False
            if hasattr(ontology_graph, "resolve_drug") and ontology_graph.resolve_drug(clean) is not None:
                resolved = True
            elif hasattr(ontology_graph, "extract_concepts") and ontology_graph.extract_concepts(clean):
                resolved = True
            if not resolved and clean not in unmapped:
                unmapped.append(clean)
                if gap_log is not None:
                    try:
                        from backend.knowledge import GapEmitters

                        GapEmitters(gap_log).emit_unmapped_drug(
                            clean,
                            journey="ontology_verifier",
                            family="entity_linking",
                            record_kind=channel,
                            channel=channel,
                        )
                    except Exception:
                        pass

    hard_vetoes = [v for v in violations if v.severity == "hard_veto"]
    return SymbolicVerificationResult(
        passed=len(hard_vetoes) == 0,
        vetoed=len(hard_vetoes) > 0,
        violations=tuple(violations),
        required_urgency_floor=required_floor,
        can_attest_all_clear=can_attest_all_clear,
        missing_critical_edge_ids=tuple(missing_edge_ids),
        invalid_codes=tuple(invalid_codes),
        unmapped_entities=tuple(unmapped),
        latency_ms=round((time.perf_counter() - t0) * 1000, 3),
    )
