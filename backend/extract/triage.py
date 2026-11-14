"""Urgency screening for a patient's own message (care-line replies, side-effect reports): the shared reader's task.

The schema can only say that a message is worse or carries a red flag, each with the patient's exact words. It has
no field for "fine" or "reassuring": a model reading a message can raise the doctor's attention and never lower it,
and the journey keeps whatever its keyword reader found (union only). Fixed replies to the patient stay fixed.
"""
from __future__ import annotations

from .reader import Task

TASK = Task(
    name="triage", version="triage-1",
    instructions=(
        "The document is one message a patient (or a relative or carer writing for them) sent to their family doctor's "
        "clinic, in Cantonese, English, written Chinese or a mix. You are screening it for the doctor; the doctor reads "
        "every message anyway, so your only job is to say when it needs the doctor sooner. Decide:\n"
        "- red_flag: does the message describe a symptom that needs a same-day emergency decision? For example trouble "
        "breathing or breathless at rest, chest pain or tightness, coughing or vomiting blood, fainting or collapse, blue "
        "or grey lips, swelling of the lips, face or tongue, new confusion or not recognising people, hard to wake, a fit, "
        "sudden weakness of one side or slurred speech, severe bleeding, black stools, thoughts of self-harm, or a very "
        "unwell baby. A symptom someone else reports counts. red_flag_quote: the patient's words that say it.\n"
        "- worse: is the patient not getting better as planned? New or continuing fever, a symptom getting worse or not "
        "improving ('same as before', 'no change' after several days), cannot keep fluids down, a rash or other reaction "
        "after a medicine, new pain, cannot sleep for the symptom, or a worry that something is wrong. worse_quote: the "
        "words that say it.\n"
        "A symptom the message says has stopped or is absent ('no fever since Tuesday', '冇再發燒') is not worse. If the "
        "message only says the patient is better, thanks the clinic or asks an admin question, answer false for both. "
        "When unsure whether something is a red flag, say red_flag true: the doctor decides."),
    schema={"type": "object", "additionalProperties": False,
            "required": ["red_flag", "red_flag_quote", "worse", "worse_quote"],
            "properties": {"red_flag": {"type": "boolean"}, "red_flag_quote": {"type": ["string", "null"]},
                           "worse": {"type": "boolean"}, "worse_quote": {"type": ["string", "null"]}}},
    quoted={"red_flag": "red_flag_quote", "worse": "worse_quote"})

RANK = {"better": 0, "unclear": 1, "worse": 2, "red_flag": 3}


def screen(label: str, hit: str | None, ans: dict | None) -> tuple[str, str | None, bool]:
    """Union of a keyword label and a verified model answer: (label, the words, raised by the model).
    The model can only move a label up (better/unclear -> worse -> red_flag), never down."""
    if not ans:
        return label, hit, False
    for lab, f, q in (("red_flag", "red_flag", "red_flag_quote"), ("worse", "worse", "worse_quote")):
        if ans.get(f) and ans.get(q) and RANK[lab] > RANK[label]:
            return lab, ans[q], True
    return label, hit, False
