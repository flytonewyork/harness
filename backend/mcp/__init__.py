"""Zero-dependency, spec-compliant MCP (Model Context Protocol 2025-03-26) Clinic EMR Server & Client.

Provides the single, auditable protocol boundary between the Lead Clinical Agent (`GovernedAgentRunner`)
and the Clinic EMR (`EMRConnectorProtocol` / `SimulatedPostgresEMRAdapter`), CDS rules, scheduling,
and quote-verified Agent Memory — exposed both in-process (zero IPC latency) and over Streamable HTTP
JSON-RPC 2.0 (`POST /mcp/clinic-emr`).

Strict boundary invariant: NEVER imports `loopbench`.
"""
from .clinic_emr_server import (
    MCP_AUTHORITY_DENIED_CODE,
    MCP_PROTOCOL_VERSION,
    ClinicEMRMCPServer,
)
from .client import InProcessMCPClient, MCPClientProtocol, build_mcp_tool_registry

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_AUTHORITY_DENIED_CODE",
    "ClinicEMRMCPServer",
    "MCPClientProtocol",
    "InProcessMCPClient",
    "build_mcp_tool_registry",
]
