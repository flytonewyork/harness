"""Batch runner for the shared reader: one `claude -p` call per document, resumable. Mac mini only (CLAUDE.md).

    python -m backend.extract.reader_runner --task insurance --docs insurance_docs.jsonl --estimate
    python -m backend.extract.reader_runner --task insurance --docs insurance_docs.jsonl --out insurance__sonnet.jsonl --model sonnet

`--task <journey>` loads backend.journeys.<journey>.TASK (precare: backend.precare.screens); a dotted module path also
works. `--estimate` prints the call count and a cautious API-price estimate and makes no call. Each output line:
{key, model, task, version, fingerprint, answer, error, cost_usd}. Answers are stored raw; the journey verifies every
quote against the document when it replays them. Stops at the first login error, and before a call once the run's
reported cost passes --max-usd. Exit 1 when nothing could be answered.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib
import json
import threading
from pathlib import Path

from .cursor_reader import CursorAgentReader, CursorCallError, cursor_model
from .reader import ClaudeCLIReader, LoginError, doc_key, estimate, pin


def load_task(name: str):
    """A journey name, `precare`, `triage`, or a dotted module path that defines TASK."""
    mod = {"precare": "backend.precare.screens", "triage": "backend.extract.triage"}.get(name) \
        or (name if "." in name else f"backend.journeys.{name}")
    return importlib.import_module(mod).TASK


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--task", required=True)
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out")
    ap.add_argument("--model", default="sonnet", help="alias (opus, sonnet, haiku) or a full model id; pinned in the output")
    ap.add_argument("--effort", default=None, choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-usd", type=float, default=10.0, help="stop starting calls once the reported cost passes this")
    ap.add_argument("--estimate", action="store_true", help="print the estimate for every model and exit; no calls")
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--cursor-agent", default=None, help="cursor-agent binary, for a gemini- or grok- model id")
    a = ap.parse_args(argv)
    task = load_task(a.task)
    docs = [json.loads(ln) for ln in Path(a.docs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    docs = [d for d in docs if not d.get("task") or d["task"] == task.name]
    if a.estimate:
        models = (a.model,) if cursor_model(pin(a.model)) else ("opus", "sonnet", "haiku")
        for m in models:
            row = {"task": task.name, "version": task.version, "fingerprint": task.fingerprint()} | estimate(task, [d["text"] for d in docs], m)
            if cursor_model(pin(m)):
                row["usd"] = None
                row["price"] = "cursor-subscription"
            print(json.dumps(row))
        return 0
    if not a.out:
        ap.error("--out is required unless --estimate")
    out = Path(a.out)
    done, models = set(), set()
    if out.exists():
        for ln in out.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                models.add(r.get("model"))
                if r.get("error") is None:
                    done.add(r["key"])
    def open_reader():
        if cursor_model(pin(a.model)):
            return CursorAgentReader(pin(a.model), binary=a.cursor_agent)
        return ClaudeCLIReader(a.model, a.effort, binary=a.claude)

    reader, lock = open_reader(), threading.Lock()
    if models - {reader.name}:
        raise SystemExit(f"{out} already holds answers from {sorted(models - {reader.name})}; one model per file")
    todo = [d for d in docs if doc_key(task, d["text"]) not in done]
    print(f"{len(docs)} documents, {len(docs) - len(todo)} already answered, {len(todo)} to run on {pin(a.model)}", flush=True)
    state = {"usd": 0.0, "stop": None}

    def one(d):
        if state["stop"]:
            return False
        key = doc_key(task, d["text"])
        rec = {"key": key, "model": reader.name, "task": task.name, "version": task.version,
               "fingerprint": task.fingerprint(), "scenario": d.get("scenario")}
        r = open_reader()                                                 # one per call: `last` is per call
        try:
            rec |= {"answer": r.read(task, d["text"]), "error": None, "cost_usd": r.last.get("cost_usd")}
        except (LoginError, CursorCallError) as e:
            state["stop"] = str(e)
            return False                                                   # not written: rerun picks it up; do not score it
        except Exception as e:
            if cursor_model(pin(a.model)):
                state["stop"] = str(e)[:300]                               # a Cursor failure is not an answer
                return False
            rec |= {"answer": None, "error": str(e)[:300]}                 # Claude path: recorded, rerun retries it
        with lock:
            state["usd"] += rec.get("cost_usd") or 0
            if state["usd"] > a.max_usd:
                state["stop"] = f"reported cost ${state['usd']:.2f} passed --max-usd {a.max_usd}"
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec["error"] is None

    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        ok = sum(ex.map(one, todo))
    print(f"{ok}/{len(todo)} answered, reported cost ${state['usd']:.2f}" + (f"; stopped: {state['stop']}" if state["stop"] else ""))
    if cursor_model(pin(a.model)):
        return 0 if ok == len(todo) and not state["stop"] else 1
    return 0 if (ok or not todo) and not state["stop"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
