"""Models that can read patient messages on top of the rules floor (wrap one in reader.ModelReader).

- ClaudeCLIReader: `claude -p` (Mac mini only, per CLAUDE.md), one message at a time, JSON out. Same safe call as the
  shared reader: system prompt, no tools, no MCP, empty working directory, and a login error stops the batch.
  Outputs are cached by prompt fingerprint + model + message hash, so a scoring run replays without calling a model.
- ReplayReader: answers only from that cache; an unseen message returns nothing (the floor still reads it), sets
  `last_miss`, and is counted. The floor's findings are not written back into the cache.

Whatever a model returns, ModelReader keeps a finding only if its quote is verbatim in the message, and it can add
findings or raise their urgency, never remove or lower one. The model never sees other patients' data: one message
and the classes of medicine the patient takes.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from backend.extract.reader import LOGIN, LoginError, pin

PROMPT_VERSION = "med-reader-1"
SYSTEM = """You read one message a patient sent to their GP clinic in Hong Kong (Cantonese, English or mixed) after a
change to their medicines. List anything the doctor must see. You never advise the patient and never decide anything.
Return JSON only: {"findings": [{"kind": one of red_flag|side_effect|crisis|confusion|adherence|dose_question|repeat|question,
"label": short English label, "urgency": 1-4 (4 = emergency now, 3 = same day, 2 = within 3 days, 1 = routine),
"quote": the exact words from the message, copied character for character}]}.
Negated symptoms ("no rash"), someone else's symptoms and long-past events are not findings, except an emergency about
someone else (urgency 3). If nothing needs the doctor, return {"findings": []}."""


def _prompt_fp() -> str:
    """Hash of the system prompt. A prompt edit cannot replay answers written for the old one."""
    return hashlib.sha256(SYSTEM.encode()).hexdigest()[:12]


def _key(model: str, text: str, classes: list[str]) -> str:
    return hashlib.sha256(json.dumps([PROMPT_VERSION, _prompt_fp(), model, text, sorted(classes)], ensure_ascii=False).encode()).hexdigest()


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
    def __init__(self, model: str = "claude-opus-5-5", cache: str | Path = "results/med_reader_cache.json", timeout: int = 60,
                 binary: str = "claude"):
        self.model, self.timeout, self.binary = pin(model), timeout, binary
        self.name = f"claude-cli[{self.model}]"
        self.cache = _Cache(cache)

    def command(self) -> list[str]:
        return [self.binary, "-p", "--model", self.model, "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config",
                "--disable-slash-commands", "--no-session-persistence", "--output-format", "json"]

    def run(self, text: str, context: dict) -> list[dict]:
        classes = list(context.get("classes", []))
        k = _key(self.model, text, classes)
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        user = f"The patient takes: {', '.join(classes) or 'not known'}.\n<document>\n{text}\n</document>"
        with tempfile.TemporaryDirectory(prefix="med-reader-") as empty:
            p = subprocess.run(self.command(), input=user, capture_output=True, text=True, timeout=self.timeout, cwd=empty)
        try:                                       # claude -p reports some failures (login, API errors) only in its envelope
            env = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            env = {}
        err = (p.stderr + " " + (str(env.get("result", "")) if env.get("is_error") else "")).strip()
        if p.returncode != 0 or env.get("is_error") or not env:
            if LOGIN.search(err):
                raise LoginError(f"claude is not logged in (run `claude`, then /login): {err[:200]}")
            raise RuntimeError(f"claude -p failed ({p.returncode}): {err[:300]}")
        ans = env.get("structured_output")
        if ans is None:
            raw = env.get("result", "")
            if isinstance(raw, dict):
                ans = raw
            elif isinstance(raw, str) and raw.strip():
                try:
                    ans = json.loads(raw)
                except ValueError:
                    m = re.search(r"\{.*\}", raw, re.S)
                    ans = json.loads(m.group(0)) if m else None
        if not isinstance(ans, dict):
            raise RuntimeError("claude -p returned no JSON findings")
        found = list(ans.get("findings", []))
        self.cache.put(k, found)                   # the model's answer only; never the rules floor
        return found


class ReplayReader:
    def __init__(self, model: str = "claude-opus-5-5", cache: str | Path = "results/med_reader_cache.json"):
        self.model = pin(model)
        self.name = f"replay[{self.model}]"
        self.cache = _Cache(cache)
        self.misses = 0
        self.last_miss = False

    def run(self, text: str, context: dict) -> list[dict]:
        hit = self.cache.get(_key(self.model, text, list(context.get("classes", []))))
        self.last_miss = hit is None
        if hit is None:
            self.misses += 1
            return []                              # do not cache the floor's findings as the model's
        return hit
