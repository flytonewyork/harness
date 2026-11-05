"""The extraction prompt. Standard library only, so the batch runner can run on any machine with `claude`.

Bump PROMPT_VERSION whenever the wording, kinds or schema change: cached outputs are keyed by it.
"""
from __future__ import annotations

import json

PROMPT_VERSION = "fu-extract-v1"

KINDS = ["review", "repeat_test", "imaging", "referral", "result_check", "medication_monitoring", "other"]
OWNERS = ["clinic", "patient", "hospital", "other"]

SYSTEM = ("You read clinical documents from a Hong Kong private GP clinic and list the follow-up commitments in each "
          "one. You report only what each document says. You never infer, advise, diagnose or add anything.")

INSTRUCTIONS = """For each document below, list every follow-up commitment: something the clinic, the patient or a hospital or specialist says will happen later, which someone has to check actually happened.

Kinds:
- review: a follow-up visit or review ("RV 3/12", "see again in 2 weeks", "follow up with family doctor in 2 weeks")
- repeat_test: a test to repeat or do later ("repeat K+ in 1 week", "HbA1c in 3 months")
- imaging: further imaging ("follow-up CT thorax in 3 months", "repeat imaging in 6-8 weeks")
- referral: a referral to make, or a reply to chase
- result_check: results to be reviewed or explained later ("results to be reviewed")
- medication_monitoring: monitoring after starting or changing a medicine
- other: any other follow-up commitment

Clinic shorthand: RV means review. n/7 is n days, n/52 is n weeks, n/12 is n months (count a month as 30 days). "prn" means as needed.

For each commitment give:
- kind: one of the kinds above
- what: a few words, such as "review BP" or "repeat RFT"
- due_in_days: whole days from the document date until it is due, or null if the document gives no timing
- owner: who has to make it happen: "clinic", "patient", "hospital" or "other"
- conditional: true if it only happens should something occur ("RV prn", "return if not better", "if fever persists"); safety-net advice is conditional
- quote: the shortest span, copied character for character from the document, that states the commitment

Rules: one entry per distinct commitment, and an empty list when a document has none. Judge each document on its own. Do not invent anything or use outside knowledge. Copy quotes exactly, including abbreviations and punctuation.

Return one result for every document, with its doc_id."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "commitments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": KINDS},
                                "what": {"type": "string"},
                                "due_in_days": {"type": ["integer", "null"]},
                                "owner": {"type": "string", "enum": OWNERS},
                                "conditional": {"type": "boolean"},
                                "quote": {"type": "string"},
                            },
                            "required": ["kind", "what", "due_in_days", "owner", "conditional", "quote"],
                        },
                    },
                },
                "required": ["doc_id", "commitments"],
            },
        }
    },
    "required": ["results"],
}

KIND_LABEL = {"consult_note": "consultation note", "discharge_summary": "hospital discharge summary",
              "aed_summary": "A&E attendance summary", "specialist_letter": "specialist letter",
              "imaging_report": "imaging report", "message": "patient message", "referral_letter": "referral letter"}


def build_prompt(docs: list[dict]) -> str:
    """docs: [{"doc_id", "kind", "date", "text"}]. Nothing else about the patient is shown to the model."""
    payload = [{"doc_id": d["doc_id"], "type": KIND_LABEL.get(d["kind"], d["kind"]), "date": d["date"], "text": d["text"]}
               for d in docs]
    return (INSTRUCTIONS + "\n\nReply with JSON only, in the form {\"results\": [{\"doc_id\": ..., \"commitments\": [...]}]}."
            + "\n\nDocuments:\n" + json.dumps(payload, ensure_ascii=False, indent=1))
