"""Swappable composer for one care-line draft.

The first model for trying a user flow is Grok 4.7 extra high, through the existing
Cursor subscription (`cursor-agent` with a grok- id). Sonnet 5.5 is a named later
option and is not selected unless asked for. Extractors in `backend/extract/models.py`
read documents into commitments, so a draft uses this seam instead, and the call
reuses the existing Cursor and Claude readers.

A successful answer is cached by prompt version, model, and input hash. A failed
call is not stored and not scored. Tests pass `call` and do not hit a live model.
Nothing here sends a message or starts the care line.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.extract.cursor_reader import CursorAgentReader
from backend.extract.reader import ClaudeCLIReader, Task, pin

from .flow import QUOTE_MAX

PROMPT_VERSION = "care-draft-1"
# Grok 4.7 extra high. This is the default for a user-flow try.
GROK_MODEL = "grok-4.7-xhigh"
# Named later option. `pin` keeps the id the rest of the repo already uses.
SONNET_MODEL = pin("sonnet")

INSTRUCTIONS = (
    "The document is one message a patient sent to their clinic. "
    "Write the one sentence the patient would read after a doctor taps to send it. "
    "Put that sentence in text and nowhere else. "
    "Do not say the patient is fine, change a dose, invent a result, or promise that someone will call. "
    "Do not include your reasoning, a guardrail, or a template id."
)
LANG_LINE = "Write the sentence in {language}."

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {"text": {"type": "string"}},
}

# Doctor-facing note. It is attached here, after the call, and it is not sent to the model.
REASON = {
    "en": "Held for a doctor tap. The draft gives no result, no dose, and no clearance.",
    "zh": "等醫生撳先至送出。草稿冇結果、冇劑量、亦冇話病人冇事。",
}


class DraftCallError(RuntimeError):
    """The model call failed, or the model name is not one this seam will use. Nothing was stored."""


class _DraftTask(Task):
    """The composer instruction only. The shared reader preamble asks for quotes, which this task does not."""

    def system(self) -> str:
        return self.instructions


def prompt_version() -> str:
    """Prompt version plus a fingerprint of the instruction text, so an edited prompt cannot replay."""
    fp = hashlib.sha256((INSTRUCTIONS + "\n" + LANG_LINE).encode()).hexdigest()[:12]
    return f"{PROMPT_VERSION}:{fp}"


def input_hash(text: str, lang: str) -> str:
    blob = json.dumps({"lang": lang, "text": text}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_key(model: str, text: str, lang: str) -> str:
    """Hash of prompt version, model, and input hash. Those three are the whole key."""
    parts = [prompt_version(), model, input_hash(text, lang)]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def resolve(model: str | None) -> str:
    """Default Grok. Sonnet only by name. Any other grok- id stays on the same subscription path."""
    if model is None or model == "grok":
        return GROK_MODEL
    if model in ("sonnet", SONNET_MODEL):
        return SONNET_MODEL
    if model.startswith("grok-") and "claude" not in model and "composer" not in model:
        return model
    raise DraftCallError(f"{model} is not a care-line draft model")


def task_for(lang: str) -> _DraftTask:
    language = "Cantonese" if lang == "zh" else "English"
    instructions = INSTRUCTIONS + " " + LANG_LINE.format(language=language)
    return _DraftTask(name="care-draft", version=PROMPT_VERSION, instructions=instructions, schema=SCHEMA)


def open_reader(model: str, *, binary: str | None = None):
    """The reader for a live try. Grok uses cursor-agent. Sonnet uses `claude -p` and is not the default."""
    model = resolve(model)
    if model.startswith("grok-"):
        return CursorAgentReader(model, binary=binary)
    if model == SONNET_MODEL:
        return ClaudeCLIReader(model)
    raise DraftCallError(f"{model} is not a care-line draft model")


def patient_text(raw) -> str:
    """The sentence the patient would see. A reasoning field from the model is ignored."""
    if isinstance(raw, dict):
        raw = raw.get("text")
    if not isinstance(raw, str):
        raise DraftCallError("model returned no patient text")
    text = raw.strip()
    if not text:
        raise DraftCallError("model returned no patient text")
    return text


def live_call(model: str, text: str, lang: str) -> str:
    """One live call. Tests do not use this; they pass `call`."""
    if lang not in REASON:
        raise DraftCallError(f"unsupported language {lang}")
    return patient_text(open_reader(model).read(task_for(lang), text))


class DraftCache:
    """Successes only. `put` is the only write, and a failed call never reaches it."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.data: dict = {}
        if self.path and self.path.exists() and self.path.read_text(encoding="utf-8").strip():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, key: str):
        hit = self.data.get(key)
        if isinstance(hit, dict) and isinstance(hit.get("text"), str) and hit["text"].strip():
            return hit
        return None

    def put(self, key: str, value: dict) -> None:
        self.data[key] = value
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=0), encoding="utf-8")


class DraftComposer:
    """`(state, message) -> draft | None`, for `CareFlow`. Default model is Grok."""

    def __init__(self, model: str | None = None, cache: str | Path | None = None, call=None):
        self.model = resolve(model)
        self.cache = DraftCache(cache)
        self.call = call
        self.failures = 0

    def _invoke(self, text: str, lang: str):
        if self.call is not None:
            return self.call(text, lang)
        return live_call(self.model, text, lang)

    def _from_text(self, text: str, lang: str, msg: dict, quote: str) -> dict:
        return {
            "text": text,
            "lang": lang,
            "reasoning": [{
                "step": "finding",
                "text": REASON[lang],
                "cites": [{"record": msg["id"], "quote": quote}],
            }],
        }

    def __call__(self, st: dict, msg: dict) -> dict | None:
        quote = (msg.get("text") or "").strip()[:QUOTE_MAX]
        if not quote:
            return None
        lang = st.get("lang")
        if lang not in REASON:
            lang = "en"
        key = cache_key(self.model, quote, lang)
        hit = self.cache.get(key)
        if hit is not None:
            return self._from_text(hit["text"], lang, msg, quote)
        try:
            text = patient_text(self._invoke(quote, lang))
        except Exception:
            self.failures += 1
            return None
        self.cache.put(key, {"text": text, "lang": lang, "model": self.model, "prompt_version": prompt_version()})
        return self._from_text(text, lang, msg, quote)
