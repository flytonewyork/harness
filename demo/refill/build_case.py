"""Build the refill desk demo's Mr Chan card from a LoopBench product feed.

The demo's one engine-backed case: CHAN Tai Man (陳大文, the LoopBench demo patient), whose critical potassium lands
on the lab portal on a Saturday night. This script copies the engine's item for that result, unchanged, plus every
record it cites and the consult note naming his medicines, into `case_mr_chan.json`. It reads the run file the way
the product's adapters do and never imports LoopBench.

    cd ../loopbench && PYTHONPATH=src python -m loopbench.cli product-feed --clinic hk_solo --system apricot_vita \\
        --harness agentic --seeds 1
    cd ../harness && python demo/refill/build_case.py \\
        --run ../loopbench/results/product/hk_solo_apricot_vita_agentic-rules_s1.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.adapters.loopbench_run import load_run  # noqa: E402

DOB = "1964-09-23"          # the LoopBench demo patient (scenario dev_mr_chan)
OUT = Path(__file__).with_name("case_mr_chan.json")


def build(run: dict) -> dict:
    pid = next(k for k, p in run["patients"].items() if p["dob"] == DOB and p["name_zh"] == "陳大文")
    items = [it for it in run["items"] if it["patient_id"] == pid and it["family"] == "abnormal_result"
             and it["urgency_at_open"] == 4]
    if len(items) != 1:
        raise SystemExit(f"expected one critical result item for Mr Chan, found {len(items)}")
    item = items[0]
    docs = run["documents"]
    note = next(d for d in docs.values() if d.get("patient_id") == pid and d["kind"] == "consult_note")
    cited = {e: docs[e] for e in item["evidence"]}
    for step in item["reasoning"]:
        for c in step.get("cites") or ():
            cited[c["record"]] = docs[c["record"]]
    keep = ("id", "kind", "channel", "available_at", "author", "text")
    return {
        "source": run["source"],
        "patient": run["patients"][pid],
        "item": {k: item[k] for k in ("id", "family", "reason", "urgency_at_open", "opened_at", "due", "evidence",
                                      "reasoning")},
        "documents": {i: {k: d.get(k) for k in keep} for i, d in cited.items()},
        "consult_note": {k: note.get(k) for k in keep},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="harness-run/1 file from `loopbench product-feed`")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    case = build(load_run(a.run))
    Path(a.out).write_text(json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: item {case['item']['id']}, {len(case['documents'])} cited records")


if __name__ == "__main__":
    main()
