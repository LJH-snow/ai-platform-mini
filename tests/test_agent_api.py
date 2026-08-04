from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

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
)
from app.api.agent import get_agent_service
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.exceptions.base import ProviderError, QuotaExceededError
from app.main import app
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatMessage, ChatResponse
from app.services.agent_service import (
    AgentRunOutcome,
    AgentService,
    _ChatServiceAgentModel,
)

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


@dataclass
class FakeAgentService:
    outcome: AgentRunOutcome | None = None
    error: Exception | None = None
    requests: list[AgentRunRequest] | None = None

    async def run(
        self,
        request: AgentRunRequest,
        *,
        context: RequestContext,
        api_key: APIKey,
    ) -> AgentRunOutcome:
        del context, api_key
        if self.requests is not None:
            self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        return self.outcome


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


def _override(service: FakeAgentService) -> None:
    app.dependency_overrides[get_agent_service] = lambda: service


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_agent_service, None)


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
            "tool_succeeded": None,
        }
    ]


def test_agent_endpoint_maps_model_failure_without_leaking_internal_error() -> None:
    service = FakeAgentService(
        error=ProviderError("internal provider payload must not be exposed")
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

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "PROVIDER_ERROR"
    assert body["message"] == "internal provider payload must not be exposed"
    assert "request_id" in body


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


def test_agent_request_validation_rejects_runtime_limits() -> None:
    response = client.post(
        "/api/v1/agent/runs",
        json={"message": "hello", "max_steps": 21},
        headers=_AUTH_HEADERS,
    )

    assert response.status_code == 422


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
