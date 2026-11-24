"""Multi-provider LLM transports (Vertex AI asia-east2 HK ADC, Gemini, Anthropic, OpenAI) + deterministic replay.

Zero duplicate LLM logic: speaks the exact `ModelTransport` protocol consumed by `GovernedAgentRunner`
and uses `ToolRegistry` adapters for native tool declarations & response parsing.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .runner import ModelTurnResponse
from .tools import ToolRegistry

_ADC_CACHE: dict[str, Any] = {"token": None, "project": None, "expires_at": 0.0}


def acquire_vertex_adc_token(project_override: str | None = None) -> tuple[str, str]:
    """Acquire GCP ADC token via `google-auth` with Cloud Run metadata-server fallback & TTL cache."""
    now = time.time()
    if _ADC_CACHE["token"] and now < float(_ADC_CACHE["expires_at"]) - 60:
        proj = project_override or _ADC_CACHE["project"]
        if proj:
            return str(_ADC_CACHE["token"]), str(proj)

    # 1. Try google-auth when installed
    try:
        import google.auth  # type: ignore[import-untyped]
        import google.auth.transport.requests  # type: ignore[import-untyped]

        creds, default_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        proj = project_override or default_project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
        if creds.token and proj:
            _ADC_CACHE.update({"token": str(creds.token), "project": str(proj), "expires_at": now + 2400})
            return str(creds.token), str(proj)
    except Exception:
        pass

    # 2. Fast GCE / Cloud Run metadata server fallback
    proj = project_override or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID") or os.environ.get("VERTEX_PROJECT_ID")
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
            )
            if r.status_code == 200:
                data = r.json()
                tok = str(data.get("access_token") or "")
                ttl = int(data.get("expires_in") or 1800)
                if not proj:
                    pr = client.get(
                        "http://metadata.google.internal/computeMetadata/v1/project/project-id",
                        headers={"Metadata-Flavor": "Google"},
                    )
                    if pr.status_code == 200:
                        proj = pr.text.strip()
                if tok and proj:
                    _ADC_CACHE.update({"token": tok, "project": proj, "expires_at": now + ttl})
                    return tok, proj
    except Exception:
        pass

    raise RuntimeError("Vertex AI ADC credentials unavailable in this environment.")


DEFAULT_GEMINI_REASONING_MODEL = "gemini-4-neon"
DEFAULT_GEMINI_FAST_MODEL = "gemini-4-hydrogen"
_VALID_GEMINI4_THINKING_LEVELS = ("LOW", "MEDIUM", "HIGH", "XHIGH")
_GEMINI_MODEL_ALIASES: dict[str, str] = {
    "neon": DEFAULT_GEMINI_REASONING_MODEL,
    "gemini-neon": DEFAULT_GEMINI_REASONING_MODEL,
    "gemini-4-neon": DEFAULT_GEMINI_REASONING_MODEL,
    "hydrogen": DEFAULT_GEMINI_FAST_MODEL,
    "gemini-hydrogen": DEFAULT_GEMINI_FAST_MODEL,
    "gemini-4-hydrogen": DEFAULT_GEMINI_FAST_MODEL,
}


def normalize_gemini_model(raw: str | None, *, default: str = DEFAULT_GEMINI_REASONING_MODEL) -> str:
    """Resolve short aliases (`neon` -> `gemini-4-neon`, `hydrogen` -> `gemini-4-hydrogen`) or default."""
    val = (raw or "").strip()
    if not val:
        return default
    return _GEMINI_MODEL_ALIASES.get(val.lower(), val)


def is_gemini_4_model(model: str) -> bool:
    low = (model or "").strip().lower()
    return low.startswith("gemini-4-") or low.startswith("gemini-3.")


def build_vertex_generate_content_url(*, region: str, project: str, model: str) -> str:
    """Build Vertex AI generateContent URL supporting both regional (`asia-east2`) and `global` endpoints."""
    loc = (region or "asia-east2").strip() or "asia-east2"
    api_ver = "v1beta1" if is_gemini_4_model(model) else "v1"
    host = "aiplatform.googleapis.com" if loc.lower() == "global" else f"{loc}-aiplatform.googleapis.com"
    return (
        f"https://{host}/{api_ver}/projects/{project}"
        f"/locations/{loc}/publishers/google/models/{model}:generateContent"
    )


def build_gemini_generation_config(
    model: str,
    *,
    temperature: float = 0.1,
    max_output_tokens: int = 1100,
    expect_json: bool = False,
    thinking_level: str | None = None,
) -> dict[str, Any]:
    """Build spec-compliant `generationConfig` for Gemini 4 Neon/Hydrogen vs legacy Gemini models.

    Per Google's Gemini 4 Neon & Hydrogen Developer Guides:
    - Control reasoning depth via `thinkingConfig.thinkingLevel` (`LOW` | `MEDIUM` | `HIGH` | `XHIGH`).
    - Do NOT send `temperature`, `topP`, `topK`, or `thinkingBudget` on Gemini 4 (ignored or HTTP 400).
    - `maxOutputTokens` covers BOTH internal thinking tokens and visible output, so enforce a safe
      floor (`>= 8192`) on Gemini 4 to prevent truncated/empty responses during clinical reasoning.
    """
    if is_gemini_4_model(model):
        raw_level = (
            thinking_level
            or os.environ.get("GEMINI_THINKING_LEVEL")
            or ("HIGH" if "neon" in model.lower() else "MEDIUM")
        ).strip().upper()
        if raw_level not in _VALID_GEMINI4_THINKING_LEVELS:
            raw_level = "HIGH" if "neon" in model.lower() else "MEDIUM"
        cfg: dict[str, Any] = {
            "maxOutputTokens": max(int(max_output_tokens or 1100), 8192),
            "thinkingConfig": {"thinkingLevel": raw_level},
        }
    else:
        cfg = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        }
    if expect_json:
        cfg["responseMimeType"] = "application/json"
    return cfg


def extract_gemini_visible_text(parts: list[dict[str, Any]]) -> str:
    """Extract visible response text from Gemini `parts`, skipping internal `thought: true` summaries."""
    visible = [
        str(p.get("text", ""))
        for p in (parts or [])
        if isinstance(p, dict) and "text" in p and not p.get("thought")
    ]
    return " ".join(x for x in visible if x).strip()


def detect_adc_quota_project() -> str | None:
    """Read `quota_project_id` from official gcloud ADC file if present (zero network calls)."""
    candidates = [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        os.path.expanduser("~/.config/gcloud/application_default_credentials.json"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            proj = str(data.get("quota_project_id") or data.get("project_id") or "").strip()
            if proj:
                return proj
        except Exception:
            continue
    return None


def detect_provider_chain() -> list[dict[str, Any]]:
    """Return ordered live provider chain prioritizing HK-resident Vertex AI (`gemini-4-neon` / `gemini-4-hydrogen`)."""
    forced = (os.environ.get("AGENT_LLM_PROVIDER") or "").strip().lower()
    if forced in ("deterministic", "off"):
        return []

    chain: list[dict[str, Any]] = []
    gcp_proj = (
        os.environ.get("VERTEX_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT_ID")
        or detect_adc_quota_project()
    )
    if forced in ("", "vertex") and gcp_proj:
        region = os.environ.get("VERTEX_LOCATION") or os.environ.get("VERTEX_REGION") or os.environ.get("HARNESS_REGION") or "asia-east2"
        model = normalize_gemini_model(os.environ.get("AGENT_LLM_MODEL") or os.environ.get("VERTEX_MODEL"))
        chain.append({"provider": "vertex", "model": model, "fallback_model": DEFAULT_GEMINI_FAST_MODEL, "region": region, "project": gcp_proj})

    if forced in ("", "gemini") and os.environ.get("GEMINI_API_KEY"):
        chain.append({
            "provider": "gemini",
            "model": normalize_gemini_model(os.environ.get("AGENT_LLM_MODEL")),
            "fallback_model": DEFAULT_GEMINI_FAST_MODEL,
            "region": "global",
        })
    if forced in ("", "anthropic") and os.environ.get("ANTHROPIC_API_KEY"):
        chain.append({
            "provider": "anthropic",
            "model": os.environ.get("AGENT_LLM_MODEL") or "claude-sonnet-4-20250514",
            "region": "us-global",
        })
    if forced in ("", "openai") and os.environ.get("OPENAI_API_KEY"):
        chain.append({
            "provider": "openai",
            "model": os.environ.get("AGENT_LLM_MODEL") or "gpt-4o-mini",
            "region": "us-global",
        })
    return chain


class LiveHTTPModelTransport:
    """Unified httpx transport supporting Vertex AI asia-east2 (Gemini 4 Neon/Hydrogen), Gemini API, Anthropic, and OpenAI."""

    def __init__(
        self,
        *,
        provider: str,
        model: str = DEFAULT_GEMINI_REASONING_MODEL,
        tool_registry: ToolRegistry,
        region: str = "asia-east2",
        project: str | None = None,
        timeout_seconds: float = 25.0,
        thinking_level: str | None = None,
    ) -> None:
        self.provider = provider.lower()
        self.model = normalize_gemini_model(model) if self.provider in ("vertex", "gemini") else model
        self.tool_registry = tool_registry
        self.region = region
        self.project = project
        self.timeout_seconds = timeout_seconds
        self.thinking_level = thinking_level

    def invoke(
        self,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse:
        allowed = {str(t["name"]) for t in tools if isinstance(t, dict) and t.get("name")} or None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            if self.provider in ("vertex", "gemini"):
                return self._invoke_gemini_family(
                    client,
                    system_message=system_message,
                    messages=messages,
                    allowed_tools=allowed,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    expect_json=expect_json,
                )
            if self.provider == "anthropic":
                return self._invoke_anthropic(
                    client,
                    system_message=system_message,
                    messages=messages,
                    allowed_tools=allowed,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
            if self.provider == "openai":
                return self._invoke_openai(
                    client,
                    system_message=system_message,
                    messages=messages,
                    allowed_tools=allowed,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    expect_json=expect_json,
                )
        raise ValueError(f"Unsupported provider: '{self.provider}'")

    def _invoke_gemini_family(
        self,
        client: httpx.Client,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        allowed_tools: set[str] | None,
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse:
        candidate_models = [self.model]
        if self.model == DEFAULT_GEMINI_REASONING_MODEL:
            candidate_models.append(DEFAULT_GEMINI_FAST_MODEL)

        contents = [
            {"role": "model" if m.get("role") == "assistant" else "user", "parts": [{"text": str(m.get("content") or "")}]}
            for m in messages
        ]

        last_resp: httpx.Response | None = None
        used_model = self.model
        for mdl in candidate_models:
            used_model = mdl
            if self.provider == "vertex":
                token, project = acquire_vertex_adc_token(self.project)
                url = build_vertex_generate_content_url(region=self.region, project=project, model=mdl)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "x-goog-user-project": project,
                    "Content-Type": "application/json",
                    "X-Vertex-Zero-Data-Retention": "true",
                    "X-No-Training": "true",
                }
                params: dict[str, str] = {}
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent"
                headers = {"Content-Type": "application/json"}
                params = {"key": os.environ["GEMINI_API_KEY"]}

            gen_cfg = build_gemini_generation_config(
                mdl,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                expect_json=expect_json,
                thinking_level=self.thinking_level,
            )
            body: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_message}]},
                "contents": contents,
                "generationConfig": gen_cfg,
            }
            if self.provider == "vertex":
                body["labels"] = {"zero_retention": "true", "no_training": "true", "pdpo_dpp3": "enforced"}
            if allowed_tools:
                gem_tools = self.tool_registry.to_vertex_gemini_tools(allowed_tools)
                if gem_tools:
                    body["tools"] = gem_tools

            resp = client.post(url, headers=headers, params=params, json=body)
            last_resp = resp
            if resp.status_code in (403, 404) and mdl != candidate_models[-1]:
                continue
            resp.raise_for_status()
            break

        assert last_resp is not None
        data = last_resp.json()
        tool_calls = self.tool_registry.parse_provider_tool_calls(self.provider, data)
        cand = (data.get("candidates") or [{}])[0]
        parts = ((cand.get("content") or {}).get("parts") or [])
        txt = extract_gemini_visible_text(parts)
        usage = data.get("usageMetadata") or {}
        parsed_json = None
        if expect_json and txt:
            try:
                parsed_json = json.loads(txt)
            except ValueError:
                parsed_json = None
        return ModelTurnResponse(
            text=txt,
            json_payload=parsed_json if isinstance(parsed_json, dict) else None,
            tool_calls=tool_calls,
            provider=self.provider,
            model=used_model,
            region=self.region,
            prompt_tokens_actual=usage.get("promptTokenCount"),
            output_tokens_actual=usage.get("candidatesTokenCount"),
        )

    def _invoke_anthropic(
        self,
        client: httpx.Client,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        allowed_tools: set[str] | None,
        temperature: float,
        max_output_tokens: int,
    ) -> ModelTurnResponse:
        anth_msgs = [
            {"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content") or "")}
            for m in messages
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "system": system_message,
            "messages": anth_msgs,
        }
        if allowed_tools:
            anth_tools = self.tool_registry.to_anthropic_tools(allowed_tools)
            if anth_tools:
                body["tools"] = anth_tools
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        tool_calls = self.tool_registry.parse_provider_tool_calls("anthropic", data)
        txt = " ".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text").strip()
        usage = data.get("usage") or {}
        return ModelTurnResponse(
            text=txt,
            tool_calls=tool_calls,
            provider="anthropic",
            model=self.model,
            region=self.region,
            prompt_tokens_actual=usage.get("input_tokens"),
            output_tokens_actual=usage.get("output_tokens"),
        )

    def _invoke_openai(
        self,
        client: httpx.Client,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        allowed_tools: set[str] | None,
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse:
        oai_msgs = [{"role": "system", "content": system_message}] + [
            {"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content") or "")}
            for m in messages
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "store": False,
            "messages": oai_msgs,
        }
        if expect_json:
            body["response_format"] = {"type": "json_object"}
        if allowed_tools:
            oai_tools = self.tool_registry.to_openai_tools(allowed_tools)
            if oai_tools:
                body["tools"] = oai_tools
        resp = client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        tool_calls = self.tool_registry.parse_provider_tool_calls("openai", data)
        txt = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        usage = data.get("usage") or {}
        return ModelTurnResponse(
            text=txt,
            tool_calls=tool_calls,
            provider="openai",
            model=self.model,
            region=self.region,
            prompt_tokens_actual=usage.get("prompt_tokens"),
            output_tokens_actual=usage.get("completion_tokens"),
        )


class ScriptedReplayTransport:
    """Deterministic multi-turn replay transport for zero-flake CI, red-team & self-repair tests."""

    def __init__(self, responses: list[ModelTurnResponse]) -> None:
        self._responses = list(responses)
        self.invocations: list[dict[str, Any]] = []

    def invoke(
        self,
        *,
        system_message: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        expect_json: bool,
    ) -> ModelTurnResponse:
        self.invocations.append({
            "system_message": system_message,
            "messages": list(messages),
            "tools": list(tools),
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "expect_json": expect_json,
        })
        if not self._responses:
            return ModelTurnResponse(text="", provider="scripted_replay", model="replay-v1")
        return self._responses.pop(0)
