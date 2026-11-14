"""python -m backend.lookback --export DIR --out DIR [--extractor rules|replay:RAW.jsonl --docs DOCS.jsonl]"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..extract import Document, Extractor, RulesModel
from ..extract.extractor import load_raw_runs
from ..extract.models import ReplayModel
from .runner import run, write


def make_extractor(spec: str, docs_path: str | None) -> Extractor:
    if spec == "rules":
        return Extractor(RulesModel())
    if spec.startswith("replay:"):
        docs = {}
        for line in Path(docs_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = Document.model_validate_json(line)
                docs[d.doc_id] = d
        exs = load_raw_runs(spec.split(":", 1)[1], docs)
        name = exs[0].model if exs else "replay"
        return Extractor(ReplayModel({e.doc_id: [c.model_dump() for c in e.commitments] for e in exs if e.error is None},
                                     name=name))
    raise SystemExit(f"unknown extractor {spec}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m backend.lookback")
    ap.add_argument("--export", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extractor", default="rules")
    ap.add_argument("--docs", default=None, help="docs.jsonl the replay run was made from")
    ap.add_argument("--from", dest="start", default=None, help="index window start (YYYY-MM-DD)")
    ap.add_argument("--to", dest="end", default=None)
    a = ap.parse_args(argv)
    res = run(a.export, make_extractor(a.extractor, a.docs),
              datetime.fromisoformat(a.start) if a.start else None, datetime.fromisoformat(a.end) if a.end else None)
    write(res, a.out)
    print(json.dumps(res["summary"], indent=1))
    from .checks import report_text
    print(report_text(res["summary"]["counters"]))


if __name__ == "__main__":
    main()
