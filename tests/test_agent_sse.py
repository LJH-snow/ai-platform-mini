import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request, Response

from app.agents.models import (
    AgentDecision,
    AgentEvent,
    AgentEventKind,
    AgentRunResult,
    AgentState,
    RunStatus,
    StopReason,
    ToolCall,
    ToolResult,
)
from app.agents.runtime import AgentRuntime
from app.agents.stream import AgentEventStream, AgentStreamSetupError
from app.api.agent import (
    _serialize_sse,
    _stream_events,
    _to_stream_event,
    stream_agent_run,
)
from app.auth.hash import hash_api_key
from app.auth.models import APIKey
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.service import ConversationService
from app.core.context import RequestContext
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentRunOutcome, AgentService

_TEST_OWNER = hash_api_key("sk-test-integration")


def _event(
    kind: AgentEventKind,
    sequence: int,
    *,
    step_index: int | None = None,
    message: str | None = None,
    decision: AgentDecision | None = None,
    tool_call: ToolCall | None = None,
    tool_result: ToolResult | None = None,
    status: RunStatus | None = None,
    stop_reason: StopReason | None = None,
) -> AgentEvent:
    return AgentEvent(
        kind=kind,
        run_id="run-1",
        sequence=sequence,
        occurred_at=datetime.now(UTC),
        step_index=step_index,
        message=message,
        decision=decision,
        tool_call=tool_call,
        tool_result=tool_result,
        status=status,
        stop_reason=stop_reason,
    )


def test_stream_projection_preserves_sequence_and_hides_prompt() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.RUN_STARTED,
            1,
            message="secret prompt",
        ),
        "request-1",
    )

    assert projected.event == "run_started"
    assert projected.sequence == 1
    assert projected.request_id == "request-1"
    assert projected.answer is None
    assert "secret" not in _serialize_sse(projected)


def test_stream_projection_emits_complete_answer_without_token_claims() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.ANSWER,
            2,
            step_index=1,
            message="real answer",
            decision=AgentDecision(answer="real answer"),
        ),
        "request-1",
    )

    assert projected.event == "assistant_message"
    assert projected.answer == "real answer"
    assert "token" not in _serialize_sse(projected)


def test_stream_projection_emits_sanitized_answer_delta_only() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.ANSWER_DELTA,
            2,
            step_index=1,
            message="delta api_key=secret-value",
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert projected.event == "answer_delta"
    assert projected.delta == "delta [redacted]"
    assert "secret-value" not in serialized
    assert '"answer"' not in serialized
    assert '"tool_name"' not in serialized


def test_knowledge_search_uses_safe_rag_projection() -> None:
    result = ToolResult(
        call_id="call-1",
        name="knowledge_search",
        content='{"ok":true,"results":[{"document_id":"doc-1",'
        '"chunk_id":"chunk-1","chunk_index":0,"content":"reference",'
        '"distance":0.2}]}',
        succeeded=True,
    )
    projected = _to_stream_event(
        _event(
            AgentEventKind.TOOL_COMPLETED,
            3,
            step_index=1,
            tool_call=ToolCall(call_id="call-1", name="knowledge_search"),
            tool_result=result,
        ),
        "request-1",
    )

    assert projected.event == "tool_completed"
    assert projected.rag is not None
    assert projected.rag.references[0].document_id == "doc-1"
    assert "reference" in _serialize_sse(projected)
    assert "call_id" in _serialize_sse(projected)


def test_knowledge_search_started_emits_loading_without_tool_output() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.TOOL_STARTED,
            2,
            step_index=1,
            tool_call=ToolCall(
                call_id="call-1",
                name="knowledge_search",
                arguments={"query": "secret query"},
            ),
        ),
        "request-1",
    )

    assert projected.event == "rag_started"
    assert projected.rag is not None
    assert projected.rag.status == "loading"
    assert "secret query" not in _serialize_sse(projected)


def test_assistant_projection_redacts_sensitive_content() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.ANSWER,
            2,
            step_index=1,
            message="api_key=secret-value from /Users/private/app.py",
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert "secret-value" not in serialized
    assert "/Users/private/app.py" not in serialized
    assert "redacted" in serialized


def test_terminal_projection_has_one_explicit_terminal_name() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.RUN_STOPPED,
            4,
            status=RunStatus.TIMED_OUT,
            stop_reason=StopReason.DEADLINE_EXCEEDED,
        ),
        "request-1",
    )

    assert projected.event == "run_timed_out"
    assert projected.status == RunStatus.TIMED_OUT
    assert projected.stop_reason == StopReason.DEADLINE_EXCEEDED


@pytest.mark.asyncio
async def test_agent_event_stream_is_fifo() -> None:
    stream = AgentEventStream()
    first = _event(AgentEventKind.RUN_STARTED, 1)
    second = _event(AgentEventKind.ANSWER, 2, message="answer")

    stream.observe(first)
    stream.observe(second)

    assert await stream.receive() == first
    assert await stream.receive() == second


@pytest.mark.parametrize(
    ("status", "event_name"),
    [
        (RunStatus.COMPLETED, "run_completed"),
        (RunStatus.FAILED, "run_failed"),
        (RunStatus.TIMED_OUT, "run_timed_out"),
        (RunStatus.CANCELLED, "run_cancelled"),
    ],
)
def test_terminal_mapping_has_one_name_per_status(
    status: RunStatus,
    event_name: str,
) -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.RUN_STOPPED,
            3,
            status=status,
            stop_reason=StopReason.DIRECT_ANSWER,
        ),
        "request-1",
    )

    assert projected.event == event_name


def test_runtime_observer_receives_step_lifecycle_without_sync_result_events() -> None:
    class Model:
        async def decide(self, state: object) -> AgentDecision:
            del state
            return AgentDecision(answer="answer")

    class Observer:
        def __init__(self) -> None:
            self.events: list[AgentEvent] = []

        def observe(self, event: AgentEvent) -> None:
            self.events.append(event)

    observer = Observer()

    async def run() -> tuple[list[AgentEvent], list[AgentEventKind]]:
        result = await AgentRuntime(Model(), observer=observer).run("hello")
        return observer.events, [event.kind for event in result.events]

    events, result_kinds = asyncio.run(run())
    assert [event.kind for event in events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.STEP_STARTED,
        AgentEventKind.MODEL_DECISION,
        AgentEventKind.ANSWER,
        AgentEventKind.STEP_COMPLETED,
        AgentEventKind.RUN_STOPPED,
    ]
    assert result_kinds == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.MODEL_DECISION,
        AgentEventKind.ANSWER,
        AgentEventKind.RUN_STOPPED,
    ]


class _RecordingObserver:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def observe(self, event: AgentEvent) -> None:
        self.events.append(event)


class _BlockingModel:
    async def decide(self, state: object) -> AgentDecision:
        del state
        await asyncio.Future()
        return AgentDecision(answer="unreachable")


class _ErrorModel:
    async def decide(self, state: object) -> AgentDecision:
        del state
        raise RuntimeError("provider secret")


class _ToolModel:
    async def decide(self, state: object) -> AgentDecision:
        del state
        return AgentDecision(tool_calls=(ToolCall("call-1", "blocking"),))


class _BlockingTool:
    async def execute(self, arguments: object, context: object) -> str:
        del arguments, context
        await asyncio.Future()
        return "unreachable"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["timeout", "cancel", "model_error"])
async def test_early_model_stop_closes_started_step(stop_kind: str) -> None:
    observer = _RecordingObserver()
    cancel_event = asyncio.Event()
    model: object = _ErrorModel() if stop_kind == "model_error" else _BlockingModel()
    runtime = AgentRuntime(model, observer=observer)  # type: ignore[arg-type]

    if stop_kind == "timeout":
        result = await runtime.run("hello", timeout=0.001)
    elif stop_kind == "cancel":
        task = asyncio.create_task(runtime.run("hello", cancel_event=cancel_event))
        await asyncio.sleep(0)
        cancel_event.set()
        result = await task
    else:
        result = await runtime.run("hello", cancel_event=cancel_event)

    kinds = [event.kind for event in observer.events]
    assert kinds.count(AgentEventKind.STEP_STARTED) == 1
    assert kinds.count(AgentEventKind.STEP_COMPLETED) == 1
    assert kinds.index(AgentEventKind.STEP_STARTED) < kinds.index(
        AgentEventKind.STEP_COMPLETED
    )
    assert result.events[-1].kind is AgentEventKind.RUN_STOPPED


@pytest.mark.asyncio
async def test_early_tool_stop_closes_started_step() -> None:
    observer = _RecordingObserver()
    runtime = AgentRuntime(
        _ToolModel(),
        tools={"blocking": _BlockingTool()},  # type: ignore[arg-type]
        observer=observer,
    )

    result = await runtime.run("use tool", timeout=0.01)
    kinds = [event.kind for event in observer.events]
    assert result.status is RunStatus.TIMED_OUT
    assert kinds.count(AgentEventKind.STEP_STARTED) == 1
    assert kinds.count(AgentEventKind.STEP_COMPLETED) == 1
    assert kinds.index(AgentEventKind.STEP_STARTED) < kinds.index(
        AgentEventKind.STEP_COMPLETED
    )


class _ConnectedRequest:
    def __init__(self, disconnected: bool = False) -> None:
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


@pytest.mark.asyncio
async def test_sse_generator_consumes_real_observer_events_in_order() -> None:
    stream = AgentEventStream()
    cancel_event = asyncio.Event()

    async def produce() -> None:
        stream.observe(_event(AgentEventKind.RUN_STARTED, 1))
        stream.observe(_event(AgentEventKind.ANSWER, 2, message="real answer"))
        stream.observe(
            _event(
                AgentEventKind.RUN_STOPPED,
                3,
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.DIRECT_ANSWER,
            )
        )
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

    assert [frame.splitlines()[0] for frame in frames] == [
        "event: run_started",
        "event: assistant_message",
        "event: run_completed",
    ]
    assert '"answer":"real answer"' in frames[1]
    assert sum("event: run_" in frame for frame in frames) == 2
    assert not cancel_event.is_set()


@pytest.mark.asyncio
async def test_setup_failure_is_explicit_and_not_backend_cancellation() -> None:
    stream = AgentEventStream()
    stream.fail_setup()
    item = await stream.receive()

    assert isinstance(item, AgentStreamSetupError)
    assert item.error_code == "stream_setup_failed"


@pytest.mark.asyncio
async def test_disconnect_requests_cancel_without_forging_terminal_event() -> None:
    stream = AgentEventStream()
    cancel_event = asyncio.Event()
    producer_finished = asyncio.Event()

    async def produce() -> None:
        await cancel_event.wait()
        producer_finished.set()

    producer = asyncio.create_task(produce())
    frames = [
        frame
        async for frame in _stream_events(
            cast(Request, _ConnectedRequest(disconnected=True)),
            stream,
            "request-1",
            producer,
            cancel_event,
        )
    ]

    assert frames == []
    assert cancel_event.is_set()
    assert producer_finished.is_set()
    assert all("run_cancelled" not in frame for frame in frames)


class _StreamingAgentService:
    async def run(
        self,
        request: object,
        *,
        context: object,
        api_key: object,
        observer: AgentEventStream,
        cancel_event: asyncio.Event,
        streaming: bool,
    ) -> None:
        del request, context, api_key, cancel_event, streaming
        observer.observe(_event(AgentEventKind.RUN_STARTED, 1))
        observer.observe(
            _event(
                AgentEventKind.RUN_STOPPED,
                2,
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.DIRECT_ANSWER,
            )
        )


class _UnexpectedFailureAgentService:
    async def run(
        self,
        request: object,
        *,
        context: object,
        api_key: object,
        observer: AgentEventStream,
        cancel_event: asyncio.Event,
        streaming: bool,
    ) -> None:
        del request, context, api_key, cancel_event, streaming
        observer.observe(_event(AgentEventKind.RUN_STARTED, 1))
        raise RuntimeError("unexpected producer failure")


class _SetupFailureAgentService:
    async def run(
        self,
        request: object,
        *,
        context: object,
        api_key: object,
        observer: AgentEventStream,
        cancel_event: asyncio.Event,
        streaming: bool,
    ) -> None:
        del request, context, api_key, observer, cancel_event, streaming
        raise RuntimeError("setup failure")


class _MemoryStreamingAgentService:
    async def run(
        self,
        request: object,
        *,
        context: object,
        api_key: object,
        observer: AgentEventStream,
        cancel_event: asyncio.Event,
        streaming: bool,
    ) -> AgentRunOutcome:
        del request, context, api_key, cancel_event, streaming
        observer.observe(_event(AgentEventKind.RUN_STARTED, 1))
        observer.observe(
            _event(
                AgentEventKind.RUN_STOPPED,
                2,
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.DIRECT_ANSWER,
            )
        )
        run_id = "run-memory-1"
        state = AgentState(run_id=run_id, user_input="hello")
        return AgentRunOutcome(
            result=AgentRunResult(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.DIRECT_ANSWER,
                answer="final answer",
                state=state,
                events=(),
                token_usage=0,
            ),
            model="test-model",
            prompt_tokens=1,
            completion_tokens=1,
            estimated_usage=False,
        )


@pytest.mark.asyncio
async def test_started_run_unexpected_producer_error_emits_one_run_failed() -> None:
    response = await stream_agent_run(
        AgentRunRequest(message="hello"),
        cast(Request, _RateLimitedRequest()),
        Response(),
        cast(AgentService, _UnexpectedFailureAgentService()),
        APIKey(key="sk-test", name="test"),
    )

    frames = [cast(str, frame) async for frame in response.body_iterator]
    event_names = [frame.splitlines()[0] for frame in frames]

    assert "event: stream_error" not in event_names
    assert event_names.count("event: run_failed") == 1


@pytest.mark.asyncio
async def test_pre_start_producer_error_remains_setup_failure() -> None:
    response = await stream_agent_run(
        AgentRunRequest(message="hello"),
        cast(Request, _RateLimitedRequest()),
        Response(),
        cast(AgentService, _SetupFailureAgentService()),
        APIKey(key="sk-test", name="test"),
    )

    frames = [cast(str, frame) async for frame in response.body_iterator]

    assert len(frames) == 1
    assert frames[0].startswith("event: stream_error\n")
    assert '"error_code":"stream_setup_failed"' in frames[0]


@pytest.mark.asyncio
async def test_pre_start_producer_error_carries_resolved_thread_id() -> None:
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    response = await stream_agent_run(
        AgentRunRequest(message="hello"),
        cast(Request, _RateLimitedRequest()),
        Response(),
        cast(AgentService, _SetupFailureAgentService()),
        APIKey(key=_TEST_OWNER, name="test"),
        conversation_service=conversation_service,
    )

    frames = [cast(str, frame) async for frame in response.body_iterator]

    assert len(frames) == 1
    assert frames[0].startswith("event: stream_error\n")
    assert '"error_code":"stream_setup_failed"' in frames[0]
    assert '"thread_id":"' in frames[0]


@pytest.mark.asyncio
async def test_stream_persists_final_answer_and_carries_thread_id() -> None:
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository()
    )
    response = await stream_agent_run(
        AgentRunRequest(message="hello"),
        cast(Request, _RateLimitedRequest()),
        Response(),
        cast(AgentService, _MemoryStreamingAgentService()),
        APIKey(key=_TEST_OWNER, name="test"),
        conversation_service=conversation_service,
    )

    frames = [cast(str, frame) async for frame in response.body_iterator]

    assert len(frames) == 2
    first_data = json.loads(frames[0].split("data: ", 1)[1])
    assert first_data["thread_id"]
    thread_id = first_data["thread_id"]
    history = await conversation_service.load_history(_TEST_OWNER, thread_id)
    assert [(message.role, message.content) for message in history] == [
        ("user", "hello"),
        ("assistant", "final answer"),
    ]


class _RateLimitedRequest:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            context=RequestContext(request_id="request-1"),
            rate_limit_limit=60,
            rate_limit_remaining=59,
            rate_limit_reset_after=42,
        )

    async def is_disconnected(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_agent_sse_response_includes_rate_limit_headers() -> None:
    response = await stream_agent_run(
        AgentRunRequest(message="hello"),
        cast(Request, _RateLimitedRequest()),
        Response(),
        cast(AgentService, _StreamingAgentService()),
        APIKey(key="sk-test", name="test"),
    )

    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"
    assert response.headers["X-RateLimit-Reset"] == "42"

    async for _ in response.body_iterator:
        pass


def test_step_planned_projection_exposes_safe_decision_metadata() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.MODEL_DECISION,
            2,
            step_index=1,
            decision=AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="calc-1",
                        name="calculator",
                        arguments={
                            "expression": "2 + 2",
                            "api_key": "sk-never-public-12345",
                        },
                    ),
                )
            ),
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert projected.event == "step_planned"
    assert projected.decision_kind == "tool_call"
    assert projected.tool_names == ["calculator"]
    assert projected.tool_count == 1
    assert projected.summary == "Planned 1 tool call(s): calculator."
    assert "api_key" not in serialized
    assert "sk-never-public" not in serialized
    assert "arguments" not in serialized


def test_calculator_tool_summaries_are_bounded_and_redacted() -> None:
    call = ToolCall(
        call_id="calc-1",
        name="calculator",
        arguments={
            "expression": "api_key=sk-never-public-12345 " + ("1 + " * 120),
        },
    )
    started = _to_stream_event(
        _event(
            AgentEventKind.TOOL_STARTED,
            3,
            step_index=1,
            tool_call=call,
        ),
        "request-1",
    )
    completed = _to_stream_event(
        _event(
            AgentEventKind.TOOL_COMPLETED,
            4,
            step_index=1,
            tool_call=call,
            tool_result=ToolResult(
                call_id="calc-1",
                name="calculator",
                content="42",
                succeeded=True,
            ),
        ),
        "request-1",
    )

    assert started.input_summary is not None
    assert len(started.input_summary) <= 256
    assert "sk-never-public" not in _serialize_sse(started)
    assert completed.output_summary == "result: 42"
    assert completed.result_chars == 2


def test_knowledge_search_hides_query_and_exposes_safe_rag_summary() -> None:
    call = ToolCall(
        call_id="rag-1",
        name="knowledge_search",
        arguments={"query": "private api_key=sk-never-public-12345 question"},
    )
    content = (
        '{"ok":true,"results":[{"document_id":"doc-1",'
        '"chunk_id":"chunk-1","chunk_index":0,"content":"reference",'
        '"distance":0.2}]}'
    )
    projected = _to_stream_event(
        _event(
            AgentEventKind.TOOL_COMPLETED,
            4,
            step_index=1,
            tool_call=call,
            tool_result=ToolResult(
                call_id="rag-1",
                name="knowledge_search",
                content=content,
                succeeded=True,
            ),
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert projected.input_summary == "knowledge search requested; query redacted"
    assert projected.output_summary == "retrieved 1 safe reference(s)"
    assert projected.rag is not None
    assert projected.rag.status == "success_with_sources"
    assert projected.rag.references[0].document_id == "doc-1"
    assert "private api_key" not in serialized
    assert "sk-never-public" not in serialized
    assert "question" not in serialized


def test_tool_name_is_sanitized_in_public_sse_projection() -> None:
    projected = _to_stream_event(
        _event(
            AgentEventKind.TOOL_STARTED,
            3,
            step_index=1,
            tool_call=ToolCall(
                call_id="unknown-1",
                name="/Users/private/tool name<script>",
                arguments={"secret": "value"},
            ),
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert projected.tool_name == "_Users_private_tool_name_script_"
    assert "/Users/private" not in serialized
    assert "<script>" not in serialized


def test_unknown_tool_only_exposes_counts_not_payloads() -> None:
    call = ToolCall(
        call_id="unknown-1",
        name="filesystem",
        arguments={"path": "/Users/private/secret.txt", "token": "secret-value"},
    )
    result = ToolResult(
        call_id="unknown-1",
        name="filesystem",
        content="secret output api_key=sk-never-public-12345",
        succeeded=True,
    )
    projected = _to_stream_event(
        _event(
            AgentEventKind.TOOL_COMPLETED,
            4,
            step_index=1,
            tool_call=call,
            tool_result=result,
        ),
        "request-1",
    )

    serialized = _serialize_sse(projected)
    assert projected.input_summary == "parameters: 2"
    assert projected.output_summary is None
    assert projected.result_chars == len(result.content)
    assert "/Users/private" not in serialized
    assert "secret-value" not in serialized
    assert "sk-never-public" not in serialized


@pytest.mark.asyncio
async def test_sse_emits_step_planned_between_step_and_tool_events() -> None:
    stream = AgentEventStream()
    cancel_event = asyncio.Event()
    call = ToolCall(
        call_id="calc-1",
        name="calculator",
        arguments={"expression": "2 + 2"},
    )

    async def produce() -> None:
        stream.observe(_event(AgentEventKind.RUN_STARTED, 1))
        stream.observe(_event(AgentEventKind.STEP_STARTED, 2, step_index=1))
        stream.observe(
            _event(
                AgentEventKind.MODEL_DECISION,
                3,
                step_index=1,
                decision=AgentDecision(tool_calls=(call,)),
            )
        )
        stream.observe(
            _event(
                AgentEventKind.TOOL_STARTED,
                4,
                step_index=1,
                tool_call=call,
            )
        )
        stream.observe(
            _event(
                AgentEventKind.TOOL_COMPLETED,
                5,
                step_index=1,
                tool_call=call,
                tool_result=ToolResult(
                    call_id="calc-1",
                    name="calculator",
                    content="4",
                    succeeded=True,
                ),
            )
        )
        stream.observe(_event(AgentEventKind.STEP_COMPLETED, 6, step_index=1))
        stream.observe(
            _event(
                AgentEventKind.RUN_STOPPED,
                7,
                status=RunStatus.COMPLETED,
                stop_reason=StopReason.DIRECT_ANSWER,
            )
        )
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

    assert [frame.splitlines()[0] for frame in frames] == [
        "event: run_started",
        "event: step_started",
        "event: step_planned",
        "event: tool_started",
        "event: tool_completed",
        "event: step_completed",
        "event: run_completed",
    ]
    assert '"decision_kind":"tool_call"' in frames[2]
    assert '"input_summary":"expression: 2 + 2"' in frames[3]
    assert '"output_summary":"result: 4"' in frames[4]
