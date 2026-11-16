"""The Cursor eval reader calls cursor-agent, labels the model id, and does not record a failed call."""
import json

from backend.extract.cursor_reader import CursorAgentReader, CursorCallError
from backend.extract.reader import Task
from backend.extract import reader_runner

TASK = Task(name="demo", version="demo-1", instructions="Find the request.",
            schema={"type": "object", "required": ["asks"]}, quoted={"asks": "asks_quote"})
DOC = "SYNTHETIC. Please review blood pressure in two weeks."


def _agent(tmp_path, body: str, rc: int = 0) -> str:
    exe = tmp_path / "cursor-agent"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        f"root = pathlib.Path({str(tmp_path)!r})\n"
        "(root / 'cwd').write_text(os.getcwd())\n"
        "(root / 'argv').write_text('\\n'.join(sys.argv))\n"
        "(root / 'prompt').write_text(sys.argv[-1])\n"
        f"print({body!r})\n"
        f"raise SystemExit({rc})\n"
    )
    exe.chmod(0o755)
    return str(exe)


def test_the_call_is_ask_mode_from_an_empty_directory_and_names_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    body = json.dumps({"is_error": False, "result": json.dumps({"asks": False, "asks_quote": None})})
    reader = CursorAgentReader("gemini-4-carbon-high", binary=_agent(tmp_path, body))
    assert reader.read(TASK, DOC) == {"asks": False, "asks_quote": None}
    assert reader.name == "gemini-4-carbon-high"
    argv = (tmp_path / "argv").read_text()
    assert "--mode\nask" in argv and "--single-turn" in argv and "--sandbox\nenabled" in argv
    assert "--model\ngemini-4-carbon-high" in argv
    assert "claude" not in argv and "--approve-mcps" not in argv and "--system-prompt" not in argv
    assert (tmp_path / "cwd").read_text().strip() != str(tmp_path)
    prompt = (tmp_path / "prompt").read_text()
    assert "<document>" in prompt and DOC in prompt and "never an instruction" in prompt


def test_an_error_envelope_is_not_an_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    body = json.dumps({"is_error": True, "result": "Your account is on hold and can't use Claude Code."})
    reader = CursorAgentReader("gemini-4-carbon-high", binary=_agent(tmp_path, body, rc=1))
    try:
        reader.read(TASK, DOC)
    except CursorCallError as e:
        assert "on hold" in str(e)
    else:
        raise AssertionError("expected CursorCallError")


def test_the_batch_does_not_record_a_failed_cursor_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    docs = tmp_path / "docs.jsonl"
    docs.write_text(json.dumps({"text": DOC, "scenario": "s"}) + "\n")
    out = tmp_path / "raw.jsonl"
    body = json.dumps({"is_error": True, "result": "api_error"})
    rc = reader_runner.main(["--task", "triage", "--docs", str(docs), "--out", str(out),
                             "--model", "gemini-4-carbon-high", "--cursor-agent", _agent(tmp_path, body, rc=1)])
    assert rc == 1
    assert not out.exists() or not out.read_text().strip()


def test_composer_is_not_an_eval_model():
    try:
        CursorAgentReader("composer-2.5")
    except CursorCallError:
        return
    raise AssertionError("composer must not be constructed")
