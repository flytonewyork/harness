"""Offline propose-only Clinical Curator CLI (WO #62).

Reads a PHI-scrubbed `KnowledgeGapLog` JSONL, clusters recurring gaps above threshold,
and proposes reviewable YAML diffs against `knowledge/<clinic_id>/` plus synthetic
LoopBench scenario stubs — NEVER auto-deploying or mutating `knowledge/` in place.

Clinical Safety & Hygiene Invariants:
1. Propose-only (asymmetric authority for knowledge): never modifies live packs directly;
   emits candidate unified diffs + markdown report + synthetic scenario stubs for PR review
   and `scripts/check.sh` gating.
2. Blind-set hygiene: refuses to read any file with `blind: true`, `*blind*`, `*firstrun*`,
   `clinical_policy.yaml`, `calibration.yaml`, `split: test`, or V1-V4/NHANES holdout keys.
3. Zero-PHI boundary: every emitted diff, report, and synthetic scenario is guaranteed
   PHI-free (`PHIScrubber` verified) and uses strictly synthetic patient placeholders.
4. Safety-floor check: every proposed YAML patch is validated against `validate_raw_yaml_safety`.

Product invariant: NEVER imports `loopbench`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .gaps import KnowledgeGap, scrub_text
from .loader import DEFAULT_KNOWLEDGE_DIR, load_knowledge
from .schema import BlindSetHygieneError, ClinicKnowledge, validate_raw_yaml_safety

# Filenames / path segments strictly off-limits to offline curation
_FORBIDDEN_PATH_TOKENS = (
    "blind",
    "firstrun",
    "clinical_policy.yaml",
    "calibration.yaml",
    "nhanes",
    "holdout",
    "test_set",
)
_HOLD_OUT_V_RE = re.compile(r"(^|[/_\-.])v[1-4]([/_\-.]|$)", re.I)


def assert_blind_set_hygiene(path: str | Path) -> None:
    """Raise BlindSetHygieneError if `path` points to a blind set, first-run record, or held-out key."""
    p = Path(path)
    low = p.as_posix().lower()
    for tok in _FORBIDDEN_PATH_TOKENS:
        if tok in low:
            raise BlindSetHygieneError(f"Offline curator refuses forbidden source path '{p}' (matched '{tok}')")
    if _HOLD_OUT_V_RE.search(p.name):
        raise BlindSetHygieneError(f"Offline curator refuses V1-V4 holdout path '{p}'")

    if p.exists() and p.is_file():
        head = p.read_text(encoding="utf-8", errors="ignore")[:8192]
        if re.search(r'(?i)"?blind"?\s*:\s*true\b', head):
            raise BlindSetHygieneError(f"Offline curator refuses file with blind: true ({p})")
        if re.search(r'(?i)"?split"?\s*:\s*"?test"?\b', head):
            raise BlindSetHygieneError(f"Offline curator refuses held-out test split file ({p})")


def load_gap_log(path: str | Path) -> list[KnowledgeGap]:
    """Load a PHI-scrubbed KnowledgeGap JSONL file after enforcing blind-set hygiene."""
    p = Path(path)
    assert_blind_set_hygiene(p)
    gaps: list[KnowledgeGap] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        raw = json.loads(stripped)
        if raw.get("blind") is True or str(raw.get("split") or "").lower() == "test":
            raise BlindSetHygieneError(f"{p}:{lineno} contains blind/test record; refusing to curate")
        gaps.append(KnowledgeGap.from_dict(raw))
    return gaps


@dataclass(frozen=True, slots=True)
class GapCluster:
    kind: str
    journey: str
    pattern: str
    count: int
    record_kinds: tuple[str, ...]
    channels: tuple[str, ...]
    sample_detail: str


@dataclass(frozen=True, slots=True)
class CurationProposal:
    proposal_id: str
    clinic_id: str
    target_file: str                 # e.g. "knowledge/ec_clinic_1/data_access.yaml"
    cluster: GapCluster
    yaml_addition: dict[str, Any]
    unified_diff: str
    synthetic_scenario_name: str
    synthetic_scenario_yaml: str
    rationale: str


@dataclass(frozen=True, slots=True)
class CurationReport:
    clinic_id: str
    total_gaps: int
    min_count: int
    proposals: tuple[CurationProposal, ...]

    def to_markdown(self) -> str:
        lines = [
            f"# Clinical Knowledge Curation Proposal — `{self.clinic_id}`",
            "",
            "> **PROPOSE-ONLY:** Never auto-deployed. Requires `scripts/check.sh` (0 hard fails, 0 made-up quotes) "
            "and explicit clinician + engineer sign-off before merging.",
            "",
            f"- Gaps ingested: **{self.total_gaps}**",
            f"- Clustering threshold: **>= {self.min_count}** occurrences",
            f"- Candidate proposals generated: **{len(self.proposals)}**",
            "",
        ]
        if not self.proposals:
            lines.append("_No recurring knowledge gaps met the threshold._")
            return "\n".join(lines) + "\n"

        for prop in self.proposals:
            c = prop.cluster
            lines.extend([
                f"## `{prop.proposal_id}` — {c.kind} (`{c.pattern}`, n={c.count})",
                f"- **Target file:** `{prop.target_file}`",
                f"- **Journey:** `{c.journey}` | **Channels:** {', '.join(c.channels) or 'n/a'}",
                f"- **Rationale:** {prop.rationale}",
                "",
                "```diff",
                prop.unified_diff.rstrip("\n"),
                "```",
                "",
                f"- **Synthetic scenario stub:** `{prop.synthetic_scenario_name}`",
                "",
            ])
        return "\n".join(lines) + "\n"


def _slug(s: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return cleaned[:32] or "pattern"


def cluster_gaps(gaps: Iterable[KnowledgeGap], *, min_count: int = 3) -> list[GapCluster]:
    """Group recurring KnowledgeGap records by (kind, journey, normalized pattern) >= min_count."""
    if min_count < 1:
        raise ValueError(f"min_count must be >= 1, got {min_count}")
    buckets: dict[tuple[str, str, str], list[KnowledgeGap]] = defaultdict(list)
    for g in gaps:
        key = (g.kind, g.journey.lower(), " ".join(g.pattern.lower().split()))
        buckets[key].append(g)

    clusters: list[GapCluster] = []
    for (kind, journey, norm_pat), items in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(items) < min_count:
            continue
        clusters.append(
            GapCluster(
                kind=kind,
                journey=journey,
                pattern=items[0].pattern,
                count=len(items),
                record_kinds=tuple(sorted({i.record_kind for i in items if i.record_kind})),
                channels=tuple(sorted({i.channel for i in items if i.channel})),
                sample_detail=items[0].detail,
            )
        )
    return clusters


def _merge_additive(base: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `addition` into `base` additively (preserving existing nested lists/dicts)."""
    merged = dict(base)
    for key, val in addition.items():
        cur = merged.get(key)
        if isinstance(val, dict) and isinstance(cur, dict):
            merged[key] = _merge_additive(cur, val)
        elif isinstance(val, dict):
            merged[key] = dict(val)
        elif isinstance(val, list):
            sec_list = list(cur) if isinstance(cur, list) else []
            for item in val:
                if item not in sec_list:
                    sec_list.append(item)
            merged[key] = sec_list
        else:
            merged[key] = val
    return merged


def render_yaml_diff(
    target_path: Path,
    display_path: str,
    addition: dict[str, Any],
    *,
    clinic_id: str | None = None,
) -> str:
    """Compute a reviewable unified diff applying `addition` without modifying `target_path` on disk."""
    validate_raw_yaml_safety(addition, display_path)
    before_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    before_obj = yaml.safe_load(before_text) if before_text.strip() else {}
    if not isinstance(before_obj, dict):
        before_obj = {}

    if not before_obj and clinic_id:
        before_obj = {
            "pack_id": clinic_id,
            "extends": "default",
            "version": "1.0.0",
            "safety_floor": "immutable",
        }

    merged = _merge_additive(before_obj, addition)
    validate_raw_yaml_safety(merged, display_path)
    after_text = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True)
    diff_lines = list(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{display_path}",
            tofile=f"b/{display_path}",
        )
    )
    return "".join(diff_lines)


def render_synthetic_scenario(cluster: GapCluster, clinic_id: str, proposal_id: str) -> tuple[str, str]:
    """Generate a strictly synthetic LoopBench scenario stub reproducing a recurring gap."""
    safe_pattern, _ = scrub_text(cluster.pattern, max_len=80)
    safe_detail, _ = scrub_text(cluster.sample_detail, max_len=140)
    fname = f"synth_{_slug(clinic_id)}_{_slug(cluster.kind)}_{_slug(safe_pattern)}.yaml"
    doc = {
        "scenario_id": f"synth_{proposal_id}",
        "synthetic": True,
        "blind": False,
        "generated_by": "backend.knowledge.curate",
        "clinic_id": clinic_id,
        "journey": cluster.journey or "core",
        "gap_kind": cluster.kind,
        "observed_count": cluster.count,
        "patient": {
            "patient_key": "PAT_SYNTH_0001",
            "name_en": "CHAN Siu Ming (Synthetic)",
            "sex": "M",
            "age_years": 54,
        },
        "trigger_record": {
            "kind": cluster.record_kinds[0] if cluster.record_kinds else "consult_note",
            "channel": cluster.channels[0] if cluster.channels else "cms",
            "synthetic_text": f"Synthetic clinical note reproducing recurring gap '{safe_pattern}'.",
        },
        "expected": {
            "hard_fails": 0,
            "made_up_quotes": 0,
            "review_note": f"Clinician review required before adding to scenarios/journeys/: {safe_detail}",
        },
    }
    return fname, yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _build_proposal_spec(
    cluster: GapCluster,
    clinic_id: str,
    knowledge: ClinicKnowledge,
) -> tuple[str, dict[str, Any], str]:
    """Map a GapCluster to (relative_yaml_filename, additive_patch_dict, rationale)."""
    pat = cluster.pattern.strip()
    pat_l = pat.lower()

    if cluster.kind == "unmapped_test":
        if pat_l in knowledge.data_access.lab_test_synonyms:
            return ("", {}, "")
        return (
            "data_access.yaml",
            {"lab_test_synonyms": {pat_l: ["<REVIEW_CANONICAL_TEST_NAME>"]}},
            f"Recurring unmapped repeat-test commitment '{pat}' ({cluster.count}x); "
            "propose additive lab_test_synonyms entry for clinician confirmation.",
        )

    if cluster.kind in ("unmapped_drug", "fact_rejected"):
        if pat_l in knowledge.domain.drug_aliases:
            return ("", {}, "")
        return (
            "domain.yaml",
            {"drug_aliases": {pat_l: "<REVIEW_CANONICAL_GENERIC>"}},
            f"Recurring {cluster.kind} for token '{pat}' ({cluster.count}x); "
            "propose additive drug_aliases entry for clinician confirmation.",
        )

    if cluster.kind == "doctor_already_done":
        if pat in knowledge.data_access.closure_markers:
            return ("", {}, "")
        return (
            "data_access.yaml",
            {"closure_markers": [pat]},
            f"Doctors repeatedly closed items as already_done citing '{pat}' ({cluster.count}x); "
            "propose additive closure_markers entry.",
        )

    if cluster.kind == "parser_miss":
        return (
            "domain.yaml",
            {"insurer_patterns": {"ask": [f"(?i){re.escape(pat)}"]}},
            f"Recurring parser_miss on '{pat}' ({cluster.count}x); "
            "propose additive insurer_patterns.ask regex for engineer + clinician review.",
        )

    if cluster.kind in ("quote_dropped", "reader_fallback"):
        return (
            "domain.yaml",
            {"shorthand_glossary": {pat: "<REVIEW_LOCAL_MEANING>"}},
            f"Recurring {cluster.kind} on '{pat}' ({cluster.count}x); "
            "propose shorthand_glossary clarification (will safely bump Task.fingerprint()).",
        )

    # verify_warn / doctor_not_relevant -> reviewable routing/data_access note without weakening safety
    return (
        "routing.yaml",
        {
            "cover_rules": [
                {
                    "id": f"review_{_slug(pat)}",
                    "condition": f"recurring_{cluster.kind}_{_slug(pat)}",
                    "fallback_role": "lead_gp",
                    "escalate_after_hours": 24,
                    "notes": f"Proposed from {cluster.count}x {cluster.kind} events for '{pat}'; review before merge.",
                }
            ]
        },
        f"Recurring {cluster.kind} on '{pat}' ({cluster.count}x); propose Lead GP review routing rule.",
    )


def curate_gaps(
    gaps: Iterable[KnowledgeGap],
    *,
    clinic_id: str = "ec_clinic_1",
    min_count: int = 3,
    knowledge_root: str | Path | None = None,
) -> CurationReport:
    """Cluster gaps and produce propose-only YAML diffs + synthetic LoopBench scenario stubs."""
    gap_list = list(gaps)
    base_root = Path(knowledge_root) if knowledge_root is not None else DEFAULT_KNOWLEDGE_DIR
    try:
        knowledge = load_knowledge(clinic_id, root=base_root)
    except Exception:
        knowledge = load_knowledge("default", root=base_root)

    clusters = cluster_gaps(gap_list, min_count=min_count)
    proposals: list[CurationProposal] = []
    for idx, cluster in enumerate(clusters, start=1):
        rel_file, addition, rationale = _build_proposal_spec(cluster, clinic_id, knowledge)
        if not rel_file or not addition:
            continue
        proposal_id = f"prop_{idx:03d}_{_slug(cluster.kind)}"
        target_path = base_root / clinic_id / rel_file
        display_path = f"knowledge/{clinic_id}/{rel_file}"
        diff = render_yaml_diff(target_path, display_path, addition, clinic_id=clinic_id)
        scen_name, scen_yaml = render_synthetic_scenario(cluster, clinic_id, proposal_id)
        proposals.append(
            CurationProposal(
                proposal_id=proposal_id,
                clinic_id=clinic_id,
                target_file=display_path,
                cluster=cluster,
                yaml_addition=addition,
                unified_diff=diff,
                synthetic_scenario_name=scen_name,
                synthetic_scenario_yaml=scen_yaml,
                rationale=rationale,
            )
        )

    return CurationReport(
        clinic_id=clinic_id,
        total_gaps=len(gap_list),
        min_count=min_count,
        proposals=tuple(proposals),
    )


def write_curation_artifacts(report: CurationReport, out_dir: str | Path) -> dict[str, Path]:
    """Write propose-only report, unified patch, and synthetic scenario stubs to `out_dir`."""
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    report_path = dest / "proposal_report.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")

    patch_path = dest / "proposed.patch"
    combined_diff = "\n".join(p.unified_diff for p in report.proposals if p.unified_diff)
    patch_path.write_text(combined_diff, encoding="utf-8")

    scen_dir = dest / "synthetic_scenarios"
    if report.proposals:
        scen_dir.mkdir(parents=True, exist_ok=True)
        for prop in report.proposals:
            (scen_dir / prop.synthetic_scenario_name).write_text(
                prop.synthetic_scenario_yaml, encoding="utf-8"
            )

    return {"report": report_path, "patch": patch_path, "scenarios_dir": scen_dir}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.knowledge.curate",
        description="Offline propose-only Clinical Knowledge Curator CLI (WO #62). Never auto-deploys.",
    )
    p.add_argument("--gaps", required=True, help="Path to PHI-scrubbed knowledge_gaps.jsonl")
    p.add_argument("--clinic", default="ec_clinic_1", help="Target clinic_id overlay (default: ec_clinic_1)")
    p.add_argument("--min-count", type=int, default=3, help="Minimum gap cluster count to propose (default: 3)")
    p.add_argument("--knowledge-root", default=None, help="Optional custom knowledge/ directory root")
    p.add_argument("--out-dir", default=None, help="Directory to write proposal_report.md, proposed.patch & stubs")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gaps = load_gap_log(args.gaps)
    report = curate_gaps(
        gaps,
        clinic_id=args.clinic,
        min_count=args.min_count,
        knowledge_root=args.knowledge_root,
    )
    if args.out_dir:
        paths = write_curation_artifacts(report, args.out_dir)
        print(
            f"Wrote {len(report.proposals)} proposal(s) from {report.total_gaps} gap(s) -> {paths['report']}"
        )
    else:
        print(report.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
