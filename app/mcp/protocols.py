from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.mcp.models import MCPToolCallResult, MCPToolDefinition


@runtime_checkable
class MCPClient(Protocol):
    """Transport-independent client boundary used by MCP adapters."""

    async def start(self) -> None: ...

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> MCPToolCallResult: ...

    async def close(self) -> None: ...
