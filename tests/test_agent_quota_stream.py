from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import Request

from app.agents.models import (
    AgentDecision,
    AgentEvent,
    AgentEventKind,
    AgentState,
    RunStatus,
    StopReason,
)
from app.agents.runtime import AgentRuntime
from app.agents.stream import AgentEventStream
from app.api.agent import _stream_events
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.exceptions.base import QuotaExceededError
from app.quota.models import QuotaReservation
from app.schemas.agent import AgentRunRequest
from app.schemas.chat import ChatMessage, ChatResponse
from app.services.agent_service import AgentService
from app.usage.collector import UsageCollector


class _QuotaModel:
    async def decide(self, state: AgentState) -> AgentDecision:
        del state
        raise QuotaExceededError("prompt reservation extension failed")


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def observe(self, event: AgentEvent) -> None:
        self.events.append(event)


class _ToolThenAnswerChatService:
    default_model = "test-model"

    def __init__(self) -> None:
        self.requests: list[object] = []

    async def chat(self, request: object) -> ChatResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            content = (
                '{"type":"tool_call","call_id":"call-1",'
                '"name":"calculator","arguments":{"expression":"1 + 1"}}'
            )
        else:
            content = '{"type":"final_answer","answer":"2"}'
        return ChatResponse(
            model="test-model",
            message=ChatMessage(role="assistant", content=content),
            done=True,
            prompt_tokens=4,
            completion_tokens=2,
        )


class _FailingQuotaService:
    reservation_renewal_seconds = 60

    def __init__(self) -> None:
        self.extended: list[tuple[str, int]] = []
        self.released: list[str] = []
        self.settled: list[str] = []

    async def reserve(
        self,
        api_key_hash: str,
        max_tokens: int | None = None,
        prompt_tokens: int = 0,
        *,
        workspace_id: str | None = None,
    ) -> QuotaReservation:
        del api_key_hash, max_tokens, prompt_tokens, workspace_id
        return QuotaReservation(
            reservation_id="reservation-1",
            api_key_hash="hashed",
            reserved_tokens=100,
            usage_date="2026-08-05",
        )

    async def extend(
        self,
        reservation_id: str,
        additional_tokens: int,
        *,
        workspace_id: str | None = None,
    ) -> None:
        del workspace_id
        self.extended.append((reservation_id, additional_tokens))
        raise QuotaExceededError("daily quota exceeded")

    async def settle(self, reservation_id: str) -> None:
        self.settled.append(reservation_id)

    async def release(self, reservation_id: str) -> None:
        self.released.append(reservation_id)

    async def renew(self, reservation_id: str) -> bool:
        del reservation_id
        return True


class _RecordingUsageCollector:
    def __init__(self) -> None:
        self.responses: list[ChatResponse] = []

    async def record_chat(
        self,
        context: RequestContext,
        response: object,
        latency_ms: float,
    ) -> None:
        del context, latency_ms
        self.responses.append(cast(ChatResponse, response))


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_runtime_quota_failure_after_run_started_emits_one_failed_terminal() -> (
    None
):
    observer = _RecordingObserver()

    result = await AgentRuntime(_QuotaModel(), observer=observer).run("hello")

    terminal_events = [
        event for event in observer.events if event.kind is AgentEventKind.RUN_STOPPED
    ]
    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert len(terminal_events) == 1
    assert terminal_events[0].status is RunStatus.FAILED
    assert terminal_events[0].stop_reason is StopReason.MODEL_ERROR
    assert [event.kind for event in observer.events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.STEP_STARTED,
        AgentEventKind.STEP_COMPLETED,
        AgentEventKind.RUN_STOPPED,
    ]


@pytest.mark.asyncio
async def test_service_returns_quota_terminal_and_keeps_usage_trace_consistent() -> (
    None
):
    quota = _FailingQuotaService()
    usage = _RecordingUsageCollector()
    observer = _RecordingObserver()
    service = AgentService(
        chat_service=_ToolThenAnswerChatService(),  # type: ignore[arg-type]
        quota_service=cast(object, quota),  # type: ignore[arg-type]
        usage_collector=cast(UsageCollector, usage),
    )

    outcome = await service.run(
        AgentRunRequest(message="calculate", model="test-model"),
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
        observer=observer,
        streaming=True,
    )

    terminal_events = [
        event for event in observer.events if event.kind is AgentEventKind.RUN_STOPPED
    ]
    assert outcome.result.status is RunStatus.FAILED
    assert len(terminal_events) == 1
    assert outcome.result.run_id == terminal_events[0].run_id
    assert len(usage.responses) == 1
    assert usage.responses[0].done_reason == "quota_exceeded"
    assert usage.responses[0].prompt_tokens == 4
    assert usage.responses[0].completion_tokens == 2
    assert quota.released == ["reservation-1"]
    assert quota.settled == []


@pytest.mark.asyncio
async def test_sse_emits_run_failed_after_started_quota_failure() -> None:
    quota = _FailingQuotaService()
    service = AgentService(
        chat_service=_ToolThenAnswerChatService(),  # type: ignore[arg-type]
        quota_service=cast(object, quota),  # type: ignore[arg-type]
        usage_collector=cast(UsageCollector, _RecordingUsageCollector()),
    )
    stream = AgentEventStream()
    cancel_event = asyncio.Event()

    async def produce() -> None:
        try:
            await service.run(
                AgentRunRequest(message="calculate", model="test-model"),
                context=RequestContext(request_id="request-1", api_key="hashed"),
                api_key=APIKey(key="hashed", name="test"),
                observer=stream,
                cancel_event=cancel_event,
                streaming=True,
            )
        finally:
            if not stream.terminal_observed:
                stream.close()

    producer = asyncio.create_task(produce())
    frames = [
        frame
        async for frame in _stream_events(
            cast(Request, _ConnectedRequest()),
            stream,
            "request-1",
            producer,
            cancel_event,
        )
    ]
    await producer

    event_names = [frame.splitlines()[0] for frame in frames]
    assert "event: stream_error" not in event_names
    assert "event: run_failed" in event_names
    assert sum(name.startswith("event: run_") for name in event_names) == 2


class _BlockingModel:
    async def decide(self, state: AgentState) -> AgentDecision:
        del state
        await asyncio.Future()
        raise AssertionError("unreachable")


class _FailingRenewalQuotaService(_FailingQuotaService):
    reservation_renewal_seconds = 0

    async def extend(
        self,
        reservation_id: str,
        additional_tokens: int,
        *,
        workspace_id: str | None = None,
    ) -> None:
        del reservation_id, additional_tokens, workspace_id

    async def renew(self, reservation_id: str) -> bool:
        del reservation_id
        raise QuotaExceededError("quota renewal failed")


@pytest.mark.asyncio
async def test_service_maps_renewal_quota_failure_to_one_failed_terminal() -> None:
    observer = _RecordingObserver()
    quota = _FailingRenewalQuotaService()
    service = AgentService(
        chat_service=cast(object, _BlockingModel()),  # type: ignore[arg-type]
        quota_service=cast(object, quota),  # type: ignore[arg-type]
        usage_collector=cast(UsageCollector, _RecordingUsageCollector()),
    )

    outcome = await service.run(
        AgentRunRequest(message="wait", model="test-model"),
        context=RequestContext(request_id="request-1", api_key="hashed"),
        api_key=APIKey(key="hashed", name="test"),
        observer=observer,
        streaming=True,
    )

    terminal_events = [
        event for event in observer.events if event.kind is AgentEventKind.RUN_STOPPED
    ]
    assert outcome.result.status is RunStatus.FAILED
    assert len(terminal_events) == 1


@pytest.mark.asyncio
async def test_sse_maps_renewal_quota_failure_to_run_failed() -> None:
    quota = _FailingRenewalQuotaService()
    service = AgentService(
        chat_service=cast(object, _BlockingModel()),  # type: ignore[arg-type]
        quota_service=cast(object, quota),  # type: ignore[arg-type]
        usage_collector=cast(UsageCollector, _RecordingUsageCollector()),
    )
    stream = AgentEventStream()
    cancel_event = asyncio.Event()

    async def produce() -> None:
        try:
            await service.run(
                AgentRunRequest(message="wait", model="test-model"),
                context=RequestContext(request_id="request-1", api_key="hashed"),
                api_key=APIKey(key="hashed", name="test"),
                observer=stream,
                cancel_event=cancel_event,
                streaming=True,
            )
        finally:
            if not stream.terminal_observed:
                stream.close()

    producer = asyncio.create_task(produce())
    frames = [
        frame
        async for frame in _stream_events(
            cast(Request, _ConnectedRequest()),
            stream,
            "request-1",
            producer,
            cancel_event,
        )
    ]
    await producer

    event_names = [frame.splitlines()[0] for frame in frames]
    assert "event: stream_error" not in event_names
    assert "event: run_cancelled" not in event_names
    assert event_names.count("event: run_failed") == 1
    assert sum(name.startswith("event: run_") for name in event_names) == 2
