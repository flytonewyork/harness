"""Score a reader on the medication probe (evals/medication_probe.yaml): python -m backend.journeys.medication.probe"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .reader import RulesReader

PROBE = Path(__file__).resolve().parents[3] / "evals" / "medication_probe.yaml"


def load(path: Path = PROBE) -> dict:
    import yaml                                  # dev dependency (the eval, not the product)
    return yaml.safe_load(path.read_text())


def judge(case: dict, fs: list[dict]) -> tuple[bool, str]:
    top = max((f["urgency"] for f in fs), default=0)
    kinds = {f["kind"] for f in fs}
    if case.get("quiet"):
        return not fs, f"expected quiet, got {sorted(kinds)} u{top}" if fs else ""
    if not kinds & set(case["kinds"]):
        return False, f"expected {case['kinds']}, got {sorted(kinds) or 'nothing'}"
    lo, hi = case.get("min", 1), case.get("max", 4)
    if not lo <= top <= hi:
        return False, f"urgency {top}, expected {lo}-{hi}"
    return True, ""


def score(reader=None, path: Path = PROBE) -> dict:
    reader = reader or RulesReader()
    doc = load(path)
    rows = []
    for c in doc["cases"]:
        fs = reader.read(c["text"], set(c.get("classes", [])))
        ok, why = judge(c, fs)
        rows.append({"text": c["text"], "ok": ok, "why": why, "emergency": c.get("min", 0) >= 4, "quiet": bool(c.get("quiet"))})
    em = [r for r in rows if r["emergency"]]
    qu = [r for r in rows if r["quiet"]]
    return {"cases": len(rows), "correct": sum(r["ok"] for r in rows),
            "emergencies": len(em), "emergencies_caught": sum(r["ok"] for r in em),
            "quiet": len(qu), "quiet_kept": sum(r["ok"] for r in qu),
            "misses": [r for r in rows if not r["ok"]], "first_pass": doc.get("first_pass")}


if __name__ == "__main__":
    out = score()
    print(json.dumps({k: v for k, v in out.items() if k != "misses"}, ensure_ascii=False))
    for m in out["misses"]:
        print(" MISS", m["text"], "->", m["why"])
    sys.exit(0)
