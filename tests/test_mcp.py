from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.agents import AgentDecision, AgentRuntime, RunStatus, ToolCall
from app.core.settings import Settings
from app.mcp import (
    MCPClientError,
    MCPProcessClient,
    MCPReadinessState,
    MCPServerConfig,
    MCPServerState,
    MCPToolAdapter,
    MCPToolCallResult,
    MCPToolDefinition,
    MCPToolManager,
)
from app.tools import (
    RiskLevel,
    ToolContext,
    ToolExecutionStatus,
    ToolExecutor,
    ToolRegistry,
)

_DEMO_SERVER = Path(__file__).parent / "fixtures" / "mcp_readonly_server.py"


def _demo_config(
    name: str,
    mode: str,
    allowed_tools: frozenset[str],
) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        command=(sys.executable, "-u", str(_DEMO_SERVER)),
        allowed_tools=allowed_tools,
        startup_timeout_seconds=2,
        request_timeout_seconds=2,
        environment={"MCP_DEMO_MODE": mode},
    )


@dataclass
class FakeMCPClient:
    definitions: tuple[MCPToolDefinition, ...] = ()
    calls: list[tuple[str, Mapping[str, object]]] = field(default_factory=list)
    closed: bool = False
    started: bool = False
    result: MCPToolCallResult = field(
        default_factory=lambda: MCPToolCallResult(
            content=("read-only result",),
        )
    )

    async def start(self) -> None:
        self.started = True

    async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
        return self.definitions

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> MCPToolCallResult:
        self.calls.append((name, arguments))
        return self.result

    async def close(self) -> None:
        self.closed = True


@dataclass
class ScriptedAgentModel:
    decisions: list[AgentDecision]

    async def decide(self, state: object) -> AgentDecision:
        del state
        if not self.decisions:
            raise AssertionError("the scripted model ran out of decisions")
        return self.decisions.pop(0)


@pytest.mark.asyncio
async def test_mcp_adapter_uses_internal_tool_contract() -> None:
    client = FakeMCPClient()
    definition = MCPToolDefinition(
        name="read_status",
        description="Read service status.",
        input_schema={
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
            "additionalProperties": False,
        },
    )
    tool = MCPToolAdapter("demo", client, definition)
    executor = ToolExecutor(
        ToolRegistry([tool]),
        granted_permissions=frozenset({"mcp:server:demo"}),
    )

    result = await executor.execute(
        tool.name,
        {"service": "api"},
        ToolContext(run_id="run-1", step_index=1),
    )

    assert result.succeeded
    assert json.loads(result.output) == {
        "content": ["read-only result"],
        "error": None,
        "ok": True,
    }
    assert tool.name == "mcp__demo__read_status"
    assert tool.required_permissions == ("mcp:server:demo",)
    assert client.calls == [("read_status", {"service": "api"})]


@pytest.mark.asyncio
async def test_mcp_manager_discovers_only_allowlisted_low_risk_tools() -> None:
    read_definition = MCPToolDefinition(
        name="read_status",
        description="Read status.",
        input_schema={"type": "object"},
        risk_level=RiskLevel.LOW,
    )
    write_definition = MCPToolDefinition(
        name="write_status",
        description="Write status.",
        input_schema={"type": "object"},
        risk_level=RiskLevel.HIGH,
    )
    client = FakeMCPClient(definitions=(read_definition, write_definition))
    config = MCPServerConfig(
        name="demo",
        command=("unused",),
        allowed_tools=frozenset({"read_status", "write_status"}),
        max_risk_level=RiskLevel.LOW,
    )
    manager = MCPToolManager(
        [config],
        client_factory=lambda _: client,
    )

    tools = await manager.discover_tools()

    assert [tool.name for tool in tools] == ["mcp__demo__read_status"]
    assert client.started
    await manager.close()
    assert client.closed


@pytest.mark.asyncio
async def test_mcp_manager_grants_permissions_for_active_servers() -> None:
    client = FakeMCPClient(
        definitions=(
            MCPToolDefinition(
                name="read_status",
                description="Read status.",
                input_schema={"type": "object"},
            ),
        )
    )
    manager = MCPToolManager(
        [MCPServerConfig(name="demo", command=("unused",))],
        client_factory=lambda _: client,
    )

    await manager.discover_tools()

    assert manager.granted_permissions() == frozenset({"mcp:server:demo"})
    await manager.close()


@pytest.mark.asyncio
async def test_mcp_manager_isolates_server_with_duplicate_tool_names() -> None:
    duplicate_client = FakeMCPClient(
        definitions=(
            MCPToolDefinition(
                name="read_status",
                description="Read status.",
                input_schema={"type": "object"},
            ),
            MCPToolDefinition(
                name="read_status",
                description="Read status again.",
                input_schema={"type": "object"},
            ),
        )
    )
    healthy_client = FakeMCPClient(
        definitions=(
            MCPToolDefinition(
                name="read_health",
                description="Read health.",
                input_schema={"type": "object"},
            ),
        )
    )
    clients = {"duplicate": duplicate_client, "healthy": healthy_client}
    manager = MCPToolManager(
        [
            MCPServerConfig(name="duplicate", command=("unused",)),
            MCPServerConfig(name="healthy", command=("unused",)),
        ],
        client_factory=lambda config: clients[config.name],
    )

    tools = await manager.discover_tools()

    assert [tool.name for tool in tools] == ["mcp__healthy__read_health"]
    assert duplicate_client.closed
    assert manager.granted_permissions() == frozenset({"mcp:server:healthy"})
    await manager.close()


@pytest.mark.asyncio
async def test_mcp_manager_isolates_discovery_when_client_close_fails() -> None:
    class FailingDiscoveryClient(FakeMCPClient):
        async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
            raise MCPClientError("tools/list failed")

        async def close(self) -> None:
            raise RuntimeError("close raced with process exit")

    failed_client = FailingDiscoveryClient()
    healthy_client = FakeMCPClient(
        definitions=(
            MCPToolDefinition(
                name="read_health",
                description="Read health.",
                input_schema={"type": "object"},
            ),
        )
    )
    clients = {"failed": failed_client, "healthy": healthy_client}
    manager = MCPToolManager(
        [
            MCPServerConfig(name="failed", command=("unused",)),
            MCPServerConfig(name="healthy", command=("unused",)),
        ],
        client_factory=lambda config: clients[config.name],
    )

    tools = await manager.discover_tools()

    assert [tool.name for tool in tools] == ["mcp__healthy__read_health"]
    assert healthy_client.started
    readiness = manager.readiness_status()
    assert readiness.state is MCPReadinessState.DEGRADED
    assert [status.state for status in readiness.servers] == [
        MCPServerState.FAILED,
        MCPServerState.READY,
    ]
    assert manager.granted_permissions() == frozenset({"mcp:server:healthy"})
    await manager.close()
    assert healthy_client.closed


@pytest.mark.asyncio
async def test_mcp_manager_closes_clients_when_discovery_is_cancelled() -> None:
    class BlockingClient(FakeMCPClient):
        def __init__(self) -> None:
            super().__init__()
            self.list_started = asyncio.Event()

        async def list_tools(self) -> tuple[MCPToolDefinition, ...]:
            self.list_started.set()
            await asyncio.Event().wait()
            return ()

    healthy_client = FakeMCPClient(
        definitions=(
            MCPToolDefinition(
                name="read_health",
                description="Read health.",
                input_schema={"type": "object"},
            ),
        )
    )
    blocking_client = BlockingClient()
    clients = {"healthy": healthy_client, "blocking": blocking_client}
    manager = MCPToolManager(
        [
            MCPServerConfig(name="healthy", command=("unused",)),
            MCPServerConfig(name="blocking", command=("unused",)),
        ],
        client_factory=lambda config: clients[config.name],
    )

    discovery_task = asyncio.create_task(manager.discover_tools())
    await blocking_client.list_started.wait()
    discovery_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await discovery_task

    assert blocking_client.closed
    assert not healthy_client.closed
    await manager.close()
    assert healthy_client.closed


@pytest.mark.asyncio
async def test_mcp_manager_closes_all_clients_when_close_is_cancelled() -> None:
    class BlockingCloseClient(FakeMCPClient):
        def __init__(self) -> None:
            super().__init__()
            self.close_started = asyncio.Event()
            self.allow_close = asyncio.Event()

        async def close(self) -> None:
            self.close_started.set()
            await self.allow_close.wait()
            self.closed = True

    blocking_client = BlockingCloseClient()
    healthy_client = FakeMCPClient()
    clients = {"blocking": blocking_client, "healthy": healthy_client}
    manager = MCPToolManager(
        [
            MCPServerConfig(name="blocking", command=("unused",)),
            MCPServerConfig(name="healthy", command=("unused",)),
        ],
        client_factory=lambda config: clients[config.name],
    )

    await manager.discover_tools()
    close_task = asyncio.create_task(manager.close())
    await blocking_client.close_started.wait()
    close_task.cancel()
    blocking_client.allow_close.set()

    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert blocking_client.closed
    assert healthy_client.closed
    assert manager.granted_permissions() == frozenset()


@pytest.mark.asyncio
async def test_mcp_manager_terminates_stdio_process_when_discovery_is_cancelled(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "tools-list-started"
    server = tmp_path / "blocking_mcp_server.py"
    server.write_text(
        "import json\n"
        "import sys\n"
        "import time\n"
        f"marker = {str(marker)!r}\n"
        "\n"
        "for raw_line in sys.stdin:\n"
        "    message = json.loads(raw_line)\n"
        '    method = message.get("method")\n'
        '    if "id" not in message:\n'
        "        continue\n"
        '    if method == "initialize":\n'
        '        result = {"protocolVersion": "2024-11-05", "capabilities": {}}\n'
        '    elif method == "tools/list":\n'
        '        open(marker, "w", encoding="utf-8").close()\n'
        "        time.sleep(60)\n"
        "        continue\n"
        "    else:\n"
        "        result = {}\n"
        '    print(json.dumps({"jsonrpc": "2.0", '
        '"id": message["id"], "result": result}), flush=True)\n',
        encoding="utf-8",
    )
    clients: list[MCPProcessClient] = []

    def client_factory(config: MCPServerConfig) -> MCPProcessClient:
        client = MCPProcessClient(
            config.command,
            startup_timeout_seconds=2,
            request_timeout_seconds=30,
        )
        clients.append(client)
        return client

    manager = MCPToolManager(
        [
            MCPServerConfig(
                name="blocking",
                command=(sys.executable, "-u", str(server)),
                startup_timeout_seconds=2,
                request_timeout_seconds=30,
            )
        ],
        client_factory=client_factory,
    )

    async def wait_for_marker() -> None:
        while not marker.exists():
            await asyncio.sleep(0.01)

    discovery_task = asyncio.create_task(manager.discover_tools())
    await asyncio.wait_for(wait_for_marker(), timeout=2)
    process = clients[0]._process
    assert process is not None

    discovery_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await discovery_task

    assert process.returncode is not None
    assert clients[0]._process is None


@pytest.mark.asyncio
async def test_mcp_manager_skips_unavailable_server() -> None:
    class UnavailableClient(FakeMCPClient):
        async def start(self) -> None:
            raise MCPClientError("server unavailable")

    client = UnavailableClient()
    manager = MCPToolManager(
        [MCPServerConfig(name="offline", command=("unused",))],
        client_factory=lambda _: client,
    )

    assert await manager.discover_tools() == ()
    assert client.closed


def test_settings_parse_explicit_mcp_server_allowlist() -> None:
    settings = Settings(
        mcp_enabled=True,
        mcp_servers_json=json.dumps(
            [
                {
                    "name": "demo",
                    "command": ["python", "server.py"],
                    "allowed_tools": ["read_status"],
                    "max_risk_level": "low",
                    "startup_timeout_seconds": 3,
                    "request_timeout_seconds": 4,
                    "environment": {"DEMO_MODE": "readonly"},
                }
            ]
        ),
    )

    configs = settings.get_mcp_server_configs()

    assert configs[0].name == "demo"
    assert configs[0].command == ("python", "server.py")
    assert configs[0].allowed_tools == frozenset({"read_status"})
    assert configs[0].startup_timeout_seconds == 3
    assert configs[0].environment == {"DEMO_MODE": "readonly"}


def test_disabled_mcp_does_not_parse_server_configuration() -> None:
    settings = Settings(mcp_enabled=False, mcp_servers_json="not-json")

    assert settings.get_mcp_server_configs() == ()


@pytest.mark.asyncio
async def test_stdio_mcp_client_discovers_and_calls_tool(tmp_path: Path) -> None:
    server = tmp_path / "mcp_server.py"
    server.write_text(
        "import json\n"
        "import sys\n"
        "\n"
        "for raw_line in sys.stdin:\n"
        "    message = json.loads(raw_line)\n"
        '    method = message.get("method")\n'
        '    if "id" not in message:\n'
        "        continue\n"
        '    if method == "initialize":\n'
        '        result = {"protocolVersion": "2024-11-05", "capabilities": {}}\n'
        '    elif method == "tools/list":\n'
        '        result = {"tools": [{"name": "read_status", '
        '"description": "Read status.", '
        '"inputSchema": {"type": "object"}, '
        '"annotations": {"readOnlyHint": True, "destructiveHint": False}}]}\n'
        '    elif method == "tools/call":\n'
        '        result = {"content": [{"type": "text", "text": "ok"}]}\n'
        "    else:\n"
        "        result = {}\n"
        '    print(json.dumps({"jsonrpc": "2.0", '
        '"id": message["id"], "result": result}), flush=True)\n',
        encoding="utf-8",
    )
    client = MCPProcessClient(
        (sys.executable, "-u", str(server)),
        startup_timeout_seconds=2,
        request_timeout_seconds=2,
    )

    await client.start()
    try:
        definitions = await client.list_tools()
        result = await client.call_tool("read_status", {})
    finally:
        await client.close()

    assert definitions[0].name == "read_status"
    assert definitions[0].risk_metadata_known
    assert definitions[0].risk_level is RiskLevel.LOW
    assert result.content == ({"type": "text", "text": "ok"},)
    assert not result.is_error


@pytest.mark.asyncio
async def test_stdio_mcp_manager_fails_closed_for_unknown_and_high_risk_tools(
    tmp_path: Path,
) -> None:
    server = tmp_path / "risk_mcp_server.py"
    server.write_text(
        "import json\n"
        "import sys\n"
        "\n"
        "for raw_line in sys.stdin:\n"
        "    message = json.loads(raw_line)\n"
        '    method = message.get("method")\n'
        '    if "id" not in message:\n'
        "        continue\n"
        '    if method == "initialize":\n'
        '        result = {"protocolVersion": "2024-11-05", "capabilities": {}}\n'
        '    elif method == "tools/list":\n'
        '        result = {"tools": [\n'
        '            {"name": "read_status", "description": "Read status.", '
        '             "inputSchema": {"type": "object"}, '
        '             "annotations": {"readOnlyHint": True, '
        '"destructiveHint": False}},\n'
        '            {"name": "delete_status", "description": "Delete status.", '
        '             "inputSchema": {"type": "object"}, '
        '             "annotations": {"readOnlyHint": False, '
        '"destructiveHint": True}},\n'
        '            {"name": "unknown_status", "description": "Unknown status.", '
        '             "inputSchema": {"type": "object"}}\n'
        "        ]}\n"
        "    else:\n"
        "        result = {}\n"
        '    print(json.dumps({"jsonrpc": "2.0", '
        '"id": message["id"], "result": result}), flush=True)\n',
        encoding="utf-8",
    )
    manager = MCPToolManager(
        [
            MCPServerConfig(
                name="risk-demo",
                command=(sys.executable, "-u", str(server)),
                allowed_tools=frozenset(
                    {"read_status", "delete_status", "unknown_status"}
                ),
                max_risk_level=RiskLevel.LOW,
                startup_timeout_seconds=2,
                request_timeout_seconds=2,
            )
        ]
    )

    try:
        tools = await manager.discover_tools()
        assert [tool.name for tool in tools] == ["mcp__risk-demo__read_status"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_agent_runtime_calls_mcp_server_with_manager_grant(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "calls.log"
    server = tmp_path / "agent_mcp_server.py"
    server.write_text(
        "import json\n"
        "import sys\n"
        f"marker = {str(marker)!r}\n"
        "\n"
        "for raw_line in sys.stdin:\n"
        "    message = json.loads(raw_line)\n"
        '    method = message.get("method")\n'
        '    if "id" not in message:\n'
        "        continue\n"
        '    if method == "initialize":\n'
        '        result = {"protocolVersion": "2024-11-05", "capabilities": {}}\n'
        '    elif method == "tools/list":\n'
        '        result = {"tools": [{"name": "read_status", '
        '        "description": "Read status.", '
        '        "inputSchema": {"type": "object"}, '
        '        "annotations": {"readOnlyHint": True, "destructiveHint": False}}]}\n'
        '    elif method == "tools/call":\n'
        '        with open(marker, "a", encoding="utf-8") as handle:\n'
        '            handle.write("tools/call\\n")\n'
        '        result = {"content": [{"type": "text", "text": "status: ok"}]}\n'
        "    else:\n"
        "        result = {}\n"
        '    print(json.dumps({"jsonrpc": "2.0", '
        '"id": message["id"], "result": result}), flush=True)\n',
        encoding="utf-8",
    )
    manager = MCPToolManager(
        [
            MCPServerConfig(
                name="demo",
                command=(sys.executable, "-u", str(server)),
                allowed_tools=frozenset({"read_status"}),
                startup_timeout_seconds=2,
                request_timeout_seconds=2,
            )
        ]
    )

    try:
        tools = await manager.discover_tools()
        registry = ToolRegistry(list(tools))
        executor = ToolExecutor(
            registry,
            granted_permissions=manager.granted_permissions(),
        )
        model = ScriptedAgentModel(
            [
                AgentDecision(
                    tool_calls=(
                        ToolCall(
                            call_id="call-1",
                            name="mcp__demo__read_status",
                            arguments={},
                        ),
                    )
                ),
                AgentDecision(answer="The service is healthy."),
            ]
        )

        result = await AgentRuntime(model, tool_executor=executor).run(
            "check service status"
        )

        assert result.status is RunStatus.COMPLETED
        assert result.answer == "The service is healthy."
        assert json.loads(result.state.messages[-2].content) == {
            "content": [{"text": "status: ok", "type": "text"}],
            "error": None,
            "ok": True,
        }
        assert marker.read_text(encoding="utf-8") == "tools/call\n"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_stdio_mcp_tool_failure_is_normalized_and_other_server_survives() -> None:
    manager = MCPToolManager(
        [
            _demo_config("failure", "failure", frozenset({"fail_status"})),
            _demo_config("healthy", "healthy", frozenset({"read_status"})),
        ]
    )

    try:
        tools = await manager.discover_tools()
        executor = ToolExecutor(
            ToolRegistry(list(tools)),
            granted_permissions=manager.granted_permissions(),
        )

        failed = await executor.execute(
            "mcp__failure__fail_status",
            {},
            ToolContext(run_id="run-failure", step_index=1),
        )
        healthy = await executor.execute(
            "mcp__healthy__read_status",
            {},
            ToolContext(run_id="run-failure", step_index=2),
        )

        assert failed.status is ToolExecutionStatus.FAILED
        assert failed.error_code == "tool_execution_failed"
        assert failed.output == "Tool execution failed."
        assert healthy.succeeded

        model = ScriptedAgentModel(
            [
                AgentDecision(
                    tool_calls=(
                        ToolCall(
                            call_id="failed-call",
                            name="mcp__failure__fail_status",
                            arguments={},
                        ),
                        ToolCall(
                            call_id="healthy-call",
                            name="mcp__healthy__read_status",
                            arguments={},
                        ),
                    )
                ),
                AgentDecision(answer="The tool boundary stayed isolated."),
            ]
        )
        run = await AgentRuntime(model, tool_executor=executor).run(
            "verify tool isolation"
        )

        assert run.status is RunStatus.COMPLETED
        assert run.state.steps[0].tool_results[0].error == "tool_execution_failed"
        assert run.state.steps[0].tool_results[1].succeeded
    finally:
        await manager.close()

    assert manager.readiness_status().state is MCPReadinessState.CLOSED


@pytest.mark.asyncio
async def test_stdio_mcp_runtime_disconnect_is_normalized_and_cleanup_is_safe() -> None:
    manager = MCPToolManager(
        [
            _demo_config(
                "disconnect",
                "disconnect",
                frozenset({"disconnect_status"}),
            ),
            _demo_config("healthy", "healthy", frozenset({"read_status"})),
        ]
    )

    try:
        tools = await manager.discover_tools()
        executor = ToolExecutor(
            ToolRegistry(list(tools)),
            granted_permissions=manager.granted_permissions(),
        )

        disconnected = await executor.execute(
            "mcp__disconnect__disconnect_status",
            {},
            ToolContext(run_id="run-disconnect", step_index=1),
        )
        healthy = await executor.execute(
            "mcp__healthy__read_status",
            {},
            ToolContext(run_id="run-disconnect", step_index=2),
        )

        assert disconnected.status is ToolExecutionStatus.FAILED
        assert disconnected.error_code == "tool_execution_failed"
        assert healthy.succeeded
    finally:
        await manager.close()
        await manager.close()

    assert manager.granted_permissions() == frozenset()
    assert manager.readiness_status().state is MCPReadinessState.CLOSED
