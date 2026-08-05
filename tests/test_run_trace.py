import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.agents import (
    AgentDecision,
    AgentRuntime,
    AgentState,
    RunStatus,
    StopReason,
    ToolCall,
    ToolContext,
)
from app.runs import (
    DefaultRunTraceSanitizer,
    InMemoryRunTraceRecorder,
    RunTrace,
)


@dataclass
class ScriptedModel:
    responses: list[AgentDecision]
    error: Exception | None = None

    async def decide(self, state: object) -> AgentDecision:
        del state
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


@dataclass
class EchoTool:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: object,
    ) -> str:
        del context
        return str(arguments["value"])


@dataclass
class ConcurrentModel:
    async def decide(self, state: object) -> AgentDecision:
        assert isinstance(state, AgentState)
        await asyncio.sleep(0)
        if not state.steps:
            return AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id=f"call-{state.run_id}",
                        name="capture",
                        arguments={"value": state.user_input},
                    ),
                )
            )
        return AgentDecision(answer=state.user_input)


@dataclass
class ContextCaptureTool:
    contexts: list[ToolContext]

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        await asyncio.sleep(0)
        self.contexts.append(context)
        return str(arguments["value"])


class FailingTool:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: object,
    ) -> str:
        del arguments, context
        raise RuntimeError("tool backend unavailable")


class BrokenRecorder:
    def start(
        self,
        run_id: str,
        *,
        request_id: str | None,
        model: str | None,
        started_at: object,
    ) -> None:
        del run_id, request_id, model, started_at
        raise RuntimeError("recorder start failed")

    def observe(self, event: object) -> None:
        del event
        raise RuntimeError("recorder observe failed")

    def finish(self, result: object) -> None:
        del result
        raise RuntimeError("recorder finish failed")


@pytest.mark.asyncio
async def test_trace_records_direct_answer_metadata_and_terminal_state() -> None:
    recorder = InMemoryRunTraceRecorder()
    runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="answer", token_usage=7)]),
        recorder=recorder,
    )

    result = await runtime.run(
        "private prompt",
        run_id="run-1",
        request_id="request-1",
        model="fake-model",
    )
    trace = recorder.snapshot()

    assert result.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.DIRECT_ANSWER
    assert trace is not None
    assert trace.run_id == "run-1"
    assert trace.request_id == "request-1"
    assert trace.model == "fake-model"
    assert trace.status is RunStatus.COMPLETED
    assert trace.stop_reason is StopReason.DIRECT_ANSWER
    assert trace.token_usage == 7
    assert trace.step_count == 1
    assert [event.kind for event in trace.events] == [
        "run_started",
        "model_decision",
        "answer",
        "run_stopped",
    ]


@pytest.mark.asyncio
async def test_trace_summarizes_successful_and_failed_tools_without_payloads() -> None:
    recorder = InMemoryRunTraceRecorder()
    runtime = AgentRuntime(
        ScriptedModel(
            [
                AgentDecision(
                    tool_calls=(
                        ToolCall(
                            "success-call",
                            "echo",
                            {"api_key": "super-secret", "value": "private"},
                        ),
                        ToolCall("failure-call", "unstable", {}),
                    ),
                    token_usage=3,
                ),
                AgentDecision(answer="done", token_usage=2),
            ]
        ),
        tools={"echo": EchoTool(), "unstable": FailingTool()},
        recorder=recorder,
    )

    result = await runtime.run("do not retain this prompt")
    trace = recorder.snapshot()
    assert result.status is RunStatus.COMPLETED
    assert trace is not None
    assert {summary.name for summary in trace.tool_summaries} == {"echo", "unstable"}
    assert any(
        event.tool_name == "echo" and event.tool_succeeded is True
        for event in trace.events
    )
    assert any(
        event.tool_name == "unstable" and event.tool_succeeded is False
        for event in trace.events
    )
    payload = trace.to_jsonl()
    assert "super-secret" not in payload
    assert "do not retain this prompt" not in payload
    assert "private" not in payload
    assert "tool backend unavailable" not in payload


@pytest.mark.asyncio
async def test_trace_covers_max_steps_timeout_cancel_and_model_error() -> None:
    max_steps_recorder = InMemoryRunTraceRecorder()
    max_steps_runtime = AgentRuntime(
        ScriptedModel(
            [AgentDecision(tool_calls=(ToolCall("call", "missing", {}),))] * 2
        ),
        recorder=max_steps_recorder,
    )
    max_steps_result = await max_steps_runtime.run("max", max_steps=1)
    assert max_steps_result.stop_reason is StopReason.MAX_STEPS
    assert max_steps_recorder.snapshot().stop_reason is StopReason.MAX_STEPS  # type: ignore[union-attr]

    timeout_recorder = InMemoryRunTraceRecorder()
    timeout_runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="late")], error=None),
        recorder=timeout_recorder,
    )
    timeout_result = await timeout_runtime.run("timeout", timeout=0)
    assert timeout_result.stop_reason is StopReason.DEADLINE_EXCEEDED
    assert timeout_recorder.snapshot().status is RunStatus.TIMED_OUT  # type: ignore[union-attr]

    cancel_recorder = InMemoryRunTraceRecorder()
    cancel_event = asyncio.Event()
    cancel_event.set()
    cancel_runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="never")]),
        recorder=cancel_recorder,
    )
    cancel_result = await cancel_runtime.run("cancel", cancel_event=cancel_event)
    assert cancel_result.stop_reason is StopReason.EXTERNAL_CANCELLED
    assert cancel_recorder.snapshot().status is RunStatus.CANCELLED  # type: ignore[union-attr]

    error_recorder = InMemoryRunTraceRecorder()
    error_runtime = AgentRuntime(
        ScriptedModel([], error=RuntimeError("model secret api_key=hidden")),
        recorder=error_recorder,
    )
    error_result = await error_runtime.run("model error")
    error_trace = error_recorder.snapshot()
    assert error_result.stop_reason is StopReason.MODEL_ERROR
    assert error_trace is not None
    assert error_trace.status is RunStatus.FAILED
    assert error_trace.events[-1].error_summary is not None
    assert "hidden" not in error_trace.to_jsonl()


@pytest.mark.asyncio
async def test_recorder_failures_never_break_runtime() -> None:
    runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="still works")]),
        recorder=BrokenRecorder(),  # type: ignore[arg-type]
    )

    result = await runtime.run("input")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "still works"


def test_trace_sanitizer_redacts_and_truncates_message_summary() -> None:
    sanitizer = DefaultRunTraceSanitizer(summary_max_chars=24)
    recorder = InMemoryRunTraceRecorder(sanitizer=sanitizer)
    runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="x")]),
        recorder=recorder,
    )

    asyncio.run(runtime.run("prompt api_key=secret-value"))
    trace = recorder.snapshot()
    assert trace is not None
    started = trace.events[0]
    assert started.message_summary is not None
    assert "secret-value" not in started.message_summary
    assert "prompt api_key" not in started.message_summary


def test_trace_jsonl_round_trip() -> None:
    recorder = InMemoryRunTraceRecorder()
    runtime = AgentRuntime(
        ScriptedModel([AgentDecision(answer="round trip")]),
        recorder=recorder,
    )
    asyncio.run(runtime.run("input", run_id="round-trip"))
    trace = recorder.snapshot()
    assert trace is not None

    restored = RunTrace.from_jsonl(trace.to_jsonl())

    assert restored == trace
    assert restored.to_dict() == trace.to_dict()


@pytest.mark.asyncio
async def test_concurrent_runs_keep_tool_context_and_trace_metadata_isolated() -> None:
    recorders: list[InMemoryRunTraceRecorder] = []
    contexts: list[ToolContext] = []

    def recorder_factory() -> InMemoryRunTraceRecorder:
        recorder = InMemoryRunTraceRecorder()
        recorders.append(recorder)
        return recorder

    runtime = AgentRuntime(
        ConcurrentModel(),
        tools={"capture": ContextCaptureTool(contexts)},
        recorder_factory=recorder_factory,
    )
    results = await asyncio.gather(
        runtime.run(
            "first",
            run_id="run-first",
            request_id="request-first",
            model="model-first",
        ),
        runtime.run(
            "second",
            run_id="run-second",
            request_id="request-second",
            model="model-second",
        ),
    )

    assert {(context.run_id, context.request_id) for context in contexts} == {
        ("run-first", "request-first"),
        ("run-second", "request-second"),
    }
    assert {result.run_id for result in results} == {"run-first", "run-second"}
    traces = [recorder.snapshot() for recorder in recorders]
    assert {trace.run_id for trace in traces if trace is not None} == {
        "run-first",
        "run-second",
    }
    for trace in traces:
        assert trace is not None
        assert all(event.run_id == trace.run_id for event in trace.events)
        assert trace.request_id == (
            "request-first" if trace.run_id == "run-first" else "request-second"
        )


def test_in_memory_recorder_declares_and_enforces_single_run_ownership() -> None:
    recorder = InMemoryRunTraceRecorder()
    started_at = datetime.now(UTC)
    recorder.start(
        "run-one", request_id="request-one", model="model", started_at=started_at
    )

    with pytest.raises(ValueError, match="single-run"):
        recorder.start(
            "run-two",
            request_id="request-two",
            model="model",
            started_at=started_at,
        )
