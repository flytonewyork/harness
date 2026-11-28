"""Frozen schemas and immutable safety-floor validation for Clinic Knowledge Packs.

Clinical Safety Invariants enforced at load time:
1. Immutable safety floor: RulesHarness(version="1.0") and union_only red-flag protocols
   (protocols/flu_intake_v1.yaml) can NEVER be weakened, suppressed, or overridden.
2. Asymmetric authority: clinic packs may never authorize auto_close, auto_dismiss,
   auto_reassure, autonomous prescribing, or autonomous patient messaging.
3. Monitoring overrides: tightening intervals or adding tests is always permitted;
   loosening beyond the default baseline requires explicit clinician sign-off
   (clinician_signed=True, signed_by, signed_at) and can never drop all tests or disable monitoring.
4. Templates: every recall/care-line template must declare doctor_sent_only=True, and
   WhatsApp templates must pass WhatsAppLeakageGuard (zero clinical content or IDs).

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from ..acute.security import MCHK_REG_RE, WhatsAppLeakageGuard


class KnowledgeError(ValueError):
    """Malformed or invalid clinic knowledge pack schema."""


class SafetyFloorViolation(KnowledgeError):
    """Attempt to weaken immutable v1.0 / union_only safety floors or asymmetric authority."""


class BlindSetHygieneError(PermissionError):
    """Offline curation attempted to read a blind set, first-run record, or held-out test key."""


# Keys that a clinic knowledge pack may NEVER declare (cannot override frozen v1.0 or red-flag floors)
FORBIDDEN_PACK_KEYS: frozenset[str] = frozenset({
    "override_v1",
    "disable_v1",
    "disable_red_flags",
    "suppress_red_flags",
    "weaken_red_flags",
    "red_flag_overrides",
    "bypass_safety_floor",
    "disable_safety_floor",
    "auto_close",
    "auto_dismiss",
    "auto_downgrade",
    "auto_reassure",
    "autonomous_rx",
    "allow_autonomous_rx",
    "autonomous_send",
    "auto_send_patient",
})

# Actions that MUST remain doctor-only in role_split (cannot be moved to front_desk_actions)
MANDATORY_DOCTOR_ONLY_ACTIONS: frozenset[str] = frozenset({
    "close_item",
    "confirm_fact",
    "reject_fact",
    "sign_prescription",
    "sign_medical_certificate",
    "send_patient_message",
})

# Tokens dropped when computing case-log canonical drug keys (mirrors backend/case/model.py:_DROP)
_CASE_KEY_DROP: frozenset[str] = frozenset({
    "sodium", "calcium", "potassium", "tartrate", "besylate", "hydrochloride", "hcl",
    "tablet", "tablets", "capsule", "capsules", "oral", "mg", "mcg", "ml",
    "daily", "nocte", "bd", "tds", "od", "mane",
})


def _freeze_mapping(d: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(d))


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def validate_raw_yaml_safety(raw: Any, source_label: str) -> dict[str, Any]:
    """Recursively validate raw parsed YAML against immutable safety-floor invariants."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise KnowledgeError(f"{source_label}: expected YAML mapping at root, got {type(raw).__name__}")

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                key_l = str(k).strip().lower()
                cur_path = f"{path}.{k}" if path else str(k)
                if key_l in FORBIDDEN_PACK_KEYS:
                    raise SafetyFloorViolation(
                        f"{source_label}: forbidden safety-floor override key '{cur_path}'"
                    )
                if key_l == "union_only" and v is not True:
                    raise SafetyFloorViolation(
                        f"{source_label}: '{cur_path}' must remain true (cannot weaken union_only red-flag floor)"
                    )
                if key_l == "safety_floor" and str(v).strip().lower() != "immutable":
                    raise SafetyFloorViolation(
                        f"{source_label}: '{cur_path}' must be 'immutable', got {v!r}"
                    )
                if key_l == "doctor_sent_only" and v is not True:
                    raise SafetyFloorViolation(
                        f"{source_label}: '{cur_path}' must be true (journeys never message patients directly)"
                    )
                if key_l == "auto_send" and bool(v):
                    raise SafetyFloorViolation(
                        f"{source_label}: '{cur_path}' cannot be enabled (asymmetric authority)"
                    )
                _walk(v, cur_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(raw, "")
    return raw


@dataclass(frozen=True, slots=True)
class MonitoringRule:
    tests: tuple[str, ...]
    max_days: int
    clinician_signed: bool = False
    signed_by: str = ""
    signed_at: str = ""

    def __post_init__(self) -> None:
        if not self.tests or any(not str(t).strip() for t in self.tests):
            raise SafetyFloorViolation("MonitoringRule must specify at least one non-empty test")
        if not isinstance(self.max_days, int) or self.max_days <= 0:
            raise SafetyFloorViolation(f"MonitoringRule max_days must be a positive integer, got {self.max_days!r}")
        if self.clinician_signed:
            if not self.signed_by.strip() or not self.signed_at.strip():
                raise SafetyFloorViolation(
                    "clinician_signed MonitoringRule requires non-empty signed_by and signed_at"
                )


@dataclass(frozen=True, slots=True)
class InsurerPatterns:
    ask: tuple[str, ...] = ()
    not_ask: tuple[str, ...] = ()
    consent: tuple[str, ...] = ()
    consent_missing: tuple[str, ...] = ()
    consent_given: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("ask", "not_ask", "consent", "consent_missing", "consent_given"):
            for pat in getattr(self, field_name):
                try:
                    re.compile(pat)
                except re.error as exc:
                    raise KnowledgeError(f"Invalid regex in insurer_patterns.{field_name}: {pat!r} ({exc})") from exc


@dataclass(frozen=True, slots=True)
class DomainKnowledge:
    drug_aliases: Mapping[str, str]
    drug_classes: Mapping[str, tuple[str, ...]]
    purpose_terms: Mapping[str, tuple[str, ...]]
    high_risk_classes: frozenset[str]
    controlled_classes: frozenset[str]
    short_course_classes: frozenset[str]
    no_gap_classes: frozenset[str]
    monitoring_rules: Mapping[str, MonitoringRule]
    doac_renal_monitoring: MonitoringRule
    review_days: int
    shorthand_glossary: Mapping[str, str]
    insurer_patterns: InsurerPatterns


@dataclass(frozen=True, slots=True)
class EmrProfile:
    vendor: str = "generic_hk_cms"
    structured_labs: bool = True
    attachment_labs: bool = True
    ehealth_pull_available: bool = False
    scan_lag_hours: int = 24
    courier_lag_hours: int = 48
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DataAccessKnowledge:
    emr_profile: EmrProfile
    lab_test_synonyms: Mapping[str, tuple[str, ...]]
    closure_markers: tuple[str, ...]
    document_id_patterns: tuple[str, ...]

    def __post_init__(self) -> None:
        for pat in self.document_id_patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise KnowledgeError(f"Invalid regex in document_id_patterns: {pat!r} ({exc})") from exc


@dataclass(frozen=True, slots=True)
class CoverRule:
    id: str
    condition: str
    fallback_role: str
    escalate_after_hours: int
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.fallback_role.strip():
            raise KnowledgeError("CoverRule requires non-empty id and fallback_role")
        if self.escalate_after_hours <= 0:
            raise KnowledgeError(f"CoverRule escalate_after_hours must be > 0, got {self.escalate_after_hours}")


@dataclass(frozen=True, slots=True)
class RoutingTemplate:
    id: str
    channel: str
    lang: str
    doctor_sent_only: bool
    body: str

    def __post_init__(self) -> None:
        if self.doctor_sent_only is not True:
            raise SafetyFloorViolation(
                f"RoutingTemplate '{self.id}': doctor_sent_only must be True (journeys never message patients)"
            )
        if not self.body.strip():
            raise KnowledgeError(f"RoutingTemplate '{self.id}': body must be non-empty")
        if self.channel.strip().lower() == "whatsapp":
            violations = WhatsAppLeakageGuard.scan(self.body)
            if violations:
                raise SafetyFloorViolation(
                    f"RoutingTemplate '{self.id}' violates WhatsApp zero-leakage guard: {violations}"
                )


@dataclass(frozen=True, slots=True)
class RoutingKnowledge:
    cover_rules: tuple[CoverRule, ...]
    doctor_only_actions: frozenset[str]
    front_desk_actions: frozenset[str]
    templates: tuple[RoutingTemplate, ...]

    def __post_init__(self) -> None:
        leaked = MANDATORY_DOCTOR_ONLY_ACTIONS & self.front_desk_actions
        if leaked:
            raise SafetyFloorViolation(
                f"Mandatory doctor-only actions cannot be assigned to front_desk_actions: {sorted(leaked)}"
            )
        missing = MANDATORY_DOCTOR_ONLY_ACTIONS - self.doctor_only_actions
        if missing:
            raise SafetyFloorViolation(
                f"Mandatory doctor-only actions missing from doctor_only_actions: {sorted(missing)}"
            )


def validate_monitoring_override(
    drug_class: str,
    baseline: MonitoringRule | None,
    candidate: MonitoringRule,
) -> None:
    """Enforce that clinic monitoring overrides never weaken safety floors without clinician sign-off."""
    if baseline is None:
        return
    missing_tests = set(baseline.tests) - set(candidate.tests)
    if missing_tests:
        raise SafetyFloorViolation(
            f"monitoring_overrides.{drug_class}: cannot drop baseline monitoring tests {sorted(missing_tests)}"
        )
    if candidate.max_days > baseline.max_days:
        if not candidate.clinician_signed:
            raise SafetyFloorViolation(
                f"monitoring_overrides.{drug_class}: loosening max_days ({baseline.max_days} -> {candidate.max_days}) "
                "requires clinician_signed: true with signed_by and signed_at"
            )
        # Verify signed_by carries a clinician name or valid MCHK reg token when loosening
        signer = candidate.signed_by.strip()
        has_mchk = any(MCHK_REG_RE.match(tok.strip("()[].,")) for tok in signer.split())
        if not has_mchk and not signer.lower().startswith("dr"):
            raise SafetyFloorViolation(
                f"monitoring_overrides.{drug_class}: signed_by ({signer!r}) must name a clinician ('Dr ...') or MCHK reg"
            )


@dataclass(frozen=True, slots=True)
class ClinicKnowledge:
    """Immutable merged knowledge pack for one clinic (knowledge/default + optional clinic overlay)."""

    clinic_id: str
    version: str
    domain: DomainKnowledge
    data_access: DataAccessKnowledge
    routing: RoutingKnowledge
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "clinic_id": self.clinic_id,
            "version": self.version,
            "drug_aliases": sorted(self.domain.drug_aliases.items()),
            "drug_classes": sorted((k, list(v)) for k, v in self.domain.drug_classes.items()),
            "monitoring_rules": sorted(
                (k, [list(r.tests), r.max_days, r.clinician_signed])
                for k, r in self.domain.monitoring_rules.items()
            ),
            "shorthand_glossary": sorted(self.domain.shorthand_glossary.items()),
            "lab_test_synonyms": sorted((k, list(v)) for k, v in self.data_access.lab_test_synonyms.items()),
            "closure_markers": list(self.data_access.closure_markers),
            "document_id_patterns": list(self.data_access.document_id_patterns),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        object.__setattr__(self, "fingerprint", digest)

    # ------------------------------------------------------------------ unified domain helpers
    def resolve_generic(self, name: str) -> str:
        """Resolve brand, Cantonese, or generic name to canonical formulary generic.

        Matches backend/journeys/medication/formulary.py:generic() 1:1 on the default pack.
        """
        n = _norm_ws(name)
        for alias, gen in self.domain.drug_aliases.items():
            if alias in n:
                return gen
        for gens in self.domain.drug_classes.values():
            for g in sorted(gens, key=len, reverse=True):
                if g in n:
                    return g
        return n.split(" ")[0] if n else n

    def resolve_drug_key(self, name: str) -> str:
        """Resolve medication name to longitudinal case-log canonical key.

        Matches backend/case/model.py:drug_key() 1:1 on the default pack (folds hyphens to spaces
        so 'Augmentin' / '安滅菌' -> 'amoxicillin clavulanate').
        """
        raw = str(name or "").strip()
        low = raw.lower()
        for alias, canon in self.domain.drug_aliases.items():
            if alias in low or alias in raw:
                return canon.replace("-", " ")
        tokens = re.findall(r"[a-z]+", low)
        kept = [t for t in tokens if t not in _CASE_KEY_DROP]
        if kept:
            joined = " ".join(kept)
            resolved = self.domain.drug_aliases.get(joined, joined)
            return resolved.replace("-", " ")
        if tokens:
            return " ".join(tokens)
        cjk = re.findall(r"[\u4e00-\u9fff]+", raw)
        return "".join(cjk) if cjk else low

    def drug_class(self, name: str) -> str | None:
        """Return canonical drug class for a medicine name, or None if unmapped."""
        g = self.resolve_generic(name)
        for cls_name, gens in self.domain.drug_classes.items():
            if g in gens:
                return cls_name
        return None

    def monitoring_for(self, name: str) -> tuple[tuple[str, ...], int] | None:
        """Return (tests, max_days) for repeat-prescription monitoring, matching formulary.monitoring_for()."""
        c = self.drug_class(name)
        if c == "anticoagulant" and self.resolve_generic(name) != "warfarin":
            r = self.domain.doac_renal_monitoring
            return (r.tests, r.max_days)
        rule = self.domain.monitoring_rules.get(c) if c else None
        return (rule.tests, rule.max_days) if rule else None

    def mentions(self, text: str) -> list[str]:
        """Generic names mentioned directly in text (by generic, brand, or Cantonese alias)."""
        t = _norm_ws(text)
        out: list[str] = []
        for alias, gen in self.domain.drug_aliases.items():
            if alias in t and gen not in out:
                out.append(gen)
        for gens in self.domain.drug_classes.values():
            for g in gens:
                if re.search(rf"(?<![a-z]){re.escape(g)}(?![a-z])", t) and g not in out:
                    out.append(g)
        return out

    def purposes(self, text: str) -> set[str]:
        """Drug classes implied by patient purpose phrases ('blood pressure', '薄血', etc.)."""
        t = _norm_ws(text)
        out: set[str] = set()
        for phrase, classes in self.domain.purpose_terms.items():
            matched = (
                bool(re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", t))
                if phrase.isascii()
                else phrase in t
            )
            if matched:
                out |= set(classes)
        return out

    def tests_for(self, what: str) -> list[str]:
        """Expand a commitment phrase ('repeat RFT') to canonical lab test names.

        Matches backend/engine/agentic.py:AgenticHarness.tests_for() 1:1 on the default pack.
        """
        low = str(what or "").lower()
        out: list[str] = []
        # Match longer synonym phrases first so 'lipid profile' precedes 'lipid'
        for token in sorted(self.data_access.lab_test_synonyms.keys(), key=len, reverse=True):
            if token in low:
                for test_name in self.data_access.lab_test_synonyms[token]:
                    if test_name not in out:
                        out.append(test_name)
        return out

    def compiled_name_patterns(self) -> tuple[re.Pattern[str], ...]:
        """Return compiled regexes matching printed patient name headers."""
        return tuple(re.compile(p) for p in self.data_access.document_id_patterns)

    def glossary_block(self) -> str:
        """Render a deterministic clinic shorthand glossary prompt block, or '' if empty."""
        if not self.domain.shorthand_glossary:
            return ""
        lines = ["Clinic shorthand glossary (treat as clinic-specific abbreviations only):"]
        for abbr, meaning in sorted(self.domain.shorthand_glossary.items(), key=lambda kv: kv[0].lower()):
            lines.append(f"- {abbr}: {meaning}")
        return "\n".join(lines)

    def enrich_task(self, task: Any) -> Any:
        """Return a Task enriched with clinic shorthand_glossary, shifting Task.fingerprint() deterministically.

        When shorthand_glossary is empty (e.g. knowledge/default/), returns `task` unchanged so
        cached replay fingerprints remain bit-for-bit identical.
        """
        block = self.glossary_block()
        if not block:
            return task
        from ..extract.reader import Task

        if not isinstance(task, Task):
            raise TypeError(f"enrich_task expects backend.extract.reader.Task, got {type(task).__name__}")
        return Task(
            name=task.name,
            version=task.version,
            instructions=f"{task.instructions}\n\n{block}",
            schema=task.schema,
            quoted=dict(task.quoted),
        )
