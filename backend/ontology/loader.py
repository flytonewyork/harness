"""Cached YAML Loader for the Primary Care Clinical Knowledge Ontology."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .graph import OntologyGraph
from .overlay import compile_ontology
from .schema import (
    ClinicalEdge,
    Concept,
    CoreBundle,
    JurisdictionCode,
    ObservationRule,
    OverlayPatch,
    PathwayPointer,
    PresentationFrame,
)

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def load_core_bundle(data_dir: Path = DATA_DIR) -> CoreBundle:
    """Load all declarative Core YAML packs under `data/core/`."""
    core_dir = data_dir / "core"
    concepts: list[Concept] = []
    edges: list[ClinicalEdge] = []
    frames: list[PresentationFrame] = []
    obs_rules: list[ObservationRule] = []
    pointers: list[PathwayPointer] = []

    for yml_path in sorted(core_dir.glob("*.yaml")):
        raw = _load_yaml(yml_path)
        for item in raw.get("concepts") or ():
            concepts.append(Concept.model_validate(item))
        for item in raw.get("edges") or ():
            edges.append(ClinicalEdge.model_validate(item))
        for item in raw.get("frames") or ():
            frames.append(PresentationFrame.model_validate(item))
        for item in raw.get("observation_rules") or ():
            obs_rules.append(ObservationRule.model_validate(item))
        for item in raw.get("pointers") or ():
            pointers.append(PathwayPointer.model_validate(item))

    return CoreBundle(
        concepts=tuple(concepts),
        edges=tuple(edges),
        frames=tuple(frames),
        observation_rules=tuple(obs_rules),
        pointers=tuple(pointers),
    )


def load_overlay(path: Path) -> OverlayPatch:
    return OverlayPatch.model_validate(_load_yaml(path))


@lru_cache(maxsize=16)
def get_ontology(
    jurisdiction: JurisdictionCode = "HK",
    clinic: str | None = None,
) -> OntologyGraph:
    """Return the cached, compiled, immutable OntologyGraph for `HK` or `AU` (+ optional clinic overlay)."""
    core = load_core_bundle(DATA_DIR)
    ov_dir = DATA_DIR / "overlays"
    overlays: list[OverlayPatch] = [load_overlay(ov_dir / f"{jurisdiction.lower()}.yaml")]
    if clinic:
        overlays.append(load_overlay(ov_dir / f"clinic_{clinic.lower()}.yaml"))
    compiled = compile_ontology(core, tuple(overlays))
    return OntologyGraph(compiled)
