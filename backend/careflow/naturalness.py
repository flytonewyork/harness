"""Naturalness eval for patient-visible care-flow text. Synthetic fixtures only. No model is called.

This is not a send-time judge and not the later dual outbound judge. It looks at one outbound string
and says whether a patient would see a reasoning trace, a guardrail, or a template id in it.
"""
from __future__ import annotations

import re

TEMPLATE_ID = re.compile(r"\b(?:fixed|ask|prep)\.[a-z_]+|careflow-fixed-v\d+|template_id", re.I)
SURFACED = re.compile(r"guardrail|reasoning trace|\bstep:\s*\w+|hard fail|clinical_closure", re.I)


def _pieces(reasoning) -> list[str]:
    if reasoning is None:
        return []
    if isinstance(reasoning, str):
        return [reasoning.strip()]
    out = []
    for step in reasoning:
        if isinstance(step, str):
            out.append(step.strip())
        elif isinstance(step, dict):
            out.append(str(step.get("text") or "").strip())
            out.append(str(step.get("step") or "").strip())
    return [p for p in out if p]


def naturalness(text: str, lang: str, reasoning=None) -> dict:
    """Pass or fail for one synthetic outbound. `lang` is `en` or `zh`. No network."""
    fails = []
    if lang not in ("en", "zh"):
        fails.append("lang")
    if TEMPLATE_ID.search(text):
        fails.append("template_id")
    if SURFACED.search(text):
        fails.append("guardrail")
    for piece in _pieces(reasoning):
        if len(piece) >= 12 and piece in text:
            fails.append("reasoning")
            break
    return {"pass": not fails, "fails": fails, "lang": lang}
