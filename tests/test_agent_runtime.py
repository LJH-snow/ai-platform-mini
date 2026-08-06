import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field

import pytest

from app.agents import (
    AgentAnswerChunk,
    AgentDecision,
    AgentEventKind,
    AgentRuntime,
    AgentState,
    AgentTool,
    RunStatus,
    StopReason,
    ToolCall,
    ToolContext,
)
from app.agents.models import AgentEvent
from app.tools import CalculatorTool, ToolExecutor, ToolRegistry


@dataclass
class ScriptedModel:
    responses: list[AgentDecision]
    delay: float = 0.0
    seen_states: list[AgentState] = field(default_factory=list)

    async def decide(self, state: AgentState) -> AgentDecision:
        self.seen_states.append(deepcopy(state))
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.responses:
            raise AssertionError("the scripted model ran out of responses")
        return self.responses.pop(0)


@dataclass
class StreamingScriptedModel:
    responses: list[AgentDecision]
    chunks: list[AgentAnswerChunk]
    closed: bool = False

    async def decide(self, state: AgentState) -> AgentDecision:
        del state
        return self.responses.pop(0)

    async def stream_answer(self, state: AgentState) -> AsyncIterator[AgentAnswerChunk]:
        del state
        try:
            for chunk in self.chunks:
                await asyncio.sleep(0)
                yield chunk
        finally:
            self.closed = True


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    def observe(self, event: AgentEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_streaming_direct_answer_emits_real_deltas_in_order() -> None:
    model = StreamingScriptedModel(
        responses=[AgentDecision(answer="decision placeholder")],
        chunks=[
            AgentAnswerChunk(content="real "),
            AgentAnswerChunk(content="answer"),
            AgentAnswerChunk(content="", done=True),
        ],
    )
    observer = RecordingObserver()

    result = await AgentRuntime(model, observer=observer).run(
        "hello", stream_answer=True
    )

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "real answer"
    assert [event.kind for event in observer.events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.STEP_STARTED,
        AgentEventKind.MODEL_DECISION,
        AgentEventKind.ANSWER_DELTA,
        AgentEventKind.ANSWER_DELTA,
        AgentEventKind.STEP_COMPLETED,
        AgentEventKind.RUN_STOPPED,
    ]
    assert [
        event.message
        for event in observer.events
        if event.kind is AgentEventKind.ANSWER_DELTA
    ] == [
        "real ",
        "answer",
    ]
    assert model.closed is True


@pytest.mark.asyncio
async def test_streaming_tool_then_answer_keeps_single_runtime_loop() -> None:
    model = StreamingScriptedModel(
        responses=[
            AgentDecision(tool_calls=(ToolCall("call-1", "echo", {"value": "x"}),)),
            AgentDecision(answer="placeholder"),
        ],
        chunks=[
            AgentAnswerChunk(content="after tool"),
            AgentAnswerChunk(content="", done=True),
        ],
    )
    result = await AgentRuntime(
        model,
        tools={"echo": EchoTool()},
    ).run("use it", stream_answer=True)

    assert result.answer == "after tool"
    assert len(result.state.steps) == 2
    assert [step.index for step in result.state.steps] == [1, 2]


@pytest.mark.asyncio
async def test_streaming_empty_answer_fails_and_closes_iterator() -> None:
    model = StreamingScriptedModel(
        responses=[AgentDecision(answer="placeholder")],
        chunks=[AgentAnswerChunk(content="", done=True)],
    )

    result = await AgentRuntime(model).run("hello", stream_answer=True)

    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert model.closed is True


@pytest.mark.asyncio
async def test_streaming_timeout_and_cancel_close_provider_iterator() -> None:
    class BlockingStreamingModel(StreamingScriptedModel):
        def __init__(self, responses: list[AgentDecision]) -> None:
            super().__init__(responses=responses, chunks=[])
            self.started = asyncio.Event()

        async def stream_answer(
            self, state: AgentState
        ) -> AsyncIterator[AgentAnswerChunk]:
            del state
            try:
                self.started.set()
                await asyncio.Future()
                yield AgentAnswerChunk(content="unreachable")
            finally:
                self.closed = True

    timeout_model = BlockingStreamingModel(
        responses=[AgentDecision(answer="placeholder")]
    )
    timeout_result = await AgentRuntime(timeout_model).run(
        "hello", stream_answer=True, timeout=0.01
    )
    assert timeout_result.status is RunStatus.TIMED_OUT
    assert timeout_model.closed is True

    cancel_model = BlockingStreamingModel(
        responses=[AgentDecision(answer="placeholder")]
    )
    cancel_event = asyncio.Event()
    task = asyncio.create_task(
        AgentRuntime(cancel_model).run(
            "hello", stream_answer=True, cancel_event=cancel_event
        )
    )
    await asyncio.wait_for(cancel_model.started.wait(), timeout=1)
    cancel_event.set()
    cancel_result = await task
    assert cancel_result.status is RunStatus.CANCELLED
    assert cancel_model.closed is True


@dataclass
class EchoTool:
    name: str = "echo"
    calls: list[tuple[Mapping[str, object], ToolContext]] = field(default_factory=list)

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        self.calls.append((arguments, context))
        return str(arguments["value"])


@dataclass
class FailingTool:
    name: str = "unstable"
    calls: int = 0

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        del arguments, context
        self.calls += 1
        raise RuntimeError("upstream unavailable")


class BlockingTool:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str:
        del arguments, context
        self.started.set()
        await asyncio.Future()
        return "unreachable"


@pytest.mark.asyncio
async def test_direct_answer_completes_with_observable_events() -> None:
    model = ScriptedModel([AgentDecision(answer="hello")])
    runtime = AgentRuntime(model)

    result = await runtime.run("hi", run_id="direct-run")

    assert result.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.DIRECT_ANSWER
    assert result.answer == "hello"
    assert result.state.steps[0].index == 1
    assert [event.kind for event in result.events] == [
        AgentEventKind.RUN_STARTED,
        AgentEventKind.MODEL_DECISION,
        AgentEventKind.ANSWER,
        AgentEventKind.RUN_STOPPED,
    ]


@pytest.mark.asyncio
async def test_single_tool_call_is_fed_back_before_final_answer() -> None:
    tool = EchoTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="call-1",
                        name="echo",
                        arguments={"value": "tool output"},
                    ),
                )
            ),
            AgentDecision(answer="final answer"),
        ]
    )
    runtime = AgentRuntime(model, tools={"echo": tool})

    assert isinstance(tool, AgentTool)
    result = await runtime.run("use the tool")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "final answer"
    assert result.state.messages[-2].role == "tool"
    assert result.state.messages[-2].content == "tool output"
    assert len(tool.calls) == 1
    assert tool.calls[0][1].step_index == 1
    assert [step.index for step in result.state.steps] == [1, 2]


@pytest.mark.asyncio
async def test_runtime_can_execute_tools_through_registry_and_executor() -> None:
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="call-calculator",
                        name="calculator",
                        arguments={"expression": "2 + 3"},
                    ),
                )
            ),
            AgentDecision(answer="5"),
        ]
    )
    executor = ToolExecutor(ToolRegistry([CalculatorTool()]))

    result = await AgentRuntime(model, tool_executor=executor).run("calculate 2 + 3")

    assert result.answer == "5"
    assert result.state.steps[0].tool_results[0].succeeded is True
    assert result.state.messages[-2].content == "5"


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_one_decision_are_executed_and_recorded() -> None:
    first = EchoTool(name="first")
    second = EchoTool(name="second")
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(
                    ToolCall("call-1", "first", {"value": "one"}),
                    ToolCall("call-2", "second", {"value": "two"}),
                )
            ),
            AgentDecision(answer="combined"),
        ]
    )
    runtime = AgentRuntime(model, tools={"first": first, "second": second})

    result = await runtime.run("combine results")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "combined"
    assert [message.content for message in result.state.messages[-3:-1]] == [
        "one",
        "two",
    ]
    assert len(result.state.steps[0].tool_results) == 2
    assert [event.kind for event in result.events].count(
        AgentEventKind.TOOL_COMPLETED
    ) == 2


@pytest.mark.asyncio
async def test_tool_failure_is_fed_back_and_model_can_recover() -> None:
    tool = FailingTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "unstable", {}),),
            ),
            AgentDecision(answer="fallback answer"),
        ]
    )
    runtime = AgentRuntime(model, tools={"unstable": tool})

    result = await runtime.run("try the service")

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "fallback answer"
    failed_result = result.state.steps[0].tool_results[0]
    assert failed_result.succeeded is False
    assert failed_result.error == "tool_execution_failed"
    assert failed_result.content == (
        "Tool execution failed. [error_code=tool_execution_failed]"
    )
    assert "upstream unavailable" not in " ".join(
        message.content for message in result.state.messages
    )
    assert "upstream unavailable" not in " ".join(
        event.message or "" for event in result.events
    )
    assert AgentEventKind.TOOL_FAILED in [event.kind for event in result.events]


@pytest.mark.asyncio
async def test_max_steps_stops_repeated_tool_decisions() -> None:
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "missing", {}),),
            ),
            AgentDecision(
                tool_calls=(ToolCall("call-2", "missing", {}),),
            ),
            AgentDecision(answer="must not be reached"),
        ]
    )
    runtime = AgentRuntime(model)

    result = await runtime.run("never finish", max_steps=2)

    assert result.status is RunStatus.STOPPED
    assert result.stop_reason is StopReason.MAX_STEPS
    assert result.answer is None
    assert len(result.state.steps) == 2
    assert len(model.seen_states) == 2


@pytest.mark.asyncio
async def test_token_budget_exactly_reached_is_allowed() -> None:
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "missing", {}),),
                token_usage=2,
            ),
            AgentDecision(answer="done", token_usage=3),
        ]
    )
    runtime = AgentRuntime(model)

    result = await runtime.run("use exactly five", token_budget=5)

    assert result.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.DIRECT_ANSWER
    assert result.answer == "done"
    assert result.token_usage == 5
    assert result.state.token_usage == 5
    assert [
        event.cumulative_token_usage
        for event in result.events
        if event.kind is AgentEventKind.MODEL_DECISION
    ] == [2, 5]


@pytest.mark.asyncio
async def test_token_budget_exceeded_after_accumulation_stops_before_answer() -> None:
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "missing", {}),),
                token_usage=3,
            ),
            AgentDecision(answer="over budget", token_usage=3),
        ]
    )
    runtime = AgentRuntime(model)

    result = await runtime.run("use six", token_budget=5)

    assert result.status is RunStatus.STOPPED
    assert result.stop_reason is StopReason.TOKEN_BUDGET_EXCEEDED
    assert result.answer is None
    assert result.token_usage == 6
    assert result.state.token_usage == 6
    assert result.state.steps[-1].decision.answer == "over budget"
    assert result.state.messages[-1].role == "tool"


@pytest.mark.asyncio
async def test_unknown_token_usage_stops_before_a_tool_round() -> None:
    tool = EchoTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        call_id="call-unknown",
                        name="echo",
                        arguments={"value": "should not run"},
                    ),
                ),
                usage_complete=False,
            ),
            AgentDecision(answer="must not be requested"),
        ]
    )
    runtime = AgentRuntime(model, tools={"echo": tool})

    result = await runtime.run("unknown usage", token_budget=100)

    assert result.status is RunStatus.STOPPED
    assert result.stop_reason is StopReason.TOKEN_BUDGET_EXCEEDED
    assert result.answer is None
    assert tool.calls == []
    assert len(model.seen_states) == 1


@pytest.mark.asyncio
async def test_unknown_token_usage_does_not_trigger_budget_stop() -> None:
    model = ScriptedModel([AgentDecision(answer="usage unavailable")])
    runtime = AgentRuntime(model)

    result = await runtime.run("unknown usage", token_budget=0)

    assert result.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.DIRECT_ANSWER
    assert result.answer == "usage unavailable"
    assert result.token_usage == 0
    decision_event = next(
        event for event in result.events if event.kind is AgentEventKind.MODEL_DECISION
    )
    assert decision_event.decision is not None
    assert decision_event.decision.token_usage is None
    assert decision_event.cumulative_token_usage == 0


@pytest.mark.asyncio
async def test_repeated_calculator_calls_are_cached_and_finalized() -> None:
    calculator = CalculatorTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "calculator", {"expression": "3*2"}),)
            ),
            AgentDecision(
                tool_calls=(ToolCall("call-2", "calculator", {"expression": "3*2"}),)
            ),
            AgentDecision(
                tool_calls=(ToolCall("call-3", "calculator", {"expression": "3*2"}),)
            ),
        ]
    )

    result = await AgentRuntime(model, tools={"calculator": calculator}).run(
        "计算 3*2", max_steps=3
    )

    assert result.status is RunStatus.COMPLETED
    assert result.stop_reason is StopReason.DIRECT_ANSWER
    assert result.answer == "6"
    assert len(result.state.steps) == 2
    assert result.state.steps[1].tool_results[0].cached is True
    assert len(model.responses) == 1


@pytest.mark.asyncio
async def test_streaming_calculator_shortcut_publishes_complete_answer_event() -> None:
    calculator = CalculatorTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "calculator", {"expression": "3*2"}),)
            ),
            AgentDecision(
                tool_calls=(ToolCall("call-2", "calculator", {"expression": "3*2"}),)
            ),
        ]
    )
    observer = RecordingObserver()

    result = await AgentRuntime(
        model,
        tools={"calculator": calculator},
        observer=observer,
    ).run("计算 3*2", max_steps=2, stream_answer=True)

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "6"
    answer_events = [
        event for event in observer.events if event.kind is AgentEventKind.ANSWER
    ]
    assert [event.message for event in answer_events] == ["6"]
    assert answer_events[-1].sequence < next(
        event.sequence
        for event in observer.events
        if event.kind is AgentEventKind.RUN_STOPPED
    )


@pytest.mark.asyncio
async def test_streaming_calculator_max_steps_fallback_publishes_answer_event() -> None:
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(ToolCall("call-1", "calculator", {"expression": "2 + 2"}),)
            )
        ]
    )
    observer = RecordingObserver()

    result = await AgentRuntime(
        model,
        tools={"calculator": CalculatorTool()},
        observer=observer,
    ).run("计算 2+2", max_steps=1, stream_answer=True)

    assert result.status is RunStatus.COMPLETED
    assert result.answer == "4"
    answer_events = [
        event for event in observer.events if event.kind is AgentEventKind.ANSWER
    ]
    assert [event.message for event in answer_events] == ["4"]
    assert answer_events[-1].sequence < next(
        event.sequence
        for event in observer.events
        if event.kind is AgentEventKind.RUN_STOPPED
    )


@pytest.mark.asyncio
async def test_timeout_stops_a_slow_model() -> None:
    model = ScriptedModel([AgentDecision(answer="late")], delay=0.05)
    runtime = AgentRuntime(model)

    result = await runtime.run("be quick", timeout=0.001)

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason is StopReason.DEADLINE_EXCEEDED
    assert result.answer is None


@pytest.mark.asyncio
async def test_absolute_deadline_stops_a_slow_tool() -> None:
    tool = BlockingTool()
    model = ScriptedModel(
        [AgentDecision(tool_calls=(ToolCall("call-1", "blocking", {}),))]
    )
    runtime = AgentRuntime(model, tools={"blocking": tool})

    loop = asyncio.get_running_loop()
    result = await runtime.run(
        "deadline test",
        deadline=loop.time() + 0.01,
    )

    assert result.status is RunStatus.TIMED_OUT
    assert result.stop_reason is StopReason.DEADLINE_EXCEEDED
    assert result.state.steps == []


@pytest.mark.asyncio
async def test_external_cancel_event_interrupts_a_blocking_tool() -> None:
    tool = BlockingTool()
    model = ScriptedModel(
        [AgentDecision(tool_calls=(ToolCall("call-1", "blocking", {}),))]
    )
    runtime = AgentRuntime(model, tools={"blocking": tool})
    cancel_event = asyncio.Event()

    task = asyncio.create_task(runtime.run("cancel me", cancel_event=cancel_event))
    await tool.started.wait()
    cancel_event.set()
    result = await task

    assert result.status is RunStatus.CANCELLED
    assert result.stop_reason is StopReason.EXTERNAL_CANCELLED
    assert result.answer is None


@pytest.mark.asyncio
async def test_task_cancellation_returns_a_cancelled_result() -> None:
    model = ScriptedModel([AgentDecision(answer="late")], delay=1.0)
    runtime = AgentRuntime(model)

    task = asyncio.create_task(runtime.run("cancel task"))
    await asyncio.sleep(0)
    task.cancel()
    result = await task

    assert result.status is RunStatus.CANCELLED
    assert result.stop_reason is StopReason.EXTERNAL_CANCELLED


@pytest.mark.asyncio
async def test_invalid_empty_decision_fails_instead_of_looping() -> None:
    model = ScriptedModel([AgentDecision()])
    runtime = AgentRuntime(model)

    result = await runtime.run("invalid decision")

    assert result.status is RunStatus.FAILED
    assert result.stop_reason is StopReason.INVALID_DECISION
    assert result.error == "decision must contain an answer or at least one tool call"
    assert len(model.seen_states) == 1


@pytest.mark.asyncio
async def test_invalid_runtime_arguments_are_rejected() -> None:
    runtime = AgentRuntime(ScriptedModel([AgentDecision(answer="ok")]))

    with pytest.raises(ValueError, match="max_steps"):
        await runtime.run("input", max_steps=0)
    with pytest.raises(ValueError, match="timeout"):
        await runtime.run("input", timeout=-1)
    with pytest.raises(ValueError, match="user_input"):
        await runtime.run("   ")


@pytest.mark.asyncio
async def test_model_can_observe_prior_tool_messages() -> None:
    tool = EchoTool()
    model = ScriptedModel(
        [
            AgentDecision(tool_calls=(ToolCall("call-1", "echo", {"value": "x"}),)),
            AgentDecision(answer="observed"),
        ]
    )
    runtime = AgentRuntime(model, tools={"echo": tool})

    await runtime.run("observe")

    assert [message.role for message in model.seen_states[1].messages] == [
        "user",
        "tool",
    ]
    assert model.seen_states[1].messages[-1].content == "x"


@pytest.mark.asyncio
async def test_events_have_monotonic_sequence_and_utc_timestamps() -> None:
    model = ScriptedModel([AgentDecision(answer="hello")])
    result = await AgentRuntime(model).run("hi")

    assert [event.sequence for event in result.events] == list(
        range(1, len(result.events) + 1)
    )
    assert all(event.occurred_at.tzinfo is not None for event in result.events)
    assert all(
        left.occurred_at <= right.occurred_at
        for left, right in zip(result.events, result.events[1:], strict=False)
    )


@pytest.mark.asyncio
async def test_tool_output_is_truncated_before_model_feedback() -> None:
    tool = EchoTool()
    model = ScriptedModel(
        [
            AgentDecision(
                tool_calls=(
                    ToolCall(
                        "call-1",
                        "echo",
                        {"value": "abcdefghijklmnopqrstuvwxyz0123456789"},
                    ),
                )
            ),
            AgentDecision(answer="done"),
        ]
    )
    runtime = AgentRuntime(
        model,
        tools={"echo": tool},
        tool_output_max_chars=30,
    )

    result = await runtime.run("truncate")

    tool_message = result.state.messages[-2]
    tool_result = result.state.steps[0].tool_results[0]
    assert len(tool_message.content) == 30
    assert tool_message.content.endswith("[tool output truncated]")
    assert tool_result.truncated is True
    assert model.seen_states[1].messages[-1].content == tool_message.content


@pytest.mark.asyncio
async def test_tool_exception_details_are_not_returned_in_run_summary() -> None:
    tool = FailingTool()
    model = ScriptedModel(
        [
            AgentDecision(tool_calls=(ToolCall("call-1", "unstable", {}),)),
            AgentDecision(answer="recovered"),
        ]
    )

    result = await AgentRuntime(model, tools={"unstable": tool}).run("safe failure")

    assert result.answer == "recovered"
    assert result.error is None
    assert all(
        "upstream unavailable" not in value
        for value in (
            *(message.content for message in result.state.messages),
            *(event.message or "" for event in result.events),
        )
    )
