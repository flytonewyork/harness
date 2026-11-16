"""The clinic backend demo (spec 002) may only show engine output it can prove.

`demo/clinic-backend/data.json` is built from a LoopBench run by `build_data.py`, and the same JSON is embedded in the
page, which draws the engine's items from it. These tests hold the page to that data, the data's quotes to their
records, and the care team's own copy to the authority line. The EC version of the page (`demo/ec-care-line/`) carries
the same data and is held to the same tests, and its ask to needing no patient data.
"""
import json
import re
from pathlib import Path

import pytest

from backend.engine.verify import STEPS, norm

DEMO = Path(__file__).resolve().parents[1] / "demo" / "clinic-backend"
DATA = json.loads((DEMO / "data.json").read_text(encoding="utf-8"))
PAGES = {name: (DEMO.parent / name / "index.html").read_text(encoding="utf-8") for name in ("clinic-backend", "ec-care-line")}
PAGE = PAGES["clinic-backend"]
both = pytest.mark.parametrize("page", list(PAGES.values()), ids=list(PAGES))


def embedded(page=PAGE):
    m = re.search(r'<script type="application/json" id="engine-data">(.*?)</script>', page, flags=re.S)
    assert m, "the page should embed the engine data"
    return json.loads(m.group(1).replace("<\\/", "</"))


@both
def test_page_embeds_exactly_the_built_data(page):
    assert embedded(page) == DATA


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


@both
def test_note_quotes_are_verbatim_in_mr_chans_note(page):
    note = norm(DATA["consult_note"]["text"])
    shown = [norm(q) for q in re.findall(r'<q class="q-note">(.*?)</q>', page, flags=re.S)]
    assert shown
    for q in shown:
        assert q in note, q


@both
def test_page_renders_engine_text_from_the_data_not_by_hand(page):
    # Engine words reach the page only through the embedded JSON: none of its step texts are typed into the markup.
    markup = page.split("<!-- DATA:START -->")[0]
    for it in DATA["items"]:
        for s in it["reasoning"]:
            assert norm(s["text"]) not in norm(markup), f"hand-copied engine text: {s['text'][:60]!r}"


@both
def test_care_team_never_signs_prescribes_or_closes(page):
    agent = re.findall(r'data-actor="agent"[^>]*>(.*?)<span class="meta">', page, flags=re.S)
    assert len(agent) >= 4
    for line in agent:
        assert not re.search(r"\b(prescribed|signed|closed|dismissed|refilled)\b|已經幫你配咗|已批", line, flags=re.I), line


def section(page, sid):
    m = re.search(rf'<section[^>]*id="{sid}".*?</section>', page, flags=re.S)
    assert m, sid
    return re.sub(r"<[^>]+>", " ", m.group(0))


def test_ec_page_asks_for_no_patient_data():
    # EC talks are exploratory: the first step asks for people's time, never for records or access to them.
    page = PAGES["ec-care-line"]
    ask = section(page, "ask")
    assert "no patient data" in ask
    assert not re.search(r"read(-only)? access|credentials|a shadow|export of", ask, flags=re.I), ask
    emr = section(page, "emr")
    assert "Nothing leaves EC" in emr and "None of this is needed to start" in emr
    never = re.search(r"<th>Never</th><td>(.*?)</td>", page, flags=re.S).group(1)
    for act in ("Prescribes", "diagnoses", "closes"):
        assert act in never, act
    assert "not an EC service" in page                 # a demo for EC, never presented as EC's own


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


@both
def test_only_doctor_actions_close(page):
    # Close buttons exist only in the doctor's decision box.
    for m in re.finditer(r"data-close=", page):
        before = page[max(0, m.start() - 600): m.start()]
        assert "Your decision" in before


def test_ec_discovery_scope_needs_no_patient_data():
    # The scope that follows Wednesday's meeting asks for people's time only, and says so.
    scope = (DEMO.parents[1] / "docs" / "EC_DISCOVERY_SCOPE.md").read_text(encoding="utf-8")
    needs = re.search(r"## What it needs from EC\n(.*?)\n## ", scope, flags=re.S).group(1)
    assert not re.search(r"patient (records?|data)|an export of|access to|credentials|log-?in", needs, flags=re.I), needs
    never = re.search(r"## What it never needs\n(.*?)\n## ", scope, flags=re.S).group(1)
    for word in ("No patient records", "exports", "access"):
        assert word in never, word


def raw_section(page, sid):
    m = re.search(rf'<section[^>]*id="{sid}".*?</section>', page, flags=re.S)
    assert m, sid
    return m.group(0)


def plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))


def test_kwok_walkthrough_has_the_five_beats_then_the_ask():
    page = PAGES["ec-care-line"]
    walk = re.search(r"kwok: \[(.*?)\]", page, flags=re.S).group(1)
    ids = re.findall(r"id: '([\w-]+)'", walk)
    assert ids == ["front"] * 4 + ["kwok-item", "numbers", "ask"]
    assert re.findall(r"beat: (\d)", walk) == ["1", "2", "3", "4"]
    for i in set(ids):
        assert f'id="{i}"' in page, i


def test_kwok_item_quotes_are_verbatim_from_her_chat_and_signed_plan():
    page = PAGES["ec-care-line"]
    item = raw_section(page, "kwok-item")
    quotes = re.findall(r"<q class=\"q-engine\" data-quote>(.*?)</q>", item)
    assert len(quotes) >= 3
    rest = plain(page.replace(item, ""))                 # the quotes must come from elsewhere on the page
    for q in quotes:
        assert q in rest, q


def test_kwok_item_keeps_asymmetric_authority():
    item = raw_section(PAGES["ec-care-line"], "kwok-item")
    done = re.findall(r'class="row yes">(.*?)<', item)
    assert done == ["Opened the item", "Raised its urgency", "Alerted Dr Chan"]   # open_item, raise_urgency, alarm
    cant = " ".join(re.findall(r'class="row no">(.*?)<', item))
    for act in ("Close", "dismiss", "downgrade", "Reassure", "prescribe"):
        assert act in cant, act
    assert re.search(r'data-kact="close" disabled', item)       # closes only after the doctor has acted
    assert "doctors only" in item
