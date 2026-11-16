"""What does the patient want? A small, fixed set of answers.

The model never writes to a patient. It only picks one label from INTENTS for a free-text message the rules
couldn't place; the agent then does that label's fixed steps. Red flags, stop and clinical questions are decided by
the deterministic safety layer first, so no model can talk the agent out of them.

Pluggable like the extractor (`backend/extract/models.py`):
- RulesIntent: keywords in English and Cantonese. No model calls; the floor, and what CI scores.
- ClaudeAPIIntent: the Anthropic API (`pip install anthropic`, ANTHROPIC_API_KEY). Structured output, one label.
- ClaudeCLIIntent: `claude -p` with the logged-in CLI on the Mac mini.
A model answer is cached by prompt version + model + text hash, and a miss filled by the rules floor carries the
floor's name (`fell_back`), so one model is never credited with another's answers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

PROMPT_VERSION = "careline-intent-v1"
INTENTS = ("book", "cancel", "info", "staff", "results", "tests", "repeat", "plan", "hospital", "receipts", "fees",
           "sick_leave", "clinical", "greeting", "other")

KEYWORDS = [
    ("staff", r"\b(talk to|speak to|call me|a person|human|staff|nurse|receptionist)\b|搵職員|真人|打俾我|姑娘"),
    ("cancel", r"\bcancel\b|取消預約|唔嚟得|改唔到期"),
    ("book", r"\b(book|appointment|reschedul|change (my )?(visit|appointment)|see the doctor)\b|預約|約醫生|改期|睇醫生|掛號|book位"),
    ("info", r"\b(open(ing)? hours?|hours|address|where are you|when are you open|holiday|location)\b|開門|幾點|地址|喺邊|假期|營業時間|診所資料"),
    ("fees", r"\b(fee|fees|cost|price|how much|voucher|insurance)\b|收費|幾錢|醫療券|保險|價錢"),
    ("receipts", r"\b(receipt|invoice|letter|document|certificate of attendance)\b|收據|單據|文件|證明"),
    ("results", r"\b(result|report|blood test results?|lab)\b.*\b(back|ready|out|yet)\b|\bmy results?\b|\bmy reports?\b|報告|結果|驗血結果"),
    ("repeat", r"\b(repeat|refill|more|run(ning)? out of|renew)\b.*\b(medicine|medication|pills?|tablets?|prescription|drug)|\brepeat prescription\b|\brefill\b|覆配|配藥|攞藥|食完藥|續藥"),
    ("plan", r"\bmy plan\b|\bplan\b|\bwhat did the doctor say\b|計劃|醫生講咗咩|跟進安排"),
    ("hospital", r"\b(discharge|hospital|A&E|admitted)\b|出院|住院|急症室|醫院"),
    ("tests", r"\b(blood test|x-?ray|scan|ultrasound|fasting|prepare for)\b|驗血|照X光|照超聲波|空肚|空腹|掃描"),
    ("sick_leave", r"sick leave|medical certificate|\bMC\b|病假"),
    ("greeting", r"^\s*(hi|hello|hey|menu|start|good (morning|afternoon|evening)|你好|哈囉|選單|早晨|join|加入)\W*\s*$"),
]


class RulesIntent:
    name = "rules"

    def classify(self, text: str) -> str:
        for label, pat in KEYWORDS:
            if re.search(pat, text, re.I):
                return label
        return "other"


SYSTEM = ("You route WhatsApp messages from a patient to a family clinic's care line. Reply with the one label that "
          "best says what the patient wants. Labels: " + ", ".join(INTENTS) + ". 'clinical' is any question about "
          "symptoms, medicines, doses, side effects or what a result means. 'other' if none fits. Never answer the patient.")
SCHEMA = {"type": "object", "properties": {"intent": {"type": "string", "enum": list(INTENTS)}},
          "required": ["intent"], "additionalProperties": False}


class _Cached:
    name = "?"
    model = "?"

    def __init__(self, cache_dir: str | Path | None = None, floor: RulesIntent | None = None):
        self.cache = Path(cache_dir or os.environ.get("CARELINE_DATA", Path.home() / ".tmrw-careline")) / "intent-cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.floor = floor or RulesIntent()
        self.fell_back = 0

    def classify_named(self, text: str) -> tuple[str, str]:
        """(label, name of whoever answered)."""
        key = hashlib.sha256(f"{PROMPT_VERSION}|{self.model}|{text}".encode()).hexdigest()[:24]
        f = self.cache / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text())["intent"], self.name
        try:
            label = self._ask(text)
            if label not in INTENTS:
                raise ValueError(label)
        except Exception:  # noqa: BLE001 — any failure falls to the floor, under the floor's name
            self.fell_back += 1
            return self.floor.classify(text), f"{self.floor.name} (fell_back from {self.name})"
        f.write_text(json.dumps({"intent": label, "model": self.model, "prompt": PROMPT_VERSION}))
        return label, self.name

    def classify(self, text: str) -> str:
        return self.classify_named(text)[0]


class ClaudeAPIIntent(_Cached):
    def __init__(self, model: str | None = None, **kw):
        super().__init__(**kw)
        import anthropic  # optional dependency
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CARELINE_MODEL", "claude-opus-5-5")
        self.name = f"claude-api:{self.model}"

    def _ask(self, text: str) -> str:
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=256, system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": text}])
        if r.stop_reason == "refusal":
            raise ValueError("refusal")
        return json.loads(next(b.text for b in r.content if b.type == "text"))["intent"]


class ClaudeCLIIntent(_Cached):
    def __init__(self, model: str | None = None, **kw):
        super().__init__(**kw)
        self.model = model or os.environ.get("CARELINE_MODEL", "claude-opus-5-5")
        self.name = f"claude-cli:{self.model}"

    def _ask(self, text: str) -> str:
        out = subprocess.run(["claude", "-p", "--model", self.model, "--output-format", "json", "--append-system-prompt", SYSTEM,
                              f"Message: {json.dumps(text, ensure_ascii=False)}\nAnswer with JSON only: {{\"intent\": \"<label>\"}}"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        result = json.loads(out).get("result", out)
        return json.loads(re.search(r"\{.*\}", result, re.S).group(0))["intent"]


def make(name: str | None):
    name = name or os.environ.get("CARELINE_INTENT", "rules")
    if name == "rules":
        return RulesIntent()
    if name == "claude-api":
        return ClaudeAPIIntent()
    if name == "claude-cli":
        return ClaudeCLIIntent()
    raise ValueError(f"unknown intent model {name!r}: rules, claude-api or claude-cli")
