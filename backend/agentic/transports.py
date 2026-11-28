"""Multi-provider LLM transports (Vertex AI asia-east2 HK ADC, Gemini, Anthropic, OpenAI) + deterministic replay.

Zero duplicate LLM logic: speaks the exact `ModelTransport` protocol consumed by `GovernedAgentRunner`
and uses `ToolRegistry` adapters for native tool declarations & response parsing.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import httpx

from .runner import ModelTurnResponse
from .tools import ToolRegistry

_ADC_LOCK = threading.Lock()
_ADC_CACHE: dict[str, Any] = {"token": None, "project": None, "expires_at": 0.0, "failed_until": 0.0}
_ADC_NEGATIVE_TTL_SECONDS = 30.0
_ADC_QUOTA_PROJ_CACHE: dict[str, tuple[float, str | None]] = {}
_SHARED_HTTP_CLIENT: httpx.Client | None = None
_SHARED_HTTP_LOCK = threading.Lock()


def reset_adc_cache() -> None:
    """Clear positive/negative ADC caches and quota-project file cache (used by tests & manual re-probe)."""
    with _ADC_LOCK:
        _ADC_CACHE.update({"token": None, "project": None, "expires_at": 0.0, "failed_until": 0.0})
        _ADC_QUOTA_PROJ_CACHE.clear()


def get_shared_llm_http_client() -> httpx.Client:
    """Return a shared keep-alive httpx.Client to avoid per-turn DNS/TCP/TLS handshakes across all LLM transports."""
    global _SHARED_HTTP_CLIENT
    with _SHARED_HTTP_LOCK:
        if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
            _SHARED_HTTP_CLIENT = httpx.Client(
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=120.0),
            )
        return _SHARED_HTTP_CLIENT


def acquire_vertex_adc_token(project_override: str | None = None) -> tuple[str, str]:
    """Acquire GCP ADC token via `google-auth` with Cloud Run metadata-server fallback & positive/negative TTL cache."""
    now = time.time()
    with _ADC_LOCK:
        if _ADC_CACHE["token"] and now < float(_ADC_CACHE["expires_at"]) - 60:
            proj = project_override or _ADC_CACHE["project"]
            if proj:
                return str(_ADC_CACHE["token"]), str(proj)
        if now < float(_ADC_CACHE["failed_until"]):
            raise RuntimeError("Vertex AI ADC credentials unavailable in this environment (cached).")

    # 1. Try google-auth when installed
    try:
        import google.auth  # type: ignore[import-untyped]
        import google.auth.transport.requests  # type: ignore[import-untyped]

        creds, default_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        proj = project_override or default_project or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
        if creds.token and proj:
            with _ADC_LOCK:
                _ADC_CACHE.update({"token": str(creds.token), "project": str(proj), "expires_at": now + 2400, "failed_until": 0.0})
            return str(creds.token), str(proj)
    except Exception:
        pass

    # 2. Fast GCE / Cloud Run link-local metadata server fallback (responds <5ms on GCP; fail fast off-GCP)
    proj = project_override or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID") or os.environ.get("VERTEX_PROJECT_ID")
    try:
        with httpx.Client(timeout=0.35) as client:
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
                    with _ADC_LOCK:
                        _ADC_CACHE.update({"token": tok, "project": proj, "expires_at": now + ttl, "failed_until": 0.0})
                    return tok, proj
    except Exception:
        pass

    with _ADC_LOCK:
        _ADC_CACHE["failed_until"] = now + _ADC_NEGATIVE_TTL_SECONDS
    raise RuntimeError("Vertex AI ADC credentials unavailable in this environment.")


DEFAULT_GEMINI_REASONING_MODEL = "gemini-4-neon"
DEFAULT_GEMINI_FAST_MODEL = "gemini-4-hydrogen"
DEFAULT_GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"
_VALID_GEMINI4_THINKING_LEVELS = ("LOW", "MEDIUM", "HIGH", "XHIGH")
_VALID_GEMINI3_MINIMAL_THINKING_LEVELS = ("MINIMAL", "LOW", "MEDIUM", "HIGH")
_VALID_GEMINI3_STANDARD_THINKING_LEVELS = ("LOW", "MEDIUM", "HIGH")
_GEMINI_MODEL_ALIASES: dict[str, str] = {
    "neon": DEFAULT_GEMINI_REASONING_MODEL,
    "gemini-neon": DEFAULT_GEMINI_REASONING_MODEL,
    "gemini-4-neon": DEFAULT_GEMINI_REASONING_MODEL,
    "hydrogen": DEFAULT_GEMINI_FAST_MODEL,
    "gemini-hydrogen": DEFAULT_GEMINI_FAST_MODEL,
    "gemini-4-hydrogen": DEFAULT_GEMINI_FAST_MODEL,
    "flash-lite": DEFAULT_GEMINI_CHAT_MODEL,
    "3.5-flash-lite": DEFAULT_GEMINI_CHAT_MODEL,
    "gemini-3.5-flash-lite": DEFAULT_GEMINI_CHAT_MODEL,
}


def normalize_gemini_model(raw: str | None, *, default: str = DEFAULT_GEMINI_REASONING_MODEL) -> str:
    """Resolve short aliases (`neon` -> `gemini-4-neon`, `hydrogen` -> `gemini-4-hydrogen`, `flash-lite` -> `gemini-3.5-flash-lite`) or default."""
    val = (raw or "").strip()
    if not val:
        return default
    return _GEMINI_MODEL_ALIASES.get(val.lower(), val)


def is_gemini_4_model(model: str) -> bool:
    low = (model or "").strip().lower()
    return low.startswith("gemini-4-") or low.startswith("gemini-3.") or low.startswith("gemini-3-")


def supports_minimal_thinking(model: str) -> bool:
    """True for Gemini 3.x Flash/Flash-Lite models that support `thinkingLevel: MINIMAL` (near-zero thinking latency)."""
    low = (model or "").strip().lower()
    if low.startswith("gemini-4-") or low.startswith("gemini-3.8-") or low.startswith("gemini-3.7-") or "pro" in low:
        return False
    return (
        low.startswith("gemini-3.5-flash")
        or low.startswith("gemini-3.6-flash")
        or low.startswith("gemini-3.1-flash")
        or low.startswith("gemini-3-flash")
    )


def build_vertex_generate_content_url(*, region: str, project: str, model: str) -> str:
    """Build Vertex AI generateContent URL supporting both regional (`asia-east2`) and `global` endpoints."""
    loc = (region or "asia-east2").strip() or "asia-east2"
    low = (model or "").strip().lower()
    api_ver = "v1beta1" if (low.startswith("gemini-4-") or "preview" in low) else "v1"
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
    """Build spec-compliant `generationConfig` across Gemini 4, Gemini 3.5 Flash-Lite, and Gemini 2.5 models.

    - Gemini 3.5 Flash-Lite / 3.6 Flash: supports `thinkingLevel: MINIMAL` (near-zero hidden CoT latency,
      no 8192 token inflation) for fast interactive chat/routing; omits unsupported `temperature`.
    - Gemini 4 Neon / Hydrogen: `thinkingLevel` in `LOW|MEDIUM|HIGH|XHIGH` (clamps `MINIMAL`/`NONE`/`OFF` -> `LOW`
      so fallback cascades never HTTP 400); omits `temperature`; enforces `>= 8192` token floor for thinking + output.
    - Legacy Gemini 2.5 Flash: sets `thinkingBudget: 0` when `MINIMAL`/`NONE`/`OFF`/`LOW` is requested to disable
      slow default dynamic thinking.
    """
    low = (model or "").strip().lower()
    req_level = (thinking_level or os.environ.get("GEMINI_THINKING_LEVEL") or "").strip().upper()

    if low.startswith("gemini-4-"):
        if req_level in ("MINIMAL", "NONE", "OFF"):
            raw_level = "LOW"
        elif req_level in _VALID_GEMINI4_THINKING_LEVELS:
            raw_level = req_level
        else:
            raw_level = "HIGH" if "neon" in low else "MEDIUM"
        cfg: dict[str, Any] = {
            "maxOutputTokens": max(int(max_output_tokens or 1100), 8192),
            "thinkingConfig": {"thinkingLevel": raw_level},
        }
    elif low.startswith("gemini-3.") or low.startswith("gemini-3-"):
        if supports_minimal_thinking(low):
            if req_level in ("NONE", "OFF", "MINIMAL"):
                raw_level = "MINIMAL"
            elif req_level in _VALID_GEMINI3_MINIMAL_THINKING_LEVELS:
                raw_level = req_level
            elif req_level == "XHIGH":
                raw_level = "HIGH"
            else:
                raw_level = "MINIMAL" if "flash-lite" in low else "LOW"
            token_cap = (
                max(int(max_output_tokens or 512), 256)
                if raw_level == "MINIMAL"
                else max(int(max_output_tokens or 1100), 4096)
            )
            cfg = {
                "maxOutputTokens": token_cap,
                "thinkingConfig": {"thinkingLevel": raw_level},
            }
        else:
            if req_level in ("MINIMAL", "NONE", "OFF"):
                raw_level = "LOW"
            elif req_level == "XHIGH":
                raw_level = "HIGH"
            elif req_level in _VALID_GEMINI3_STANDARD_THINKING_LEVELS:
                raw_level = req_level
            else:
                raw_level = "MEDIUM"
            cfg = {
                "maxOutputTokens": max(int(max_output_tokens or 1100), 4096),
                "thinkingConfig": {"thinkingLevel": raw_level},
            }
    else:
        cfg = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        }
        if low.startswith("gemini-2.5-") and req_level in ("MINIMAL", "NONE", "OFF", "LOW"):
            cfg["thinkingConfig"] = {"thinkingBudget": 0}
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
    """Read `quota_project_id` from official gcloud ADC file if present (mtime-cached, zero network calls)."""
    candidates = [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        os.path.expanduser("~/.config/gcloud/application_default_credentials.json"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
            cached = _ADC_QUOTA_PROJ_CACHE.get(path)
            if cached is not None and cached[0] == mtime:
                if cached[1]:
                    return cached[1]
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            proj = str(data.get("quota_project_id") or data.get("project_id") or "").strip() or None
            _ADC_QUOTA_PROJ_CACHE[path] = (mtime, proj)
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
        client = get_shared_llm_http_client()
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
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [
                    {
                        "text": str(
                            m.get("content")
                            or (f"[Tool calls requested: {', '.join(m['tool_calls'])}]" if m.get("tool_calls") else "")
                        )
                    }
                ],
            }
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

            resp = client.post(url, headers=headers, params=params, json=body, timeout=self.timeout_seconds)
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
            {
                "role": "assistant" if m.get("role") == "assistant" else "user",
                "content": str(
                    m.get("content")
                    or (f"[Tool calls requested: {', '.join(m['tool_calls'])}]" if m.get("tool_calls") else "")
                ),
            }
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
            timeout=self.timeout_seconds,
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
            {
                "role": "assistant" if m.get("role") == "assistant" else "user",
                "content": str(
                    m.get("content")
                    or (f"[Tool calls requested: {', '.join(m['tool_calls'])}]" if m.get("tool_calls") else "")
                ),
            }
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
            timeout=self.timeout_seconds,
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
