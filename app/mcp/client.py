from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from typing import cast

from app.mcp.models import MCPToolCallResult, MCPToolDefinition
from app.tools.models import RiskLevel

logger = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = "2024-11-05"


def _parse_risk_metadata(raw_annotations: object) -> tuple[RiskLevel, bool]:
    """Classify MCP tool risk only when read-only metadata is explicit."""

    if not isinstance(raw_annotations, Mapping):
        return RiskLevel.LOW, False
    read_only_hint = raw_annotations.get("readOnlyHint")
    destructive_hint = raw_annotations.get("destructiveHint")
    if destructive_hint is True:
        return RiskLevel.HIGH, True
    if read_only_hint is True and destructive_hint is False:
        return RiskLevel.LOW, True
    return RiskLevel.LOW, False


class MCPClientError(RuntimeError):
    """Base error for MCP transport and protocol failures."""


class MCPProcessClient:
    """Minimal JSON-RPC-over-stdio MCP client for tool discovery and calls."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None = None,
        startup_timeout_seconds: float = 10.0,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        if not command:
            raise ValueError("MCP command must not be empty")
        self._command = command
        self._environment = dict(environment or {})
        self._startup_timeout_seconds = startup_timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._request_id = 0
        self._request_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._process is not None:
            return
        env = os.environ.copy()
        env.update(self._environment)
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise MCPClientError("failed to start MCP server process") from exc

        if self._process.stdin is None or self._process.stdout is None:
            await self.close()
            raise MCPClientError("MCP server process pipes are unavailable")

        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await asyncio.wait_for(
                self._initialize(),
                timeout=self._startup_timeout_seconds,
            )
        except BaseException:
            await self.close()
            raise

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        result = await self._request("tools/list", {})
        if not isinstance(result, dict):
            raise MCPClientError("MCP tools/list result must be an object")
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list):
            raise MCPClientError("MCP tools/list tools must be a list")
        definitions: list[MCPToolDefinition] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                raise MCPClientError("MCP tool definition must be an object")
            name = raw_tool.get("name")
            description = raw_tool.get("description", name)
            input_schema = raw_tool.get("inputSchema", {"type": "object"})
            if not isinstance(name, str) or not isinstance(description, str):
                raise MCPClientError("MCP tool metadata is invalid")
            if not isinstance(input_schema, Mapping):
                raise MCPClientError("MCP tool inputSchema must be an object")
            risk_level, risk_metadata_known = _parse_risk_metadata(
                raw_tool.get("annotations")
            )
            definitions.append(
                MCPToolDefinition(
                    name=name,
                    description=description,
                    input_schema=cast(dict[str, object], dict(input_schema)),
                    risk_level=risk_level,
                    risk_metadata_known=risk_metadata_known,
                )
            )
        return tuple(definitions)

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> MCPToolCallResult:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
        )
        try:
            return MCPToolCallResult.from_payload(result)
        except ValueError as exc:
            raise MCPClientError(str(exc)) from exc

    async def close(self) -> None:
        process = self._process
        self._process = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def _initialize(self) -> None:
        await self._request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ai-platform-mini", "version": "0.1.0"},
            },
        )
        await self._send_notification("notifications/initialized", {})

    async def _request(self, method: str, params: Mapping[str, object]) -> object:
        async with self._request_lock:
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise MCPClientError("MCP client is not started")
            self._request_id += 1
            request_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            while True:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=self._request_timeout_seconds,
                )
                if not line:
                    raise MCPClientError("MCP server closed stdout")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise MCPClientError("MCP server returned invalid JSON") from exc
                if not isinstance(message, dict):
                    continue
                if message.get("id") != request_id:
                    continue
                error = message.get("error")
                if isinstance(error, dict):
                    raise MCPClientError(
                        str(error.get("message", "MCP request failed"))
                    )
                return message.get("result")

    async def _send_notification(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPClientError("MCP client is not started")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": dict(params),
        }
        process.stdin.write((json.dumps(notification) + "\n").encode())
        await process.stdin.drain()

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            logger.debug(
                "MCP server stderr: %s", line.decode(errors="replace").rstrip()
            )
