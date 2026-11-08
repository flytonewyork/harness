"""The refill desk demo (spec 002) may only show engine output it can prove.

Mr Chan's card is the engine's item copied from a LoopBench run (`demo/refill/build_case.py`). Every quote the page
shows must appear word for word in the record it cites (the product's `quotes` check), and every engine step the
page shows must be the engine's own words.
"""
import html
import json
import re
from pathlib import Path

from backend.engine.verify import norm

DEMO = Path(__file__).resolve().parents[1] / "demo" / "refill"
CASE = json.loads((DEMO / "case_mr_chan.json").read_text(encoding="utf-8"))
PAGE = (DEMO / "index.html").read_text(encoding="utf-8")
TEXT = norm(html.unescape(re.sub(r"<[^>]+>", " ", PAGE)))
SHOWN_STEPS = ("source", "identity", "finding", "assessment", "routing", "request")


def tagged(cls):
    return [norm(html.unescape(q)) for q in re.findall(rf'<q class="{cls}">(.*?)</q>', PAGE, flags=re.S)]


def test_case_is_synthetic():
    assert CASE["source"]["simulated"] is True
    assert "XXX" in CASE["patient"]["hkid"]          # masked, as LoopBench issues them


def test_engine_quotes_are_verbatim_in_their_records():
    cites = [c for s in CASE["item"]["reasoning"] for c in s.get("cites") or ()]
    assert cites, "the engine item should carry quotes"
    for c in cites:
        assert norm(c["quote"]) in norm(CASE["documents"][c["record"]]["text"]), c


def test_page_quotes_are_the_engine_quotes():
    engine = {norm(c["quote"]) for s in CASE["item"]["reasoning"] for c in s.get("cites") or ()}
    shown = tagged("q-engine")
    assert shown and set(shown) <= engine, set(shown) - engine
    assert engine <= set(shown), f"page leaves out {engine - set(shown)}"


def test_page_steps_are_the_engine_words():
    for s in CASE["item"]["reasoning"]:
        if s["step"] in SHOWN_STEPS:
            assert norm(s["text"]) in TEXT, f"{s['step']} differs from the engine: {s['text']!r}"
    context = next(s["text"] for s in CASE["item"]["reasoning"] if s["step"] == "context")
    assert "No relevant medicines dispensed at this clinic." in context
    assert "No relevant medicines dispensed at this clinic." in TEXT


def test_note_quotes_are_verbatim_in_the_consult_note():
    note = norm(CASE["consult_note"]["text"])
    shown = tagged("q-note")
    assert shown
    for q in shown:
        assert q in note, q


def test_agent_never_signs_prescribes_or_closes():
    # The authority line: what the care team says never claims a doctor's act.
    agent = re.findall(r'data-actor="agent"[^>]*>(.*?)<span class="meta">', PAGE, flags=re.S)
    assert len(agent) >= 4
    for line in agent:
        assert not re.search(r"\b(prescribed|signed|closed|dismissed|refilled)\b|已經幫你配咗|已批", line, flags=re.I), line
