from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable

from app.mcp.adapter import MCPToolAdapter
from app.mcp.client import MCPProcessClient
from app.mcp.models import MCPServerConfig
from app.mcp.protocols import MCPClient
from app.tools.models import RiskLevel
from app.tools.protocols import Tool

logger = logging.getLogger(__name__)
_RISK_RANK = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


class MCPToolManager:
    """Own MCP clients and expose an allowlisted discovered tool collection."""

    def __init__(
        self,
        configs: Iterable[MCPServerConfig],
        *,
        client_factory: Callable[[MCPServerConfig], MCPClient] | None = None,
    ) -> None:
        self._configs = tuple(configs)
        self._client_factory = client_factory or self._default_client_factory
        self._clients: dict[str, MCPClient] = {}
        self._discovered_tools: tuple[Tool, ...] = ()
        self._discovered = False

    async def discover_tools(self) -> tuple[Tool, ...]:
        if self._discovered:
            return self._discovered_tools

        tools: list[Tool] = []
        for config in self._configs:
            if config.name in self._clients:
                raise ValueError(f"MCP server already started: {config.name}")
            client = self._client_factory(config)
            try:
                await client.start()
                self._clients[config.name] = client
                definitions = await client.list_tools()
                seen_names: set[str] = set()
                server_tools: list[Tool] = []
                for definition in definitions:
                    if definition.name in seen_names:
                        raise ValueError(
                            f"MCP server returned duplicate tool: {definition.name}"
                        )
                    seen_names.add(definition.name)
                    if not definition.risk_metadata_known:
                        logger.warning(
                            "Skipping MCP tool with unknown risk metadata: %s/%s",
                            config.name,
                            definition.name,
                        )
                        continue
                    if (
                        config.allowed_tools
                        and definition.name not in config.allowed_tools
                    ):
                        continue
                    if (
                        _RISK_RANK[definition.risk_level]
                        > _RISK_RANK[config.max_risk_level]
                    ):
                        continue
                    server_tools.append(MCPToolAdapter(config.name, client, definition))
            except asyncio.CancelledError:
                self._clients.pop(config.name, None)
                await self._close_client_safely(config.name, client)
                raise
            except Exception:
                self._clients.pop(config.name, None)
                logger.exception("MCP server discovery failed: %s", config.name)
                cleanup_cancelled = await self._close_client_safely(config.name, client)
                if cleanup_cancelled:
                    raise asyncio.CancelledError from None
                continue
            tools.extend(server_tools)
        self._discovered_tools = tuple(tools)
        self._discovered = True
        return self._discovered_tools

    def list_tools(self) -> tuple[Tool, ...]:
        """Return tools discovered during application startup."""

        return self._discovered_tools

    def granted_permissions(self) -> frozenset[str]:
        """Return server permissions granted by the active application policy."""

        return frozenset(f"mcp:server:{server_name}" for server_name in self._clients)

    async def close(self) -> None:
        clients = tuple(self._clients.items())
        self._discovered_tools = ()
        self._discovered = False
        cancellation_requested = False
        for server_name, client in clients:
            cancellation_requested = (
                await self._close_client_safely(server_name, client)
                or cancellation_requested
            )
        self._clients.clear()
        if cancellation_requested:
            raise asyncio.CancelledError

    @staticmethod
    async def _close_client_safely(server_name: str, client: MCPClient) -> bool:
        """Close a client without losing cleanup progress or cancellation state."""

        close_task = asyncio.create_task(client.close())
        cancellation_requested = False
        while not close_task.done():
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                if close_task.cancelled():
                    break
                cancellation_requested = True
            except BaseException:
                break

        if close_task.cancelled():
            logger.warning("MCP client close was cancelled: %s", server_name)
        else:
            close_error = close_task.exception()
            if close_error is not None:
                logger.warning(
                    "Failed to close MCP client %s: %s",
                    server_name,
                    close_error,
                )
        return cancellation_requested

    @staticmethod
    def _default_client_factory(config: MCPServerConfig) -> MCPClient:
        return MCPProcessClient(
            config.command,
            environment=config.environment,
            startup_timeout_seconds=config.startup_timeout_seconds,
            request_timeout_seconds=config.request_timeout_seconds,
        )
