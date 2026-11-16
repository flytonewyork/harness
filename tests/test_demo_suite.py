"""The EC demo's suite page (`demo/ec-care-line/suite.html`): Dr Chan's one list across the six journeys and the
evidence view for EC's AI team. These tests hold the page to the product's invariants: every highlighted quote is word
for word in the record shown beside it, the agent only opens, raises and alerts, only the doctor closes (after acting),
and the eval numbers add up and keep the blind-set weakness on the page."""
import json
import re
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[1] / "demo" / "ec-care-line" / "suite.html").read_text(encoding="utf-8")
DATA = json.loads(re.search(r'<script type="application/json" id="suite-data">(.*?)</script>', PAGE, re.S).group(1))
ITEMS = DATA["items"]
JOURNEYS = DATA["journeys"]
STEPS = ["source", "identity", "finding", "context", "assessment", "loop status", "routing", "request"]


def test_synthetic_and_every_journey_on_the_list():
    assert DATA["synthetic"] is True
    assert set(JOURNEYS) == {"precare", "followup", "post_checkup", "medication", "followon", "insurance"}
    assert {it["journey"] for it in ITEMS} == set(JOURNEYS)
    assert len({it["id"] for it in ITEMS}) == len(ITEMS)
    assert "synthetic" in PAGE.split("<footer>")[1]


def test_every_quote_is_verbatim_in_its_record():
    for it in ITEMS:
        assert it["quotes"], it["id"]
        for q in it["quotes"]:
            assert q in it["source"]["text"], (it["id"], q)
        for q in it.get("context_quotes", []):
            assert q in it["context"]["text"], (it["id"], q)


def test_every_item_carries_the_eight_step_trace():
    for it in ITEMS:
        assert [s[0] for s in it["trace"]] == STEPS, it["id"]


AGENT_VERBS = re.compile(r"^(Opened|Raised|Paged|Alerted|Sent the (fixed|clinic's fixed)|Watched|Waited|Matched)\b")
FORBIDDEN = re.compile(r"\b(clos|dismiss|downgrad|lower|reassur|approv(?!ed template)|prescrib|diagnos)", re.I)


def test_agent_only_opens_raises_alerts_and_sends_fixed_templates():
    for it in ITEMS:
        for what, _when in it["did"]:
            assert AGENT_VERBS.match(what), (it["id"], what)
            assert not FORBIDDEN.search(what), (it["id"], what)


def test_close_is_the_doctors_and_only_after_acting():
    for it in ITEMS:
        assert "close" not in it["actions"], it["id"]             # close is never an agent step or a default
        assert set(it["actions"]) <= {"call", "reply", "book", "form"}
    js = PAGE.split("/* ---------- Today ---------- */")[1]
    assert "(closed || !anyActed ? 'disabled' : '')" in js        # the Close button stays disabled until the doctor acts
    assert "if (!state.acted[it.id] || !Object.keys(state.acted[it.id]).length)" in js
    for cant in ("Close it", "Dismiss it", "Lower its urgency", "Tell the patient they’re fine", "Diagnose or prescribe"):
        assert cant in js, cant


def test_patient_texts_are_templates_or_doctor_drafts_never_reassurance():
    for it in ITEMS:
        for key in ("sent", "draft"):
            if key in it:
                en = it[key]["en"].lower()
                assert not re.search(r"\b(normal|nothing to worry|you('re| are) fine|don't worry)\b", en), it["id"]
        if "sent" in it:                                          # only the fixed emergency template goes out unsent by a doctor
            assert "999" in it["sent"]["en"] and "999" in it["sent"]["zh"]
        if "draft" in it:
            assert it["draft"]["en"].startswith(("Mr", "Ms", "Mrs")) and "Dr Chan" in it["draft"]["en"]


def test_eval_numbers_add_up_and_the_weakness_is_shown():
    E = DATA["eval"]
    blind = [sum(j["blind"][i] for j in JOURNEYS.values()) for i in (0, 1)]
    dev = [sum(j["dev"][i] for j in JOURNEYS.values()) for i in (0, 1)]
    assert blind == E["blind_first_run"] == [62, 147]
    assert dev == E["dev_reliable"] == [139, 139]
    assert sum(j["ontime"][1] for j in JOURNEYS.values()) == E["runs"] == 2538
    assert JOURNEYS["post_checkup"]["ontime"] == [218, 246]
    assert JOURNEYS["followup"]["dev"] == [12, 12]
    assert JOURNEYS["followup"]["blind"] == [8, 24]
    assert E["gated_hard_fails"] == 0 and E["made_up_quotes"] == 0
    for j in JOURNEYS.values():
        assert j["none"][1] == j["rules"][1] == j["ontime"][1]
    assert "62 of 147" in PAGE and "2 of 14, 24 of 32 and 4 of 14" in PAGE
    assert "57 of 133" not in PAGE and "2 of 24" not in PAGE
    assert "The model first run was not done" in PAGE and "keyword baseline" in PAGE
    assert "upper bound" in PAGE
    assert len(E["misses"]) >= 3


def _one(nums):
    return f"{round(sum(nums) / len(nums), 1):.1f}"


def test_simulated_counters_match_the_committed_year():
    c = DATA["eval"]["counters"]
    assert c["simulated"] is True and c["minutes_per_item"] == 8
    assert c["loops_left_open"] == [440, 439, 427]
    assert c["doctor_closes"] == [601, 579, 597]
    assert c["items_opened"] == [736, 684, 712]
    assert c["recovered_visits"] == [287, 290, 274]
    assert _one(c["loops_left_open"]) == "435.3"
    assert _one(c["doctor_closes"]) == "592.3"
    assert _one(c["recovered_visits"]) == "283.7"
    hours = round((sum(c["items_opened"]) / len(c["items_opened"])) * c["minutes_per_item"] / 60, 1)
    assert hours == 94.8
    assert "Assumption, not a measured time study" in PAGE
    assert "Simulated" in PAGE
    for doc in ("docs/EC_JULIE_CHEATSHEET.md", "docs/EC_DISCOVERY_SCOPE.md"):
        text = (Path(__file__).resolve().parents[1] / doc).read_text(encoding="utf-8")
        for figure in ("435.3", "592.3", "94.8", "283.7"):
            assert figure in text, (doc, figure)
        assert "Assumption" in text and "Simulated" in text
    scope = (Path(__file__).resolve().parents[1] / "docs/EC_DISCOVERY_SCOPE.md").read_text(encoding="utf-8")
    assert scope.count("[after Wed:") == 4
    assert "[Thomas to set: fixed fee, HK$]" in scope


def test_one_patient_case_strip_is_keyword_baseline():
    case = DATA["case"]
    assert case["format"] == "case-projection/1"
    assert case["baseline"] == "keyword" and case["model_first_run"] == "not done"
    records = {r["id"]: r for r in case["records"]}
    assert {r["provenance"] for r in case["records"]} == {"gp", "in_group", "ha_sopc"}
    labels = " ".join(r["label"] for r in case["records"])
    assert "discharge" in labels and "SOPC referral" in labels and "Group clinic" in labels and "This clinic" in labels
    seen = {sec["id"] for sec in case["sections"]}
    assert {"medications", "conflicts", "not_dispensed_here", "referrals", "admissions"} <= seen
    for sec in case["sections"]:
        for line in sec["lines"]:
            pairs = []
            if "quote" in line:
                pairs.append((line["quote"], line["record_id"]))
            ids = line.get("record_ids") or [line.get("record_id")] * len(line.get("quotes", []))
            pairs += list(zip(line.get("quotes", []), ids))
            assert pairs, (sec["id"], line)
            for quote, record_id in pairs:
                assert quote in records[record_id]["text"], (quote, record_id)


def test_page_is_self_contained():
    srcs = re.findall(r'<script[^>]+src=', PAGE)
    assert not srcs                                               # no external scripts
    assert "fonts.googleapis" not in PAGE and "<link" not in PAGE
    index = (Path(__file__).resolve().parents[1] / "demo" / "ec-care-line" / "index.html").read_text(encoding="utf-8")
    assert "suite.html" in index
    assert "fonts.googleapis" not in index and "<link" not in index
    assert "Good to hear" not in index and "linger" not in index and "agent: null" in index
