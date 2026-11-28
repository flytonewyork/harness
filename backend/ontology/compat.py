"""Read-only backward-compatibility adapters bridging `backend/ontology/`
to legacy `harness` tables (`formulary.py`, `rules.py`, `terminology.py`).

Leaves frozen `RulesHarness(version="1.0")` untouched while proving the compiled
ontology can drive every existing deterministic safety/formulary view.
"""
from __future__ import annotations

from typing import Any

from .graph import OntologyGraph


def export_formulary_view(graph: OntologyGraph) -> dict[str, Any]:
    """Export legacy `backend/journeys/medication/formulary.py`-compatible views."""
    classes: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    purpose: dict[str, list[str]] = {}
    high_risk: set[str] = set()
    controlled: set[str] = set()
    short_course: set[str] = set()
    no_gap: set[str] = set()
    monitoring: dict[str, tuple[tuple[str, ...], int]] = {}

    for c in graph.concepts.values():
        if c.kind == "drug_class":
            cname = c.id.split(":", 1)[1]
            classes.setdefault(cname, [])
            if c.drug_facet:
                if c.drug_facet.high_risk:
                    high_risk.add(cname)
                if c.drug_facet.telehealth_blocked:
                    controlled.add(cname)
                if c.drug_facet.short_course:
                    short_course.add(cname)
                if c.drug_facet.no_gap:
                    no_gap.add(cname)
            for pterm in c.purpose_phrases.all_terms():
                purpose.setdefault(pterm.lower(), [])
                if cname not in purpose[pterm.lower()]:
                    purpose[pterm.lower()].append(cname)

    for c in graph.concepts.values():
        if c.kind != "drug":
            continue
        gen = (c.bindings.inn or c.id.split(":", 1)[1]).lower()
        for pid in c.parent_ids:
            pc = graph.get_concept(pid)
            if pc and pc.kind == "drug_class":
                cname = pc.id.split(":", 1)[1]
                classes.setdefault(cname, [])
                if gen not in classes[cname]:
                    classes[cname].append(gen)
        for alias in c.aliases.all_terms():
            al = alias.lower()
            if al != gen:
                aliases[al] = gen
        if c.display.zh_hk:
            aliases[c.display.zh_hk.lower()] = gen

    for e in graph.edges.values():
        if e.relation != "MONITORED_BY" or e.monitoring_spec is None:
            continue
        if e.monitoring_spec.repeat_interval_days is None:
            continue
        src = graph.get_concept(e.source_id)
        tgt = graph.get_concept(e.target_id)
        if not src or not tgt:
            continue
        cname = src.id.split(":", 1)[1]
        if tgt.kind == "panel" and tgt.panel_facet:
            tests = tuple(
                graph.concepts[mid].display.en
                for mid in tgt.panel_facet.member_observation_ids
                if mid in graph.concepts
            )
        else:
            tests = (tgt.display.en,)
        monitoring[cname] = (tests, e.monitoring_spec.repeat_interval_days)

    return {
        "CLASSES": {k: tuple(sorted(v)) for k, v in sorted(classes.items())},
        "ALIASES": aliases,
        "PURPOSE": {k: tuple(v) for k, v in purpose.items()},
        "HIGH_RISK": high_risk,
        "CONTROLLED": controlled,
        "SHORT_COURSE": short_course,
        "NO_GAP": no_gap,
        "MONITORING": monitoring,
    }


def export_lab_bands_view(graph: OntologyGraph) -> dict[str, dict[str, tuple[float | None, float | None]]]:
    """Export legacy `rules.py`-compatible `CRIT` and `URG` lab threshold dictionaries."""
    crit: dict[str, tuple[float | None, float | None]] = {}
    urg: dict[str, tuple[float | None, float | None]] = {}
    for rule in graph.observation_rules.values():
        oc = graph.get_concept(rule.observation_concept_id)
        if oc is None:
            continue
        name = oc.display.en
        if rule.severity_bands.critical:
            crit[name] = (
                rule.severity_bands.critical.low_threshold(),
                rule.severity_bands.critical.high_threshold(),
            )
        if rule.severity_bands.urgent:
            urg[name] = (
                rule.severity_bands.urgent.low_threshold(),
                rule.severity_bands.urgent.high_threshold(),
            )
    return {"CRIT": crit, "URG": urg}
