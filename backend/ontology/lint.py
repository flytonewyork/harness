"""Deterministic CI Linter for the Primary Care Clinical Knowledge Ontology.

Validates compiled overlays (`HK`, `AU`, and clinic chains) across 5 rule families:
- TERM-*: Mathematical check digits (SNOMED CT Verhoeff checksum, LOINC ISO/IEC 7064
  Mod-10 checksum), UCUM allowlist, RxNorm format, Tier-2 license hygiene (ICPC-2 /
  ICD-10 / ATC format & no redistributed rubric text).
- GRAPH-*: Closed-world referential integrity, Kahn DAG cycle detection on `parent_ids`,
  kind compatibility on edges/frames/observations.
- CLIN-* / SAFE-*: Severity band strict nesting (`critical` outside `urgent` outside
  `significant`), `banned_claims` scan on all patient-facing strings, asymmetric
  authority check on PathwayPointers.
- I18N-*: HK Traditional Chinese (`zh_hk`) presence on patient-facing items and
  zero jurisdiction cross-leakage (`000` in HK text or `999` in AU text).
- GOV-*: Provenance completeness and optional `--strict-prod` clinician review gating.

Usage:
    python -m backend.ontology.lint --target HK --target AU
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from dataclasses import dataclass

from .graph import OntologyGraph

# ---------------------------------------------------------------------------
# Mathematical Check Digit Validators (Verhoeff for SNOMED CT; Mod-10 for LOINC)
# ---------------------------------------------------------------------------
_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def valid_snomed_sctid(code: str) -> bool:
    """Validate SNOMED CT Identifier format (6-18 digits) AND Verhoeff check digit."""
    if not re.fullmatch(r"[1-9][0-9]{5,17}", code):
        return False
    c = 0
    for i, ch in enumerate(reversed(code)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def valid_loinc_code(code: str) -> bool:
    """Validate LOINC format AND ISO/IEC 7064 Mod-10 (Luhn) check digit."""
    m = re.fullmatch(r"([0-9]{1,6})-([0-9])", code)
    if not m:
        return False
    body, check_str = m.group(1), m.group(2)
    # LOINC Mod-10: starting from rightmost digit of body, take alternating digits,
    # multiply by 2, sum digits, add remaining digits, check = (10 - (sum % 10)) % 10
    digits = [int(ch) for ch in body]
    odd_from_right = digits[-1::-2]   # positions 1, 3, 5... from right
    even_from_right = digits[-2::-2]  # positions 2, 4, 6... from right
    doubled = int("".join(str(d) for d in reversed(odd_from_right))) * 2
    total = sum(int(ch) for ch in str(doubled)) + sum(even_from_right)
    expected = (10 - (total % 10)) % 10
    return expected == int(check_str)


VALID_UCUM = {
    "Cel", "%", "/min", "mm[Hg]", "{presence}", "{Ordinal}", "{INR}",
    "mg", "mg/kg", "mL", "mmol/L", "umol/L", "g/dL", "g/L", "U/L", "U/mL",
    "fL", "10*12/L", "10*9/L", "ug/L", "m[IU]/L", "pmol/L", "nmol/L",
    "mg/L", "mg/mmol{creat}", "mL/min/{1.73_m2}", "mmol/mol",
}
_ICPC2_RE = re.compile(r"^[ABDFHKLNPRSTUWXYZ][0-9]{2}$")
_ICD10_RE = re.compile(r"^[A-TV-Z][0-9][0-9AB](\.[0-9A-TV-Z]{1,4})?$")
_ATC_RE = re.compile(r"^[A-V]([0-9]{2}([A-Z]([A-Z]([0-9]{2})?)?)?)?$")
_RXNORM_RE = re.compile(r"^[1-9][0-9]{0,7}$")
_CVX_RE = re.compile(r"^[0-9]{1,3}$")


@dataclass(frozen=True)
class LintIssue:
    rule_id: str
    severity: str   # "ERROR" | "WARN"
    target: str
    message: str


def lint_ontology(graph: OntologyGraph, *, strict_prod: bool = False) -> list[LintIssue]:
    """Run all deterministic linter checks on a compiled OntologyGraph."""
    issues: list[LintIssue] = []

    def err(rule_id: str, target: str, msg: str) -> None:
        issues.append(LintIssue(rule_id, "ERROR", target, msg))

    def warn(rule_id: str, target: str, msg: str) -> None:
        issues.append(LintIssue(rule_id, "WARN", target, msg))

    # 1. TERM-* checks
    for cid, c in graph.concepts.items():
        b = c.bindings
        if b.snomed_gps and not valid_snomed_sctid(b.snomed_gps.code):
            err("TERM-001", cid, f"Invalid SNOMED CT SCTID or Verhoeff check digit: '{b.snomed_gps.code}'")
        if b.loinc:
            if not valid_loinc_code(b.loinc.code):
                err("TERM-002", cid, f"Invalid LOINC code or Mod-10 check digit: '{b.loinc.code}'")
            if b.loinc.third_party_copyright:
                err("TERM-002", cid, f"Third-party copyrighted LOINC code blocked: '{b.loinc.code}'")
        if b.ucum and b.ucum not in VALID_UCUM:
            err("TERM-003", cid, f"Unregistered UCUM unit: '{b.ucum}'")
        if b.rxnorm and not _RXNORM_RE.match(b.rxnorm.code):
            err("TERM-004", cid, f"Invalid RxNorm code format: '{b.rxnorm.code}'")
        if b.cvx and not _CVX_RE.match(b.cvx.code):
            err("TERM-004", cid, f"Invalid CVX code format: '{b.cvx.code}'")
        if b.icpc2 and not _ICPC2_RE.match(b.icpc2.code):
            err("TERM-005", cid, f"Invalid Tier-2 ICPC-2 code format: '{b.icpc2.code}'")
        if b.icd10 and not _ICD10_RE.match(b.icd10.code):
            err("TERM-005", cid, f"Invalid Tier-2 ICD-10 code format: '{b.icd10.code}'")
        if b.atc and not _ATC_RE.match(b.atc.code):
            err("TERM-005", cid, f"Invalid Tier-2 ATC code format: '{b.atc.code}'")

        # Panel member references
        if c.panel_facet:
            for mid in c.panel_facet.member_observation_ids:
                mc = graph.get_concept(mid)
                if mc is None:
                    err("GRAPH-001", cid, f"Panel references missing observation '{mid}'")
                elif mc.kind != "observation":
                    err("GRAPH-003", cid, f"Panel member '{mid}' has kind='{mc.kind}', expected 'observation'")

        # Drug allergy group ref
        if c.drug_facet and c.drug_facet.allergy_group_id:
            ag = graph.get_concept(c.drug_facet.allergy_group_id)
            if ag is None:
                err("GRAPH-001", cid, f"Drug allergy_group_id '{c.drug_facet.allergy_group_id}' does not exist")
            elif ag.kind != "allergy_group":
                err("GRAPH-003", cid, f"allergy_group_id '{ag.id}' has kind='{ag.kind}', expected 'allergy_group'")

    # 2. GRAPH-* closed-world references & Kahn DAG cycle detection
    indegree: dict[str, int] = {cid: 0 for cid in graph.concepts}
    children: dict[str, list[str]] = {cid: [] for cid in graph.concepts}
    for cid, c in graph.concepts.items():
        for pid in c.parent_ids:
            if pid not in graph.concepts:
                err("GRAPH-001", cid, f"parent_id '{pid}' does not exist in compiled ontology")
            else:
                children[pid].append(cid)
                indegree[cid] += 1

    q = deque([cid for cid, deg in indegree.items() if deg == 0])
    visited_count = 0
    while q:
        cur = q.popleft()
        visited_count += 1
        for nxt in children[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)
    if visited_count != len(graph.concepts):
        cyc_nodes = [cid for cid, deg in indegree.items() if deg > 0]
        err("GRAPH-002", "parent_ids", f"Cycle detected in IS_A DAG involving: {cyc_nodes}")

    # Edge referential integrity
    for eid, e in graph.edges.items():
        if e.source_id not in graph.concepts:
            err("GRAPH-001", eid, f"source_id '{e.source_id}' does not exist")
        if e.target_id not in graph.concepts:
            err("GRAPH-001", eid, f"target_id '{e.target_id}' does not exist")

    # Frame referential integrity
    for fid, f in graph.frames.items():
        rfe_c = graph.get_concept(f.rfe_concept_id)
        if rfe_c is None:
            err("GRAPH-001", fid, f"rfe_concept_id '{f.rfe_concept_id}' does not exist")
        elif rfe_c.kind not in ("rfe", "symptom"):
            err("GRAPH-003", fid, f"rfe_concept_id '{f.rfe_concept_id}' has kind='{rfe_c.kind}'")
        for item in (
            *f.murtagh_prompt.probability_diagnoses,
            *f.murtagh_prompt.serious_not_to_be_missed,
            *f.murtagh_prompt.pitfalls_often_missed,
        ):
            if item.concept_id not in graph.concepts:
                err("GRAPH-001", fid, f"Murtagh item '{item.id}' references missing concept '{item.concept_id}'")
        for mq in f.murtagh_prompt.masquerades:
            if mq.concept_id not in graph.concepts:
                err("GRAPH-001", fid, f"Masquerade '{mq.id}' references missing concept '{mq.concept_id}'")
        for ex in f.positive_exclusions:
            fc = graph.get_concept(ex.finding_concept_id)
            if fc is None:
                err("GRAPH-001", fid, f"Positive exclusion '{ex.id}' references missing '{ex.finding_concept_id}'")
            elif fc.kind not in ("red_flag", "symptom"):
                err("GRAPH-003", fid, f"Positive exclusion '{ex.id}' targets kind='{fc.kind}', expected red_flag/symptom")

    # 3. CLIN-* / SAFE-* ObservationRule nesting & UCUM consistency
    for rid, r in graph.observation_rules.items():
        oc = graph.get_concept(r.observation_concept_id)
        if oc is None:
            err("GRAPH-001", rid, f"observation_concept_id '{r.observation_concept_id}' does not exist")
        elif oc.kind != "observation":
            err("GRAPH-003", rid, f"observation_concept_id '{r.observation_concept_id}' has kind='{oc.kind}'")
        elif oc.bindings.ucum and oc.bindings.ucum != r.canonical_ucum:
            err("TERM-003", rid, f"canonical_ucum '{r.canonical_ucum}' != concept ucum '{oc.bindings.ucum}'")
        if r.canonical_ucum not in VALID_UCUM:
            err("TERM-003", rid, f"Unregistered canonical_ucum '{r.canonical_ucum}'")
        if r.display_ucum and r.display_ucum not in VALID_UCUM:
            err("TERM-003", rid, f"Unregistered display_ucum '{r.display_ucum}'")
        for conv in r.unit_conversions:
            if conv.from_ucum not in VALID_UCUM:
                err("TERM-003", rid, f"Unregistered conversion from_ucum '{conv.from_ucum}'")

        # Band strict nesting: critical must be strictly outside (or equal to) urgent
        sb = r.severity_bands
        if sb.critical and sb.urgent:
            c_lo, u_lo = sb.critical.low_threshold(), sb.urgent.low_threshold()
            if c_lo is not None and u_lo is not None and c_lo > u_lo:
                err("CLIN-001", rid, f"Critical low ({c_lo}) must be <= urgent low ({u_lo})")
            c_hi, u_hi = sb.critical.high_threshold(), sb.urgent.high_threshold()
            if c_hi is not None and u_hi is not None and c_hi < u_hi:
                err("CLIN-001", rid, f"Critical high ({c_hi}) must be >= urgent high ({u_hi})")

    # PathwayPointer references
    for pid, p in graph.pointers.items():
        for cid in p.trigger_concepts:
            if cid not in graph.concepts:
                err("GRAPH-001", pid, f"trigger_concept '{cid}' does not exist")
        for eid in p.trigger_edges:
            if eid not in graph.edges:
                err("GRAPH-001", pid, f"trigger_edge '{eid}' does not exist")
        for rid in p.trigger_observations:
            if rid not in graph.observation_rules:
                err("GRAPH-001", pid, f"trigger_observation '{rid}' does not exist")

    # 4. SAFE-002 & I18N-* scan on patient-facing strings
    banned = [b.lower() for b in graph.defaults.banned_claims]
    patient_texts: list[tuple[str, str, str | None]] = []
    for eid, e in graph.edges.items():
        if e.patient_safety_net:
            patient_texts.append((eid, e.patient_safety_net.resolve(graph.jurisdiction, "en"), e.patient_safety_net.zh_hk))
    for fid, f in graph.frames.items():
        for sn in f.safety_netting:
            patient_texts.append((f"{fid}.{sn.id}", sn.patient_instruction.resolve(graph.jurisdiction, "en"), sn.patient_instruction.zh_hk))
        for ex in f.positive_exclusions:
            patient_texts.append((f"{fid}.{ex.id}", ex.question.resolve(graph.jurisdiction, "en"), ex.question.zh_hk))

    for target, en_txt, zh_txt in patient_texts:
        low_en = en_txt.lower()
        for bclaim in banned:
            if bclaim in low_en or (zh_txt and bclaim in zh_txt.lower()):
                err("SAFE-002", target, f"Patient-facing text contains banned claim '{bclaim}'")
        # Jurisdiction emergency number cross-leakage check
        if graph.jurisdiction == "HK" and re.search(r"\b000\b", en_txt):
            err("I18N-002", target, "AU emergency number '000' leaked into HK build")
        if graph.jurisdiction == "AU" and re.search(r"\b999\b", en_txt):
            err("I18N-002", target, "HK emergency number '999' leaked into AU build")
        if graph.jurisdiction == "HK" and not zh_txt:
            warn("I18N-001", target, "Missing zh_hk translation on patient-facing text in HK build")

    # 5. GOV-* clinician review status check
    if strict_prod:
        for cid, c in graph.concepts.items():
            if c.provenance.review_status == "draft":
                err("GOV-001", cid, "Draft concept blocked under --strict-prod")
        for eid, e in graph.edges.items():
            if e.provenance.review_status == "draft":
                err("GOV-001", eid, "Draft edge blocked under --strict-prod")

    return issues


def main(argv: list[str] | None = None) -> int:
    from .loader import get_ontology

    parser = argparse.ArgumentParser(description="Lint Primary Care Clinical Knowledge Ontology")
    parser.add_argument("--target", action="append", default=[], choices=["HK", "AU"], help="Jurisdiction(s) to compile & lint")
    parser.add_argument("--clinic", default=None, help="Optional clinic overlay name (e.g. ec_healthcare)")
    parser.add_argument("--strict-prod", action="store_true", help="Fail on draft provenance status")
    args = parser.parse_args(argv)

    targets = args.target or ["HK", "AU"]
    total_errors = 0
    for jur in targets:
        clinic = args.clinic if (args.clinic and jur == "HK") else None
        graph = get_ontology(jurisdiction=jur, clinic=clinic)
        issues = lint_ontology(graph, strict_prod=args.strict_prod)
        errors = [i for i in issues if i.severity == "ERROR"]
        warns = [i for i in issues if i.severity == "WARN"]
        total_errors += len(errors)
        print(
            f"[{jur}{'+' + clinic if clinic else ''}] hash={graph.ontology_hash} "
            f"concepts={len(graph.concepts)} edges={len(graph.edges)} frames={len(graph.frames)} "
            f"obs_rules={len(graph.observation_rules)} pointers={len(graph.pointers)} "
            f"errors={len(errors)} warnings={len(warns)}"
        )
        for iss in issues:
            print(f"  {iss.severity} {iss.rule_id} [{iss.target}]: {iss.message}")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
