"""Primary Care Clinical Knowledge Ontology (Two-Level Neuro-Symbolic Kernel).

Provides deterministic clinical concept graphs, Murtagh PROMPT presentation
frames, openEHR positive-exclusion gates, LOINC/UCUM observation rules with
automatic unit conversion (HK g/dL <-> AU g/L), 3-tier overlay inheritance
(Core -> Jurisdiction [HK|AU] -> Clinic), and token-budgeted ContextPacks for
LLM agents.
"""
from .evaluator import (
    ExclusionCheckResult,
    ObservationEvalResult,
    evaluate_observation,
    evaluate_positive_exclusions,
    evaluate_predicate,
)
from .graph import FiredEdge, OntologyGraph
from .lint import LintIssue, lint_ontology, valid_loinc_code, valid_snomed_sctid
from .loader import get_ontology, load_core_bundle, load_overlay
from .overlay import CompiledBundle, OverlayMergeError, SafetyMonotonicityError, compile_ontology
from .prompt_pack import ContextPack, build_context_pack

__all__ = [
    "CompiledBundle",
    "ContextPack",
    "ExclusionCheckResult",
    "FiredEdge",
    "LintIssue",
    "ObservationEvalResult",
    "OntologyGraph",
    "OverlayMergeError",
    "SafetyMonotonicityError",
    "build_context_pack",
    "compile_ontology",
    "evaluate_observation",
    "evaluate_positive_exclusions",
    "evaluate_predicate",
    "get_ontology",
    "lint_ontology",
    "load_core_bundle",
    "load_overlay",
    "valid_loinc_code",
    "valid_snomed_sctid",
]
