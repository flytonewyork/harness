"""Data in from LoopBench: a run file ("harness-run/1") that LoopBench writes after a simulated clinic year
(`loopbench product-feed`). The product reads the file; it never imports LoopBench.

Shape: source, staff, patients{id: card}, documents{id: doc}, items[...], feeds[...] and, for simulations only,
evaluation (the answer key, for the demo panel).
"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED = ("source", "staff", "patients", "documents", "items", "feeds")


def load_run(path: str | Path) -> dict:
    run = json.loads(Path(path).read_text(encoding="utf-8"))
    if run.get("format") != "harness-run/1":
        raise ValueError(f"{path}: not a harness-run/1 file")
    missing = [k for k in REQUIRED if k not in run]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    for it in run["items"]:
        for e in it["evidence"]:
            if e not in run["documents"]:
                raise ValueError(f"item {it['id']} cites {e}, which is not in the file")
    return run
