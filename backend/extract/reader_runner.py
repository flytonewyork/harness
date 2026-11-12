"""Batch runner for the shared reader: one `claude -p` call per document, resumable. Mac mini only (CLAUDE.md).

    python -m backend.extract.reader_runner --task insurance --docs insurance_docs.jsonl --out insurance_raw_sonnet.jsonl --model sonnet

`--task <journey>` loads backend.journeys.<journey>.TASK (precare: backend.precare.screens); a dotted module path also works. Each output line: {key, model, task, version, answer, error}.
Answers are stored raw; the journey verifies every quote against the document when it replays them.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib
import json
import threading
from pathlib import Path

from .reader import ClaudeCLIReader, doc_key


def load_task(name: str):
    """A journey name, `precare`, or a dotted module path that defines TASK."""
    mod = {"precare": "backend.precare.screens"}.get(name) or (name if "." in name else f"backend.journeys.{name}")
    return importlib.import_module(mod).TASK


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task", required=True)
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    task = load_task(a.task)
    docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = Path(a.out)
    done = {json.loads(ln)["key"] for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()} if out.exists() else set()
    todo = [d for d in docs if doc_key(task, d["text"]) not in done]
    reader, lock = ClaudeCLIReader(a.model), threading.Lock()

    def one(d):
        key, rec = doc_key(task, d["text"]), {"model": reader.name, "task": task.name, "version": task.version}
        try:
            rec |= {"key": key, "answer": reader.read(task, d["text"]), "error": None}
        except Exception as e:                                       # recorded, not retried silently
            rec |= {"key": key, "answer": None, "error": str(e)[:300]}
        with lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec["error"] is None

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        ok = sum(ex.map(one, todo))
    print(f"{ok}/{len(todo)} answered ({len(done)} already in {out})")
    raise SystemExit(0 if ok or not todo else 1)


if __name__ == "__main__":
    main()
