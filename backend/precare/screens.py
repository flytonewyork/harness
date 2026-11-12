"""Pluggable red-flag screens for the precare line. A screen takes one patient message and says whether it may be
an emergency: `classify(text) -> {"kind": "crisis" | "red_flag" | None, "by": screen name, "evidence": str}`.

- RulesScreen: the fixed word lists in safety.py. No model calls; the floor, always on.
- ReplayScreen: answers from a cache of earlier model outputs (e.g. a `claude -p` batch on the Mac mini), keyed by
  prompt version + model + message hash. Messages not in the cache are counted in `missed` and answered None, so a
  scoring run never silently calls a live model.
- ClaudeCLIScreen: calls `claude -p` live. Mac mini only (CLAUDE.md); never in CI or the cloud.
- UnionScreen: rules first, then the model. The model can only add a flag, never remove one: if either says
  emergency, it is one. A model can't make the line less safe than the rules floor.

The model's evidence must be a verbatim excerpt of the message; if it isn't, the flag still stands (over-trigger)
but the evidence is dropped, so nothing invented reaches an item's reasoning.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import safety

SCREEN_PROMPT_VERSION = "precare-screen-prompt-v1"
SCREEN_SYSTEM = (
    "You screen one message a patient sent to a Hong Kong family clinic's automated pre-visit line. The message may be "
    "English, Cantonese or a mix. Decide only whether it could describe an emergency that needs a doctor or 999 today: "
    "chest pain or pressure, trouble breathing, stroke signs, collapse or fainting, severe bleeding (including black or "
    "tarry stool, vomiting blood), severe allergic reaction, sudden or worst-ever headache, stiff neck with fever, very "
    "low blood sugar, seizures, pregnancy emergencies (bleeding, reduced baby movements, severe pain), overdose or "
    "poisoning, or any risk of suicide or self-harm (including indirect signs such as goodbye letters or giving things "
    "away). When unsure, say it could be an emergency: a false alarm costs a phone call, a miss can cost a life. "
    "Do not diagnose and do not advise. Mentions of the past or of other people (\"my mum had a stroke last year\") "
    "still count if you are unsure. Reply with JSON only: {\"kind\": \"crisis\" | \"red_flag\" | \"none\", "
    "\"evidence\": \"the exact words from the message that made you decide, copied character for character\"}."
)


def key(model: str, text: str) -> str:
    return hashlib.sha256(f"{SCREEN_PROMPT_VERSION}|{model}|{text}".encode()).hexdigest()


def _clean(out: dict | None, text: str, by: str) -> dict:
    kind = (out or {}).get("kind")
    kind = kind if kind in ("crisis", "red_flag") else None
    ev = str((out or {}).get("evidence") or "")
    return {"kind": kind, "by": by, "evidence": ev if ev and ev in text else ""}


class RulesScreen:
    name = "rules:" + safety.SCREEN_VERSION

    def classify(self, text: str) -> dict:
        return {"kind": safety.screen(text), "by": self.name, "evidence": ""}


class ReplayScreen:
    def __init__(self, cache: dict[str, dict], model: str):
        self.cache, self.model, self.name = cache, model, f"replay:{model}:{SCREEN_PROMPT_VERSION}"
        self.missed: list[str] = []

    @classmethod
    def load(cls, path: str | Path) -> "ReplayScreen":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if doc.get("prompt_version") != SCREEN_PROMPT_VERSION:
            raise ValueError(f"cache is for {doc.get('prompt_version')}, this code uses {SCREEN_PROMPT_VERSION}")
        return cls(doc["outputs"], doc["model"])

    def classify(self, text: str) -> dict:
        k = key(self.model, text)
        if k not in self.cache:
            self.missed.append(k)
            return {"kind": None, "by": self.name + " (not in cache)", "evidence": ""}
        return _clean(self.cache[k], text, self.name)


class ClaudeCLIScreen:
    """Live `claude -p`. Mac mini only."""

    def __init__(self, model: str, timeout: int = 60):          # model: whichever the clinic has approved
        self.model, self.timeout, self.name = model, timeout, f"claude-cli:{model}:{SCREEN_PROMPT_VERSION}"

    def raw(self, text: str) -> dict | None:
        p = subprocess.run(["claude", "-p", "--model", self.model, "--append-system-prompt", SCREEN_SYSTEM,
                            "--output-format", "json", f"Message:\n{text}"], capture_output=True, text=True, timeout=self.timeout)
        try:
            body = json.loads(p.stdout).get("result", "")
            return json.loads(re.search(r"\{.*\}", body, re.S).group(0))
        except (ValueError, AttributeError):
            return {"kind": "red_flag", "evidence": ""}            # unreadable output: over-trigger, never silence

    def classify(self, text: str) -> dict:
        return _clean(self.raw(text), text, self.name)


class UnionScreen:
    ORDER = {None: 0, "red_flag": 1, "crisis": 2}

    def __init__(self, *screens):
        self.screens = screens
        self.name = "union(" + ", ".join(s.name for s in screens) + ")"

    def classify(self, text: str) -> dict:
        res = [s.classify(text) for s in self.screens]
        worst = max(res, key=lambda r: self.ORDER[r["kind"]])
        if worst["kind"] is None:
            return {"kind": None, "by": self.name, "evidence": ""}
        return worst


def build_cache(texts: list[str], model: str, out: str | Path, screen=None) -> dict:
    """Run a live screen over messages and write a replay cache (Mac mini):
        python -c "from backend.precare.screens import *; build_cache(texts, MODEL, 'cache.json')"."""
    s = screen or ClaudeCLIScreen(model)
    outputs = {key(model, t): s.raw(t) for t in dict.fromkeys(texts)}
    doc = {"prompt_version": SCREEN_PROMPT_VERSION, "model": model, "outputs": outputs}
    Path(out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc
