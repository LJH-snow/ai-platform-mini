from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

import pytest

from app.tools import (
    CalculatorTool,
    DuplicateToolError,
    RiskLevel,
    ToolContext,
    ToolExecutionStatus,
    ToolExecutor,
    ToolRegistry,
)


@dataclass
class EchoTool:
    name: str = "echo"
    description: str = "Echo a message."
    input_schema: Mapping[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "message": {"type": "string", "minLength": 1},
            },
            "required": ["message"],
            "additionalProperties": False,
        }
    )
    output_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "string"}
    )
    risk_level: RiskLevel = RiskLevel.LOW
    required_permissions: tuple[str, ...] = ()
    calls: list[tuple[Mapping[str, object], ToolContext]] = field(default_factory=list)

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        self.calls.append((arguments, context))
        return str(arguments["message"])


@dataclass
class SlowTool:
    name: str = "slow"
    description: str = "Sleep before returning."
    input_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    output_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "string"}
    )
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        del arguments, context
        self.started.set()
        await asyncio.sleep(1)
        return "late"


@dataclass
class FailingTool:
    name: str = "failing"
    description: str = "Always fails."
    input_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    output_schema: Mapping[str, object] = field(
        default_factory=lambda: {"type": "string"}
    )

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        del arguments, context
        raise RuntimeError("database password=super-secret")


@pytest.fixture
def context() -> ToolContext:
    return ToolContext(
        run_id="run-1",
        step_index=2,
        request_id="request-1",
        metadata={"tenant": "demo"},
    )


def test_registry_rejects_duplicate_names_and_preserves_schema_order() -> None:
    first = EchoTool()
    second = EchoTool()
    second.description = "A different description."
    registry = ToolRegistry([first])

    with pytest.raises(DuplicateToolError):
        registry.register(second)

    calculator = CalculatorTool()
    registry.register(calculator)

    assert registry.get("echo") is first
    assert registry.get("missing") is None
    assert [descriptor.name for descriptor in registry.list_descriptors()] == [
        "echo",
        "calculator",
    ]
    assert [
        cast(dict[str, object], schema["function"])["name"]
        for schema in registry.export_schemas()
    ] == [
        "echo",
        "calculator",
    ]
    assert registry.export_schemas() == registry.to_model_schemas()


def test_registry_schema_export_is_independent_from_tool_schema() -> None:
    tool = EchoTool()
    registry = ToolRegistry([tool])

    exported = registry.export_schemas()
    function_schema = exported[0]["function"]
    assert isinstance(function_schema, dict)
    parameters = function_schema["parameters"]
    assert isinstance(parameters, dict)
    parameters["properties"] = {}

    assert tool.input_schema["properties"] != {}


@pytest.mark.asyncio
async def test_executor_validates_arguments_before_entering_tool(
    context: ToolContext,
) -> None:
    tool = EchoTool()
    executor = ToolExecutor(ToolRegistry([tool]))

    result = await executor.execute("echo", {"message": 123}, context)

    assert result.status is ToolExecutionStatus.INVALID_ARGUMENTS
    assert result.error_code == "invalid_tool_arguments"
    assert result.output == "Invalid tool arguments."
    assert tool.calls == []


@pytest.mark.asyncio
async def test_executor_denies_risk_or_missing_permission_before_tool_call(
    context: ToolContext,
) -> None:
    permissioned = EchoTool(required_permissions=("tool:echo",))
    permission_executor = ToolExecutor(ToolRegistry([permissioned]))

    permission_result = await permission_executor.execute(
        "echo", {"message": "hello"}, context
    )

    assert permission_result.status is ToolExecutionStatus.PERMISSION_DENIED
    assert permission_result.error_code == "tool_permission_denied"
    assert permissioned.calls == []

    high_risk = EchoTool(name="high-risk", risk_level=RiskLevel.HIGH)
    risk_executor = ToolExecutor(ToolRegistry([high_risk]))

    risk_result = await risk_executor.execute(
        "high-risk", {"message": "hello"}, context
    )

    assert risk_result.status is ToolExecutionStatus.PERMISSION_DENIED
    assert high_risk.calls == []


@pytest.mark.asyncio
async def test_executor_accepts_granted_low_risk_tool_permission(
    context: ToolContext,
) -> None:
    tool = EchoTool(required_permissions=("tool:echo",))
    executor = ToolExecutor(
        ToolRegistry([tool]),
        granted_permissions=frozenset({"tool:echo"}),
    )

    result = await executor.execute("echo", {"message": "hello"}, context)

    assert result.succeeded
    assert result.output == "hello"
    assert len(tool.calls) == 1


@pytest.mark.asyncio
async def test_executor_forwards_same_context_to_tool(context: ToolContext) -> None:
    tool = EchoTool()
    executor = ToolExecutor(ToolRegistry([tool]))

    result = await executor.execute("echo", {"message": "hello"}, context)

    assert result.succeeded
    assert result.output == "hello"
    assert tool.calls == [({"message": "hello"}, context)]
    assert tool.calls[0][1] is context


@pytest.mark.asyncio
async def test_executor_times_out_slow_tool(context: ToolContext) -> None:
    tool = SlowTool()
    executor = ToolExecutor(
        ToolRegistry([tool]),
        default_timeout_seconds=0.01,
    )

    result = await executor.execute("slow", {}, context)

    assert result.status is ToolExecutionStatus.TIMED_OUT
    assert result.error_code == "tool_timeout"
    assert result.output == "Tool execution timed out."
    assert tool.started.is_set()


@pytest.mark.asyncio
async def test_executor_normalizes_tool_exception_without_leaking_message(
    context: ToolContext,
) -> None:
    executor = ToolExecutor(ToolRegistry([FailingTool()]))

    result = await executor.execute("failing", {}, context)

    assert result.status is ToolExecutionStatus.FAILED
    assert result.error_code == "tool_execution_failed"
    assert result.output == "Tool execution failed."
    assert "super-secret" not in result.output
    assert "database password" not in (result.error_message or "")


@pytest.mark.asyncio
async def test_executor_truncates_string_output_with_marker(
    context: ToolContext,
) -> None:
    tool = EchoTool()
    executor = ToolExecutor(ToolRegistry([tool]), output_max_chars=32)

    result = await executor.execute("echo", {"message": "x" * 100}, context)

    assert result.succeeded
    assert result.truncated
    assert len(result.output) == 32
    assert result.output.endswith("...[tool output truncated]")


@pytest.mark.asyncio
async def test_executor_handles_unknown_tool(context: ToolContext) -> None:
    result = await ToolExecutor(ToolRegistry()).execute(
        "missing",
        {},
        context,
    )

    assert result.status is ToolExecutionStatus.NOT_FOUND
    assert result.error_code == "tool_not_found"
    assert result.output == "Requested tool is unavailable."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * (3 + 4)", "15"),
        ("2 ** 3", "8"),
        ("10 % 3", "1"),
        ("-2 + 5", "3"),
        ("5 / 2", "2.5"),
    ],
)
async def test_calculator_supports_allowlisted_arithmetic(
    expression: str,
    expected: str,
    context: ToolContext,
) -> None:
    result = await ToolExecutor(ToolRegistry([CalculatorTool()])).execute(
        "calculator",
        {"expression": expression},
        context,
    )

    assert result.succeeded
    assert result.output == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "open('/tmp/should-not-exist', 'w')",
        "[1, 2, 3]",
        "x + 1",
        "1 // 2",
        "1 << 2",
    ],
)
async def test_calculator_rejects_unsafe_or_unsupported_expression(
    expression: str,
    context: ToolContext,
) -> None:
    result = await CalculatorTool().execute({"expression": expression}, context)

    assert result == "Calculator error: invalid expression."


@pytest.mark.asyncio
async def test_calculator_returns_safe_division_by_zero_error(
    context: ToolContext,
) -> None:
    result = await CalculatorTool().execute({"expression": "1 / 0"}, context)

    assert result == "Calculator error: division by zero."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "1" * 257,
        "+".join("1" for _ in range(65)),
        "2 ** 101",
        "10 ** 101",
        "1e309",
    ],
)
async def test_calculator_enforces_expression_limits(
    expression: str,
    context: ToolContext,
) -> None:
    result = await CalculatorTool().execute({"expression": expression}, context)

    assert result == "Calculator error: invalid expression."
