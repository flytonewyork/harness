"""Models that can read patient messages on top of the rules floor (wrap one in reader.ModelReader).

- ClaudeCLIReader: `claude -p` (Mac mini only, per CLAUDE.md), one message at a time, JSON out. Outputs are cached by
  prompt version + model + message hash, so a scoring run replays without calling a model.
- ReplayReader: answers only from that cache; an unseen message returns nothing (the floor still reads it) and is
  counted, so a scoring run never silently calls a live model.

Whatever a model returns, ModelReader keeps a finding only if its quote is verbatim in the message, and it can add
findings or raise their urgency, never remove or lower one. The model never sees other patients' data: one message
and the classes of medicine the patient takes.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

PROMPT_VERSION = "med-reader-1"
SYSTEM = """You read one message a patient sent to their GP clinic in Hong Kong (Cantonese, English or mixed) after a
change to their medicines. List anything the doctor must see. You never advise the patient and never decide anything.
Return JSON only: {"findings": [{"kind": one of red_flag|side_effect|crisis|confusion|adherence|dose_question|repeat|question,
"label": short English label, "urgency": 1-4 (4 = emergency now, 3 = same day, 2 = within 3 days, 1 = routine),
"quote": the exact words from the message, copied character for character}]}.
Negated symptoms ("no rash"), someone else's symptoms and long-past events are not findings, except an emergency about
someone else (urgency 3). If nothing needs the doctor, return {"findings": []}."""


def _key(model: str, text: str, classes: list[str]) -> str:
    return hashlib.sha256(json.dumps([PROMPT_VERSION, model, text, sorted(classes)], ensure_ascii=False).encode()).hexdigest()


class _Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=0))


class ClaudeCLIReader:
    def __init__(self, model: str = "claude-opus-5-5", cache: str | Path = "results/med_reader_cache.json", timeout: int = 60):
        self.model, self.timeout = model, timeout
        self.name = f"claude-cli[{model}]"
        self.cache = _Cache(cache)

    def run(self, text: str, context: dict) -> list[dict]:
        classes = list(context.get("classes", []))
        k = _key(self.model, text, classes)
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        prompt = f"{SYSTEM}\n\nThe patient takes: {', '.join(classes) or 'not known'}.\nMessage:\n{text}"
        out = subprocess.run(["claude", "-p", prompt, "--model", self.model, "--output-format", "json"],
                             capture_output=True, text=True, timeout=self.timeout, check=True).stdout
        body = json.loads(out)
        body = json.loads(body["result"]) if isinstance(body, dict) and "result" in body else body
        found = list(body.get("findings", [])) if isinstance(body, dict) else []
        self.cache.put(k, found)
        return found


class ReplayReader:
    def __init__(self, model: str = "claude-opus-5-5", cache: str | Path = "results/med_reader_cache.json"):
        self.model = model
        self.name = f"replay[{model}]"
        self.cache = _Cache(cache)
        self.misses = 0

    def run(self, text: str, context: dict) -> list[dict]:
        hit = self.cache.get(_key(self.model, text, list(context.get("classes", []))))
        if hit is None:
            self.misses += 1
            return []
        return hit
