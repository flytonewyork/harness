"""Immutable, O(1)-indexed in-memory Clinical Ontology Graph (`OntologyGraph`).

Zero external database dependencies. Precomputes transitive IS_A closures,
bilingual EN/zh-HK drug & symptom alias maps, FHIR R4 serialization, and
deterministic SHA-256 `ontology_hash` for replay cache pinning.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from .evaluator import (
    ExclusionCheckResult,
    ObservationEvalResult,
    evaluate_observation,
    evaluate_positive_exclusions,
    evaluate_predicate,
)
from .overlay import CompiledBundle
from .schema import (
    ClinicalEdge,
    Concept,
    ConceptId,
    EdgeRelation,
    ExclusionState,
    JurisdictionCode,
    JurisdictionDefaults,
    ObservationRule,
    PathwayPointer,
    PresentationFrame,
)

FHIR_SYSTEMS = {
    "snomed_gps": "http://snomed.info/sct",
    "loinc": "http://loinc.org",
    "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "cvx": "http://hl7.org/fhir/sid/cvx",
    "icd10": "http://hl7.org/fhir/sid/icd-10",
    "icpc2": "http://hl7.org/fhir/sid/icpc-2",
    "atc": "http://www.whocc.no/atc",
}


def _norm_term(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).lower()
    s = re.sub(r"[\s\-_/]+", " ", s).strip()
    return s


@dataclass(frozen=True)
class FiredEdge:
    edge: ClinicalEdge
    matched_source_id: ConceptId
    matched_target_id: ConceptId | None
    clinician_rationale: str
    clinician_action: str | None
    patient_safety_net: str | None


class OntologyGraph:
    """Immutable compiled ontology graph with O(1) indexed lookups and IS_A inheritance."""

    def __init__(self, bundle: CompiledBundle) -> None:
        self._bundle = bundle
        self.jurisdiction: JurisdictionCode = bundle.jurisdiction  # type: ignore[assignment]
        self.overlay_chain: tuple[str, ...] = bundle.overlay_chain
        self.defaults: JurisdictionDefaults = bundle.defaults
        self.concepts: dict[str, Concept] = dict(bundle.concepts)
        self.edges: dict[str, ClinicalEdge] = dict(bundle.edges)
        self.frames: dict[str, PresentationFrame] = dict(bundle.frames)
        self.observation_rules: dict[str, ObservationRule] = dict(bundle.observation_rules)
        self.pointers: dict[str, PathwayPointer] = dict(bundle.pointers)
        self.field_provenance: dict[str, str] = dict(bundle.field_provenance)

        # Precompute transitive IS_A closure (ancestors & descendants include self)
        self._ancestors: dict[str, frozenset[str]] = {}
        self._descendants: dict[str, set[str]] = {cid: {cid} for cid in self.concepts}
        for cid in self.concepts:
            anc = self._compute_ancestors(cid)
            self._ancestors[cid] = anc
            for a in anc:
                if a in self._descendants:
                    self._descendants[a].add(cid)
        self._descendants_frozen: dict[str, frozenset[str]] = {
            k: frozenset(v) for k, v in self._descendants.items()
        }

        # O(1) indexes
        self._by_code: dict[tuple[str, str], str] = {}
        self._drug_alias: dict[str, str] = {}
        self._concept_alias: dict[str, list[str]] = {}
        self._purpose_to_concepts: dict[str, list[str]] = {}
        self._obs_rule_by_concept: dict[str, ObservationRule] = {}
        self._frame_by_rfe: dict[str, PresentationFrame] = {}
        self._out_edges: dict[str, list[ClinicalEdge]] = {}
        self._in_edges: dict[str, list[ClinicalEdge]] = {}

        self._build_indexes()
        self.ontology_hash: str = self._compute_hash()

    def _compute_ancestors(self, cid: str) -> frozenset[str]:
        visited: set[str] = {cid}
        stack = [cid]
        while stack:
            cur = stack.pop()
            concept = self.concepts.get(cur)
            if concept is None:
                continue
            for p in concept.parent_ids:
                if p not in visited:
                    visited.add(p)
                    stack.append(p)
        return frozenset(visited)

    def _build_indexes(self) -> None:
        for cid, c in self.concepts.items():
            b = c.bindings
            if b.snomed_gps:
                self._by_code[("snomed_gps", b.snomed_gps.code)] = cid
                self._by_code[("snomed", b.snomed_gps.code)] = cid
            if b.loinc:
                self._by_code[("loinc", b.loinc.code)] = cid
            if b.rxnorm:
                self._by_code[("rxnorm", b.rxnorm.code)] = cid
            if b.unii:
                self._by_code[("unii", b.unii)] = cid
            if b.cvx:
                self._by_code[("cvx", b.cvx.code)] = cid
            if b.inn:
                self._by_code[("inn", _norm_term(b.inn))] = cid
            if b.icd10:
                self._by_code[("icd10", b.icd10.code)] = cid
            if b.icpc2:
                self._by_code[("icpc2", b.icpc2.code)] = cid
            if b.atc:
                self._by_code[("atc", b.atc.code)] = cid

            # Aliases & display terms
            surface_terms: list[str] = [c.display.en]
            if c.display.zh_hk:
                surface_terms.append(c.display.zh_hk)
            if c.display.en_au:
                surface_terms.append(c.display.en_au)
            if c.display.en_hk:
                surface_terms.append(c.display.en_hk)
            if b.inn:
                surface_terms.append(b.inn)
            surface_terms.extend(c.aliases.all_terms())

            for term in surface_terms:
                nt = _norm_term(term)
                if not nt:
                    continue
                self._concept_alias.setdefault(nt, [])
                if cid not in self._concept_alias[nt]:
                    self._concept_alias[nt].append(cid)
                if c.kind == "drug":
                    self._drug_alias[nt] = cid

            for pterm in c.purpose_phrases.all_terms():
                np = _norm_term(pterm)
                if np:
                    self._purpose_to_concepts.setdefault(np, [])
                    if cid not in self._purpose_to_concepts[np]:
                        self._purpose_to_concepts[np].append(cid)

        for rule in self.observation_rules.values():
            self._obs_rule_by_concept[rule.observation_concept_id] = rule

        for frame in self.frames.values():
            self._frame_by_rfe[frame.rfe_concept_id] = frame

        for edge in self.edges.values():
            self._out_edges.setdefault(edge.source_id, []).append(edge)
            self._in_edges.setdefault(edge.target_id, []).append(edge)
            if edge.bidirectional:
                self._out_edges.setdefault(edge.target_id, []).append(edge)
                self._in_edges.setdefault(edge.source_id, []).append(edge)

        # Longest-first sorted alias list for deterministic text extraction
        self._sorted_drug_aliases = sorted(self._drug_alias.items(), key=lambda kv: len(kv[0]), reverse=True)
        self._sorted_all_aliases = sorted(self._concept_alias.items(), key=lambda kv: len(kv[0]), reverse=True)

    def _compute_hash(self) -> str:
        payload = {
            "jurisdiction": self.jurisdiction,
            "overlay_chain": list(self.overlay_chain),
            "defaults": self.defaults.model_dump(mode="json"),
            "concepts": {k: self.concepts[k].model_dump(mode="json") for k in sorted(self.concepts)},
            "edges": {k: self.edges[k].model_dump(mode="json") for k in sorted(self.edges)},
            "frames": {k: self.frames[k].model_dump(mode="json") for k in sorted(self.frames)},
            "observation_rules": {k: self.observation_rules[k].model_dump(mode="json") for k in sorted(self.observation_rules)},
            "pointers": {k: self.pointers[k].model_dump(mode="json") for k in sorted(self.pointers)},
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    # -----------------------------------------------------------------------
    # Core Graph & Subsumption Queries
    # -----------------------------------------------------------------------
    def has_concept(self, concept_id: str) -> bool:
        return concept_id in self.concepts

    def get_concept(self, concept_id: str) -> Concept | None:
        return self.concepts.get(concept_id)

    def is_a(self, child_id: str, ancestor_id: str) -> bool:
        """O(1) transitive IS_A subsumption check (reflexive: x IS_A x)."""
        return ancestor_id in self._ancestors.get(child_id, frozenset())

    def ancestors(self, concept_id: str) -> frozenset[str]:
        return self._ancestors.get(concept_id, frozenset())

    def descendants(self, concept_id: str) -> frozenset[str]:
        return self._descendants_frozen.get(concept_id, frozenset())

    def by_code(self, system: str, code: str) -> Concept | None:
        key = (system.lower(), _norm_term(code) if system.lower() == "inn" else code)
        cid = self._by_code.get(key)
        return self.concepts.get(cid) if cid else None

    def validate_code(self, system: str, code: str) -> bool:
        return self.by_code(system, code) is not None

    # -----------------------------------------------------------------------
    # Bilingual Drug, Alias & Purpose Resolution
    # -----------------------------------------------------------------------
    def resolve_drug(self, name: str) -> Concept | None:
        """Resolve generic, brand, or Cantonese/zh-HK drug name (incl. dose text) to canonical Drug Concept."""
        nt = _norm_term(name)
        if not nt:
            return None
        if nt in self._drug_alias:
            return self.concepts[self._drug_alias[nt]]
        for alias, cid in self._sorted_drug_aliases:
            if alias.isascii():
                if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", nt):
                    return self.concepts[cid]
            elif alias in nt:
                return self.concepts[cid]
        return None

    def extract_drugs(self, text: str) -> list[Concept]:
        """Extract all distinct canonical drug concepts mentioned in EN/zh-HK free text."""
        nt = _norm_term(text)
        seen: set[str] = set()
        out: list[Concept] = []
        for alias, cid in self._sorted_drug_aliases:
            if cid in seen:
                continue
            hit = (
                bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", nt))
                if alias.isascii()
                else alias in nt
            )
            if hit:
                seen.add(cid)
                out.append(self.concepts[cid])
        return out

    def extract_concepts(self, text: str, *, kinds: Sequence[str] | None = None) -> list[Concept]:
        """Extract canonical concepts of specified kinds mentioned in EN/zh-HK free text."""
        nt = _norm_term(text)
        kind_set = set(kinds) if kinds else None
        seen: set[str] = set()
        out: list[Concept] = []
        for alias, cids in self._sorted_all_aliases:
            hit = (
                bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", nt))
                if alias.isascii()
                else alias in nt
            )
            if not hit:
                continue
            for cid in cids:
                if cid in seen:
                    continue
                c = self.concepts[cid]
                if kind_set is None or c.kind in kind_set:
                    seen.add(cid)
                    out.append(c)
        return out

    def concepts_for_purpose(self, text: str) -> list[Concept]:
        """Map lay patient purpose phrases (e.g. '薄血丸', 'blood pressure') to drug_class/drug concepts."""
        nt = _norm_term(text)
        seen: set[str] = set()
        out: list[Concept] = []
        for phrase, cids in self._purpose_to_concepts.items():
            hit = (
                bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", nt))
                if phrase.isascii()
                else phrase in nt
            )
            if hit:
                for cid in cids:
                    if cid not in seen:
                        seen.add(cid)
                        out.append(self.concepts[cid])
        return out

    # -----------------------------------------------------------------------
    # Clinical Edge Evaluation (with IS_A Inheritance)
    # -----------------------------------------------------------------------
    def edges_for(
        self,
        concept_id: ConceptId,
        *,
        relations: Sequence[EdgeRelation] | None = None,
        inherit_ancestors: bool = True,
    ) -> list[ClinicalEdge]:
        """Return outgoing clinical edges for `concept_id` (optionally inheriting ancestor class edges)."""
        source_ids = self.ancestors(concept_id) if inherit_ancestors else frozenset({concept_id})
        rel_set = set(relations) if relations else None
        seen: set[str] = set()
        out: list[ClinicalEdge] = []
        for sid in sorted(source_ids):
            for edge in self._out_edges.get(sid, ()):
                if edge.id in seen:
                    continue
                if rel_set is not None and edge.relation not in rel_set:
                    continue
                seen.add(edge.id)
                out.append(edge)
        return out

    def evaluate_patient_edges(
        self,
        *,
        active_concept_ids: Sequence[ConceptId] = (),
        active_med_names: Sequence[str] = (),
        facts: dict[str, Any] | None = None,
        lang: str = "en",
    ) -> list[FiredEdge]:
        """Evaluate all deterministic clinical edges relevant to a patient's active concepts,
        medications (resolved via EN/zh-HK aliases), and structured clinical facts.
        """
        resolved_meds: list[ConceptId] = []
        for m in active_med_names:
            if m in self.concepts:
                resolved_meds.append(m)
            else:
                c = self.resolve_drug(m)
                if c:
                    resolved_meds.append(c.id)

        all_active = list(dict.fromkeys(list(active_concept_ids) + resolved_meds))
        ctx = dict(facts or {})
        ctx.setdefault("jurisdiction", self.jurisdiction)
        ctx["on_meds"] = tuple(dict.fromkeys(list(ctx.get("on_meds") or ()) + resolved_meds))
        ctx["concepts"] = tuple(dict.fromkeys(list(ctx.get("concepts") or ()) + all_active))

        fired: list[FiredEdge] = []
        seen_edge_ids: set[str] = set()

        for cid in all_active:
            for edge in self.edges_for(cid, inherit_ancestors=True):
                if edge.id in seen_edge_ids:
                    continue
                # For INTERACTS_WITH edges, require the OTHER side to also be active (via IS_A)
                matched_target: ConceptId | None = None
                if edge.relation == "INTERACTS_WITH":
                    other_id = edge.target_id if self.is_a(cid, edge.source_id) else edge.source_id
                    other_hits = [x for x in all_active if x != cid and self.is_a(x, other_id)]
                    if not other_hits:
                        continue
                    matched_target = other_hits[0]
                elif edge.relation == "CONTRAINDICATED_IN":
                    # Fires if target is an active concept OR if predicate `when` explicitly governs it
                    if edge.when is None:
                        target_hits = [x for x in all_active if self.is_a(x, edge.target_id)]
                        if not target_hits:
                            continue
                        matched_target = target_hits[0]

                if not evaluate_predicate(edge.when, ctx, is_a=self.is_a):
                    continue

                seen_edge_ids.add(edge.id)
                fired.append(
                    FiredEdge(
                        edge=edge,
                        matched_source_id=cid,
                        matched_target_id=matched_target,
                        clinician_rationale=edge.clinician_rationale.resolve(self.jurisdiction, lang),
                        clinician_action=(
                            edge.clinician_action_prompt.resolve(self.jurisdiction, lang)
                            if edge.clinician_action_prompt
                            else None
                        ),
                        patient_safety_net=(
                            edge.patient_safety_net.resolve(self.jurisdiction, lang)
                            if edge.patient_safety_net
                            else None
                        ),
                    )
                )

        # Deterministic sort: highest urgency_floor & severity first, then edge ID
        sev_rank = {"hard_block": 5, "critical": 4, "high": 3, "moderate": 2, "advisory": 1, "info": 0}
        fired.sort(
            key=lambda f: (-(f.edge.urgency_floor or 0), -sev_rank[f.edge.severity], f.edge.id)
        )
        return fired

    # -----------------------------------------------------------------------
    # Observation & PresentationFrame Helpers
    # -----------------------------------------------------------------------
    def get_observation_rule(self, observation_concept_id: ConceptId) -> ObservationRule | None:
        return self._obs_rule_by_concept.get(observation_concept_id)

    def evaluate_observation(
        self,
        observation_concept_id: ConceptId,
        value: float,
        *,
        ucum: str | None = None,
        facts: dict[str, Any] | None = None,
        lang: str = "en",
        previous_value: float | None = None,
        previous_ucum: str | None = None,
        days_since_previous: int | None = None,
    ) -> ObservationEvalResult:
        rule = self._obs_rule_by_concept.get(observation_concept_id)
        if rule is None:
            raise KeyError(f"No ObservationRule registered for '{observation_concept_id}'")
        return evaluate_observation(
            rule,
            value,
            ucum=ucum,
            facts=facts,
            jurisdiction=self.jurisdiction,
            lang=lang,
            previous_value=previous_value,
            previous_ucum=previous_ucum,
            days_since_previous=days_since_previous,
            is_a=self.is_a,
        )

    def get_frame_for_rfe(self, rfe_concept_id: ConceptId) -> PresentationFrame | None:
        return self._frame_by_rfe.get(rfe_concept_id)

    def check_positive_exclusions(
        self,
        rfe_concept_id: ConceptId,
        exclusion_states: dict[ConceptId, ExclusionState] | None = None,
        *,
        present_findings: Sequence[ConceptId] | set[ConceptId] | frozenset[ConceptId] | None = None,
        explicitly_excluded_findings: Sequence[ConceptId] | set[ConceptId] | frozenset[ConceptId] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> ExclusionCheckResult:
        frame = self._frame_by_rfe.get(rfe_concept_id)
        if frame is None:
            raise KeyError(f"No PresentationFrame registered for '{rfe_concept_id}'")
        states: dict[ConceptId, ExclusionState] = dict(exclusion_states or {})
        for cid in explicitly_excluded_findings or ():
            states.setdefault(cid, "explicitly_excluded")
        for cid in present_findings or ():
            states[cid] = "present"
        ctx = dict(facts or {})
        ctx.setdefault("jurisdiction", self.jurisdiction)
        return evaluate_positive_exclusions(frame, states, facts=ctx, is_a=self.is_a)

    def build_context_pack(
        self,
        case_state: Any | None = None,
        *,
        active_rfe_ids: Sequence[ConceptId] = (),
        active_concept_ids: Sequence[ConceptId] = (),
        active_med_names: Sequence[str] = (),
        observations: Sequence[dict[str, Any]] = (),
        exclusion_states: dict[ConceptId, ExclusionState] | None = None,
        facts: dict[str, Any] | None = None,
        lang: str = "en",
        max_tokens: int = 1500,
    ) -> Any:
        """Build a progressive 1-hop token-budgeted ContextPack from kwargs or a CaseEvidenceState-like object."""
        from .prompt_pack import build_context_pack as _build_context_pack

        rfe_ids = list(active_rfe_ids)
        concept_ids = list(active_concept_ids)
        med_names = list(active_med_names)
        obs_list = list(observations)
        exc_map: dict[ConceptId, ExclusionState] = dict(exclusion_states or {})
        fact_dict: dict[str, Any] = dict(facts or {})

        if case_state is not None:
            rfe_ids = list(dict.fromkeys(rfe_ids + list(getattr(case_state, "active_rfe_ids", ()) or ())))
            present = getattr(case_state, "present_findings", None) or {}
            excluded = getattr(case_state, "explicitly_excluded_findings", None) or {}
            present_keys = list(present.keys()) if isinstance(present, dict) else list(present)
            excluded_keys = list(excluded.keys()) if isinstance(excluded, dict) else list(excluded)
            concept_ids = list(dict.fromkeys(concept_ids + present_keys))
            med_names = list(dict.fromkeys(med_names + list(getattr(case_state, "active_med_ids", ()) or ())))
            for cid in excluded_keys:
                exc_map.setdefault(cid, "explicitly_excluded")
            for cid in present_keys:
                exc_map[cid] = "present"
            for lr in getattr(case_state, "lab_results", ()) or ():
                if isinstance(lr, dict) and lr.get("concept_id") and lr.get("value") is not None:
                    obs_list.append(lr)
            if hasattr(case_state, "to_ontology_facts"):
                merged_facts = dict(case_state.to_ontology_facts() or {})
                merged_facts.update(fact_dict)
                fact_dict = merged_facts

        if concept_ids:
            existing_concepts = list(fact_dict.get("concepts") or ())
            fact_dict["concepts"] = tuple(dict.fromkeys(existing_concepts + concept_ids))
        if exc_map:
            fact_dict["exclusion_states"] = exc_map

        return _build_context_pack(
            self,
            patient_context=fact_dict or None,
            rfe_ids=rfe_ids,
            med_names=med_names,
            lab_results=obs_list,
            max_tokens=max_tokens,
            lang=lang,
        )

    def pointers_for(
        self,
        *,
        concept_ids: Sequence[ConceptId] = (),
        edge_ids: Sequence[str] = (),
        obs_rule_ids: Sequence[str] = (),
        facts: dict[str, Any] | None = None,
    ) -> list[PathwayPointer]:
        ctx = dict(facts or {})
        ctx.setdefault("jurisdiction", self.jurisdiction)
        edge_set = set(edge_ids)
        obs_set = set(obs_rule_ids)
        out: list[PathwayPointer] = []
        for ptr in self.pointers.values():
            trig_c = any(self.is_a(c, tc) for c in concept_ids for tc in ptr.trigger_concepts)
            trig_e = any(e in edge_set for e in ptr.trigger_edges)
            trig_o = any(o in obs_set for o in ptr.trigger_observations)
            if not (trig_c or trig_e or trig_o):
                continue
            if evaluate_predicate(ptr.when, ctx, is_a=self.is_a):
                out.append(ptr)
        out.sort(key=lambda p: p.id)
        return out

    # -----------------------------------------------------------------------
    # HL7 FHIR R4 Export (License-Safe Two-Tier Coding)
    # -----------------------------------------------------------------------
    def to_fhir_codeable_concept(
        self,
        concept_id: ConceptId,
        *,
        include_tier2_refs: bool = False,
    ) -> dict[str, Any]:
        """Serialize a canonical concept to an HL7 FHIR R4 CodeableConcept."""
        c = self.concepts[concept_id]
        codings: list[dict[str, Any]] = []
        b = c.bindings

        if b.snomed_gps:
            codings.append({
                "system": FHIR_SYSTEMS["snomed_gps"],
                "code": b.snomed_gps.code,
                "display": b.snomed_gps.display,
            })
        if b.loinc:
            codings.append({
                "system": FHIR_SYSTEMS["loinc"],
                "code": b.loinc.code,
                "display": b.loinc.display,
            })
        if b.rxnorm:
            codings.append({
                "system": FHIR_SYSTEMS["rxnorm"],
                "code": b.rxnorm.code,
                "display": b.rxnorm.display,
            })
        if b.cvx:
            codings.append({
                "system": FHIR_SYSTEMS["cvx"],
                "code": b.cvx.code,
                "display": b.cvx.display,
            })
        if include_tier2_refs:
            for ref in (b.icd10, b.icpc2, b.atc):
                if ref is not None:
                    # Bare code-only pointer: never emits redistributed rubric text
                    codings.append({"system": ref.system, "code": ref.code})

        result: dict[str, Any] = {
            "coding": codings,
            "text": c.display.resolve(self.jurisdiction, "en"),
        }
        if c.display.zh_hk:
            result["extension"] = [{
                "url": "http://tmrw.hk/fhir/StructureDefinition/display-zh-HK",
                "valueString": c.display.zh_hk,
            }]
        return result
