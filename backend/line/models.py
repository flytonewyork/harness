"""The model port: what the line may ask a model to do, and nothing else.

A model drafts; it never sends. Three jobs:
- `compose`: the doctor's voice note or text → a transcript and structured items, each carrying the exact
  transcript words it came from (`quote`), plus patient-facing English and Chinese.
- `draft_reply`: a patient's question → a draft for the doctor, grounded only in the approved plan.
- `classify`: a patient's message → one label, when the rules can't place it.

`FakeModel` is used in tests, CI, sim and every Claude session. `VertexGemini` is the only model that may read real
patient content (10 Nov decision), in Google Cloud's Hong Kong region by default.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

SECTIONS = ("take", "watch", "next", "other")
LABELS = ("question", "repeat", "result", "plan", "thanks", "greeting", "other")


class Model(Protocol):
    name: str

    def compose(self, *, kind: str, audio: bytes | None = None, mime: str | None = None, text: str | None = None,
                patient: dict | None = None, doctor: dict | None = None, context: str = "") -> dict: ...

    def draft_reply(self, *, question: str, plan_text: str, doctor: dict, lang: str) -> dict: ...

    def classify(self, text: str) -> str: ...


PROMPT_VERSION = "line-compose-v1"

COMPOSE_PROMPT = """You turn a doctor's own words, dictated after a consultation in English, Cantonese or both, into a
plan for the patient. You are a scribe, not a doctor.

Rules:
1. First write `transcript`: exactly what the doctor said, word for word, in the language they spoke (Cantonese in
   Traditional Chinese characters; English as spoken; mixed is fine). Do not tidy it.
2. Then list `items`. Every item must come from something the doctor said. `quote` is the exact words from your
   transcript that the item comes from, copied character for character. If you can't quote it, leave it out.
3. Never add advice, doses, numbers, timings, warnings or reassurance the doctor did not say. Never add "normal",
   "nothing to worry about" or a diagnosis the doctor did not name.
4. `section`: take (a medicine to take, stop or change), watch (what to watch for and when to seek help), next
   (tests, visits, referrals, when), other.
5. `en` and `zh`: the item for the patient, short and plain, in English and in standard written Chinese with
   Traditional characters. Keep every number, dose and time exactly as the doctor said it.
6. `med`: for a `take` item only, the medicine and strength as a short name (e.g. "Amlodipine 5 mg"), else "".
7. `checkin_days`: the day the doctor wants a check-in, if they said one, else null.
8. `reminders`: things the doctor said should happen on a date or after some days (a blood test in 2 weeks), each
   with `quote`, `en`, `zh` and `due_in_days`.
Kind of message: {kind}. {context}
Return only JSON matching the schema."""

REPLY_PROMPT = """A patient wrote to their doctor's care line. Draft a reply for the doctor to approve or change.
Use ONLY facts in the doctor's approved plan below. If the plan doesn't answer the question, the draft says the
doctor will look into it. Never add advice, numbers or reassurance that is not in the plan. Short, warm, plain.
Write it in {lang_name}.
Approved plan:
{plan}
Patient's message:
{question}
Return JSON: {{"draft": "...", "uses": ["exact phrases from the plan you relied on"]}}"""

CLASSIFY_PROMPT = """Label this message to a doctor's care line with exactly one of: {labels}.
question = a clinical or care question for the doctor; repeat = asks for more of a medicine; result = about a lab or
scan report; plan = asks for their plan again; thanks; greeting; other. Message: {text}
Return JSON: {{"label": "..."}}"""


class FakeModel:
    """Deterministic stand-in. `audio` is treated as UTF-8 text so tests can 'dictate'. Each sentence becomes an
    item, quoted word for word; a sentence naming a medicine with a strength becomes a `take` item."""
    name = "fake"

    MED = re.compile(r"\b([A-Z][a-z]+(?:in|ol|ide|ine|one|an|am|il|ate|en))\s+(\d+(?:\.\d+)?\s?(?:mg|mcg|g))", re.I)

    def compose(self, *, kind, audio=None, mime=None, text=None, patient=None, doctor=None, context=""):
        said = (audio.decode("utf-8") if audio else text or "").strip()
        items, reminders, checkin = [], [], None
        for s in [x.strip() for x in re.split(r"(?<=[.。!！?？])\s*", said) if x.strip()]:
            m = self.MED.search(s)
            low = s.lower()
            if m:
                sec, med = "take", f"{m.group(1).capitalize()} {m.group(2)}"
            elif re.search(r"come back|see (a doctor|me)|a&e|999|if (it|you|the)|watch|warning|紅|急症", low):
                sec, med = "watch", ""
            elif re.search(r"test|blood|scan|x-?ray|review|follow|refer|visit|weeks?|days?|驗|覆診", low):
                sec, med = "next", ""
            else:
                sec, med = "other", ""
            items.append({"section": sec, "quote": s, "en": s, "zh": s, "med": med})
            d = re.search(r"in (\d+) (day|week)s?", low)
            if d and sec == "next":
                reminders.append({"quote": s, "en": s, "zh": s, "due_in_days": int(d.group(1)) * (7 if d.group(2) == "week" else 1)})
            c = re.search(r"check.?in (?:on )?day (\d+)", low)
            if c:
                checkin = int(c.group(1))
        return {"transcript": said, "items": items, "checkin_days": checkin, "reminders": reminders, "model": self.name,
                "prompt": PROMPT_VERSION}

    def draft_reply(self, *, question, plan_text, doctor, lang):
        first = next((ln.strip("• ").strip() for ln in plan_text.splitlines() if ln.startswith("•")), "")
        if first:
            draft = (f"Thanks for your message. From your plan: {first}" if lang == "en" else f"多謝你嘅訊息。你嘅計劃寫住：{first}")
            return {"draft": draft, "uses": [first], "model": self.name}
        return {"draft": "Thanks for your message. I'll look into it and get back to you." if lang == "en"
                else "多謝你嘅訊息，我會睇吓再覆你。", "uses": [], "model": self.name}

    def classify(self, text):
        return "question"


class VertexGemini:
    """Gemini on Vertex AI, Hong Kong region by default. Structured JSON out, temperature 0, no tools."""

    def __init__(self, project: str, region: str = "asia-east2", model: str = "gemini-2.5-flash"):
        from google import genai  # only in the live image
        from google.genai import types
        self.types = types
        self.client = genai.Client(vertexai=True, project=project, location=region)
        self.model = model
        self.name = f"vertex:{model}@{region}"

    def _json(self, parts, schema=None) -> dict:
        cfg = self.types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               **({"response_schema": self.types.Schema.model_validate(schema)} if schema else {}))
        r = self.client.models.generate_content(model=self.model, contents=parts, config=cfg)
        return json.loads(r.text)

    def compose(self, *, kind, audio=None, mime=None, text=None, patient=None, doctor=None, context=""):
        T = self.types
        item = {"type": "OBJECT", "properties": {"section": {"type": "STRING", "enum": list(SECTIONS)}, "quote": {"type": "STRING"},
                                                 "en": {"type": "STRING"}, "zh": {"type": "STRING"}, "med": {"type": "STRING"}},
                "required": ["section", "quote", "en", "zh", "med"]}
        rem = {"type": "OBJECT", "properties": {"quote": {"type": "STRING"}, "en": {"type": "STRING"}, "zh": {"type": "STRING"},
                                                "due_in_days": {"type": "INTEGER"}}, "required": ["quote", "en", "zh", "due_in_days"]}
        schema = {"type": "OBJECT", "properties": {"transcript": {"type": "STRING"}, "items": {"type": "ARRAY", "items": item},
                                                   "checkin_days": {"type": "INTEGER", "nullable": True},
                                                   "reminders": {"type": "ARRAY", "items": rem}},
                  "required": ["transcript", "items", "checkin_days", "reminders"]}
        parts = [COMPOSE_PROMPT.format(kind=kind, context=context)]
        if audio:
            parts.append(T.Part.from_bytes(data=audio, mime_type=(mime or "audio/ogg").split(";")[0]))
        else:
            parts.append("Doctor's words (typed):\n" + (text or ""))
        out = self._json(parts, schema)
        out.update(model=self.name, prompt=PROMPT_VERSION)
        return out

    def draft_reply(self, *, question, plan_text, doctor, lang):
        out = self._json([REPLY_PROMPT.format(plan=plan_text, question=question,
                                              lang_name="English" if lang == "en" else "standard written Chinese, Traditional characters")])
        out["model"] = self.name
        return out

    def classify(self, text):
        try:
            label = self._json([CLASSIFY_PROMPT.format(labels=", ".join(LABELS), text=text)]).get("label", "other")
        except Exception:
            return "other"
        return label if label in LABELS else "other"


def open_model(cfg) -> Model:
    if cfg.model == "fake":
        return FakeModel()
    if cfg.model == "vertex":
        return VertexGemini(cfg.vertex_project, cfg.vertex_region, cfg.vertex_model)
    raise ValueError(f"unknown model {cfg.model!r}")
