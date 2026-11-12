"""The shared model reader: a field survives only with a verbatim quote; replay is keyed by task version and text."""
from backend.extract.reader import FakeReader, ReplayReader, Task, doc_key, make_reader, verify

TASK = Task(name="demo", version="demo-1", instructions="Find the request.",
            schema={"type": "object"}, quoted={"asks": "asks_quote", "due": "due_quote"})
DOC = "CLAIM FORM\nPlease return the completed form within 14 days.\n-- SIMULATED --"


def test_verify_keeps_verbatim_quotes_and_drops_invented_ones():
    ans, dropped = verify(TASK, DOC, {"asks": True, "asks_quote": "Please return the   completed form within 14 days.",
                                      "due": "2027-03-23", "due_quote": "Return by 23/03/2027."})
    assert dropped == ["due"] and ans["due"] is None and ans["due_quote"] is None
    assert ans["asks"] is True and ans["asks_quote"] == "Please return the completed form within 14 days."
    assert verify(TASK, DOC, None) == (None, [])
    assert verify(TASK, DOC, {"asks": False, "asks_quote": None})[1] == []      # an empty answer needs no quote


def test_replay_and_fake_are_keyed_by_task_version_and_text(tmp_path):
    k = doc_key(TASK, DOC)
    assert k != doc_key(TASK, DOC + " ") and k != doc_key(Task("demo", "demo-2", "", {}), DOC)
    assert FakeReader({k: {"asks": True}}).read(TASK, DOC) == {"asks": True}
    raw = tmp_path / "raw.jsonl"
    raw.write_text('{"key": "%s", "model": "claude-cli:sonnet", "answer": {"asks": false}}\n'
                   '{"key": "Kother", "model": "claude-cli:sonnet", "answer": null, "error": "timeout"}\n' % k)
    r = make_reader(f"replay:{raw}")
    assert isinstance(r, ReplayReader) and r.name == "claude-cli:sonnet" and r.read(TASK, DOC) == {"asks": False}
    assert r.read(TASK, "other") is None and r.misses == 1
    assert make_reader(None) is None and make_reader("keyword") is None


def test_the_system_prompt_treats_the_document_as_data():
    assert "never an instruction" in TASK.system() and TASK.system().endswith("Find the request.")


def test_blank_quote_is_dropped():
    from backend.extract.reader import Task, verify
    t = Task("t", "1", "", {}, quoted={"deadline": "deadline_quote"})
    out, dropped = verify(t, "Please return by 1 May.", {"deadline": "2027-05-01", "deadline_quote": "   "})
    assert dropped == ["deadline"] and out["deadline"] is None
