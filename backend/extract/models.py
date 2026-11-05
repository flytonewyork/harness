"""Pluggable models for follow-up extraction. Which model may touch EC data is still open, so the extractor
takes any object with `name` and `run(docs) -> {doc_id: [raw commitment dicts]}`.

- RulesModel: regular expressions for the shorthand the rules engine already knows. No model calls; the floor.
- ReplayModel: answers from a cache of earlier outputs (e.g. a `claude -p` batch run); unknown documents fall
  back to another model (default: rules) and are counted, so a scoring run never silently calls a live model.
- ClaudeCLIModel: calls `claude -p` live (same prompt and flags as claude_runner.py).
"""
from __future__ import annotations

import json
import re
import subprocess

from .prompt import PROMPT_VERSION, SCHEMA, SYSTEM, build_prompt

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10, "twelve": 12, "a": 1, "an": 1}
UNIT = {"day": 1, "week": 7, "month": 30, "year": 365}


def _num(s: str) -> int | None:
    s = s.lower()
    return int(s) if s.isdigit() else WORDS.get(s)


class RulesModel:
    """Shorthand and plain-English timing that a regular expression can catch."""
    name = "rules"

    RV = re.compile(r"\bRV\s+(\d+)\s*/\s*(7|52|12)\b\.?(\s*prn)?", re.I)
    RV_PRN = re.compile(r"\bRV\s+prn\b", re.I)
    TIMED = re.compile(r"(?P<span>(?P<verb>follow[- ]?up|review|see (?:you|him|her|them)? ?again|recheck|repeat (?P<test>[A-Za-z0-9+/ ]{1,30}?))"
                       r"[^.\n]{0,60}?\bin (?P<n>\d+|one|two|three|four|five|six|eight|ten|twelve|a|an) "
                       r"(?P<unit>day|week|month|year)s?)", re.I)
    COND = re.compile(r"\b(prn|if (?:not|no|worse|symptoms|fever)|should .{0,20} worsen|return if)\b", re.I)
    RESULTS = re.compile(r"results? to be (?:reviewed|explained)", re.I)

    def run(self, docs: list[dict]) -> dict[str, list[dict]]:
        return {d["doc_id"]: self.one(d["text"]) for d in docs}

    def one(self, text: str) -> list[dict]:
        out = []
        for m in self.RV.finditer(text):
            n, unit = int(m.group(1)), int(m.group(2))
            days = n * {7: 1, 52: 7, 12: 30}[unit]
            rest = text[m.end(): text.find("\n", m.end()) if text.find("\n", m.end()) >= 0 else len(text)]
            out.append({"kind": "review", "what": "review", "due_in_days": days, "owner": "clinic",
                        "conditional": bool(m.group(3)) or bool(self.COND.search(rest)), "quote": m.group(0).strip().rstrip(".")})
        for m in self.RV_PRN.finditer(text):
            out.append({"kind": "review", "what": "review as needed", "due_in_days": None, "owner": "patient",
                        "conditional": True, "quote": m.group(0)})
        for m in self.TIMED.finditer(text):
            verb = m.group("verb").lower()
            days = (_num(m.group("n")) or 1) * UNIT[m.group("unit").lower()]
            if verb.startswith("repeat"):
                test = (m.group("test") or "").strip()
                kind = "imaging" if re.search(r"imaging|CT|X-?ray|ultrasound|MRI|mammo", test, re.I) else "repeat_test"
                what = f"repeat {test}"
            else:
                kind, what = "review", "follow-up review"
            if re.search(r"CT|imaging", m.group("span")) and kind == "review":
                kind, what = "imaging", "follow-up imaging"
            line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end()) if text.find("\n", m.end()) >= 0 else len(text)]
            out.append({"kind": kind, "what": what, "due_in_days": days,
                        "owner": "hospital" if "family doctor" not in line and "hospital" in line.lower() else "clinic",
                        "conditional": bool(self.COND.search(line)), "quote": m.group("span")})
        for m in self.RESULTS.finditer(text):
            out.append({"kind": "result_check", "what": "review results", "due_in_days": None, "owner": "clinic",
                        "conditional": False, "quote": m.group(0)})
        return out


class ReplayModel:
    """Outputs from an earlier run (cache); falls back for documents it has not seen."""

    def __init__(self, cache: dict[str, list[dict]], name: str, fallback=None):
        self.cache, self.name = cache, name
        self.fallback = fallback if fallback is not None else RulesModel()
        self.misses = 0

    def run(self, docs: list[dict]) -> dict[str, list[dict]]:
        out, miss = {}, []
        for d in docs:
            if d["doc_id"] in self.cache:
                out[d["doc_id"]] = self.cache[d["doc_id"]]
            else:
                miss.append(d)
        if miss:
            self.misses += len(miss)
            out.update(self.fallback.run(miss))
        return out


class ClaudeCLIModel:
    """Live `claude -p` (use claude_runner.py for big batches)."""

    def __init__(self, model: str = "sonnet", binary: str = "claude", timeout: int = 900):
        self.model, self.binary, self.timeout = model, binary, timeout
        self.name = f"claude-cli:{model}"

    def run(self, docs: list[dict]) -> dict[str, list[dict]]:
        cmd = [self.binary, "-p", "--model", self.model, "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config",
               "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(SCHEMA)]
        p = subprocess.run(cmd, input=build_prompt(docs), capture_output=True, text=True, timeout=self.timeout)
        if p.returncode != 0:
            raise RuntimeError(f"claude -p failed ({p.returncode}): {p.stderr[:300]}")
        return parse_cli_output(p.stdout)


def parse_cli_output(stdout: str) -> dict[str, list[dict]]:
    """`claude -p --output-format json` wraps the answer in an envelope; with --json-schema the answer is in
    `structured_output`, otherwise it is JSON text in `result` (possibly fenced)."""
    env = json.loads(stdout)
    payload = env.get("structured_output")
    if payload is None:
        txt = env.get("result", "")
        m = re.search(r"\{.*\}", txt, re.S)
        payload = json.loads(m.group(0)) if m else {"results": []}
    return {r["doc_id"]: r.get("commitments", []) for r in payload.get("results", [])}


__all__ = ["ClaudeCLIModel", "PROMPT_VERSION", "ReplayModel", "RulesModel", "parse_cli_output"]
