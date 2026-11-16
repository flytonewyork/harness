"""Live reader for one Cursor model id. Never calls `claude -p`.

This account cannot set a replacement system prompt (`--system-prompt` is rejected), so the task's
system text is the instruction and the clinic document is only the fenced user data. The call is still
one shot from an empty directory: ask mode, no MCP approval, no saved session is resumed, sandbox on.
Composer is not an eval model. A failed call raises and is not recorded.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HOLD = re.compile(r"account is on hold|can'?t use Claude|cannot use Claude", re.I)


class CursorCallError(RuntimeError):
    """The model call failed. The line is not written."""


class CursorTimeout(CursorCallError):
    """The call exceeded its time limit. Leave it unwritten and keep going."""


def cursor_model(model: str) -> bool:
    """Gemini and Grok ids are Cursor models. Claude aliases and composer are not."""
    mid = model.lower()
    if "composer" in mid or "claude" in mid:
        return False
    return mid.startswith("gemini-") or mid.startswith("grok-")


def default_binary() -> str:
    env = os.environ.get("CURSOR_AGENT_BIN")
    if env:
        return env
    found = shutil.which("cursor-agent")
    if found:
        return found
    root = Path.home() / ".cursor-server/data/User/globalStorage/anysphere.cursor-agent-worker/agent-cli/.local/share/cursor-agent/versions"
    bins = sorted(root.glob("*/cursor-agent"))
    if not bins:
        raise CursorCallError("cursor-agent is not on PATH and CURSOR_AGENT_BIN is unset")
    return str(bins[-1])


class CursorAgentReader:
    """One `cursor-agent -p` call per document. `name` is the model id that was asked to answer."""

    def __init__(self, model: str, binary: str | None = None, timeout: int = 1200):
        if not cursor_model(model):
            raise CursorCallError(f"{model} is not a Cursor eval model this runner will call")
        self.model = model
        self.name = model
        self.binary = binary or default_binary()
        self.timeout = timeout
        self.last: dict = {}

    def argv(self, workspace: str) -> list[str]:
        return [self.binary, "-p", "--output-format", "json", "--mode", "ask", "--single-turn",
                "--sandbox", "enabled", "--trust", "--model", self.model, "--workspace", workspace]

    def user_text(self, task, text: str) -> str:
        return (task.system()
                + "\n\nReturn one JSON object matching this schema and nothing else. Do not use tools.\n"
                + json.dumps(task.schema, ensure_ascii=False)
                + "\n\n<document>\n" + text + "\n</document>\n")

    def read(self, task, text: str) -> dict | None:
        if not os.environ.get("CURSOR_API_KEY"):
            raise CursorCallError("CURSOR_API_KEY is unset; refusing to call cursor-agent")
        with tempfile.TemporaryDirectory(prefix="reader-") as empty:
            try:
                p = subprocess.run(self.argv(empty) + [self.user_text(task, text)], cwd=empty, capture_output=True,
                                   text=True, timeout=self.timeout, env=os.environ.copy())
            except subprocess.TimeoutExpired as e:
                raise CursorTimeout(f"cursor-agent timed out after {self.timeout}s") from e
        err = (p.stderr or "").strip()
        if HOLD.search(err) or HOLD.search(p.stdout or ""):
            raise CursorCallError(err[:300] or (p.stdout or "")[:300])
        try:
            env = json.loads(p.stdout) if (p.stdout or "").strip() else {}
        except ValueError:
            env = {}
        result = env.get("result") if isinstance(env, dict) else None
        if p.returncode != 0 or not isinstance(env, dict) or env.get("is_error") or HOLD.search(str(result or "")):
            detail = err or (str(result)[:300] if result else (p.stdout or "")[:300])
            raise CursorCallError(f"cursor-agent failed ({p.returncode}): {detail}")
        told = env.get("model") or env.get("modelName")
        if told and told != self.model:
            raise CursorCallError(f"asked {self.model} but the envelope says {told}")
        ans = _object(result)
        if not isinstance(ans, dict):
            raise CursorCallError("cursor-agent returned no JSON object")
        usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}
        self.last = {"cost_usd": None, "usage": usage, "duration_ms": env.get("duration_ms")}
        return ans


def _object(result):
    if isinstance(result, dict):
        return result
    if not isinstance(result, str) or not result.strip():
        return None
    try:
        return json.loads(result)
    except ValueError:
        m = re.search(r"\{.*\}", result, re.S)
        return json.loads(m.group(0)) if m else None
