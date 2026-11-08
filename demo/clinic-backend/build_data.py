"""Build the clinic backend demo's data from a LoopBench product feed.

The demo (spec 002) shows Harbourview Family Clinic, the simulated solo GP, at 09:30 on Sunday 13 Jun 2027. This
script takes, unchanged, every item the engine had open at that moment (Mr Chan's critical potassium among them),
the records they cite, Mr Chan's consult note, what the engine opened over the weekend and the year's totals. It
writes `data.json` and embeds the same JSON in `index.html`, so the page shows the engine's own words. It reads the
run file the way the product's adapters do and never imports LoopBench.

    cd ../loopbench && PYTHONPATH=src python -m loopbench.cli product-feed --clinic hk_solo --system apricot_vita \\
        --harness agentic --seeds 1
    cd ../harness && python demo/clinic-backend/build_data.py \\
        --run ../loopbench/results/product/hk_solo_apricot_vita_agentic-rules_s1.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from backend.adapters.loopbench_run import load_run  # noqa: E402

NOW = "2027-06-13T09:30"          # Sunday, the clinic is shut
WEEKEND_FROM = "2027-06-12T13:00"  # Saturday's session ends
MR_CHAN_DOB = "1964-09-23"         # the LoopBench demo patient (scenario dev_mr_chan)
KEEP_DOC = ("id", "kind", "channel", "available_at", "text")
KEEP_ITEM = ("id", "patient_id", "family", "reason", "urgency_at_open", "urgency", "opened_at", "due", "reasoning")
START, END = "<!-- DATA:START -->", "<!-- DATA:END -->"


def build(run: dict) -> dict:
    items = [it for it in run["items"]
             if it["opened_at"] <= NOW and (it["resolved_at"] is None or it["resolved_at"] >= NOW)]
    items.sort(key=lambda it: (-it["urgency"], it["opened_at"]))
    docs = run["documents"]
    cited = {}
    for it in items:
        for e in it["evidence"]:
            cited[e] = docs[e]
        for step in it["reasoning"]:
            for c in step.get("cites") or ():
                cited[c["record"]] = docs[c["record"]]
    chan = next(k for k, p in run["patients"].items() if p["dob"] == MR_CHAN_DOB and p["name_zh"] == "陳大文")
    note = next(d for d in docs.values() if d.get("patient_id") == chan and d["kind"] == "consult_note")
    weekend = [it for it in run["items"] if WEEKEND_FROM <= it["opened_at"] <= NOW]
    pids = {it["patient_id"] for it in items}
    return {
        "source": run["source"],
        "now": NOW,
        "mr_chan": chan,
        "patients": {p: {k: run["patients"][p][k] for k in ("name_en", "name_zh", "sex", "dob", "hkid")} for p in pids},
        "items": [{k: it[k] for k in KEEP_ITEM} for it in items],
        "documents": {i: {k: d.get(k) for k in KEEP_DOC} for i, d in sorted(cited.items())},
        "consult_note": {k: note.get(k) for k in KEEP_DOC},
        "weekend": {"opened": len(weekend), "by_family": dict(Counter(it["family"] for it in weekend)),
                    "critical": sum(1 for it in weekend if it["urgency_at_open"] == 4)},
        "year": {"documents": len(docs), "items": len(run["items"]), "feed_alarms": len(run["feeds"]),
                 "by_family": dict(Counter(it["family"] for it in run["items"]).most_common())},
    }


def embed(page: Path, data: dict) -> None:
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    block = f'{START}\n<script type="application/json" id="engine-data">{blob}</script>\n{END}'
    text = page.read_text(encoding="utf-8")
    new, n = re.subn(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    if n != 1:
        raise SystemExit(f"{page}: expected one {START} ... {END} block, found {n}")
    page.write_text(new, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="harness-run/1 file from `loopbench product-feed`")
    a = ap.parse_args()
    data = build(load_run(a.run))
    (HERE / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    embed(HERE / "index.html", data)
    print(f"wrote data.json and embedded it: {len(data['items'])} open items at {NOW}, "
          f"{len(data['documents'])} cited records")


if __name__ == "__main__":
    main()
