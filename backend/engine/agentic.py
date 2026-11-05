"""The agentic harness: rules engine v1.1 plus the free-text extractor.

Each note, letter or report that reaches the clinic is read by the extractor (one document at a time, with
nothing else about the patient). Non-conditional commitments that the clinic or patient owns, with a timing,
become watches the engine tracks like any other loop:

- a review ("see her back in 2 weeks") is tracked like a planned review: raised if no consultation happens
  around the due date;
- a repeat test or further imaging ("repeat K+ in 1 week") is raised if no matching result or consultation
  arrives by the due date.

The rules stay the floor: anything the rules engine already tracks for a record is not duplicated, and every
quote the engine cites comes from the extractor's verified quote, so the platform's word-for-word check holds.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta

from ..extract import Extractor, content_id
from .rules import RulesHarness, Why, cite, day, line_with

FREE_TEXT = ("consult_note", "discharge_summary", "aed_summary", "specialist_letter", "imaging_report", "message")
TEST_WORDS = {"rft": ["Creatinine", "Potassium", "Sodium", "Urea", "eGFR"], "renal": ["Creatinine", "eGFR"],
              "k+": ["Potassium"], "potassium": ["Potassium"], "egfr": ["eGFR"], "creatinine": ["Creatinine"],
              "hba1c": ["HbA1c"], "a1c": ["HbA1c"], "glucose": ["Glucose"], "lipid": ["cholesterol", "Triglycerides"],
              "cholesterol": ["cholesterol"], "lft": ["ALT", "AST", "ALP"], "liver": ["ALT", "AST"], "cbc": ["Haemoglobin"],
              "blood count": ["Haemoglobin"], "haemoglobin": ["Haemoglobin"], "inr": ["INR"]}


class AgenticHarness(RulesHarness):
    def __init__(self, extractor: Extractor, **kw):
        super().__init__(version="1.1", **kw)
        self.extractor = extractor
        self.name = f"agentic[{extractor.model.name}]"

    def reset(self, api):
        super().reset(api)
        self.test_watches: list[dict] = []
        self.stats = Counter()

    # ------------------------------------------------------------------------------ reading
    def on_cms(self, api, r: dict):
        n_reviews = len(self.reviews)
        super().on_cms(api, r)
        if r["kind"] in FREE_TEXT and r["patient_id"]:
            ruled = len(self.reviews) > n_reviews        # the rules engine already tracks this note's review
            self.read_commitments(api, r, r["patient_id"], skip_reviews=ruled)

    def read_commitments(self, api, r: dict, pid: str, skip_reviews: bool = False):
        t = datetime.fromisoformat(r["available_at"])
        doc = {"doc_id": content_id(r["kind"], r["text"]), "kind": r["kind"], "date": t.date().isoformat(), "text": r["text"]}
        ex = self.extractor.extract([doc])[doc["doc_id"]]
        self.stats["documents"] += 1
        self.stats["errors"] += ex.error is not None
        for c in ex.commitments:
            if c.conditional or c.owner == "hospital" or c.due_in_days is None:
                continue
            if r["kind"] in ("discharge_summary", "aed_summary", "imaging_report"):
                continue                                  # the rules engine raises these on arrival already
            if c.kind == "review":
                if skip_reviews or any(v["rid"] == r["id"] for v in self.reviews):
                    continue
                self.reviews.append({"pid": pid, "t": t, "days": c.due_in_days, "rid": r["id"], "owner": r["author"],
                                     "quote": c.quote, "via": self.extractor.model.name})
                self.stats["reviews_added"] += 1
            elif c.kind in ("repeat_test", "imaging"):
                self.test_watches.append({"pid": pid, "t": t, "days": c.due_in_days, "rid": r["id"], "owner": r["author"],
                                          "quote": c.quote, "what": c.what, "kind": c.kind})
                self.stats["tests_added"] += 1

    def on_discharge(self, api, pid, r, t):
        super().on_discharge(api, pid, r, t)

    # ----------------------------------------------------------------------------- watching
    def check_watches(self, api):
        super().check_watches(api)
        now = api.now
        for w8 in list(self.test_watches):
            due = w8["t"] + timedelta(days=w8["days"])
            if self.done_since(w8):
                self.test_watches.remove(w8)
            elif now >= due + timedelta(days=2):
                key = ("cmt", w8["rid"], w8["quote"])
                if key not in self.raised:
                    w = Why()
                    w.add("source", f"The {('consultation' if w8['rid'] else 'note')} on {day(w8['t'])} committed to {w8['what']}.",
                          cite(w8["rid"], w8["quote"]))
                    w.add("finding", f"It was due around {day(due)}; it is now {(now - due).days} days past that. "
                                     f"Found by the follow-up extractor ({self.extractor.model.name}).")
                    tests = self.tests_for(w8["what"])
                    kinds = ["imaging_report"] if w8["kind"] == "imaging" else ["lab_report"]
                    n, q = self.chart_search(api, w8["pid"], kinds + ["consult_note"], w8["t"] + timedelta(minutes=1), tests)
                    w.add("loop_status", f"{'No' if n == 0 else n} matching result{'s' if n != 1 else ''} or consultation{'s' if n != 1 else ''} in the chart since then.",
                          search=q)
                    w.add("assessment", f"Working problem: committed {'imaging' if w8['kind'] == 'imaging' else 'test'} not done. "
                                        "Plan: arrange it and review the result.")
                    self.raise_item(api, key, w8["pid"], "followup_recommended", f"Committed {w8['what']} not done"[:200],
                                    [w8["rid"]], 2, w8["owner"], 10, why=w)
                self.test_watches.remove(w8)

    def tests_for(self, what: str) -> list[str]:
        out = []
        for k, v in TEST_WORDS.items():
            if k in what.lower():
                out += v
        return sorted(set(out))

    def done_since(self, w8) -> bool:
        tests = set(self.tests_for(w8["what"]))
        for t, got in self.tests_by_pid.get(w8["pid"], ()):
            if t > w8["t"] and (not tests or got & tests or any(any(x.lower() in g.lower() for g in got) for x in tests)):
                return True
        return any(t > w8["t"] + timedelta(days=max(1, w8["days"] - 7)) for t in self.consults.get(w8["pid"], ()))
