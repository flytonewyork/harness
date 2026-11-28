"""In-Process MCP Client & `ToolRegistry` adapter connecting `GovernedAgentRunner` to `ClinicEMRMCPServer`.

Every tool call from the Lead Clinical Agent travels as a real JSON-RPC 2.0 MCP envelope
(`tools/call`) through `InProcessMCPClient`, preserving zero-latency in-process execution while
maintaining the exact same wire contract as `POST /mcp/clinic-emr`.
"""
from __future__ import annotations

import itertools
from typing import Any, Protocol

from backend.agentic.tools import AGENT_PERMITTED_AUTHORITIES, ToolRegistry, build_default_clinic_tool_registry

from .clinic_emr_server import ClinicEMRMCPServer


class MCPClientProtocol(Protocol):
    def initialize(self) -> dict[str, Any]: ...
    def list_tools(self, *, include_blocked: bool = True) -> list[dict[str, Any]]: ...
    def list_resources(self) -> dict[str, Any]: ...
    def read_resource(self, uri: str, *, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        actor_role: str = "assistant",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class InProcessMCPClient:
    """Zero-IPC JSON-RPC 2.0 MCP client bound to a tenant-scoped `ClinicEMRMCPServer`."""

    def __init__(self, server: ClinicEMRMCPServer, *, tenant_id: str) -> None:
        self.server = server
        self.tenant_id = tenant_id
        self._id_counter = itertools.count(1)

    def _rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        actor_role: str = "assistant",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        envelope = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": method,
            "params": params or {},
        }
        return self.server.handle_jsonrpc(
            envelope,
            tenant_id=self.tenant_id,
            actor_role=actor_role,
            runtime_context=runtime_context,
        )

    def initialize(self) -> dict[str, Any]:
        resp = self._rpc("initialize", {})
        return dict(resp.get("result") or {})

    def list_tools(self, *, include_blocked: bool = True) -> list[dict[str, Any]]:
        resp = self._rpc("tools/list", {"include_blocked": include_blocked})
        return list((resp.get("result") or {}).get("tools") or [])

    def list_resources(self) -> dict[str, Any]:
        resp = self._rpc("resources/list", {})
        return dict(resp.get("result") or {})

    def read_resource(self, uri: str, *, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._rpc("resources/read", {"uri": uri}, runtime_context=runtime_context)
        if "error" in resp:
            raise RuntimeError(f"MCP resources/read error ({resp['error'].get('code')}): {resp['error'].get('message')}")
        return dict(resp.get("result") or {})

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        actor_role: str = "assistant",
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
            actor_role=actor_role,
            runtime_context=runtime_context,
        )
        if "error" in resp:
            err = resp["error"]
            raise PermissionError(err.get("message")) if err.get("code") == -32003 else RuntimeError(
                f"MCP tools/call error ({err.get('code')}): {err.get('message')}"
            )
        result = resp.get("result") or {}
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        return dict(result)


def build_mcp_tool_registry(mcp_client: MCPClientProtocol) -> ToolRegistry:
    """Bind every canonical clinical `ToolSpec` directly to `mcp_client.call_tool(...)` (zero SQL in agent runtime)."""
    base = build_default_clinic_tool_registry()
    handlers: dict[str, Any] = {}
    for spec in base.list_specs(include_blocked=False):
        if spec.authority not in AGENT_PERMITTED_AUTHORITIES:
            continue

        def _make_handler(tool_name: str):
            def _handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
                return mcp_client.call_tool(
                    tool_name,
                    args,
                    actor_role="assistant",
                    runtime_context=ctx,
                )
            return _handler

        handlers[spec.name] = _make_handler(spec.name)

    return build_default_clinic_tool_registry(handlers=handlers)
