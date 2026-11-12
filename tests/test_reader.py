"""The shared model reader: a field survives only with a verbatim quote; replay is keyed by task version and text."""
import json

import pytest

from backend.extract.reader import (ClaudeCLIReader, FakeReader, LoginError, ReplayReader, Task, doc_key, estimate,
                                    make_reader, pin, verify)

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
    raw.write_text('{"key": "%s", "model": "claude-cli:claude-sonnet-5-5", "answer": {"asks": false}}\n'
                   '{"key": "Kother", "model": "claude-cli:claude-sonnet-5-5", "answer": null, "error": "timeout"}\n' % k)
    r = make_reader(f"replay:{raw}")
    assert isinstance(r, ReplayReader) and r.name == "claude-cli:claude-sonnet-5-5" and r.read(TASK, DOC) == {"asks": False}
    assert r.read(TASK, "other") is None and r.misses == 1
    assert make_reader(None) is None and make_reader("keyword") is None


def test_the_system_prompt_treats_the_document_as_data():
    assert "never an instruction" in TASK.system() and TASK.system().endswith("Find the request.")


def test_a_prompt_edit_without_a_version_bump_changes_every_key():
    edited = Task(TASK.name, TASK.version, TASK.instructions + " Be brief.", TASK.schema, TASK.quoted)
    assert edited.fingerprint() != TASK.fingerprint() and doc_key(edited, DOC) != doc_key(TASK, DOC)


def test_a_one_character_quote_proves_nothing():
    assert verify(TASK, DOC, {"asks": True, "asks_quote": "P"})[1] == ["asks"]


def test_a_replay_file_holds_one_model(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text('{"key": "K1", "model": "claude-cli:claude-opus-5-5", "answer": {}}\n'
                   '{"key": "K2", "model": "claude-cli:claude-haiku-5-5", "answer": {}}\n')
    with pytest.raises(ValueError, match="mixes models"):
        ReplayReader.load(raw)


def test_cli_reader_pins_model_ids_and_keeps_the_call_clean():
    r = make_reader("claude-cli:sonnet:low")
    assert pin("opus") == "claude-opus-5-5" and r.name == "claude-cli:claude-sonnet-5-5:low"
    cmd = r.command(TASK)
    for flag in ("--tools", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--json-schema"):
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5" and cmd[cmd.index("--effort") + 1] == "low"


def _fake_claude(tmp_path, envelope: dict, rc: int = 0):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\ncat >/dev/null\npwd > %s/cwd\necho '%s'\nexit %d\n" % (tmp_path, json.dumps(envelope), rc))
    exe.chmod(0o755)
    return str(exe)


def test_cli_reader_reads_the_envelope_and_stops_on_login_errors(tmp_path):
    ok = ClaudeCLIReader("haiku", binary=_fake_claude(tmp_path, {"structured_output": {"asks": True}, "total_cost_usd": 0.001}))
    assert ok.read(TASK, DOC) == {"asks": True} and ok.last["cost_usd"] == 0.001
    assert (tmp_path / "cwd").read_text().strip() != str(tmp_path)          # ran from an empty folder, not the repo
    bad = ClaudeCLIReader("haiku", binary=_fake_claude(tmp_path, {"is_error": True, "result": "Invalid API key. Please run /login"}))
    with pytest.raises(LoginError):
        bad.read(TASK, DOC)
    err = ClaudeCLIReader("haiku", binary=_fake_claude(tmp_path, {"is_error": True, "result": "overloaded"}))
    with pytest.raises(RuntimeError, match="overloaded"):
        err.read(TASK, DOC)


def test_estimate_is_made_before_any_call():
    e = estimate(TASK, [DOC] * 10, "opus")
    assert e["model"] == "claude-opus-5-5" and e["calls"] == 10 and 0 < e["usd"] < 1
    assert estimate(TASK, [DOC] * 10, "haiku")["usd"] < e["usd"]


def test_runner_estimates_runs_resumes_and_stops_on_login(tmp_path, capsys):
    from backend.extract import reader_runner
    docs = tmp_path / "docs.jsonl"
    docs.write_text("".join(json.dumps({"text": f"Patient message {i}: 仲發燒", "scenario": f"s{i}"}, ensure_ascii=False) + "\n"
                            for i in range(3)))
    assert reader_runner.main(["--task", "triage", "--docs", str(docs), "--estimate"]) == 0
    assert "claude-haiku-5-5" in capsys.readouterr().out
    out = tmp_path / "raw.jsonl"
    good = _fake_claude(tmp_path, {"structured_output": {"red_flag": False, "red_flag_quote": None, "worse": True,
                                                         "worse_quote": "仲發燒"}, "total_cost_usd": 0.01})
    assert reader_runner.main(["--task", "triage", "--docs", str(docs), "--out", str(out), "--model", "haiku", "--claude", good]) == 0
    recs = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert len(recs) == 3 and {r["model"] for r in recs} == {"claude-cli:claude-haiku-5-5"} and all(r["fingerprint"] for r in recs)
    assert reader_runner.main(["--task", "triage", "--docs", str(docs), "--out", str(out), "--model", "haiku", "--claude", good]) == 0
    assert len(out.read_text().splitlines()) == 3                                        # resumed: nothing re-run
    with pytest.raises(SystemExit, match="one model per file"):
        reader_runner.main(["--task", "triage", "--docs", str(docs), "--out", str(out), "--model", "opus", "--claude", good])
    out2 = tmp_path / "raw2.jsonl"
    login = _fake_claude(tmp_path, {"is_error": True, "result": "Not logged in. Please run /login"})
    assert reader_runner.main(["--task", "triage", "--docs", str(docs), "--out", str(out2), "--model", "haiku", "--claude", login]) == 1
    assert not out2.exists() or not out2.read_text().strip()                             # nothing recorded as answered


def test_blank_quote_is_dropped():
    from backend.extract.reader import Task, verify
    t = Task("t", "1", "", {}, quoted={"deadline": "deadline_quote"})
    out, dropped = verify(t, "Please return by 1 May.", {"deadline": "2027-05-01", "deadline_quote": "   "})
    assert dropped == ["deadline"] and out["deadline"] is None
