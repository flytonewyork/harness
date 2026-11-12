"""Red-flag screens for the precare line. A screen takes one patient message and says whether it may be an
emergency: `classify(text) -> {"kind": "crisis" | "red_flag" | None, "by": who decided, "evidence": str}`.

- RulesScreen: the fixed word lists in safety.py. No model calls; the floor, always on.
- ReaderScreen: the shared model reader (backend/extract/reader.py) running this module's TASK. Its answer is
  verified first (`reader.verify`): a flag whose quote is not word for word in the message is dropped and counted.
  A message the reader never answered (a replay miss) returns None and falls back to the rules, and says so.
- UnionScreen: rules first, then the reader. The model can only add a flag, never remove one: if either says
  emergency, it is one. A model can't make the line less safe than the rules floor.

No model runs in CI or the cloud. A batch run on the Mac mini (Thomas's go-ahead) uses
`python -m backend.extract.reader_runner --task <this TASK>` and the eval replays it.
"""
from __future__ import annotations

from backend.extract.reader import Task, verify

from . import safety

TASK = Task(
    name="precare_red_flag",
    version="precare-red-flag-v1",
    instructions=(
        "The document is one message a patient sent to a Hong Kong family clinic's automated pre-visit line, in English, "
        "Cantonese or a mix. Decide only whether it could describe an emergency that needs a doctor or 999 today: chest pain "
        "or pressure, trouble breathing, stroke signs, collapse or fainting, severe bleeding (including black or tarry stool, "
        "vomiting blood), severe allergic reaction, sudden or worst-ever headache, stiff neck with fever, very low blood "
        "sugar, seizures, a very sick child, pregnancy emergencies, overdose or poisoning, or any risk of suicide or "
        "self-harm (including indirect signs such as goodbye letters or giving things away). When unsure, say it could be "
        "an emergency: a false alarm costs a phone call, a miss can cost a life. Do not diagnose and do not advise. "
        "kind is crisis for suicide or self-harm risk, red_flag for any other emergency, none otherwise; evidence_quote is "
        "the exact words that made you decide."),
    schema={"type": "object", "properties": {"kind": {"type": "string", "enum": ["crisis", "red_flag", "none"]},
                                             "evidence_quote": {"type": ["string", "null"]}},
            "required": ["kind", "evidence_quote"], "additionalProperties": False},
    quoted={"kind": "evidence_quote"},
)


class RulesScreen:
    name = "rules:" + safety.SCREEN_VERSION

    def classify(self, text: str) -> dict:
        return {"kind": safety.screen(text), "by": self.name, "evidence": ""}


class ReaderScreen:
    def __init__(self, reader):
        self.reader = reader
        self.name = f"reader:{reader.name}:{TASK.version}"
        self.dropped = 0                                    # flags thrown away because their quote wasn't verbatim
        self.fell_back = 0                                  # messages the reader never answered

    def classify(self, text: str) -> dict:
        raw = self.reader.read(TASK, text)
        if raw is None:
            self.fell_back += 1
            return {"kind": None, "by": f"{self.name} (no answer; rules only)", "evidence": ""}
        if raw.get("kind") == "none":
            return {"kind": None, "by": self.name, "evidence": ""}
        ans, dropped = verify(TASK, text, raw)
        self.dropped += bool(dropped)
        kind = (ans or {}).get("kind")
        return {"kind": kind if kind in ("crisis", "red_flag") else None, "by": self.name,
                "evidence": (ans or {}).get("evidence_quote") or ""}


class UnionScreen:
    ORDER = {None: 0, "red_flag": 1, "crisis": 2}

    def __init__(self, *screens):
        self.screens = screens
        self.name = "union(" + ", ".join(s.name for s in screens) + ")"

    def classify(self, text: str) -> dict:
        res = [s.classify(text) for s in self.screens]
        worst = max(res, key=lambda r: self.ORDER[r["kind"]])
        return worst if worst["kind"] else {"kind": None, "by": self.name, "evidence": ""}


def with_reader(reader) -> object:
    """The screen the agent uses: rules alone, or rules + the shared reader (union only)."""
    return RulesScreen() if reader is None else UnionScreen(RulesScreen(), ReaderScreen(reader))
