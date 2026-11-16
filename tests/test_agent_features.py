"""The agents' features table, the eval rubric (spec 003, `evals/agent_features.csv`).

These tests keep the table honest as it grows: every task in the catalogue has a row, every row uses the
vocabularies the spec defines, its gate follows from its status and severity, and no row lets the agent close,
dismiss, downgrade or reassure. Whether a task is live is never written here: it is computed from LoopBench results.
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "evals" / "agent_features.csv"
SPEC = ROOT / "archive" / "docs" / "specs" / "003-agent-features-rubric.md"
CATALOGUE = ROOT / "demo" / "clinic-backend" / "tasks.json"
LANDING_V3 = ROOT / "demo" / "landing" / "v3.dc.html"

COLUMNS = ["id", "area", "feature", "menu_option", "catalogue_task", "loopbench_family", "trigger", "hours",
           "agent_may", "must_never", "escalates_to", "pass_criteria", "hard_fail", "severity", "eval_method",
           "gate", "status"]
AREAS = {"AC": "Access and identity", "FD": "Front desk", "RT": "Results and tests", "MD": "Medicines",
         "AV": "After the visit", "AH": "After hospital", "CR": "Chronic care and recalls",
         "HE": "Hours and escalation", "NV": "Never", "RP": "Records and privacy"}
HOURS = {"any hour", "opening hours", "immediately", "engine runs", "daytime only"}
ESCALATES = {"staff", "doctor", "named doctor", "999 reply", "none"}
LOOPBENCH_FAILS = {"authority", "false_all_clear", "no_evidence", "unsupported_evidence", "wrong_patient",
                   "unsupported_claim"}
MESSAGE_FAILS = {"clinical_advice", "unapproved_send", "disclosure", "missed_red_flag", "dropped_message",
                 "false_promise", "impersonation", "no_consent", "promotion"}
SEVERITY_GATE = {"critical": "G-critical", "high": "G-high", "medium": "G-standard", "low": "G-standard"}
EVAL_METHODS = {"loopbench", "scripted", "red-team", "shadow", "settings"}
STATUS_KIND = {"scored": "scored", "unscored": "message", "fixed": "fixed", "refused": "never"}
REQUIRED = [c for c in COLUMNS if c not in ("menu_option", "catalogue_task", "loopbench_family")]
MENU_ROW = re.compile(
    r'<li style="display: flex; align-items: center; gap: 12px; padding: 7px 16px">'
    r'<span [^>]*><span style="font-size: 14.5px; font-weight: 600">([^<]+)</span>'
)


def rows():
    with TABLE.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == COLUMNS
        return list(reader)


def catalogue():
    return {t["id"]: t for t in json.loads(CATALOGUE.read_text(encoding="utf-8"))["tasks"]}


def tokens(cell, sep):
    return [t.strip() for t in cell.split(sep)]


def test_ids_are_unique_and_match_their_area():
    ids = [r["id"] for r in rows()]
    assert len(ids) == len(set(ids))
    for r in rows():
        assert re.fullmatch(r"[A-Z]{2}-\d{2}", r["id"]), r["id"]
        assert AREAS[r["id"][:2]] == r["area"], r["id"]


def test_every_row_is_complete_and_uses_the_vocabularies():
    for r in rows():
        for col in REQUIRED:
            assert r[col].strip(), (r["id"], col)
        assert r["hours"] in HOURS, r["id"]
        assert set(tokens(r["escalates_to"], ";")) <= ESCALATES, r["id"]
        assert set(tokens(r["eval_method"], "+")) <= EVAL_METHODS, r["id"]
        fails = set(tokens(r["hard_fail"], ";"))
        assert fails == {"none"} or fails <= LOOPBENCH_FAILS | MESSAGE_FAILS, r["id"]


def test_every_catalogue_task_has_a_row_and_every_row_names_a_real_task():
    tasks = catalogue()
    named = {r["catalogue_task"] for r in rows() if r["catalogue_task"]}
    assert named == set(tasks)


def test_status_follows_from_the_catalogue_and_the_gate_from_status_and_severity():
    tasks = catalogue()
    for r in rows():
        status, task, family = r["status"], r["catalogue_task"], r["loopbench_family"]
        if status == "designed":
            assert not task and not family, r["id"]
            assert r["gate"] in (SEVERITY_GATE[r["severity"]], "G-fixed"), r["id"]
            continue
        assert tasks[task]["kind"] == STATUS_KIND[status], r["id"]
        if status == "scored":
            assert family == task == tasks[task]["family"] and r["gate"] == "G-loop", r["id"]
            continue
        assert not family, r["id"]
        expected = {"fixed": "G-fixed", "refused": "G-refuse"}.get(status, SEVERITY_GATE[r["severity"]])
        assert r["gate"] == expected, r["id"]


def test_no_row_lets_the_agent_close_dismiss_downgrade_or_reassure():
    for r in rows():
        assert not re.search(r"\b(clos(e|es|ed|ing)|dismiss\w*|downgrad\w*|reassur\w*)\b", r["agent_may"], re.I), r["id"]


def test_every_menu_option_on_the_landing_page_has_a_row():
    menu = MENU_ROW.findall(LANDING_V3.read_text(encoding="utf-8"))
    assert len(menu) == 10
    assert {r["menu_option"] for r in rows() if r["menu_option"]} == set(menu)


def test_the_spec_defines_every_hard_fail_and_gate_the_table_uses():
    spec = SPEC.read_text(encoding="utf-8")
    used = {f for r in rows() for f in tokens(r["hard_fail"], ";")} - {"none"}
    gates = {r["gate"] for r in rows()}
    for word in sorted(used | gates):
        assert f"`{word}`" in spec, word
