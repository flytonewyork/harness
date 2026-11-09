"""The clinic backend demo (spec 002) may only show engine output it can prove.

`demo/clinic-backend/data.json` is built from a LoopBench run by `build_data.py`, and the same JSON is embedded in the
page, which draws the engine's items from it. These tests hold the page to that data, the data's quotes to their
records, and the care team's own copy to the authority line.
"""
import json
import re
from pathlib import Path

from backend.engine.verify import STEPS, norm

DEMO = Path(__file__).resolve().parents[1] / "demo" / "clinic-backend"
DATA = json.loads((DEMO / "data.json").read_text(encoding="utf-8"))
PAGE = (DEMO / "index.html").read_text(encoding="utf-8")


def embedded():
    m = re.search(r'<script type="application/json" id="engine-data">(.*?)</script>', PAGE, flags=re.S)
    assert m, "the page should embed the engine data"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_page_embeds_exactly_the_built_data():
    assert embedded() == DATA


def test_data_is_synthetic():
    assert DATA["source"]["simulated"] is True
    for p in DATA["patients"].values():
        assert "XXX" in p["hkid"]                     # masked, as LoopBench issues them


def test_every_engine_quote_is_verbatim_in_its_record():
    n = 0
    for it in DATA["items"]:
        assert it["reasoning"], it["id"]
        for s in it["reasoning"]:
            assert s["step"] in STEPS
            for c in s.get("cites") or ():
                assert c["record"] in DATA["documents"], (it["id"], c["record"])
                if c.get("quote"):
                    n += 1
                    assert norm(c["quote"]) in norm(DATA["documents"][c["record"]]["text"]), (it["id"], c["quote"])
    assert n >= 10


def test_mr_chan_is_the_critical_item_at_the_top():
    top = DATA["items"][0]
    assert top["patient_id"] == DATA["mr_chan"] and top["urgency"] == 4
    assert [it["urgency"] for it in DATA["items"]] == sorted((it["urgency"] for it in DATA["items"]), reverse=True)


def test_note_quotes_are_verbatim_in_mr_chans_note():
    note = norm(DATA["consult_note"]["text"])
    shown = [norm(q) for q in re.findall(r'<q class="q-note">(.*?)</q>', PAGE, flags=re.S)]
    assert shown
    for q in shown:
        assert q in note, q


def test_page_renders_engine_text_from_the_data_not_by_hand():
    # Engine words reach the page only through the embedded JSON: none of its step texts are typed into the markup.
    markup = PAGE.split("<!-- DATA:START -->")[0]
    for it in DATA["items"]:
        for s in it["reasoning"]:
            assert norm(s["text"]) not in norm(markup), f"hand-copied engine text: {s['text'][:60]!r}"


def test_care_team_never_signs_prescribes_or_closes():
    agent = re.findall(r'data-actor="agent"[^>]*>(.*?)<span class="meta">', PAGE, flags=re.S)
    assert len(agent) >= 4
    for line in agent:
        assert not re.search(r"\b(prescribed|signed|closed|dismissed|refilled)\b|已經幫你配咗|已批", line, flags=re.I), line


CATALOGUE = json.loads((DEMO / "tasks.json").read_text(encoding="utf-8"))
KINDS = {"scored", "message", "fixed", "never"}


def live(family):
    g = DATA["gate"]
    years = g["families"].get(family) or []
    return bool(years) and all(y["on_time_rate"] == 1 and y["missed"] == 0 and y["late"] == 0 for y in years) \
        and g["hard_fails"] == 0 and g["made_up_quotes"] == 0


def test_page_embeds_the_catalogue_unchanged():
    assert DATA["catalogue"] == CATALOGUE


def test_catalogue_never_claims_its_own_status():
    # Live or not is computed from the simulator's results, never written into the list.
    for t in CATALOGUE["tasks"]:
        assert t["kind"] in KINDS, t
        assert not {"status", "live", "pass", "accuracy"} & set(t), t
        if t["kind"] == "scored":
            assert t["family"] in DATA["gate"]["families"], t
        assert t["zh"] and t["en"]


def test_gate_is_strict_and_matches_the_results():
    assert len(DATA["gate"]["seeds"]) >= 3
    passing = sorted(t["family"] for t in CATALOGUE["tasks"] if t["kind"] == "scored" and live(t["family"]))
    assert passing == ["drug_monitoring", "positive_screen", "test_never_resulted"]   # update when the numbers move
    assert not live("abnormal_result") and not live("followup_recommended")


def test_unscored_tasks_are_never_live_and_doctor_acts_stay_off_the_list():
    agent_text = " ".join(t.get("agents", "") for t in CATALOGUE["tasks"]).lower()
    for word in ("prescribe", "diagnose", "close", "dismiss", "change the dose"):
        assert word not in agent_text, word
    never = " ".join(t["en"] for t in CATALOGUE["tasks"] if t["kind"] == "never").lower()
    for word in ("diagnosing", "medicine", "antibiotics", "result", "sick leave", "closing"):
        assert word in never, word


def test_only_doctor_actions_close():
    # Close buttons exist only in the doctor's decision box.
    for m in re.finditer(r"data-close=", PAGE):
        before = PAGE[max(0, m.start() - 600): m.start()]
        assert "Your decision" in before
