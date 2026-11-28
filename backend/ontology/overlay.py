"""Deterministic 3-Tier Overlay Compiler (Core -> Jurisdiction [HK|AU] -> Clinic).

Merge Semantics:
- Mappings: RFC-7396 recursive key-wise deep merge.
- Scalars: overwrite; explicit `{"$unset": true}` required to clear an optional field
  (bare `null` is rejected to prevent accidental erasure).
- Primitive lists: explicit `{"$add": [...], "$remove": [...]}` or `{"$replace": [...]}`
  (bare list in a patch is rejected to prevent silent wiping of Core aliases/safety items).
- Keyed object lists (items with `id`): explicit `{"$upsert_by_id": [...], "$remove_by_id": [...]}`.

Monotonic Safety Guard:
- Overlays may freely ESCALATE safety (tighter thresholds, higher urgency_floor,
  higher severity, additional mandatory positive exclusions).
- Any overlay RELAXING a critical/urgent band, lowering `urgency_floor`, lowering
  `severity` from `hard_block`/`critical`, or removing a `mandatory: true` exclusion
  MUST carry an explicit `SafetyOverrideApproval` targeting that path — otherwise
  compilation raises `SafetyMonotonicityError`.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .schema import (
    ClinicalEdge,
    Concept,
    CoreBundle,
    JurisdictionDefaults,
    NumericBound,
    ObservationRule,
    OverlayPatch,
    PathwayPointer,
    PresentationFrame,
)

SEVERITY_RANK = {
    "info": 0,
    "advisory": 1,
    "moderate": 2,
    "high": 3,
    "critical": 4,
    "hard_block": 5,
}

PATCH_DIRECTIVES = {"$add", "$remove", "$replace", "$upsert_by_id", "$remove_by_id", "$unset"}


class OverlayMergeError(ValueError):
    """Raised when an overlay patch uses forbidden bare lists, bare null wipes, or bad IDs."""


class SafetyMonotonicityError(ValueError):
    """Raised when an overlay weakens a clinical safety invariant without clinician approval."""


@dataclass(frozen=True)
class CompiledBundle:
    jurisdiction: str
    overlay_chain: tuple[str, ...]
    defaults: JurisdictionDefaults
    concepts: dict[str, Concept]
    edges: dict[str, ClinicalEdge]
    frames: dict[str, PresentationFrame]
    observation_rules: dict[str, ObservationRule]
    pointers: dict[str, PathwayPointer]
    field_provenance: dict[str, str]


def apply_patch_dict(
    base: dict[str, Any],
    patch: dict[str, Any],
    *,
    path: str,
    layer_id: str,
    field_provenance: dict[str, str],
) -> dict[str, Any]:
    """Deterministically apply a structured patch dictionary onto a base dictionary."""
    out = copy.deepcopy(base)
    for key, pval in patch.items():
        fpath = f"{path}.{key}"
        if pval is None:
            raise OverlayMergeError(
                f"[{layer_id}] Bare null at '{fpath}' is forbidden; use {{\"$unset\": true}} explicitly."
            )
        if isinstance(pval, list):
            raise OverlayMergeError(
                f"[{layer_id}] Bare list at '{fpath}' is forbidden; use $add/$remove/$replace or $upsert_by_id."
            )
        if isinstance(pval, dict) and any(k in PATCH_DIRECTIVES for k in pval):
            unknown = set(pval.keys()) - PATCH_DIRECTIVES
            if unknown:
                raise OverlayMergeError(f"[{layer_id}] Mixed patch directives and keys {unknown} at '{fpath}'.")
            if pval.get("$unset"):
                out[key] = None
            elif "$replace" in pval:
                out[key] = copy.deepcopy(pval["$replace"])
            elif "$add" in pval or "$remove" in pval:
                existing = list(out.get(key) or [])
                to_remove = set(pval.get("$remove") or [])
                merged = [x for x in existing if x not in to_remove]
                for item in pval.get("$add") or []:
                    if item not in merged:
                        merged.append(item)
                out[key] = merged
            elif "$upsert_by_id" in pval or "$remove_by_id" in pval:
                existing_list = list(out.get(key) or [])
                by_id: dict[str, dict[str, Any]] = {}
                order: list[str] = []
                for elem in existing_list:
                    eid = elem["id"]
                    by_id[eid] = copy.deepcopy(elem)
                    order.append(eid)
                for rid in pval.get("$remove_by_id") or []:
                    if rid in by_id:
                        del by_id[rid]
                        order = [x for x in order if x != rid]
                for item in pval.get("$upsert_by_id") or []:
                    if not isinstance(item, dict) or "id" not in item:
                        raise OverlayMergeError(f"[{layer_id}] $upsert_by_id item at '{fpath}' requires 'id'.")
                    iid = item["id"]
                    if iid in by_id:
                        by_id[iid] = apply_patch_dict(
                            by_id[iid],
                            item,
                            path=f"{fpath}[id={iid}]",
                            layer_id=layer_id,
                            field_provenance=field_provenance,
                        )
                    else:
                        by_id[iid] = copy.deepcopy(item)
                        order.append(iid)
                out[key] = [by_id[eid] for eid in order]
            field_provenance[fpath] = layer_id
        elif key != "when" and isinstance(pval, dict) and isinstance(out.get(key), dict):
            out[key] = apply_patch_dict(
                out[key],
                pval,
                path=fpath,
                layer_id=layer_id,
                field_provenance=field_provenance,
            )
        else:
            out[key] = copy.deepcopy(pval)
            field_provenance[fpath] = layer_id
    return out


def _approved_targets(overlay: OverlayPatch) -> set[str]:
    targets: set[str] = set()
    for appr in overlay.safety_override_approvals:
        targets.update(appr.target_paths)
    return targets


def _check_bound_monotonicity(
    rule_id: str,
    band_name: str,
    base_b: NumericBound | None,
    new_b: NumericBound | None,
    approved: set[str],
    layer_id: str,
) -> None:
    path = f"{rule_id}.severity_bands.{band_name}"
    if base_b is None:
        return
    if new_b is None:
        if path not in approved:
            raise SafetyMonotonicityError(
                f"[{layer_id}] Removing {path} requires explicit SafetyOverrideApproval."
            )
        return
    # Low bound relaxation: moving threshold lower (e.g. K+ < 2.8 -> K+ < 2.5) is LESS safe
    b_lo, n_lo = base_b.low_threshold(), new_b.low_threshold()
    if b_lo is not None and (n_lo is None or n_lo < b_lo):
        if path not in approved:
            raise SafetyMonotonicityError(
                f"[{layer_id}] Relaxing low threshold on {path} ({b_lo} -> {n_lo}) requires SafetyOverrideApproval."
            )
    # High bound relaxation: moving threshold higher (e.g. K+ > 6.5 -> K+ > 7.0) is LESS safe
    b_hi, n_hi = base_b.high_threshold(), new_b.high_threshold()
    if b_hi is not None and (n_hi is None or n_hi > b_hi):
        if path not in approved:
            raise SafetyMonotonicityError(
                f"[{layer_id}] Relaxing high threshold on {path} ({b_hi} -> {n_hi}) requires SafetyOverrideApproval."
            )


def _check_monotonic_safety(
    *,
    overlay: OverlayPatch,
    base_edges: dict[str, ClinicalEdge],
    new_edges: dict[str, ClinicalEdge],
    base_frames: dict[str, PresentationFrame],
    new_frames: dict[str, PresentationFrame],
    base_obs: dict[str, ObservationRule],
    new_obs: dict[str, ObservationRule],
) -> None:
    approved = _approved_targets(overlay)
    lid = overlay.overlay_id

    for eid, base_e in base_edges.items():
        new_e = new_edges[eid]
        if base_e.urgency_floor is not None:
            if new_e.urgency_floor is None or new_e.urgency_floor < base_e.urgency_floor:
                p = f"{eid}.urgency_floor"
                if p not in approved:
                    raise SafetyMonotonicityError(
                        f"[{lid}] Lowering {p} ({base_e.urgency_floor} -> {new_e.urgency_floor}) "
                        f"requires SafetyOverrideApproval."
                    )
        if SEVERITY_RANK[base_e.severity] >= SEVERITY_RANK["critical"] and (
            SEVERITY_RANK[new_e.severity] < SEVERITY_RANK[base_e.severity]
        ):
            p = f"{eid}.severity"
            if p not in approved:
                raise SafetyMonotonicityError(
                    f"[{lid}] Lowering {p} ({base_e.severity} -> {new_e.severity}) "
                    f"requires SafetyOverrideApproval."
                )

    for fid, base_f in base_frames.items():
        new_f = new_frames[fid]
        new_by_id = {item.id: item for item in new_f.positive_exclusions}
        for item in base_f.positive_exclusions:
            if not item.mandatory:
                continue
            p = f"{fid}.positive_exclusions.{item.id}"
            if item.id not in new_by_id or not new_by_id[item.id].mandatory:
                if p not in approved:
                    raise SafetyMonotonicityError(
                        f"[{lid}] Removing or downgrading mandatory positive exclusion {p} "
                        f"requires SafetyOverrideApproval."
                    )

    for rid, base_r in base_obs.items():
        new_r = new_obs[rid]
        _check_bound_monotonicity(rid, "critical", base_r.severity_bands.critical, new_r.severity_bands.critical, approved, lid)
        _check_bound_monotonicity(rid, "urgent", base_r.severity_bands.urgent, new_r.severity_bands.urgent, approved, lid)


def compile_ontology(core: CoreBundle, overlays: tuple[OverlayPatch, ...]) -> CompiledBundle:
    """Compile Core + ordered Overlays into a validated, provenance-tracked CompiledBundle."""
    concepts = {c.id: c for c in core.concepts}
    edges = {e.id: e for e in core.edges}
    frames = {f.id: f for f in core.frames}
    obs_rules = {r.id: r for r in core.observation_rules}
    pointers = {p.id: p for p in core.pointers}

    field_provenance: dict[str, str] = {}
    for cid in concepts:
        field_provenance[cid] = "core"
    for eid in edges:
        field_provenance[eid] = "core"
    for fid in frames:
        field_provenance[fid] = "core"
    for rid in obs_rules:
        field_provenance[rid] = "core"
    for pid in pointers:
        field_provenance[pid] = "core"

    defaults: JurisdictionDefaults | None = None
    jurisdiction = "HK"
    chain: list[str] = ["core"]

    for ov in overlays:
        expected_parent = chain[-1]
        if ov.extends != expected_parent:
            raise OverlayMergeError(
                f"Overlay '{ov.overlay_id}' declares extends='{ov.extends}', expected '{expected_parent}'."
            )
        jurisdiction = ov.jurisdiction
        if ov.defaults is not None:
            defaults = ov.defaults
        elif ov.layer == "jurisdiction" and defaults is None:
            raise OverlayMergeError(f"Jurisdiction overlay '{ov.overlay_id}' must define 'defaults'.")

        prev_edges = dict(edges)
        prev_frames = dict(frames)
        prev_obs = dict(obs_rules)

        # Apply patches onto existing entities
        for cid, patch in ov.concept_patches.items():
            if cid not in concepts:
                raise OverlayMergeError(f"[{ov.overlay_id}] concept_patches target '{cid}' does not exist.")
            merged = apply_patch_dict(
                concepts[cid].model_dump(mode="python", by_alias=True),
                patch,
                path=cid,
                layer_id=ov.overlay_id,
                field_provenance=field_provenance,
            )
            concepts[cid] = Concept.model_validate(merged)

        for eid, patch in ov.edge_patches.items():
            if eid not in edges:
                raise OverlayMergeError(f"[{ov.overlay_id}] edge_patches target '{eid}' does not exist.")
            merged = apply_patch_dict(
                edges[eid].model_dump(mode="python", by_alias=True),
                patch,
                path=eid,
                layer_id=ov.overlay_id,
                field_provenance=field_provenance,
            )
            edges[eid] = ClinicalEdge.model_validate(merged)

        for fid, patch in ov.frame_patches.items():
            if fid not in frames:
                raise OverlayMergeError(f"[{ov.overlay_id}] frame_patches target '{fid}' does not exist.")
            merged = apply_patch_dict(
                frames[fid].model_dump(mode="python", by_alias=True),
                patch,
                path=fid,
                layer_id=ov.overlay_id,
                field_provenance=field_provenance,
            )
            frames[fid] = PresentationFrame.model_validate(merged)

        for rid, patch in ov.observation_patches.items():
            if rid not in obs_rules:
                raise OverlayMergeError(f"[{ov.overlay_id}] observation_patches target '{rid}' does not exist.")
            merged = apply_patch_dict(
                obs_rules[rid].model_dump(mode="python", by_alias=True),
                patch,
                path=rid,
                layer_id=ov.overlay_id,
                field_provenance=field_provenance,
            )
            obs_rules[rid] = ObservationRule.model_validate(merged)

        for pid, patch in ov.pointer_patches.items():
            if pid not in pointers:
                raise OverlayMergeError(f"[{ov.overlay_id}] pointer_patches target '{pid}' does not exist.")
            merged = apply_patch_dict(
                pointers[pid].model_dump(mode="python", by_alias=True),
                patch,
                path=pid,
                layer_id=ov.overlay_id,
                field_provenance=field_provenance,
            )
            pointers[pid] = PathwayPointer.model_validate(merged)

        # Apply jurisdiction display_ucum overrides automatically (e.g. AU Haemoglobin -> g/L)
        if ov.defaults and ov.defaults.unit_system_overrides:
            for obs_cid, display_unit in ov.defaults.unit_system_overrides.items():
                for rid, rule in list(obs_rules.items()):
                    if rule.observation_concept_id == obs_cid:
                        updated = rule.model_copy(update={"display_ucum": display_unit})
                        obs_rules[rid] = updated
                        field_provenance[f"{rid}.display_ucum"] = ov.overlay_id

        # Enforce Monotonic Safety Guard BEFORE accepting local additions
        _check_monotonic_safety(
            overlay=ov,
            base_edges=prev_edges,
            new_edges=edges,
            base_frames=prev_frames,
            new_frames=frames,
            base_obs=prev_obs,
            new_obs=obs_rules,
        )

        # Add purely local entities
        for c in ov.add_concepts:
            if c.id in concepts:
                raise OverlayMergeError(f"[{ov.overlay_id}] add_concepts duplicate ID '{c.id}'.")
            concepts[c.id] = c
            field_provenance[c.id] = ov.overlay_id
        for e in ov.add_edges:
            if e.id in edges:
                raise OverlayMergeError(f"[{ov.overlay_id}] add_edges duplicate ID '{e.id}'.")
            edges[e.id] = e
            field_provenance[e.id] = ov.overlay_id
        for f in ov.add_frames:
            if f.id in frames:
                raise OverlayMergeError(f"[{ov.overlay_id}] add_frames duplicate ID '{f.id}'.")
            frames[f.id] = f
            field_provenance[f.id] = ov.overlay_id
        for r in ov.add_observation_rules:
            if r.id in obs_rules:
                raise OverlayMergeError(f"[{ov.overlay_id}] add_observation_rules duplicate ID '{r.id}'.")
            obs_rules[r.id] = r
            field_provenance[r.id] = ov.overlay_id
        for p in ov.add_pointers:
            if p.id in pointers:
                raise OverlayMergeError(f"[{ov.overlay_id}] add_pointers duplicate ID '{p.id}'.")
            pointers[p.id] = p
            field_provenance[p.id] = ov.overlay_id

        chain.append(ov.overlay_id)

    if defaults is None:
        raise OverlayMergeError("Compiled ontology must include a jurisdiction overlay providing defaults.")

    return CompiledBundle(
        jurisdiction=jurisdiction,
        overlay_chain=tuple(chain),
        defaults=defaults,
        concepts=concepts,
        edges=edges,
        frames=frames,
        observation_rules=obs_rules,
        pointers=pointers,
        field_provenance=field_provenance,
    )
