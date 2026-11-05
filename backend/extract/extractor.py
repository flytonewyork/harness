"""The extractor: model in, validated commitments out.

Every commitment's quote must appear in its document. A quote that matches only after ignoring case or spacing
is replaced by the document's own text (repaired); one that cannot be found is dropped and recorded. So the
engine never cites words a document does not contain, and the platform's quote check cannot fail on extractor
output.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from .prompt import PROMPT_VERSION
from .schema import Commitment, Document, Extraction


def _norm(s: str) -> str:
    return " ".join(str(s).split())


def locate_quote(quote: str, text: str) -> str | None:
    """The document's exact span for a quote: as given, or matched ignoring case and spacing."""
    if not quote:
        return None
    if _norm(quote) in _norm(text):
        return quote
    toks = [re.escape(t) for t in quote.split()]
    if not toks:
        return None
    m = re.search(r"\s+".join(toks), text, re.I)
    return m.group(0) if m else None


def validate(doc: Document, raw: list[dict], model: str) -> Extraction:
    keep, dropped, repaired = [], [], 0
    for r in raw or []:
        try:
            c = Commitment.model_validate(r)
        except ValidationError as e:
            dropped.append({"raw": r, "why": f"invalid: {e.errors()[0]['msg']}"})
            continue
        span = locate_quote(c.quote, doc.text)
        if span is None:
            dropped.append({"raw": r, "why": "quote not in document"})
            continue
        if span != c.quote:
            c = c.model_copy(update={"quote": span})
            repaired += 1
        keep.append(c)
    return Extraction(doc_id=doc.doc_id, model=model, commitments=keep, repaired=repaired, dropped=dropped)


class Extractor:
    """Extract with caching. Cache lines: one Extraction (JSON) per document, keyed by doc_id + model + prompt."""

    def __init__(self, model, cache_path: str | Path | None = None):
        self.model = model
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[tuple[str, str, str], Extraction] = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = Extraction.model_validate_json(line)
                    self.cache[(e.doc_id, e.model, e.prompt_version)] = e

    def extract(self, docs: list[Document | dict], batch: int = 40) -> dict[str, Extraction]:
        docs = [d if isinstance(d, Document) else Document.model_validate(d) for d in docs]
        out, todo = {}, []
        for d in docs:
            hit = self.cache.get((d.doc_id, self.model.name, PROMPT_VERSION))
            if hit is not None:
                out[d.doc_id] = hit
            else:
                todo.append(d)
        for i in range(0, len(todo), batch):
            chunk = todo[i: i + batch]
            try:
                raw = self.model.run([d.model_dump() for d in chunk])
                err = None
            except Exception as e:            # a failed batch is recorded, never silently treated as "nothing found"
                raw, err = {}, str(e)[:300]
            for d in chunk:
                e = validate(d, raw.get(d.doc_id, []), self.model.name)
                if err or d.doc_id not in raw:
                    e = e.model_copy(update={"error": err or "no result returned for this document"})
                out[d.doc_id] = e
                self._remember(e)
        return out

    def _remember(self, e: Extraction):
        self.cache[(e.doc_id, e.model, e.prompt_version)] = e
        if self.cache_path and e.error is None:
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(e.model_dump_json() + "\n")


def load_raw_runs(path: str | Path, docs: dict[str, Document]) -> list[Extraction]:
    """Turn claude_runner.py output (raw batches) into validated extractions."""
    from .models import parse_cli_output
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r.get("returncode") != 0:
            continue
        try:
            res = parse_cli_output(r["stdout"])
        except Exception as e:  # noqa: BLE001
            res, err = {}, f"unparseable output: {e}"
        else:
            err = None
        for did in r["doc_ids"]:
            if did not in docs:
                continue
            e = validate(docs[did], res.get(did, []), r["model"])
            if err or did not in res:
                e = e.model_copy(update={"error": err or "no result returned for this document"})
            out.append(e)
    return out
