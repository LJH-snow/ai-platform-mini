from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeVar, cast

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
from app.agents.protocols import AgentModel, AgentTool, ToolContext
from app.exceptions.base import QuotaExceededError
from app.runs.protocols import AgentEventObserver, RunTraceRecorderProtocol
from app.tools.executor import ToolExecutor

_T = TypeVar("_T")
_DEFAULT_TOOL_OUTPUT_MAX_CHARS = 8_192
_TOOL_OUTPUT_TRUNCATION_MARKER = "...[tool output truncated]"
_TOOL_EXECUTION_ERROR = "tool_execution_failed"
_TOOL_FAILURE_MESSAGE = "Tool execution failed."
_TOOL_NOT_FOUND_MESSAGE = "Requested tool is unavailable."

logger = logging.getLogger(__name__)


class AgentRuntime:
    """Run a bounded model-tool loop independent of any web framework."""

    def __init__(
        self,
        model: AgentModel,
        tools: Mapping[str, AgentTool] | None = None,
        *,
        tool_executor: ToolExecutor | None = None,
        tool_output_max_chars: int = _DEFAULT_TOOL_OUTPUT_MAX_CHARS,
        observer: AgentEventObserver | None = None,
        recorder: RunTraceRecorderProtocol | None = None,
        recorder_factory: Callable[[], RunTraceRecorderProtocol] | None = None,
    ) -> None:
        if tools is not None and tool_executor is not None:
            raise ValueError("provide tools or tool_executor, not both")
        if tool_output_max_chars < 1:
            raise ValueError("tool_output_max_chars must be greater than zero")
        self._model = model
        self._tools = dict(tools or {})
        self._tool_executor = tool_executor
        self._tool_output_max_chars = tool_output_max_chars
        if observer is not None and recorder is not None:
            raise ValueError("provide observer or recorder, not both")
        if recorder is not None and recorder_factory is not None:
            raise ValueError("provide recorder or recorder_factory, not both")
        self._observer = observer
        self._recorder = recorder
        self._recorder_factory = recorder_factory

    async def run(
        self,
        user_input: str,
        *,
        max_steps: int = 8,
        timeout: float | None = None,
        deadline: float | None = None,
        cancel_event: asyncio.Event | None = None,
        token_budget: int | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        model: str | None = None,
    ) -> AgentRunResult:
        """Execute one bounded run and return all state and observability data."""
        if not user_input.strip():
            raise ValueError("user_input must not be empty")
        if max_steps < 1:
            raise ValueError("max_steps must be greater than zero")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        if token_budget is not None and token_budget < 0:
            raise ValueError("token_budget must not be negative")

        resolved_run_id = run_id or uuid.uuid4().hex
        state = AgentState(
            run_id=resolved_run_id,
            user_input=user_input,
            request_id=request_id,
            messages=[AgentMessage(role="user", content=user_input)],
        )
        events: list[AgentEvent] = []
        recorder = self._create_recorder()
        self._start_recorder(
            recorder,
            resolved_run_id,
            request_id=request_id,
            model=model or getattr(self._model, "actual_model", None),
        )
        self._append_event(
            events,
            recorder=recorder,
            kind=AgentEventKind.RUN_STARTED,
            run_id=resolved_run_id,
            message=user_input,
        )
        loop = asyncio.get_running_loop()
        effective_deadline = self._resolve_deadline(loop.time(), timeout, deadline)

        try:
            for _ in range(max_steps):
                stop_reason = self._check_stop(effective_deadline, cancel_event)
                if stop_reason is not None:
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=self._status_for(stop_reason),
                        stop_reason=stop_reason,
                    )

                if (
                    token_budget is not None
                    and token_budget > 0
                    and state.token_usage >= token_budget
                ):
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.STOPPED,
                        stop_reason=StopReason.TOKEN_BUDGET_EXCEEDED,
                    )

                step_index = len(state.steps) + 1
                self._append_event(
                    events,
                    recorder=recorder,
                    kind=AgentEventKind.STEP_STARTED,
                    run_id=resolved_run_id,
                    step_index=step_index,
                    include_result=False,
                    include_recorder=False,
                )
                try:
                    decision = await self._await_controlled(
                        self._model.decide(state),
                        effective_deadline=effective_deadline,
                        cancel_event=cancel_event,
                    )
                except _RuntimeStop as stop:
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=self._status_for(stop.reason),
                        stop_reason=stop.reason,
                    )
                except asyncio.CancelledError:
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.CANCELLED,
                        stop_reason=StopReason.EXTERNAL_CANCELLED,
                    )
                except QuotaExceededError:
                    raise
                except Exception as exc:
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.FAILED,
                        stop_reason=StopReason.MODEL_ERROR,
                        error=str(exc),
                    )

                invalid_reason = self._validate_decision(decision)
                self._append_event(
                    events,
                    recorder=recorder,
                    kind=AgentEventKind.MODEL_DECISION,
                    run_id=resolved_run_id,
                    step_index=step_index,
                    decision=decision,
                    cumulative_token_usage=(
                        state.token_usage
                        if decision.token_usage is None
                        else state.token_usage + decision.token_usage
                    ),
                )
                if invalid_reason is not None:
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.FAILED,
                        stop_reason=StopReason.INVALID_DECISION,
                        error=invalid_reason,
                    )

                if decision.token_usage is not None:
                    state.token_usage += decision.token_usage
                if token_budget is not None and state.token_usage > token_budget:
                    state.steps.append(AgentStep(index=step_index, decision=decision))
                    self._append_event(
                        events,
                        recorder=recorder,
                        kind=AgentEventKind.STEP_COMPLETED,
                        run_id=resolved_run_id,
                        step_index=step_index,
                        include_result=False,
                        include_recorder=False,
                    )
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.STOPPED,
                        stop_reason=StopReason.TOKEN_BUDGET_EXCEEDED,
                    )

                if decision.answer is not None:
                    state.messages.append(
                        AgentMessage(role="assistant", content=decision.answer)
                    )
                    self._append_event(
                        events,
                        recorder=recorder,
                        kind=AgentEventKind.ANSWER,
                        run_id=resolved_run_id,
                        step_index=step_index,
                        message=decision.answer,
                    )
                    state.steps.append(AgentStep(index=step_index, decision=decision))
                    self._append_event(
                        events,
                        recorder=recorder,
                        kind=AgentEventKind.STEP_COMPLETED,
                        run_id=resolved_run_id,
                        step_index=step_index,
                        include_result=False,
                        include_recorder=False,
                    )
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.COMPLETED,
                        stop_reason=StopReason.DIRECT_ANSWER,
                        answer=decision.answer,
                    )

                if (
                    token_budget is not None
                    and decision.tool_calls
                    and not decision.usage_complete
                ):
                    state.steps.append(AgentStep(index=step_index, decision=decision))
                    return self._finish(
                        recorder=recorder,
                        state=state,
                        events=events,
                        status=RunStatus.STOPPED,
                        stop_reason=StopReason.TOKEN_BUDGET_EXCEEDED,
                    )

                tool_results: list[ToolResult] = []
                for tool_call in decision.tool_calls:
                    stop_reason = self._check_stop(effective_deadline, cancel_event)
                    if stop_reason is not None:
                        return self._finish(
                            recorder=recorder,
                            state=state,
                            events=events,
                            status=self._status_for(stop_reason),
                            stop_reason=stop_reason,
                        )
                    self._append_event(
                        events,
                        recorder=recorder,
                        kind=AgentEventKind.TOOL_STARTED,
                        run_id=resolved_run_id,
                        step_index=step_index,
                        tool_call=tool_call,
                    )
                    try:
                        result = await self._execute_tool(
                            tool_call,
                            resolved_run_id,
                            step_index,
                            effective_deadline,
                            cancel_event,
                            request_id=state.request_id,
                        )
                    except _RuntimeStop as stop:
                        return self._finish(
                            recorder=recorder,
                            state=state,
                            events=events,
                            status=self._status_for(stop.reason),
                            stop_reason=stop.reason,
                        )
                    except asyncio.CancelledError:
                        return self._finish(
                            recorder=recorder,
                            state=state,
                            events=events,
                            status=RunStatus.CANCELLED,
                            stop_reason=StopReason.EXTERNAL_CANCELLED,
                        )
                    tool_results.append(result)
                    state.messages.append(
                        AgentMessage(
                            role="tool",
                            content=result.content,
                            tool_call_id=result.call_id,
                            tool_name=result.name,
                        )
                    )
                    self._append_event(
                        events,
                        recorder=recorder,
                        kind=(
                            AgentEventKind.TOOL_COMPLETED
                            if result.succeeded
                            else AgentEventKind.TOOL_FAILED
                        ),
                        run_id=resolved_run_id,
                        step_index=step_index,
                        tool_call=tool_call,
                        tool_result=result,
                        message=result.error,
                    )

                state.steps.append(
                    AgentStep(
                        index=step_index,
                        decision=decision,
                        tool_results=tuple(tool_results),
                    )
                )
                self._append_event(
                    events,
                    recorder=recorder,
                    kind=AgentEventKind.STEP_COMPLETED,
                    run_id=resolved_run_id,
                    step_index=step_index,
                    include_result=False,
                    include_recorder=False,
                )
            return self._finish(
                recorder=recorder,
                state=state,
                events=events,
                status=RunStatus.STOPPED,
                stop_reason=StopReason.MAX_STEPS,
            )
        except asyncio.CancelledError:
            return self._finish(
                recorder=recorder,
                state=state,
                events=events,
                status=RunStatus.CANCELLED,
                stop_reason=StopReason.EXTERNAL_CANCELLED,
            )

    async def _execute_tool(
        self,
        tool_call: ToolCall,
        run_id: str,
        step_index: int,
        effective_deadline: float | None,
        cancel_event: asyncio.Event | None,
        request_id: str | None,
    ) -> ToolResult:
        if self._tool_executor is not None:
            remaining = self._remaining(effective_deadline)
            execution = await self._await_controlled(
                self._tool_executor.execute(
                    tool_call.name,
                    tool_call.arguments,
                    ToolContext(
                        run_id=run_id,
                        step_index=step_index,
                        request_id=request_id,
                    ),
                    timeout_seconds=remaining,
                ),
                effective_deadline=effective_deadline,
                cancel_event=cancel_event,
            )
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                content=execution.output,
                succeeded=execution.succeeded,
                error=execution.error_code,
                truncated=execution.truncated,
            )

        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                content=_TOOL_NOT_FOUND_MESSAGE,
                succeeded=False,
                error="tool_not_found",
            )
        try:
            content = await self._await_controlled(
                tool.execute(
                    tool_call.arguments,
                    ToolContext(
                        run_id=run_id,
                        step_index=step_index,
                        request_id=request_id,
                    ),
                ),
                effective_deadline=effective_deadline,
                cancel_event=cancel_event,
            )
        except (_RuntimeStop, asyncio.CancelledError):
            raise
        except Exception:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                content=f"{_TOOL_FAILURE_MESSAGE} [error_code={_TOOL_EXECUTION_ERROR}]",
                succeeded=False,
                error=_TOOL_EXECUTION_ERROR,
            )
        safe_content, truncated = self._truncate_tool_output(content)
        return ToolResult(
            call_id=tool_call.call_id,
            name=tool_call.name,
            content=safe_content,
            succeeded=True,
            truncated=truncated,
        )

    def _truncate_tool_output(self, content: str) -> tuple[str, bool]:
        if len(content) <= self._tool_output_max_chars:
            return content, False
        available = self._tool_output_max_chars - len(_TOOL_OUTPUT_TRUNCATION_MARKER)
        if available <= 0:
            return _TOOL_OUTPUT_TRUNCATION_MARKER[: self._tool_output_max_chars], True
        return content[:available] + _TOOL_OUTPUT_TRUNCATION_MARKER, True

    async def _await_controlled(
        self,
        awaitable: Awaitable[_T],
        *,
        effective_deadline: float | None,
        cancel_event: asyncio.Event | None,
    ) -> _T:
        operation: asyncio.Future[_T] = asyncio.ensure_future(awaitable)
        cancel_waiter: asyncio.Task[bool] | None = None
        try:
            waiters: list[asyncio.Future[object]] = [
                cast(asyncio.Future[object], operation)
            ]
            if cancel_event is not None:
                cancel_waiter = asyncio.create_task(cancel_event.wait())
                waiters.append(cast(asyncio.Future[object], cancel_waiter))
            timeout = self._remaining(effective_deadline)
            done, _ = await asyncio.wait(
                waiters,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise _RuntimeStop(StopReason.DEADLINE_EXCEEDED)
            if cancel_waiter is not None and cancel_waiter in done:
                raise _RuntimeStop(StopReason.EXTERNAL_CANCELLED)
            return operation.result()
        finally:
            if cancel_waiter is not None:
                cancel_waiter.cancel()
            if not operation.done():
                operation.cancel()
            if cancel_waiter is None:
                await asyncio.gather(operation, return_exceptions=True)
            else:
                await asyncio.gather(operation, cancel_waiter, return_exceptions=True)

    @staticmethod
    def _resolve_deadline(
        now: float,
        timeout: float | None,
        deadline: float | None,
    ) -> float | None:
        timeout_deadline = None if timeout is None else now + timeout
        if timeout_deadline is None:
            return deadline
        if deadline is None:
            return timeout_deadline
        return min(timeout_deadline, deadline)

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - asyncio.get_running_loop().time())

    @staticmethod
    def _check_stop(
        deadline: float | None,
        cancel_event: asyncio.Event | None,
    ) -> StopReason | None:
        if cancel_event is not None and cancel_event.is_set():
            return StopReason.EXTERNAL_CANCELLED
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            return StopReason.DEADLINE_EXCEEDED
        return None

    @staticmethod
    def _validate_decision(decision: AgentDecision) -> str | None:
        if decision.answer is not None and decision.tool_calls:
            return "decision cannot contain both an answer and tool calls"
        if decision.answer is None and not decision.tool_calls:
            return "decision must contain an answer or at least one tool call"
        if decision.answer is not None and not decision.answer.strip():
            return "answer must not be empty"
        if decision.token_usage is not None and decision.token_usage < 0:
            return "token_usage must not be negative"
        return None

    @staticmethod
    def _status_for(stop_reason: StopReason) -> RunStatus:
        if stop_reason is StopReason.DEADLINE_EXCEEDED:
            return RunStatus.TIMED_OUT
        if stop_reason is StopReason.EXTERNAL_CANCELLED:
            return RunStatus.CANCELLED
        if stop_reason in {StopReason.MODEL_ERROR, StopReason.INVALID_DECISION}:
            return RunStatus.FAILED
        return RunStatus.STOPPED

    def _append_event(
        self,
        events: list[AgentEvent],
        recorder: RunTraceRecorderProtocol | None,
        *,
        kind: AgentEventKind,
        run_id: str,
        step_index: int | None = None,
        message: str | None = None,
        decision: AgentDecision | None = None,
        tool_call: ToolCall | None = None,
        tool_result: ToolResult | None = None,
        status: RunStatus | None = None,
        stop_reason: StopReason | None = None,
        cumulative_token_usage: int | None = None,
        include_result: bool = True,
        include_recorder: bool = True,
    ) -> None:
        previous = events[-1] if events else None
        occurred_at = datetime.now(UTC)
        if previous is not None and occurred_at < previous.occurred_at:
            occurred_at = previous.occurred_at
        sequence = 1 if previous is None else previous.sequence + 1
        event = AgentEvent(
            kind=kind,
            run_id=run_id,
            sequence=sequence,
            occurred_at=occurred_at,
            step_index=step_index,
            message=message,
            decision=decision,
            tool_call=tool_call,
            tool_result=tool_result,
            status=status,
            stop_reason=stop_reason,
            cumulative_token_usage=cumulative_token_usage,
        )
        events.append(event)
        self._notify_observer(event)
        if include_recorder:
            self._notify_recorder(recorder, event)

    def _notify_observer(self, event: AgentEvent) -> None:
        if self._observer is None or self._observer is self._recorder:
            return
        try:
            self._observer.observe(event)
        except Exception:
            logger.exception("Agent event observer failed")

    def _notify_recorder(
        self, recorder: RunTraceRecorderProtocol | None, event: AgentEvent
    ) -> None:
        if recorder is None:
            return
        try:
            recorder.observe(event)
        except Exception:
            logger.exception("Agent event recorder failed")

    def _create_recorder(self) -> RunTraceRecorderProtocol | None:
        if self._recorder_factory is None:
            return self._recorder
        try:
            return self._recorder_factory()
        except Exception:
            logger.exception("Agent event recorder factory failed")
            return None

    def _start_recorder(
        self,
        recorder: RunTraceRecorderProtocol | None,
        run_id: str,
        *,
        request_id: str | None,
        model: str | None,
    ) -> None:
        if recorder is None:
            return
        try:
            recorder.start(
                run_id,
                request_id=request_id,
                model=model,
                started_at=datetime.now(UTC),
            )
        except Exception:
            logger.exception("Agent event recorder start failed")

    def _notify_recorder_finish(
        self, recorder: RunTraceRecorderProtocol | None, result: AgentRunResult
    ) -> None:
        if recorder is None:
            return
        try:
            recorder.finish(result)
        except Exception:
            logger.exception("Agent event recorder finish failed")

    def _finish(
        self,
        *,
        recorder: RunTraceRecorderProtocol | None,
        state: AgentState,
        events: list[AgentEvent],
        status: RunStatus,
        stop_reason: StopReason,
        answer: str | None = None,
        error: str | None = None,
    ) -> AgentRunResult:
        self._complete_open_steps(
            recorder=recorder,
            events=events,
            run_id=state.run_id,
        )
        self._append_event(
            events,
            recorder=recorder,
            kind=AgentEventKind.RUN_STOPPED,
            run_id=state.run_id,
            status=status,
            stop_reason=stop_reason,
            message=error,
            cumulative_token_usage=state.token_usage,
        )
        result_events = tuple(
            replace(event, sequence=index)
            for index, event in enumerate(
                (event for event in events if include_result_event(event)),
                start=1,
            )
        )
        result = AgentRunResult(
            run_id=state.run_id,
            status=status,
            stop_reason=stop_reason,
            answer=answer,
            state=state,
            events=result_events,
            token_usage=state.token_usage,
            error=error,
        )
        self._notify_recorder_finish(recorder, result)
        return result

    def _complete_open_steps(
        self,
        *,
        recorder: RunTraceRecorderProtocol | None,
        events: list[AgentEvent],
        run_id: str,
    ) -> None:
        """Close every started step before publishing the run terminal event."""
        started = {
            event.step_index
            for event in events
            if event.kind is AgentEventKind.STEP_STARTED
            and event.step_index is not None
        }
        completed = {
            event.step_index
            for event in events
            if event.kind is AgentEventKind.STEP_COMPLETED
            and event.step_index is not None
        }
        for step_index in sorted(started - completed):
            self._append_event(
                events,
                recorder=recorder,
                kind=AgentEventKind.STEP_COMPLETED,
                run_id=run_id,
                step_index=step_index,
                include_result=False,
                include_recorder=False,
            )


class _RuntimeStop(Exception):
    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def include_result_event(event: AgentEvent) -> bool:
    """Keep synchronous result events backward-compatible with the old list."""
    return event.kind not in {
        AgentEventKind.STEP_STARTED,
        AgentEventKind.STEP_COMPLETED,
    }
