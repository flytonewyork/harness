"""Patient journeys: plug-ins that teach the engine one more part of the clinic's follow-up work.

Each journey is a package `backend/journeys/<name>/` that exports:

- `JOURNEY`: a cooperative mixin class over the engine (like `AgenticHarness`). It may override `reset`, `on_cms`,
  `on_portal` and `tick`, and must call `super()` first in each, so journeys compose and the core engine always
  sees every record. A journey never suppresses or rewrites what the core engine does.
- `FAMILIES`: {family: one-line description} for the item families it opens.
- `NAME` and `SUMMARY`: the journey's id and one line for the eval report.

A journey uses only the engine port (`backend/engine/ports.py`): it reads, and it writes only with open_item,
raise_urgency, alarm and attest_all_clear. It never closes, dismisses, downgrades or reassures, and it never
messages a patient (patient messaging is the care line's job; a journey may suggest a fixed template in the
item's request step for the doctor to send). Every item carries a reasoning trace whose quotes are verbatim.

`suite(*names)` builds one engine with the named journeys (all of them by default) on top of the agentic harness.
"""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from ..engine.ports import FAMILIES as CORE_FAMILIES

_HERE = Path(__file__).resolve().parent


def names() -> list[str]:
    """Journey packages present in this checkout, in a stable order."""
    return sorted(m.name for m in pkgutil.iter_modules([str(_HERE)]) if m.ispkg and not m.name.startswith("_"))


def load(name: str):
    mod = importlib.import_module(f"{__name__}.{name}")
    for attr in ("JOURNEY", "FAMILIES"):
        if not hasattr(mod, attr):
            raise ImportError(f"journey {name} must export {attr}")
    return mod


def all_families() -> tuple[str, ...]:
    out = list(CORE_FAMILIES)
    for n in names():
        out += [f for f in load(n).FAMILIES if f not in out]
    return tuple(out)


class Push:
    """Always first in a suite: the care line delivers patient messages as they arrive, so the host may call
    push(api) between the engine's scheduled runs. It reads only what has newly landed in the clinic system and
    hands each record to on_cms (core and every journey); sweeps, escalation and silence checks stay on tick."""

    def push(self, api):
        recs, self.cur["cms"] = api.cms_since(self.cur["cms"])
        for r in recs:
            self.arrivals[r["channel"]].append(api.now)
            self.on_cms(api, r)


def suite(*wanted: str, base=None, **kw):
    """One engine with the named journeys (default: all) layered over `base` (default: the agentic harness on
    the regex extractor). Keyword arguments go to the base class."""
    if base is None:
        from ..engine.agentic import AgenticHarness
        from ..extract import Extractor, RulesModel
        base = AgenticHarness
        kw.setdefault("extractor", Extractor(RulesModel()))
    chosen = list(wanted) or names()
    mixins = tuple(load(n).JOURNEY for n in chosen)
    cls = type("Suite", (Push, *mixins, base), {})
    eng = cls(**kw)
    eng.journeys = chosen
    eng.name = ("journey:" + chosen[0]) if len(chosen) == 1 and wanted else "suite"
    return eng
