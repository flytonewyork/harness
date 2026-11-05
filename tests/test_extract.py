"""Extractor: verbatim quotes, conditional plans, batching and cached replays. Synthetic text only."""
import json

from backend.extract import Document, Extractor, RulesModel, content_id
from backend.extract.models import parse_cli_output
from backend.extract.claude_runner import make_batches
from backend.extract.extractor import load_raw_runs, validate
from backend.extract.models import ReplayModel


def doc(text, kind="consult_note", group=None):
    return Document(doc_id=content_id(kind, text), kind=kind, date="2027-03-01", text=text, group=group)


def test_quote_is_repaired_to_the_documents_own_words_or_dropped():
    d = doc("Plan:\n  Repeat K+  and eGFR in 1 week.\nRV 3/12.")
    raw = [
        {"kind": "repeat_test", "what": "K+ and eGFR", "due_in_days": 7, "owner": "clinic", "conditional": False,
         "quote": "repeat k+ and egfr in 1 week"},                       # case and spacing differ
        {"kind": "review", "what": "review", "due_in_days": 90, "owner": "clinic", "conditional": False,
         "quote": "Review in three months"},                             # not in the document
    ]
    e = validate(d, raw, "test")
    assert [c.quote for c in e.commitments] == ["Repeat K+  and eGFR in 1 week"]
    assert e.repaired == 1 and len(e.dropped) == 1 and e.dropped[0]["why"] == "quote not in document"
    assert all(c.quote in d.text for c in e.commitments)


def test_rules_model_reads_shorthand_and_marks_conditional_plans():
    found = RulesModel().one("Imp: HTN.\nRV 3/12.\nRV 1/52 if not improved.\nRV prn.")
    firm = [c for c in found if not c["conditional"]]
    assert [(c["kind"], c["due_in_days"]) for c in firm] == [("review", 90)]
    assert sum(c["conditional"] for c in found) == 2


def test_batches_never_hold_two_documents_of_one_patient():
    docs = [{"doc_id": f"D{i}", "group": f"P{i % 3}"} for i in range(9)]
    batches = make_batches(docs, size=5)
    for b in batches:
        gs = [d["group"] for d in b]
        assert len(gs) == len(set(gs))
    assert sorted(d["doc_id"] for b in batches for d in b) == sorted(d["doc_id"] for d in docs)


def test_cli_envelope_and_failed_documents_are_recorded(tmp_path):
    d1, d2 = doc("Follow up in 2 weeks."), doc("No follow-up needed.")
    payload = {"results": [{"doc_id": d1.doc_id, "commitments": [
        {"kind": "review", "what": "review", "due_in_days": 14, "owner": "clinic", "conditional": False,
         "quote": "Follow up in 2 weeks"}]}]}
    env = json.dumps({"type": "result", "structured_output": payload})
    assert parse_cli_output(env)[d1.doc_id][0]["due_in_days"] == 14
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({"batch_id": "b", "doc_ids": [d1.doc_id, d2.doc_id], "model": "claude-cli:x",
                               "returncode": 0, "stdout": env}) + "\n")
    out = {e.doc_id: e for e in load_raw_runs(raw, {d1.doc_id: d1, d2.doc_id: d2})}
    assert len(out[d1.doc_id].commitments) == 1 and out[d1.doc_id].error is None
    assert out[d2.doc_id].error == "no result returned for this document"   # never silently "nothing found"


def test_replay_is_cached_by_document_model_and_prompt(tmp_path):
    d = doc("RV 2/12.")
    rep = ReplayModel({d.doc_id: [{"kind": "review", "what": "review", "due_in_days": 60, "owner": "clinic",
                                   "conditional": False, "quote": "RV 2/12"}]}, name="replay:test")
    cache = tmp_path / "cache.jsonl"
    first = Extractor(rep, cache_path=cache).extract([d])[d.doc_id]
    again = Extractor(ReplayModel({}, name="replay:test"), cache_path=cache).extract([d])[d.doc_id]
    assert first.commitments == again.commitments and rep.misses == 0
