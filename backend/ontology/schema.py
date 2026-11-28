"""6-Primitive Clinical Knowledge Ontology Metamodel (Pydantic v2).

Two-Level Neuro-Symbolic Architecture:
- Level 1 (Declarative Data): Git-versioned YAML knowledge packs under
  `backend/ontology/data/core/` and `data/overlays/{hk,au,clinic_*}.yaml`.
- Level 2 (Typed Compiler & Runtime Graph): Strict Pydantic v2 models
  (`extra="forbid", frozen=True`) compiled into an immutable in-memory graph.

Licensing Guardrail (Two-Tier Binding Model):
- Tier 1 (100% commercial-safe operational spine): internal namespaced IDs +
  authored `IS_A` DAG (`parent_ids`) + SNOMED CT GPS (CC BY-ND 4.0, flat opaque
  code/term list) + LOINC (unmodified EN display, `third_party_copyright=False`)
  + UCUM + WHO INN + FDA GSRS UNII + RxNorm Current Prescribable (`SAB=RXNORM`)
  + CDC CVX.
- Tier 2 (`ExternalRef`, bare code-only interoperability pointers): `icpc2`,
  `icd10`, `icd11`, `atc` store ONLY the code string & FHIR system URI without
  redistributing copyrighted WHO/WONCA/WHOCC rubric tables or descriptions.

Asymmetric Clinical Authority (`backend/engine/ports.py`):
- Diagnostic differentials, working problem/plan templates, and edge rationales
  are typed `clinician_only`.
- Patient-facing text is isolated to `patient_safety_net` / `patient_instruction`
  and linted against `banned_claims`.
- `PathwayPointer` actions are strictly constrained to
  `{open_item, raise_urgency, alarm, attest_all_clear}` (never close/reassure).
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Namespaced identifier patterns, e.g. "drug:allopurinol", "obs:haemoglobin", "rfe:fatigue"
ConceptId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$")]
EdgeId = Annotated[str, Field(pattern=r"^edge:[a-z0-9][a-z0-9_]*$")]
FrameId = Annotated[str, Field(pattern=r"^frame:[a-z0-9][a-z0-9_]*$")]
RuleId = Annotated[str, Field(pattern=r"^obs_rule:[a-z0-9][a-z0-9_]*$")]
PointerId = Annotated[str, Field(pattern=r"^ptr:[a-z0-9][a-z0-9_]*$")]

JurisdictionCode = Literal["HK", "AU"]
UrgencyLevel = Literal[1, 2, 3, 4]  # 1=routine, 2=soon, 3=urgent, 4=critical/emergency
ClinicPortFamily = Literal[
    "abnormal_result",
    "test_never_resulted",
    "followup_recommended",
    "positive_screen",
    "referral",
    "drug_monitoring",
    "planned_review",
]
Severity = Literal["info", "advisory", "moderate", "high", "critical", "hard_block"]
ReviewStatus = Literal["draft", "clinician_reviewed", "approved", "deprecated"]
AllowedPortAction = Literal["open_item", "raise_urgency", "alarm", "attest_all_clear"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalizedText(StrictModel):
    en: str = Field(min_length=1)
    zh_hk: str | None = None
    en_au: str | None = None
    en_hk: str | None = None

    def resolve(self, jurisdiction: JurisdictionCode = "HK", lang: str = "en") -> str:
        if lang.lower() in ("zh", "zh-hk", "zh_hk") and self.zh_hk:
            return self.zh_hk
        if jurisdiction == "AU" and self.en_au:
            return self.en_au
        if jurisdiction == "HK" and self.en_hk:
            return self.en_hk
        return self.en


class LocalizedAliases(StrictModel):
    en: tuple[str, ...] = ()
    zh_hk: tuple[str, ...] = ()
    code_switched: tuple[str, ...] = ()
    typos_or_variants: tuple[str, ...] = ()

    def all_terms(self) -> tuple[str, ...]:
        return self.en + self.zh_hk + self.code_switched + self.typos_or_variants


class GuidelineRef(StrictModel):
    authority: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    url: str | None = None
    year: int = Field(ge=1990, le=2100)
    evidence_grade: str | None = None


class Provenance(StrictModel):
    author: str = Field(min_length=1)
    reviewer: str | None = None
    review_status: ReviewStatus = "draft"
    last_reviewed: date | None = None
    review_cycle_months: int = Field(default=24, ge=1, le=60)
    jurisdiction_scope: Literal["core", "HK", "AU", "clinic"] = "core"
    guideline_refs: tuple[GuidelineRef, ...] = ()
    notes: str | None = None


# ---------------------------------------------------------------------------
# Deterministic, eval-free Clinical Predicate AST
# ---------------------------------------------------------------------------
FactName = Literal[
    "age_years",
    "age_months",
    "sex",
    "pregnant",
    "planning_pregnancy",
    "breastfeeding",
    "postmenopausal",
    "egfr_ml_min",
    "creatinine_umol_l",
    "g6pd_deficient",
    "ethnicity",
    "birth_year",
    "hla_b_5801",
    "hla_b_1502",
    "has_concept",
    "on_med",
    "obs_value",
    "duration_hours",
    "jurisdiction",
    "setting",
    "known_copd_co2_retainer",
    "acute_dehydrating_illness",
    "planned_surgery_within_72h",
    "gluten_free_diet",
    "tcm_herb_use",
    "allergy_group",
]
PredicateOp = Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "is_a", "exists", "missing"]


class FactCondition(StrictModel):
    fact: FactName
    op: PredicateOp
    value: Any = None
    concept_id: ConceptId | None = None  # Used when fact == "obs_value"
    unit: str | None = None


class AllOf(StrictModel):
    all: tuple[WhenPredicate, ...] = Field(min_length=1)


class AnyOf(StrictModel):
    any: tuple[WhenPredicate, ...] = Field(min_length=1)


class Not(StrictModel):
    not_: WhenPredicate = Field(alias="not")


WhenPredicate = Union[FactCondition, AllOf, AnyOf, Not]
AllOf.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()


# ---------------------------------------------------------------------------
# Primitive 1: Concept
# ---------------------------------------------------------------------------
ConceptKind = Literal[
    "condition",
    "rfe",
    "symptom",
    "red_flag",
    "drug",
    "drug_class",
    "allergy_group",
    "observation",
    "panel",
    "procedure",
    "vaccine",
    "pgx_marker",
    "screening_program",
]

DrugSchedule = Literal[
    "UNSPECIFIED",
    "OTC",
    "HK_CAP137_ANTIBIOTIC",
    "HK_CAP138_PART_I_POISON",
    "HK_CAP138_PART_I_SCH1_POISONS_BOOK",
    "HK_CAP138_PART_I_SCH3_RX",
    "HK_CAP134_DANGEROUS_DRUG",
    "AU_UNSCHEDULED",
    "AU_SUSMP_S2",
    "AU_SUSMP_S3",
    "AU_SUSMP_S4",
    "AU_SUSMP_S8",
]


class CodeBinding(StrictModel):
    """Tier-1 commercial-safe open terminology binding."""

    code: str = Field(min_length=1)
    display: str = Field(min_length=1)
    version: str | None = None
    third_party_copyright: bool = False


class ExternalRef(StrictModel):
    """Tier-2 code-only interoperability pointer (never redistributes copyrighted text)."""

    code: str = Field(min_length=1)
    system: str = Field(min_length=1)
    license_gated: bool = True


class CodeBindings(StrictModel):
    # Tier 1: 100% commercial-safe open operational spine
    snomed_gps: CodeBinding | None = None
    loinc: CodeBinding | None = None
    ucum: str | None = None
    rxnorm: CodeBinding | None = None  # Strictly SAB=RXNORM Current Prescribable Content
    unii: str | None = None            # Public domain FDA GSRS UNII (covers non-US drugs like gliclazide)
    cvx: CodeBinding | None = None
    inn: str | None = None             # WHO International Nonproprietary Name (public domain)
    # Tier 2: Bare code-only interoperability pointers (no redistributed rubric descriptions)
    icd10: ExternalRef | None = None
    icd11: ExternalRef | None = None
    icpc2: ExternalRef | None = None
    atc: ExternalRef | None = None
    # Jurisdiction-specific public/national codes (e.g. "au_pbs", "hk_hadf")
    jurisdiction_codes: dict[str, CodeBinding] = Field(default_factory=dict)


class DrugFacet(StrictModel):
    schedule: DrugSchedule = "UNSPECIFIED"
    high_risk: bool = False
    no_gap: bool = False
    short_course: bool = False
    bleedy: bool = False
    telehealth_blocked: bool = False
    allergy_group_id: ConceptId | None = None
    max_daily_mg: float | None = Field(default=None, gt=0)
    max_single_mg: float | None = Field(default=None, gt=0)
    pediatric_mg_per_kg_dose: float | None = Field(default=None, gt=0)
    pediatric_min_age_years: float | None = Field(default=None, ge=0)
    g6pd_risk: bool = False
    pregnancy_caution: Literal["none", "caution", "avoid_t3", "contraindicated"] = "none"
    renal_note: str | None = None
    dosing_pearl: LocalizedText | None = None


class PanelFacet(StrictModel):
    member_observation_ids: tuple[ConceptId, ...] = Field(min_length=1)


class Concept(StrictModel):
    id: ConceptId
    kind: ConceptKind
    wave: Literal["W0", "W1", "W2"] = "W0"
    cluster: str | None = None
    parent_ids: tuple[ConceptId, ...] = ()
    bindings: CodeBindings = Field(default_factory=CodeBindings)
    display: LocalizedText
    aliases: LocalizedAliases = Field(default_factory=LocalizedAliases)
    purpose_phrases: LocalizedAliases = Field(default_factory=LocalizedAliases)
    safety_regexes: tuple[str, ...] = ()
    drug_facet: DrugFacet | None = None
    panel_facet: PanelFacet | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def _check_facets(self) -> Concept:
        if self.drug_facet is not None and self.kind not in ("drug", "drug_class"):
            raise ValueError("drug_facet is only valid for kind='drug' or 'drug_class'")
        if self.panel_facet is not None and self.kind != "panel":
            raise ValueError("panel_facet is only valid for kind='panel'")
        return self

    @property
    def label_en(self) -> str:
        return self.display.en

    @property
    def label_zh(self) -> str:
        return self.display.zh_hk or self.display.en


# ---------------------------------------------------------------------------
# Primitive 2: ClinicalEdge
# ---------------------------------------------------------------------------
EdgeRelation = Literal[
    "MONITORED_BY",
    "CONTRAINDICATED_IN",
    "INTERACTS_WITH",
    "RED_FLAG_FOR",
    "EXPLAINS_OR_CAUSES",
    "TRIGGERS_WORKUP",
    "REQUIRES_PGX_CHECK",
    "ALLERGY_CROSS_REACTS_WITH",
    "PREVENTIVE_SCREEN_FOR",
]


class MonitoringSpec(StrictModel):
    initiation_due_days: int | None = Field(default=None, ge=1)
    repeat_interval_days: int | None = Field(default=None, ge=1)
    grace_days: int = Field(default=14, ge=0)
    applies_on: Literal["initiation", "repeat", "both"] = "both"


class ClinicalEdge(StrictModel):
    id: EdgeId
    source_id: ConceptId
    relation: EdgeRelation
    target_id: ConceptId
    bidirectional: bool = False
    severity: Severity
    urgency_floor: UrgencyLevel | None = None
    clinic_port_family: ClinicPortFamily | None = None
    when: WhenPredicate | None = None
    monitoring_spec: MonitoringSpec | None = None
    clinician_rationale: LocalizedText
    clinician_action_prompt: LocalizedText | None = None
    patient_safety_net: LocalizedText | None = None
    guideline_refs: tuple[GuidelineRef, ...] = ()
    provenance: Provenance

    @model_validator(mode="after")
    def _check_relation_fields(self) -> ClinicalEdge:
        if self.relation == "MONITORED_BY" and self.monitoring_spec is None:
            raise ValueError("MONITORED_BY edge requires monitoring_spec")
        if self.severity in ("critical", "hard_block") and self.urgency_floor is not None and self.urgency_floor < 3:
            raise ValueError("critical/hard_block edges must have urgency_floor >= 3")
        return self


# ---------------------------------------------------------------------------
# Primitive 3: PresentationFrame (Murtagh PROMPT + openEHR Positive Exclusion)
# ---------------------------------------------------------------------------
MasqueradeType = Literal[
    "depression",
    "diabetes",
    "drugs",
    "anaemia",
    "thyroid_disease",
    "spinal_dysfunction",
    "uti",
    "ckd",
    "malignancy",
    "connective_tissue",
    "hiv",
    "baffling_bacterial",
    "baffling_viral",
    "neurological_dilemma",
]
ExclusionState = Literal["present", "explicitly_excluded", "not_asked"]
EscalationChannel = Literal[
    "emergency_services",
    "crisis_hotline",
    "same_day_gp",
    "urgent_clinic_callback",
]


class DifferentialItem(StrictModel):
    id: str = Field(min_length=1)
    concept_id: ConceptId
    prevalence_note: str | None = None
    key_discriminators: tuple[str, ...] = ()
    when: WhenPredicate | None = None

    @property
    def discriminating_features(self) -> tuple[str, ...]:
        return self.key_discriminators


class MasqueradeCheck(StrictModel):
    id: str = Field(min_length=1)
    tier: Literal["primary", "secondary"] = "primary"
    masquerade: MasqueradeType
    concept_id: ConceptId
    clue_prompt: LocalizedText


class MurtaghPrompt(StrictModel):
    """Strictly clinician_only cognitive scaffold (never exposed as diagnoses to patients)."""

    audience: Literal["clinician_only"] = "clinician_only"
    probability_diagnoses: tuple[DifferentialItem, ...] = Field(min_length=1)
    serious_not_to_be_missed: tuple[DifferentialItem, ...] = Field(min_length=1)
    pitfalls_often_missed: tuple[DifferentialItem, ...] = ()
    masquerades: tuple[MasqueradeCheck, ...] = ()
    patient_hidden_agenda_prompts: tuple[LocalizedText, ...] = ()


class ExclusionChecklistItem(StrictModel):
    """openEHR Positive Exclusion item: separates 'explicitly denied' from 'not asked'."""

    id: str = Field(min_length=1)
    finding_concept_id: ConceptId
    question: LocalizedText
    urgency_if_present: Literal[2, 3, 4]
    mandatory: bool = True
    when: WhenPredicate | None = None


class SafetyNetCriterion(StrictModel):
    id: str = Field(min_length=1)
    trigger_condition: LocalizedText
    timeframe_hours: int | None = Field(default=None, ge=1)
    escalation_channel: EscalationChannel
    patient_instruction: LocalizedText
    when: WhenPredicate | None = None


class PresentationFrame(StrictModel):
    id: FrameId
    rfe_concept_id: ConceptId
    murtagh_prompt: MurtaghPrompt
    positive_exclusions: tuple[ExclusionChecklistItem, ...] = Field(min_length=1)
    safety_netting: tuple[SafetyNetCriterion, ...] = Field(min_length=1)
    provenance: Provenance

    @property
    def serious_not_to_be_missed(self) -> tuple[DifferentialItem, ...]:
        return self.murtagh_prompt.serious_not_to_be_missed

    @property
    def masquerades(self) -> tuple[MasqueradeCheck, ...]:
        return self.murtagh_prompt.masquerades

    @property
    def pitfalls(self) -> tuple[DifferentialItem, ...]:
        return self.murtagh_prompt.pitfalls_often_missed

    @property
    def probable_diagnoses(self) -> tuple[DifferentialItem, ...]:
        return self.murtagh_prompt.probability_diagnoses


# ---------------------------------------------------------------------------
# Primitive 4: ObservationRule (LOINC + UCUM + UnitConversion + Bands + Pearls)
# ---------------------------------------------------------------------------
class UnitConversion(StrictModel):
    """Linear conversion: canonical_value = (input_value * factor) + offset."""

    from_ucum: str = Field(min_length=1)
    factor: float = Field(gt=0)
    offset: float = 0.0


class NumericBound(StrictModel):
    lt: float | None = None
    le: float | None = None
    gt: float | None = None
    ge: float | None = None

    @model_validator(mode="after")
    def _validate_bounds(self) -> NumericBound:
        if self.lt is not None and self.le is not None:
            raise ValueError("Specify only one of lt or le")
        if self.gt is not None and self.ge is not None:
            raise ValueError("Specify only one of gt or ge")
        if all(x is None for x in (self.lt, self.le, self.gt, self.ge)):
            raise ValueError("At least one bound must be set")
        return self

    def matches(self, val: float) -> bool:
        if self.lt is not None and val < self.lt:
            return True
        if self.le is not None and val <= self.le:
            return True
        if self.gt is not None and val > self.gt:
            return True
        if self.ge is not None and val >= self.ge:
            return True
        return False

    def low_threshold(self) -> float | None:
        return self.lt if self.lt is not None else self.le

    def high_threshold(self) -> float | None:
        return self.gt if self.gt is not None else self.ge


class ContextualInterval(StrictModel):
    id: str = Field(min_length=1)
    low: float | None = None
    high: float | None = None
    priority: int = 0
    when: WhenPredicate | None = None
    source_note: str | None = None


class SeverityBands(StrictModel):
    """Strictly nested: critical outside urgent outside significant."""

    significant: NumericBound | None = None
    urgent: NumericBound | None = None
    critical: NumericBound | None = None


class TrendRule(StrictModel):
    id: str = Field(min_length=1)
    direction: Literal["rise", "drop", "any_change"]
    delta_abs: float | None = Field(default=None, gt=0)
    delta_pct: float | None = Field(default=None, gt=0)
    window_days: int = Field(ge=1)
    urgency_floor: UrgencyLevel
    when: WhenPredicate | None = None
    clinician_rationale: LocalizedText


class ProblemPlanTemplate(StrictModel):
    audience: Literal["clinician_only"] = "clinician_only"
    working_problem: LocalizedText
    plan_template: LocalizedText
    relevant_med_class_ids: tuple[ConceptId, ...] = ()


class EpidemiologyPearl(StrictModel):
    id: str = Field(min_length=1)
    when: WhenPredicate | None = None
    clinician_pearl: LocalizedText
    guideline_ref: GuidelineRef | None = None


class ObservationRule(StrictModel):
    id: RuleId
    observation_concept_id: ConceptId
    canonical_ucum: str = Field(min_length=1)
    display_ucum: str | None = None
    unit_conversions: tuple[UnitConversion, ...] = ()
    reference_intervals: tuple[ContextualInterval, ...] = ()
    severity_bands: SeverityBands
    trend_rules: tuple[TrendRule, ...] = ()
    direction_templates: dict[
        Literal["H", "L", "delta_up", "delta_down", "positive"],
        ProblemPlanTemplate,
    ] = Field(default_factory=dict)
    epidemiology_pearls: tuple[EpidemiologyPearl, ...] = ()
    provenance: Provenance


# ---------------------------------------------------------------------------
# Primitive 5: OverlayPatch (Core -> Jurisdiction [HK|AU] -> Clinic)
# ---------------------------------------------------------------------------
class SafetyOverrideApproval(StrictModel):
    clinician_signoff_id: str = Field(min_length=1)
    approved_date: date
    rationale: str = Field(min_length=20)
    target_paths: tuple[str, ...] = Field(min_length=1)


class JurisdictionDefaults(StrictModel):
    emergency_number: str = Field(min_length=1)
    emergency_dept_label: LocalizedText
    crisis_hotlines: dict[str, str] = Field(default_factory=dict)
    poisons_hotline: str | None = None
    default_locale: Literal["en-HK", "zh-HK", "en-AU"]
    required_locales: tuple[Literal["en", "zh-HK", "en-AU"], ...]
    banned_claims: tuple[str, ...] = ()
    unit_system_overrides: dict[ConceptId, str] = Field(default_factory=dict)


class OverlayPatch(StrictModel):
    overlay_id: Annotated[str, Field(pattern=r"^(jurisdiction:(hk|au)|clinic:[a-z0-9_]+)$")]
    layer: Literal["jurisdiction", "clinic"]
    extends: str = Field(min_length=1)
    jurisdiction: JurisdictionCode
    defaults: JurisdictionDefaults | None = None

    concept_patches: dict[ConceptId, dict[str, Any]] = Field(default_factory=dict)
    edge_patches: dict[EdgeId, dict[str, Any]] = Field(default_factory=dict)
    frame_patches: dict[FrameId, dict[str, Any]] = Field(default_factory=dict)
    observation_patches: dict[RuleId, dict[str, Any]] = Field(default_factory=dict)
    pointer_patches: dict[PointerId, dict[str, Any]] = Field(default_factory=dict)

    add_concepts: tuple[Concept, ...] = ()
    add_edges: tuple[ClinicalEdge, ...] = ()
    add_frames: tuple[PresentationFrame, ...] = ()
    add_observation_rules: tuple[ObservationRule, ...] = ()
    add_pointers: tuple[PathwayPointer, ...] = ()

    safety_override_approvals: tuple[SafetyOverrideApproval, ...] = ()
    provenance: Provenance


# ---------------------------------------------------------------------------
# Primitive 6: PathwayPointer (Static Ontology -> Temporal Pathways Boundary)
# ---------------------------------------------------------------------------
class PathwayPointer(StrictModel):
    id: PointerId
    trigger_concepts: tuple[ConceptId, ...] = ()
    trigger_edges: tuple[EdgeId, ...] = ()
    trigger_observations: tuple[RuleId, ...] = ()
    when: WhenPredicate | None = None
    target_kind: Literal["protocol_yaml", "journey_module", "engine_family"]
    target_ref: str = Field(min_length=1)
    target_version_constraint: str | None = None
    autonomy_ceiling: Literal["L0_read_only", "L1_propose_only"] = "L1_propose_only"
    allowed_port_actions: tuple[AllowedPortAction, ...] = ("open_item", "raise_urgency")
    required_context_slots: tuple[str, ...] = ()
    provenance: Provenance

    @model_validator(mode="after")
    def _has_trigger(self) -> PathwayPointer:
        if not (self.trigger_concepts or self.trigger_edges or self.trigger_observations):
            raise ValueError("PathwayPointer must specify at least one trigger")
        return self


class CoreBundle(StrictModel):
    """Raw uncompiled Core knowledge pack loaded from YAML."""

    concepts: tuple[Concept, ...] = ()
    edges: tuple[ClinicalEdge, ...] = ()
    frames: tuple[PresentationFrame, ...] = ()
    observation_rules: tuple[ObservationRule, ...] = ()
    pointers: tuple[PathwayPointer, ...] = ()
