"""Declarative Clinic Knowledge Pack loader with additive overlay merging.

Loads `knowledge/default/{domain,data_access,routing}.yaml` as the immutable baseline,
then optionally merges `knowledge/<clinic_id>/{domain,data_access,routing}.yaml` strictly
additively while enforcing all clinical safety-floor invariants.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .schema import (
    ClinicKnowledge,
    CoverRule,
    DataAccessKnowledge,
    DomainKnowledge,
    EmrProfile,
    InsurerPatterns,
    KnowledgeError,
    MonitoringRule,
    RoutingKnowledge,
    RoutingTemplate,
    validate_monitoring_override,
    validate_raw_yaml_safety,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


def _read_yaml(path: Path, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise KnowledgeError(f"Required knowledge pack file missing: {path}")
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return validate_raw_yaml_safety(raw, str(path))


def _dedup_tuple(items: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for item in items:
        s = str(item)
        if s and s not in out:
            out.append(s)
    return tuple(out)


def _parse_monitoring_rule(raw: dict[str, Any], label: str) -> MonitoringRule:
    if not isinstance(raw, dict):
        raise KnowledgeError(f"{label}: expected mapping for monitoring rule")
    tests = tuple(str(t).strip() for t in (raw.get("tests") or ()))
    max_days = raw.get("max_days")
    if not isinstance(max_days, int):
        raise KnowledgeError(f"{label}: max_days must be int, got {max_days!r}")
    return MonitoringRule(
        tests=tests,
        max_days=max_days,
        clinician_signed=bool(raw.get("clinician_signed", False)),
        signed_by=str(raw.get("signed_by") or ""),
        signed_at=str(raw.get("signed_at") or ""),
    )


def _load_default_domain(raw: dict[str, Any]) -> DomainKnowledge:
    aliases = {str(k).strip().lower(): str(v).strip().lower() for k, v in (raw.get("drug_aliases") or {}).items()}
    classes = {
        str(k).strip().lower(): tuple(str(g).strip().lower() for g in v)
        for k, v in (raw.get("drug_classes") or {}).items()
    }
    purposes = {
        str(k).strip().lower(): tuple(str(c).strip().lower() for c in v)
        for k, v in (raw.get("purpose_terms") or {}).items()
    }
    monitoring: dict[str, MonitoringRule] = {}
    for cls_name, rule_raw in (raw.get("monitoring_defaults") or {}).items():
        monitoring[str(cls_name).strip().lower()] = _parse_monitoring_rule(
            rule_raw, f"default.domain.monitoring_defaults.{cls_name}"
        )
    doac_raw = raw.get("doac_renal_monitoring")
    if not isinstance(doac_raw, dict):
        raise KnowledgeError("default.domain.yaml missing doac_renal_monitoring")
    doac_rule = _parse_monitoring_rule(doac_raw, "default.domain.doac_renal_monitoring")

    ins_raw = raw.get("insurer_patterns") or {}
    insurer = InsurerPatterns(
        ask=_dedup_tuple(list(ins_raw.get("ask") or ())),
        not_ask=_dedup_tuple(list(ins_raw.get("not_ask") or ())),
        consent=_dedup_tuple(list(ins_raw.get("consent") or ())),
        consent_missing=_dedup_tuple(list(ins_raw.get("consent_missing") or ())),
        consent_given=_dedup_tuple(list(ins_raw.get("consent_given") or ())),
    )
    glossary = {str(k).strip(): str(v).strip() for k, v in (raw.get("shorthand_glossary") or {}).items()}
    review_days = int(raw.get("review_days", 365))
    if review_days <= 0:
        raise KnowledgeError(f"review_days must be positive, got {review_days}")

    return DomainKnowledge(
        drug_aliases=MappingProxyType(aliases),
        drug_classes=MappingProxyType(classes),
        purpose_terms=MappingProxyType(purposes),
        high_risk_classes=frozenset(str(x).strip().lower() for x in (raw.get("high_risk_classes") or ())),
        controlled_classes=frozenset(str(x).strip().lower() for x in (raw.get("controlled_classes") or ())),
        short_course_classes=frozenset(str(x).strip().lower() for x in (raw.get("short_course_classes") or ())),
        no_gap_classes=frozenset(str(x).strip().lower() for x in (raw.get("no_gap_classes") or ())),
        monitoring_rules=MappingProxyType(monitoring),
        doac_renal_monitoring=doac_rule,
        review_days=review_days,
        shorthand_glossary=MappingProxyType(glossary),
        insurer_patterns=insurer,
    )


def _merge_domain(base: DomainKnowledge, overlay: dict[str, Any], clinic_id: str) -> DomainKnowledge:
    if not overlay:
        return base

    # Strictly additive aliases (cannot delete or silently remap a baseline alias to another drug)
    aliases = dict(base.drug_aliases)
    for k, v in (overlay.get("drug_aliases") or {}).items():
        key = str(k).strip().lower()
        val = str(v).strip().lower()
        if key in base.drug_aliases and base.drug_aliases[key] != val:
            from .schema import SafetyFloorViolation

            raise SafetyFloorViolation(
                f"{clinic_id}.domain.drug_aliases.{key}: cannot remap baseline alias "
                f"({base.drug_aliases[key]!r} -> {val!r})"
            )
        aliases[key] = val

    classes = {k: list(v) for k, v in base.drug_classes.items()}
    for k, v in (overlay.get("drug_classes") or {}).items():
        cls_key = str(k).strip().lower()
        existing = classes.setdefault(cls_key, [])
        for g in v:
            gn = str(g).strip().lower()
            if gn not in existing:
                existing.append(gn)

    purposes = {k: list(v) for k, v in base.purpose_terms.items()}
    for k, v in (overlay.get("purpose_terms") or {}).items():
        pkey = str(k).strip().lower()
        existing = purposes.setdefault(pkey, [])
        for c in v:
            cn = str(c).strip().lower()
            if cn not in existing:
                existing.append(cn)

    # Monitoring overrides validated against immutable safety floor
    monitoring = dict(base.monitoring_rules)
    for cls_name, rule_raw in (overlay.get("monitoring_overrides") or {}).items():
        cls_key = str(cls_name).strip().lower()
        cand = _parse_monitoring_rule(rule_raw, f"{clinic_id}.domain.monitoring_overrides.{cls_key}")
        validate_monitoring_override(cls_key, monitoring.get(cls_key), cand)
        monitoring[cls_key] = cand

    doac_rule = base.doac_renal_monitoring
    if overlay.get("doac_renal_monitoring"):
        cand_doac = _parse_monitoring_rule(
            overlay["doac_renal_monitoring"], f"{clinic_id}.domain.doac_renal_monitoring"
        )
        validate_monitoring_override("doac_renal", doac_rule, cand_doac)
        doac_rule = cand_doac

    review_days = base.review_days
    if "review_days" in overlay and overlay["review_days"] is not None:
        cand_review = int(overlay["review_days"])
        if cand_review <= 0 or cand_review > base.review_days:
            raise KnowledgeError(
                f"{clinic_id}: review_days override ({cand_review}) cannot exceed baseline ({base.review_days})"
            )
        review_days = cand_review

    glossary = dict(base.shorthand_glossary)
    for k, v in (overlay.get("shorthand_glossary") or {}).items():
        glossary[str(k).strip()] = str(v).strip()

    ins_ov = overlay.get("insurer_patterns") or {}
    insurer = InsurerPatterns(
        ask=_dedup_tuple(list(base.insurer_patterns.ask) + list(ins_ov.get("ask") or ())),
        not_ask=_dedup_tuple(list(base.insurer_patterns.not_ask) + list(ins_ov.get("not_ask") or ())),
        consent=_dedup_tuple(list(base.insurer_patterns.consent) + list(ins_ov.get("consent") or ())),
        consent_missing=_dedup_tuple(
            list(base.insurer_patterns.consent_missing) + list(ins_ov.get("consent_missing") or ())
        ),
        consent_given=_dedup_tuple(
            list(base.insurer_patterns.consent_given) + list(ins_ov.get("consent_given") or ())
        ),
    )

    return DomainKnowledge(
        drug_aliases=MappingProxyType(aliases),
        drug_classes=MappingProxyType({k: tuple(v) for k, v in classes.items()}),
        purpose_terms=MappingProxyType({k: tuple(v) for k, v in purposes.items()}),
        high_risk_classes=base.high_risk_classes
        | frozenset(str(x).strip().lower() for x in (overlay.get("high_risk_classes") or ())),
        controlled_classes=base.controlled_classes
        | frozenset(str(x).strip().lower() for x in (overlay.get("controlled_classes") or ())),
        short_course_classes=base.short_course_classes
        | frozenset(str(x).strip().lower() for x in (overlay.get("short_course_classes") or ())),
        no_gap_classes=base.no_gap_classes
        | frozenset(str(x).strip().lower() for x in (overlay.get("no_gap_classes") or ())),
        monitoring_rules=MappingProxyType(monitoring),
        doac_renal_monitoring=doac_rule,
        review_days=review_days,
        shorthand_glossary=MappingProxyType(glossary),
        insurer_patterns=insurer,
    )


def _parse_emr_profile(raw: dict[str, Any] | None, base: EmrProfile | None = None) -> EmrProfile:
    b = base or EmrProfile()
    r = raw or {}
    return EmrProfile(
        vendor=str(r.get("vendor", b.vendor)),
        structured_labs=bool(r.get("structured_labs", b.structured_labs)),
        attachment_labs=bool(r.get("attachment_labs", b.attachment_labs)),
        ehealth_pull_available=bool(r.get("ehealth_pull_available", b.ehealth_pull_available)),
        scan_lag_hours=int(r.get("scan_lag_hours", b.scan_lag_hours)),
        courier_lag_hours=int(r.get("courier_lag_hours", b.courier_lag_hours)),
        notes=str(r.get("notes", b.notes)),
    )


def _load_default_data_access(raw: dict[str, Any]) -> DataAccessKnowledge:
    synonyms = {
        str(k).strip().lower(): tuple(str(t) for t in v)
        for k, v in (raw.get("lab_test_synonyms") or {}).items()
    }
    return DataAccessKnowledge(
        emr_profile=_parse_emr_profile(raw.get("emr_profile")),
        lab_test_synonyms=MappingProxyType(synonyms),
        closure_markers=_dedup_tuple(list(raw.get("closure_markers") or ())),
        document_id_patterns=_dedup_tuple(list(raw.get("document_id_patterns") or ())),
    )


def _merge_data_access(base: DataAccessKnowledge, overlay: dict[str, Any]) -> DataAccessKnowledge:
    if not overlay:
        return base
    synonyms = {k: list(v) for k, v in base.lab_test_synonyms.items()}
    for k, v in (overlay.get("lab_test_synonyms") or {}).items():
        key = str(k).strip().lower()
        existing = synonyms.setdefault(key, [])
        for t in v:
            ts = str(t)
            if ts not in existing:
                existing.append(ts)
    return DataAccessKnowledge(
        emr_profile=_parse_emr_profile(overlay.get("emr_profile"), base.emr_profile),
        lab_test_synonyms=MappingProxyType({k: tuple(v) for k, v in synonyms.items()}),
        closure_markers=_dedup_tuple(list(base.closure_markers) + list(overlay.get("closure_markers") or ())),
        document_id_patterns=_dedup_tuple(
            list(base.document_id_patterns) + list(overlay.get("document_id_patterns") or ())
        ),
    )


def _parse_cover_rules(raw_list: list[dict[str, Any]]) -> tuple[CoverRule, ...]:
    return tuple(
        CoverRule(
            id=str(r["id"]),
            condition=str(r.get("condition") or ""),
            fallback_role=str(r["fallback_role"]),
            escalate_after_hours=int(r["escalate_after_hours"]),
            notes=str(r.get("notes") or ""),
        )
        for r in raw_list
    )


def _parse_templates(raw_list: list[dict[str, Any]]) -> tuple[RoutingTemplate, ...]:
    return tuple(
        RoutingTemplate(
            id=str(r["id"]),
            channel=str(r.get("channel") or "whatsapp"),
            lang=str(r.get("lang") or "en"),
            doctor_sent_only=bool(r.get("doctor_sent_only", False)),
            body=str(r.get("body") or ""),
        )
        for r in raw_list
    )


def _load_default_routing(raw: dict[str, Any]) -> RoutingKnowledge:
    role_split = raw.get("role_split") or {}
    return RoutingKnowledge(
        cover_rules=_parse_cover_rules(list(raw.get("cover_rules") or ())),
        doctor_only_actions=frozenset(str(x).strip() for x in (role_split.get("doctor_only_actions") or ())),
        front_desk_actions=frozenset(str(x).strip() for x in (role_split.get("front_desk_actions") or ())),
        templates=_parse_templates(list(raw.get("templates") or ())),
    )


def _merge_routing(base: RoutingKnowledge, overlay: dict[str, Any]) -> RoutingKnowledge:
    if not overlay:
        return base
    rules_by_id = {r.id: r for r in base.cover_rules}
    for r in _parse_cover_rules(list(overlay.get("cover_rules") or ())):
        rules_by_id[r.id] = r
    role_split = overlay.get("role_split") or {}
    templates_by_id = {t.id: t for t in base.templates}
    for t in _parse_templates(list(overlay.get("templates") or ())):
        templates_by_id[t.id] = t
    return RoutingKnowledge(
        cover_rules=tuple(rules_by_id.values()),
        doctor_only_actions=base.doctor_only_actions
        | frozenset(str(x).strip() for x in (role_split.get("doctor_only_actions") or ())),
        front_desk_actions=base.front_desk_actions
        | frozenset(str(x).strip() for x in (role_split.get("front_desk_actions") or ())),
        templates=tuple(templates_by_id.values()),
    )


def list_clinics(root: str | Path | None = None) -> list[str]:
    """Return sorted clinic_id directories present under knowledge/."""
    base_dir = Path(root) if root is not None else DEFAULT_KNOWLEDGE_DIR
    if not base_dir.exists():
        return []
    return sorted(
        p.name for p in base_dir.iterdir()
        if p.is_dir() and not p.name.startswith((".", "_"))
    )


def load_knowledge(clinic_id: str = "default", root: str | Path | None = None) -> ClinicKnowledge:
    """Load `knowledge/default/` and additively merge `knowledge/<clinic_id>/` when clinic_id != 'default'."""
    cid = str(clinic_id or "default").strip()
    if not cid or "/" in cid or "\\" in cid or ".." in cid:
        raise KnowledgeError(f"Invalid clinic_id: {clinic_id!r}")

    base_dir = Path(root) if root is not None else DEFAULT_KNOWLEDGE_DIR
    default_dir = base_dir / "default"
    if not default_dir.is_dir():
        raise KnowledgeError(f"Baseline knowledge directory missing: {default_dir}")

    def_domain_raw = _read_yaml(default_dir / "domain.yaml", required=True)
    def_access_raw = _read_yaml(default_dir / "data_access.yaml", required=True)
    def_routing_raw = _read_yaml(default_dir / "routing.yaml", required=True)

    domain = _load_default_domain(def_domain_raw)
    data_access = _load_default_data_access(def_access_raw)
    routing = _load_default_routing(def_routing_raw)
    version = str(def_domain_raw.get("version") or "1.0.0")

    if cid != "default":
        clinic_dir = base_dir / cid
        if not clinic_dir.is_dir():
            raise KnowledgeError(f"Unknown clinic knowledge pack '{cid}' under {base_dir}")
        ov_domain = _read_yaml(clinic_dir / "domain.yaml", required=False)
        ov_access = _read_yaml(clinic_dir / "data_access.yaml", required=False)
        ov_routing = _read_yaml(clinic_dir / "routing.yaml", required=False)

        domain = _merge_domain(domain, ov_domain, cid)
        data_access = _merge_data_access(data_access, ov_access)
        routing = _merge_routing(routing, ov_routing)
        version = str(ov_domain.get("version") or ov_access.get("version") or version)

    return ClinicKnowledge(
        clinic_id=cid,
        version=version,
        domain=domain,
        data_access=data_access,
        routing=routing,
    )


@lru_cache(maxsize=8)
def default_knowledge() -> ClinicKnowledge:
    """Cached product baseline knowledge pack (`knowledge/default/`)."""
    return load_knowledge("default")
