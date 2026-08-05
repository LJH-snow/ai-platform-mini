import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import Request

from app.agents.models import (
    AgentDecision,
    AgentEvent,
    AgentEventKind,
    RunStatus,
    StopReason,
    ToolCall,
    ToolResult,
)
from app.agents.runtime import AgentRuntime
from app.agents.stream import AgentEventStream, AgentStreamSetupError
from app.api.agent import _serialize_sse, _stream_events, _to_stream_event


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
