#!/usr/bin/env python3
"""Batch runner: follow-up extraction with `claude -p` over a JSONL of documents.

Standard library only, so it runs wherever the `claude` CLI is logged in. It writes the raw model output for
each batch; parsing, validation and verbatim-quote checks happen in backend.extract.extractor. Resumable:
batches already in --out are skipped.

    python3 claude_runner.py --docs docs.jsonl --out raw.jsonl --model sonnet --batch 40 --workers 4

Each call runs with a replacement system prompt, no tools, no MCP servers and no saved session.
Batches never hold two documents with the same `group` (patient), so a batch cannot carry one patient's
later document alongside an earlier one.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt import PROMPT_VERSION, SCHEMA, SYSTEM, build_prompt  # noqa: E402


def make_batches(docs: list[dict], size: int) -> list[list[dict]]:
    """Greedy packing; no two documents from the same group in one batch."""
    batches: list[list[dict]] = []
    groups: list[set] = []
    for d in docs:
        g = d.get("group") or d["doc_id"]
        for b, gs in zip(batches, groups):
            if len(b) < size and g not in gs:
                b.append(d)
                gs.add(g)
                break
        else:
            batches.append([d])
            groups.append({g})
    return batches


def batch_id(batch: list[dict]) -> str:
    return hashlib.sha256("|".join(sorted(d["doc_id"] for d in batch)).encode()).hexdigest()[:16]


def run_batch(batch: list[dict], model: str, timeout: int, binary: str) -> dict:
    cmd = [binary, "-p", "--model", model, "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(SCHEMA)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=build_prompt(batch), capture_output=True, text=True, timeout=timeout)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = -1, "", f"timeout after {timeout}s"
    return {"batch_id": batch_id(batch), "doc_ids": [d["doc_id"] for d in batch], "model": f"claude-cli:{model}",
            "prompt_version": PROMPT_VERSION, "returncode": rc, "stdout": out[-500000:], "stderr": err[-4000:],
            "seconds": round(time.time() - t0, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit-batches", type=int, default=0)
    ap.add_argument("--claude", default="claude")
    a = ap.parse_args(argv)

    docs = [json.loads(line) for line in Path(a.docs).read_text(encoding="utf-8").splitlines() if line.strip()]
    done = set()
    out = Path(a.out)
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("returncode") == 0:
                done.update(r["doc_ids"])
    todo = [d for d in docs if d["doc_id"] not in done]
    batches = make_batches(todo, a.batch)
    if a.limit_batches:
        batches = batches[: a.limit_batches]
    print(f"{len(docs)} documents, {len(done)} already done, {len(batches)} batches to run", flush=True)
    lock = threading.Lock()
    n_ok = n_fail = 0
    with out.open("a", encoding="utf-8") as f, cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_batch, b, a.model, a.timeout, a.claude) for b in batches]
        for fut in cf.as_completed(futs):
            r = fut.result()
            with lock:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                n_ok += r["returncode"] == 0
                n_fail += r["returncode"] != 0
                print(f"[{n_ok + n_fail}/{len(batches)}] {r['batch_id']} rc={r['returncode']} {r['seconds']}s"
                      + (f" ERR {r['stderr'][:200]}" if r["returncode"] else ""), flush=True)
    print(f"done: {n_ok} ok, {n_fail} failed", flush=True)


if __name__ == "__main__":
    main()
