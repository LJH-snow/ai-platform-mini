from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.agents import AgentModel, AgentRuntime, AgentTool
from app.agents.models import (
    AgentDecision,
    AgentEvent,
    AgentEventKind,
    AgentMessage,
    AgentRunResult,
    AgentState,
    AgentStep,
    RunStatus,
    StopReason,
    ToolCall,
    ToolResult,
)
from app.api.agent import _to_response, _to_stream_event, get_agent_service
from app.auth.hash import hash_api_key
from app.auth.models import APIKey
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.service import ConversationService
from app.core.container import (
    provide_agent_run_record_service,
    provide_conversation_service,
)
from app.core.context import RequestContext
from app.exceptions.base import ProviderError, QuotaExceededError, RAGUnavailableError
from app.main import app
from app.mcp import MCPToolAdapter, MCPToolCallResult, MCPToolDefinition
from app.providers.results import ProviderChatResult
from app.runs import InMemoryRunTraceRecorder, RunTraceRecorderFactory
from app.schemas.agent import (
    DEFAULT_AGENT_MAX_STEPS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    DEFAULT_AGENT_TOKEN_BUDGET,
    MAX_AGENT_TOKEN_BUDGET,
    AgentRunRequest,
)
from app.schemas.chat import ChatMessage, ChatResponse
from app.services.agent_service import (
    AgentRunOutcome,
    AgentService,
    _ChatServiceAgentModel,
)
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")


@dataclass
class FakeAgentService:
    outcome: AgentRunOutcome | None = None
    error: Exception | None = None
    requests: list[AgentRunRequest] = field(default_factory=list)

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
    ) -> AgentRunOutcome:
        del context, api_key
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


@dataclass
class FakeAgentRunRecordService:
    saved_models: list[str | None]

    async def save(
        self,
        response: object,
        request: AgentRunRequest,
        context: RequestContext,
        api_key: APIKey,
        model: str | None = None,
    ) -> None:
        del response, request, context, api_key
        self.saved_models.append(model)


def _outcome(
    *,
    status: RunStatus = RunStatus.COMPLETED,
    stop_reason: StopReason = StopReason.DIRECT_ANSWER,
    answer: str | None = "done",
    steps: list[AgentStep] | None = None,
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 5,
    estimated_usage: bool = False,
) -> AgentRunOutcome:
    run_id = "run-test-1"
    state = AgentState(run_id=run_id, user_input="hello")
    state.steps.extend(steps or [])
    occurred_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    events = (
        AgentEvent(
            kind=AgentEventKind.RUN_STARTED,
            run_id=run_id,
            sequence=1,
            occurred_at=occurred_at,
        ),
        AgentEvent(
            kind=AgentEventKind.RUN_STOPPED,
            run_id=run_id,
            sequence=2,
            occurred_at=occurred_at,
            status=status,
            stop_reason=stop_reason,
        ),
    )
    return AgentRunOutcome(
        result=AgentRunResult(
            run_id=run_id,
            status=status,
            stop_reason=stop_reason,
            answer=answer,
            state=state,
            events=events,
            token_usage=15,
        ),
        model="test-model",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_usage=estimated_usage,
    )


def test_agent_response_exposes_step_and_tool_lifecycle_timing() -> None:
    run_id = "run-timing-1"
    start = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
    call = ToolCall(
        call_id="calc-1", name="calculator", arguments={"expression": "12 + 3"}
    )
    result = ToolResult(
        call_id="calc-1", name="calculator", content="15", succeeded=True
    )
    step = AgentStep(
        index=1, decision=AgentDecision(tool_calls=(call,)), tool_results=(result,)
    )
    events = (
        AgentEvent(AgentEventKind.RUN_STARTED, run_id, 1, start),
        AgentEvent(AgentEventKind.STEP_STARTED, run_id, 2, start, step_index=1),
        AgentEvent(
            AgentEventKind.TOOL_STARTED, run_id, 3, start, step_index=1, tool_call=call
        ),
        AgentEvent(
            AgentEventKind.TOOL_COMPLETED,
            run_id,
            4,
            start.replace(microsecond=250000),
            step_index=1,
            tool_call=call,
            tool_result=result,
        ),
        AgentEvent(
            AgentEventKind.STEP_COMPLETED,
            run_id,
            5,
            start.replace(microsecond=500000),
            step_index=1,
            status=RunStatus.COMPLETED,
        ),
        AgentEvent(
            AgentEventKind.RUN_STOPPED,
            run_id,
            6,
            start.replace(microsecond=750000),
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
        ),
    )
    state = AgentState(run_id=run_id, user_input="calculate")
    state.steps.append(step)
    outcome = AgentRunOutcome(
        result=AgentRunResult(
            run_id=run_id,
            status=RunStatus.COMPLETED,
            stop_reason=StopReason.DIRECT_ANSWER,
            answer="15",
            state=state,
            events=events,
            token_usage=0,
        ),
        model="test-model",
        prompt_tokens=None,
        completion_tokens=None,
        estimated_usage=True,
    )

    body = _to_response(outcome).model_dump(mode="json")

    assert body["events"][1]["occurred_at"] == start.isoformat().replace("+00:00", "Z")
    assert body["started_at"] == start.isoformat().replace("+00:00", "Z")
    assert body["completed_at"] == start.replace(
        microsecond=750000
    ).isoformat().replace("+00:00", "Z")
    assert body["duration_ms"] == 750.0
    assert body["steps"][0]["started_at"] == start.isoformat().replace("+00:00", "Z")
    assert body["steps"][0]["completed_at"] == start.replace(
        microsecond=500000
    ).isoformat().replace("+00:00", "Z")
    assert body["steps"][0]["duration_ms"] == 500.0
    assert body["steps"][0]["tool_calls"][0]["started_at"] == start.isoformat().replace(
        "+00:00", "Z"
    )
    assert body["steps"][0]["tool_calls"][0]["completed_at"] == start.replace(
        microsecond=250000
    ).isoformat().replace("+00:00", "Z")
    assert body["steps"][0]["tool_calls"][0]["duration_ms"] == 250.0


def _override(service: FakeAgentService) -> None:
    app.dependency_overrides[get_agent_service] = lambda: service


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_agent_service, None)


def test_agent_endpoint_accepts_rag_preset_and_rejects_unknown_presets() -> None:
    service = FakeAgentService(outcome=_outcome())
    _override(service)
    try:
        accepted = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello", "preset": "rag"},
            headers=_AUTH_HEADERS,
        )
        rejected = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello", "preset": "orchestration"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert accepted.status_code == 200
    assert rejected.status_code == 422


def test_agent_endpoint_persists_effective_model_name() -> None:
    service = FakeAgentService(outcome=_outcome())
    record_service = FakeAgentRunRecordService(saved_models=[])
    _override(service)
    app.dependency_overrides[provide_agent_run_record_service] = lambda: record_service
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()
        app.dependency_overrides.pop(provide_agent_run_record_service, None)

    assert response.status_code == 200
    assert record_service.saved_models == ["test-model"]


def test_agent_endpoint_returns_direct_answer_and_usage() -> None:
    service = FakeAgentService(outcome=_outcome())
    _override(service)
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello", "token_budget": 1000},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-test-1"
    assert body["status"] == "completed"
    assert body["answer"] == "done"
    assert body["stop_reason"] == "direct_answer"
    assert body["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "estimated": False,
    }


def test_agent_endpoint_creates_thread_and_persists_turn() -> None:
    service = FakeAgentService(outcome=_outcome(), requests=[])
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    _override(service)
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    history = asyncio.run(
        conversation_service.load_history(_TEST_OWNER, body["thread_id"])
    )
    assert [(message.role, message.content) for message in history] == [
        ("user", "hello"),
        ("assistant", "done"),
    ]


def test_agent_endpoint_reuses_thread_and_merges_server_history() -> None:
    service = FakeAgentService(outcome=_outcome(), requests=[])
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    _override(service)
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )
    try:
        first = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/api/v1/agent/runs",
            json={
                "message": "follow up",
                "thread_id": thread_id,
                "history": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "done"},
                    {"role": "user", "content": "client only"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["thread_id"] == thread_id
    assert [
        (message.role, message.content) for message in service.requests[1].history
    ] == [
        ("user", "hello"),
        ("assistant", "done"),
        ("user", "client only"),
    ]


def test_agent_endpoint_retry_does_not_duplicate_current_user() -> None:
    service = FakeAgentService(outcome=_outcome(), requests=[])
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    _override(service)
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )
    try:
        first = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/api/v1/agent/runs",
            json={
                "message": "hello",
                "thread_id": thread_id,
                "history": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "done"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert service.requests[1].message == "hello"
    assert [
        (message.role, message.content) for message in service.requests[1].history
    ] == []


def test_agent_endpoint_preserves_unknown_provider_usage() -> None:
    service = FakeAgentService(
        outcome=_outcome(
            prompt_tokens=None,
            completion_tokens=None,
            estimated_usage=True,
        )
    )
    _override(service)
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["usage"] == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "estimated": True,
    }


def test_agent_endpoint_returns_controlled_runtime_stop() -> None:
    service = FakeAgentService(
        outcome=_outcome(
            status=RunStatus.STOPPED,
            stop_reason=StopReason.MAX_STEPS,
            answer=None,
        )
    )
    _override(service)
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "keep working", "max_steps": 1},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert response.json()["stop_reason"] == "max_steps"
    assert response.json()["answer"] is None


def test_agent_endpoint_returns_step_summary_without_tool_payloads() -> None:
    step = AgentStep(
        index=1,
        decision=AgentDecision(answer="done"),
    )
    service = FakeAgentService(outcome=_outcome(steps=[step]))
    _override(service)
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert response.json()["steps"] == [
        {
            "index": 1,
            "decision_kind": "final_answer",
            "tool_names": [],
            "tool_count": 0,
            "summary": "Final answer planned.",
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "tool_succeeded": None,
        }
    ]


def test_agent_endpoint_maps_model_failure_without_leaking_internal_error() -> None:
    service = FakeAgentService(
        error=ProviderError("internal provider payload must not be exposed")
    )
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    _override(service)
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "PROVIDER_ERROR"
    assert body["message"] == "internal provider payload must not be exposed"
    assert "request_id" in body
    assert body["thread_id"]
    history = asyncio.run(
        conversation_service.load_history(_TEST_OWNER, body["thread_id"])
    )
    assert history == []


def test_agent_endpoint_preserves_quota_error_mapping() -> None:
    service = FakeAgentService(
        error=QuotaExceededError("Quota exceeded", retry_after=9)
    )
    _override(service)
    try:
        response = client.post(
            "/api/v1/agent/runs",
            json={"message": "hello"},
            headers=_AUTH_HEADERS,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.json()["code"] == "QUOTA_EXCEEDED"


def test_agent_request_uses_safe_bounded_defaults() -> None:
    request = AgentRunRequest(message="hello")

    assert request.token_budget == DEFAULT_AGENT_TOKEN_BUDGET
    assert request.max_steps == DEFAULT_AGENT_MAX_STEPS
    assert request.timeout_seconds == DEFAULT_AGENT_TIMEOUT_SECONDS


def test_agent_request_validation_rejects_runtime_limits() -> None:
    service = FakeAgentService(outcome=_outcome())
    _override(service)
    try:
        for payload in (
            {"max_steps": 21},
            {"token_budget": MAX_AGENT_TOKEN_BUDGET + 1},
            {"timeout_seconds": 120.1},
        ):
            response = client.post(
                "/api/v1/agent/runs",
                json={"message": "hello", **payload},
                headers=_AUTH_HEADERS,
            )

            assert response.status_code == 422
    finally:
        _clear_overrides()


def test_stream_event_carries_real_cumulative_token_usage() -> None:
    occurred_at = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    event = AgentEvent(
        kind=AgentEventKind.RUN_STOPPED,
        run_id="run-usage",
        sequence=3,
        occurred_at=occurred_at,
        status=RunStatus.STOPPED,
        stop_reason=StopReason.TOKEN_BUDGET_EXCEEDED,
        cumulative_token_usage=1234,
    )

    stream_event = _to_stream_event(event, "request-1")

    assert stream_event.event == "run_stopped"
    assert stream_event.stop_reason == "token_budget_exceeded"
    assert stream_event.cumulative_token_usage == 1234


def test_chat_service_agent_model_parses_final_answer_json() -> None:
    decision = _ChatServiceAgentModel._parse_decision(
        '{"type":"final_answer","answer":"done"}'
    )

    assert decision == AgentDecision(answer="done")


def test_chat_service_agent_model_parses_tool_call_json() -> None:
    decision = _ChatServiceAgentModel._parse_decision(
        '{"type":"tool_call","call_id":"call-1",'
        '"name":"search","arguments":{"query":"agent runtime"}}'
    )

    assert decision.answer is None
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].call_id == "call-1"
    assert decision.tool_calls[0].name == "search"
    assert decision.tool_calls[0].arguments == {"query": "agent runtime"}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"type":"unknown"}',
        '{"type":"final_answer","answer":"  "}',
    ],
)
def test_chat_service_agent_model_rejects_invalid_decisions(content: str) -> None:
    with pytest.raises(ValueError):
        _ChatServiceAgentModel._parse_decision(content)


@pytest.mark.asyncio
async def test_agent_model_reserves_incremental_prompt_and_budget() -> None:
    request = AgentRunRequest(
        message="查找退款政策",
        model="test-model",
        system_prompt="回答要简洁",
        history=[ChatMessage(role="user", content="之前的问题")],
    )
    chat_service = _FakeChatService()
    adapter = _ChatServiceAgentModel(
        chat_service,  # type: ignore[arg-type]
        request,
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "knowledge_search",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    estimates: list[int] = []

    async def guard(prompt_tokens: int) -> None:
        estimates.append(prompt_tokens)

    adapter.set_prompt_reservation_guard(guard)
    initial_state = AgentState(
        run_id="run-1",
        user_input=request.message,
        messages=[AgentMessage(role="user", content=request.message)],
    )
    expanded_state = AgentState(
        run_id="run-1",
        user_input=request.message,
        messages=[
            *initial_state.messages,
            AgentMessage(
                role="tool",
                content='{"ok":true,"results":[{"content":"refund policy"}]}',
                tool_call_id="call-1",
                tool_name="knowledge_search",
            ),
        ],
    )

    await adapter.decide(initial_state)
    await adapter.decide(expanded_state)

    assert estimates[0] == adapter.estimate_prompt_tokens_for_state(initial_state)
    assert estimates[1] == adapter.estimate_prompt_tokens_for_state(expanded_state)
    assert estimates[1] > estimates[0]
    assert chat_service.requests[0].max_tokens == request.token_budget  # type: ignore[attr-defined]
    assert chat_service.requests[1].max_tokens == request.token_budget - 6  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_chat_service_agent_model_passes_prompt_and_history_to_chat_request() -> (
    None
):
    from app.schemas.chat import ChatRequest

    chat_service = _FakeChatService()
    model = _ChatServiceAgentModel(
        chat_service,  # type: ignore[arg-type]
        AgentRunRequest(
            message="latest question",
            model="test-model",
            system_prompt="Use concise answers.",
            history=[
                ChatMessage(role="user", content="previous question"),
                ChatMessage(role="assistant", content="previous answer"),
            ],
        ),
    )

    decision = await model.decide(
        AgentState(
            run_id="run-1",
            user_input="latest question",
            messages=[AgentMessage(role="user", content="latest question")],
        )
    )

    assert decision.answer == "hello"
    request = chat_service.requests[0]
    assert isinstance(request, ChatRequest)
    assert request.system_prompt is not None
    assert "Use concise answers." in request.system_prompt
    assert "user: previous question" in request.message
    assert "assistant: previous answer" in request.message
    assert "user: latest question" in request.message


@pytest.mark.asyncio
async def test_rag_preset_forces_knowledge_search_before_any_answer() -> None:
    from app.schemas.chat import ChatRequest

    chat_service = _FakeChatService()
    model = _ChatServiceAgentModel(
        chat_service,  # type: ignore[arg-type]
        AgentRunRequest(message="什么是智能体？", model="test-model", preset="rag"),
    )

    initial_state = AgentState(
        run_id="run-1",
        user_input="什么是智能体？",
        messages=[AgentMessage(role="user", content="什么是智能体？")],
    )
    decision = await model.decide(initial_state)

    assert decision.answer is None
    assert len(decision.tool_calls) == 1
    assert decision.tool_calls[0].name == "knowledge_search"
    assert decision.tool_calls[0].arguments == {"query": "什么是智能体？"}

    request = chat_service.requests[0]
    assert isinstance(request, ChatRequest)
    assert request.system_prompt is not None
    assert "MUST call the knowledge_search tool" in request.system_prompt
    assert "do not invent" in request.system_prompt


@pytest.mark.asyncio
async def test_rag_preset_allows_answer_after_knowledge_search_has_run() -> None:
    chat_service = _FakeChatService()
    model = _ChatServiceAgentModel(
        chat_service,  # type: ignore[arg-type]
        AgentRunRequest(message="什么是智能体？", model="test-model", preset="rag"),
    )

    after_search_state = AgentState(
        run_id="run-1",
        user_input="什么是智能体？",
        messages=[
            AgentMessage(role="user", content="什么是智能体？"),
            AgentMessage(
                role="tool",
                content='{"ok":true,"results":[]}',
                tool_call_id="knowledge-1",
                tool_name="knowledge_search",
            ),
        ],
        steps=[
            AgentStep(
                index=1,
                decision=AgentDecision(
                    tool_calls=(
                        ToolCall(
                            call_id="knowledge-1",
                            name="knowledge_search",
                            arguments={"query": "什么是智能体？"},
                        ),
                    )
                ),
                tool_results=(
                    ToolResult(
                        call_id="knowledge-1",
                        name="knowledge_search",
                        content='{"ok":true,"results":[]}',
                        succeeded=True,
                        error=None,
                        truncated=False,
                    ),
                ),
            )
        ],
    )
    decision = await model.decide(after_search_state)

    assert decision.answer == "hello"
    assert not decision.tool_calls


def test_agent_request_accepts_only_the_rag_preset() -> None:
    assert AgentRunRequest(message="hello", preset="rag").preset == "rag"
    assert AgentRunRequest(message="hello").preset is None
    with pytest.raises(ValueError):
        AgentRunRequest.model_validate({"message": "hello", "preset": "orchestration"})


@pytest.mark.asyncio
async def test_agent_service_rejects_rag_preset_without_knowledge_search_tool() -> None:
    service = AgentService(
        chat_service=_FakeChatService(),  # type: ignore[arg-type]
        quota_service=_FakeQuotaService(),  # type: ignore[arg-type]
        usage_collector=_FakeUsageCollector(),  # type: ignore[arg-type]
    )
    request = AgentRunRequest(message="hello", model="test-model", preset="rag")
    context = RequestContext(request_id="request-1", api_key="hashed")
    api_key = APIKey(key="hashed", name="test")

    with pytest.raises(RAGUnavailableError):
        await service.run(request, context=context, api_key=api_key)


class _FakeChatService:
    default_model = "test-model"

    def __init__(
        self,
        *,
        prompt_tokens: int | None = 4,
        completion_tokens: int | None = 2,
    ) -> None:
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.requests: list[object] = []

    async def chat(self, request: object) -> object:
        from app.schemas.chat import ChatMessage, ChatResponse

        self.requests.append(request)
        assert request.model == "test-model"  # type: ignore[attr-defined]
        return ChatResponse(
            model="test-model",
            message=ChatMessage(
                role="assistant",
                content='{"type":"final_answer","answer":"hello"}',
            ),
            done=True,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )


class _CountingStreamingChatService(_FakeChatService):
    def __init__(
        self,
        *,
        stream_mode: str = "success",
    ) -> None:
        super().__init__(prompt_tokens=4, completion_tokens=2)
        self.stream_calls = 0
        self.stream_mode = stream_mode

    async def chat_stream(self, request: object) -> AsyncIterator[ProviderChatResult]:
        del request
        self.stream_calls += 1
        if self.stream_mode == "error":
            raise ProviderError("provider stream failed")
        if self.stream_mode == "empty":
            return
        yield ProviderChatResult(
            model="test-model",
            created_at=None,
            role="assistant",
            content="real answer",
            done=False,
            done_reason=None,
        )
        yield ProviderChatResult(
            model="test-model",
            created_at=None,
            role="assistant",
            content="",
            done=True,
            done_reason="stop",
            prompt_tokens=3,
            completion_tokens=2,
        )


class _StreamingRuntime:
    def __init__(self, model: AgentModel) -> None:
        self._runtime = AgentRuntime(model)

    async def run(self, user_input: str, **kwargs: object) -> AgentRunResult:
        return await self._runtime.run(user_input, **kwargs)  # type: ignore[arg-type]


def _streaming_runtime_factory(
    model: AgentModel,
    tools: Mapping[str, AgentTool] | None,
    *,
    tool_executor: ToolExecutor | None = None,
    recorder_factory: RunTraceRecorderFactory | None = None,
    observer: object | None = None,
) -> AgentRuntime:
    del tools, tool_executor, recorder_factory
    runtime = AgentRuntime(model, observer=observer)  # type: ignore[arg-type]
    return runtime


@pytest.mark.asyncio
async def test_agent_service_only_consumes_answer_stream_when_explicitly_enabled() -> (
    None
):
    chat_service = _CountingStreamingChatService()
    service = AgentService(
        chat_service=chat_service,  # type: ignore[arg-type]
        quota_service=_FakeQuotaService(),  # type: ignore[arg-type]
        usage_collector=_FakeUsageCollector(),  # type: ignore[arg-type]
        runtime_factory=_streaming_runtime_factory,
    )
    request = AgentRunRequest(message="hello", model="test-model")
    context = RequestContext(request_id="request-1", api_key="hashed")
    api_key = APIKey(key="hashed", name="test")

    sync_outcome = await service.run(request, context=context, api_key=api_key)
    assert sync_outcome.result.answer == "hello"
    assert chat_service.stream_calls == 0

    stream_outcome = await service.run(
        request,
        context=context,
        api_key=api_key,
        streaming=True,
    )
    assert stream_outcome.result.answer == "real answer"
    assert chat_service.stream_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream_mode", ["error", "empty"])
async def test_stream_failure_or_empty_stream_has_unknown_usage_and_one_terminal(
    stream_mode: str,
) -> None:
    chat_service = _CountingStreamingChatService(stream_mode=stream_mode)
    adapter = _ChatServiceAgentModel(
        chat_service,  # type: ignore[arg-type]
        AgentRunRequest(message="hello", model="test-model"),
    )
    if stream_mode == "error":
        with pytest.raises(ProviderError):
            async for _ in adapter.stream_answer(
                AgentState(run_id="run-adapter", user_input="hello")
            ):
                pass
    else:
        chunks = [
            chunk
            async for chunk in adapter.stream_answer(
                AgentState(run_id="run-adapter", user_input="hello")
            )
        ]
        assert chunks == []
    assert adapter.prompt_tokens is None
    assert adapter.completion_tokens is None

    chat_service = _CountingStreamingChatService(stream_mode=stream_mode)
    collector = _FakeUsageCollector()
    service = AgentService(
        chat_service=chat_service,  # type: ignore[arg-type]
        quota_service=_FakeQuotaService(),  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
        runtime_factory=_streaming_runtime_factory,
    )
    observer = _RecordingObserver()

    with pytest.raises(ProviderError):
        await service.run(
            AgentRunRequest(message="hello", model="test-model"),
            context=RequestContext(request_id="request-1", api_key="hashed"),
            api_key=APIKey(key="hashed", name="test"),
            observer=observer,
            streaming=True,
        )

    assert len(collector.responses) == 1
    recorded = collector.responses[0]
    assert isinstance(recorded, ChatResponse)
    assert recorded.prompt_tokens == 4
    assert recorded.completion_tokens is None
    terminal_events = [
        event for event in observer.events if event.kind is AgentEventKind.RUN_STOPPED
    ]
    assert len(terminal_events) == 1


class _ToolThenAnswerChatService:
    default_model = "test-model"

    def __init__(self) -> None:
        self.requests: list[object] = []
        self._responses = (
            '{"type":"tool_call","call_id":"call-1",'
            '"name":"calculator","arguments":{"expression":"1 + 1"}}',
            '{"type":"final_answer","answer":"2"}',
        )

    async def chat(self, request: object) -> object:
        from app.schemas.chat import ChatMessage, ChatResponse

        self.requests.append(request)
        response_index = len(self.requests) - 1
        return ChatResponse(
            model="test-model",
            message=ChatMessage(
                role="assistant",
                content=self._responses[min(response_index, len(self._responses) - 1)],
            ),
            done=True,
            prompt_tokens=4,
            completion_tokens=2,
        )


class _MCPThenAnswerChatService:
    default_model = "test-model"

    def __init__(self) -> None:
        self.requests: list[object] = []
        self._responses = (
            '{"type":"tool_call","call_id":"call-mcp",'
            '"name":"mcp__demo__read_status","arguments":{}}',
            '{"type":"final_answer","answer":"The service is healthy."}',
        )

    async def chat(self, request: object) -> object:
        self.requests.append(request)
        response_index = len(self.requests) - 1
        return ChatResponse(
            model="test-model",
            message=ChatMessage(
                role="assistant",
                content=self._responses[min(response_index, len(self._responses) - 1)],
            ),
            done=True,
            prompt_tokens=4,
            completion_tokens=2,
        )


class _FakeQuotaService:
    def __init__(self) -> None:
        self.reserved: list[dict[str, object]] = []
        self.settled: list[str] = []
        self.released: list[str] = []
        self.extended: list[tuple[str, int]] = []
        self.extend_error: Exception | None = None
        self.reservation_renewal_seconds = 60

    async def reserve(
        self,
        api_key_hash: str,
        max_tokens: int | None = None,
        prompt_tokens: int = 0,
    ) -> object:
        from app.quota.models import QuotaReservation

        self.reserved.append(
            {
                "api_key_hash": api_key_hash,
                "max_tokens": max_tokens,
                "prompt_tokens": prompt_tokens,
            }
        )
        return QuotaReservation(
            reservation_id="reservation-1",
            api_key_hash=api_key_hash,
            reserved_tokens=100,
            usage_date="2026-08-04",
        )

    async def settle(self, reservation_id: str) -> None:
        self.settled.append(reservation_id)

    async def release(self, reservation_id: str) -> None:
        self.released.append(reservation_id)

    async def extend(self, reservation_id: str, additional_tokens: int) -> None:
        if self.extend_error is not None:
            raise self.extend_error
        self.extended.append((reservation_id, additional_tokens))

    async def renew(self, reservation_id: str) -> bool:
        del reservation_id
        return True


class _FakeUsageCollector:
    def __init__(self) -> None:
        self.responses: list[object] = []

    async def record_chat(
        self,
        context: RequestContext,
        response: object,
        latency_ms: float,
    ) -> None:
        del context, latency_ms
        self.responses.append(response)


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def observe(self, event: AgentEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_agent_service_passes_request_and_model_to_runtime_trace_boundary() -> (
    None
):
    captured: dict[str, object] = {}
    recorder_factory: RunTraceRecorderFactory = InMemoryRunTraceRecorder

    class CapturingRuntime:
        async def run(
            self,
            user_input: str,
            *,
            max_steps: int,
            timeout: float | None,
            token_budget: int | None,
            request_id: str | None,
            model: str | None,
            **kwargs: object,
        ) -> AgentRunResult:
            del kwargs
            captured.update(
                {
                    "user_input": user_input,
                    "max_steps": max_steps,
                    "timeout": timeout,
                    "token_budget": token_budget,
                    "request_id": request_id,
                    "model": model,
                }
            )
            return _outcome().result

    def runtime_factory(
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None,
        *,
        tool_executor: ToolExecutor | None = None,
        recorder_factory: RunTraceRecorderFactory | None = None,
    ) -> AgentRuntime:
        del tools, tool_executor
        captured["factory_model"] = cast(_ChatServiceAgentModel, model).actual_model
        captured["recorder_factory"] = recorder_factory
        return cast(AgentRuntime, CapturingRuntime())

    chat_service = _FakeChatService()
    chat_service.default_model = "actual-model"
    service = AgentService(
        chat_service=chat_service,  # type: ignore[arg-type]
        quota_service=_FakeQuotaService(),  # type: ignore[arg-type]
        usage_collector=_FakeUsageCollector(),  # type: ignore[arg-type]
        runtime_factory=runtime_factory,
        recorder_factory=recorder_factory,
    )

    await service.run(
        AgentRunRequest(
            message="hello",
            max_steps=3,
            timeout_seconds=12.0,
            token_budget=99,
        ),
        context=RequestContext(request_id="request-from-api", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert captured["request_id"] == "request-from-api"
    assert captured["model"] == "actual-model"
    assert captured["factory_model"] == "actual-model"
    assert captured["recorder_factory"] is recorder_factory
    assert captured["max_steps"] == 3
    assert captured["timeout"] == 12.0
    assert captured["token_budget"] == 99


@pytest.mark.asyncio
async def test_agent_service_reserves_and_settles_quota_without_real_ollama() -> None:
    quota = _FakeQuotaService()
    collector = _FakeUsageCollector()
    service = AgentService(
        chat_service=_FakeChatService(),  # type: ignore[arg-type]
        quota_service=quota,  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
    )

    outcome = await service.run(
        AgentRunRequest(
            message="hello",
            model="test-model",
            token_budget=123,
        ),
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert outcome.result.status is RunStatus.COMPLETED
    assert outcome.result.answer == "hello"
    assert quota.reserved[0]["max_tokens"] == 123
    assert isinstance(quota.reserved[0]["prompt_tokens"], int)
    assert quota.reserved[0]["prompt_tokens"] < 1_000
    assert quota.settled == ["reservation-1"]
    assert quota.released == []
    assert len(collector.responses) == 1


@pytest.mark.asyncio
async def test_agent_service_extends_prompt_reservation_after_tool_result() -> None:
    quota = _FakeQuotaService()
    collector = _FakeUsageCollector()
    service = AgentService(
        chat_service=_ToolThenAnswerChatService(),  # type: ignore[arg-type]
        quota_service=quota,  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
    )

    outcome = await service.run(
        AgentRunRequest(message="calculate", model="test-model"),
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert outcome.result.answer == "2"
    assert len(quota.extended) == 1
    assert quota.extended[0][0] == "reservation-1"
    assert quota.extended[0][1] > 0


@pytest.mark.asyncio
async def test_agent_service_passes_mcp_grant_into_real_tool_execution() -> None:
    class MCPClientStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Mapping[str, object]]] = []

        async def call_tool(
            self,
            name: str,
            arguments: Mapping[str, object],
        ) -> MCPToolCallResult:
            self.calls.append((name, arguments))
            return MCPToolCallResult(content=("status: ok",))

    mcp_client = MCPClientStub()
    mcp_tool = MCPToolAdapter(
        "demo",
        mcp_client,  # type: ignore[arg-type]
        MCPToolDefinition(
            name="read_status",
            description="Read status.",
            input_schema={"type": "object"},
        ),
    )
    quota = _FakeQuotaService()
    collector = _FakeUsageCollector()
    service = AgentService(
        chat_service=_MCPThenAnswerChatService(),  # type: ignore[arg-type]
        quota_service=quota,  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
        tool_registry=ToolRegistry([mcp_tool]),
        granted_permissions=frozenset({"mcp:server:demo"}),
    )

    outcome = await service.run(
        AgentRunRequest(message="check status", model="test-model"),
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert outcome.result.answer == "The service is healthy."
    assert mcp_client.calls == [("read_status", {})]


@pytest.mark.asyncio
async def test_agent_service_records_partial_usage_when_prompt_extension_fails() -> (
    None
):
    quota = _FakeQuotaService()
    quota.extend_error = QuotaExceededError("daily quota exceeded")
    collector = _FakeUsageCollector()
    service = AgentService(
        chat_service=_ToolThenAnswerChatService(),  # type: ignore[arg-type]
        quota_service=quota,  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
    )

    with pytest.raises(QuotaExceededError):
        await service.run(
            AgentRunRequest(message="calculate", model="test-model"),
            context=RequestContext(request_id="request-1", api_key="hashed"),
            api_key=APIKey(key="hashed", name="test"),
        )

    assert len(collector.responses) == 1
    recorded = collector.responses[0]
    assert isinstance(recorded, ChatResponse)
    assert recorded.prompt_tokens == 4
    assert recorded.completion_tokens == 2
    assert recorded.done_reason == "quota_exceeded"
    assert quota.released == ["reservation-1"]
    assert quota.settled == []


@pytest.mark.asyncio
async def test_agent_service_preserves_unknown_provider_usage() -> None:
    from app.services.agent_service import _ChatServiceAgentModel

    quota = _FakeQuotaService()
    collector = _FakeUsageCollector()
    request = AgentRunRequest(
        message="hello",
        model="test-model",
        token_budget=1,
    )
    adapter = _ChatServiceAgentModel(
        _FakeChatService(  # type: ignore[arg-type]
            prompt_tokens=None,
            completion_tokens=None,
        ),
        request,
    )
    decision = await adapter.decide(AgentState(run_id="run-1", user_input="hello"))

    assert decision.token_usage is None
    assert decision.usage_complete is False

    service = AgentService(
        chat_service=_FakeChatService(
            prompt_tokens=None,
            completion_tokens=None,
        ),  # type: ignore[arg-type]
        quota_service=quota,  # type: ignore[arg-type]
        usage_collector=collector,  # type: ignore[arg-type]
    )

    outcome = await service.run(
        request,
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
    )

    assert outcome.result.token_usage == 0
    assert outcome.prompt_tokens is None
    assert outcome.completion_tokens is None
    assert outcome.estimated_usage is True
    recorded = collector.responses[0]
    assert isinstance(recorded, ChatResponse)
    assert recorded.prompt_tokens is None
    assert recorded.completion_tokens is None
