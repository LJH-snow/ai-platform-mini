"""Shared fakes and definition helpers for workflow builder tests (E2 P2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.tools.models import ToolContext, ToolExecutionResult, ToolExecutionStatus
from app.workflow_builder.execution_context import WorkflowExecutionContext
from app.workflows.engine.executor import NodeOutput
from app.workflows.engine.models import NodeType, WorkflowNode

WS_A = "ws-a"
WS_B = "ws-b"
TOOL_CALCULATOR = "calculator"


class FakeChatService:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []
        self.default_model = "fake-default-model"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            model=request.model or self.default_model,
            thread_id=None,
            created_at=None,
            message=ChatMessage(role="assistant", content=f"回答:{request.message}"),
            done=True,
            done_reason=None,
            prompt_tokens=None,
            completion_tokens=None,
        )


class FakeRAGService:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.owner_key_hashes: list[str] = []

    async def prepare(
        self, request: ChatRequest, *, owner_key_hash: str
    ) -> ChatRequest:
        self.queries.append(request.message)
        self.owner_key_hashes.append(owner_key_hash)
        return request

    async def answer(self, prepared: ChatRequest) -> ChatResponse:
        return ChatResponse(
            model="rag-model",
            message=ChatMessage(role="assistant", content=f"rag:{prepared.message}"),
            done=True,
        )


class FakeToolExecutor:
    def __init__(self, result: ToolExecutionResult | None = None) -> None:
        self.result = result
        self.calls: list[tuple[str, object, ToolContext]] = []

    async def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, object] | object,
        context: ToolContext,
        *,
        timeout_seconds: float | None = None,
    ) -> ToolExecutionResult:
        del timeout_seconds
        self.calls.append((tool_name, arguments, context))
        if self.result is not None:
            return self.result
        return ToolExecutionResult(
            tool_name=tool_name,
            status=ToolExecutionStatus.SUCCEEDED,
            output=str(arguments),
        )


@dataclass(frozen=True)
class _FakeAgentResult:
    answer: str


@dataclass(frozen=True)
class _FakeAgentOutcome:
    result: _FakeAgentResult


class FakeAgentService:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.contexts: list[object] = []

    async def run(
        self, request: object, *, context: object, api_key: object
    ) -> _FakeAgentOutcome:
        self.requests.append(request)
        self.contexts.append(context)
        del api_key
        agent_id = getattr(request, "agent_id", None)
        message = getattr(request, "message", "")
        return _FakeAgentOutcome(
            result=_FakeAgentResult(answer=f"agent-answer:{agent_id}:{message}")
        )


def run_context(
    workspace_id: str = WS_A, run_id: str = "run-test"
) -> WorkflowExecutionContext:
    return WorkflowExecutionContext(
        workspace_id=workspace_id,
        api_key_hash="key-hash",
        owner_key_hash="owner-hash",
        run_id=run_id,
    )


async def run_with_context(
    executor: object, node: WorkflowNode, variables: Mapping[str, object]
) -> NodeOutput:
    from app.workflow_builder.execution_context import (
        reset_workflow_execution_context,
        set_workflow_execution_context,
    )

    token = set_workflow_execution_context(run_context())
    try:
        return await executor.execute(node, variables, {})  # type: ignore[attr-defined, no-any-return]
    finally:
        reset_workflow_execution_context(token)


def definition_dict(*, version: int = 1) -> dict[str, object]:
    """n1(input) -> n2(llm) -> n3(output)."""
    return {
        "nodes": [
            {"id": "n1", "type": "input", "config": {}},
            {
                "id": "n2",
                "type": "llm",
                "config": {"prompt_template": "{{input.text}}"},
            },
            {
                "id": "n3",
                "type": "output",
                "config": {"output_template": "{{n2.output}}"},
            },
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
        ],
        "version": version,
    }


def tool_definition_dict(tool_name: str = TOOL_CALCULATOR) -> dict[str, object]:
    """n1(input) -> n2(tool) -> n3(output)."""
    return {
        "nodes": [
            {"id": "n1", "type": "input", "config": {}},
            {
                "id": "n2",
                "type": "tool",
                "config": {
                    "tool": tool_name,
                    "arguments_template": {"expression": "{{input.text}}"},
                },
            },
            {
                "id": "n3",
                "type": "output",
                "config": {"output_template": "{{n2.output}}"},
            },
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
        ],
        "version": 1,
    }


def agent_definition_dict(agent_id: str) -> dict[str, object]:
    """n1(input) -> n2(agent) -> n3(output)."""
    return {
        "nodes": [
            {"id": "n1", "type": "input", "config": {}},
            {
                "id": "n2",
                "type": "agent",
                "config": {"agent_id": agent_id, "prompt": "{{input.text}}"},
            },
            {
                "id": "n3",
                "type": "output",
                "config": {"output_template": "{{n2.output}}"},
            },
        ],
        "edges": [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
        ],
        "version": 1,
    }


def llm_node(node_id: str, config: Mapping[str, Any] | None = None) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=NodeType.LLM, config=dict(config or {}))


def set_prompt_template(definition: dict[str, object], template: str) -> None:
    """Replace the llm node's prompt_template (typed access into nested JSON)."""
    nodes = cast(list[dict[str, object]], definition["nodes"])
    config = cast(dict[str, object], nodes[1]["config"])
    config["prompt_template"] = template


def get_prompt_template(definition: dict[str, object]) -> str:
    """Read the llm node's prompt_template (typed access into nested JSON)."""
    nodes = cast(list[dict[str, object]], definition["nodes"])
    config = cast(dict[str, object], nodes[1]["config"])
    value = config["prompt_template"]
    assert isinstance(value, str)
    return value
