from __future__ import annotations

from collections.abc import Mapping

from app.mcp.models import MCPToolDefinition
from app.mcp.protocols import MCPClient
from app.tools.models import ToolContext


class MCPToolAdapter:
    """Expose one discovered MCP tool through the internal Tool protocol."""

    def __init__(
        self,
        server_name: str,
        client: MCPClient,
        definition: MCPToolDefinition,
    ) -> None:
        if not server_name.strip():
            raise ValueError("server_name must not be empty")
        self._server_name = server_name
        self._client = client
        self._definition = definition
        self.name = f"mcp__{server_name}__{definition.name}"
        self.description = f"[{server_name}] {definition.description}"
        self.input_schema = definition.input_schema
        self.output_schema = definition.output_schema
        self.risk_level = definition.risk_level
        self.required_permissions = (f"mcp:server:{server_name}",)

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> dict[str, object]:
        del context
        result = await self._client.call_tool(self._definition.name, arguments)
        return {
            "ok": not result.is_error,
            "content": list(result.content),
            "error": "MCP tool reported an error" if result.is_error else None,
        }
