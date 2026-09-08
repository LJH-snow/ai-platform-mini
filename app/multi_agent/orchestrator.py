"""Orchestrator: manages execution of subtasks.

The Orchestrator takes a SupervisorDecision and executes subtasks using the
existing AgentRuntime, respecting dependencies, concurrency limits, budgets,
and failure policies.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.agents.models import AgentEvent, AgentEventKind
from app.multi_agent.events import (
    MultiAgentEvent,
    MultiAgentEventKind,
    MultiAgentEventObserver,
    _bounded_summary,
)
from app.multi_agent.models import (
    AgentConfig,
    AgentRole,
    FailurePolicy,
    OrchestrationConfig,
    OrchestrationResult,
    OrchestrationState,
    OrchestrationStatus,
    SharedContext,
    Subtask,
    SubtaskResult,
    SupervisorDecision,
    TaskStatus,
)
from app.schemas.agent import AgentRunRequest

if TYPE_CHECKING:
    from app.agents.runtime import AgentRuntime
    from app.auth.models import APIKey
    from app.core.context import RequestContext
    from app.services.agent_service import AgentService
    from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

# Default agent configurations for common roles
_DEFAULT_AGENT_CONFIGS: dict[AgentRole, AgentConfig] = {
    AgentRole.RESEARCH: AgentConfig(
        role=AgentRole.RESEARCH,
        name="Research Agent",
        system_prompt=(
            "You are a research agent. Your job is to search for information, "
            "gather facts, and provide comprehensive findings. Use available "
            "tools (RAG, knowledge search) when appropriate. Be thorough but concise."
        ),
        max_steps=5,
    ),
    AgentRole.WRITER: AgentConfig(
        role=AgentRole.WRITER,
        name="Writer Agent",
        system_prompt=(
            "You are a writer agent. Your job is to generate well-structured "
            "text based on the provided context and requirements. Focus on "
            "clarity, coherence, and completeness."
        ),
        max_steps=3,
    ),
    AgentRole.REVIEWER: AgentConfig(
        role=AgentRole.REVIEWER,
        name="Reviewer Agent",
        system_prompt=(
            "You are a reviewer agent. Your job is to check the quality of "
            "provided content, verify sources, and validate consistency. "
            "Provide specific, actionable feedback."
        ),
        max_steps=3,
    ),
}


_AGENT_TO_MULTI_EVENT: dict[AgentEventKind, MultiAgentEventKind] = {
    AgentEventKind.STEP_STARTED: MultiAgentEventKind.SUBTASK_STEP_STARTED,
    AgentEventKind.STEP_COMPLETED: MultiAgentEventKind.SUBTASK_STEP_COMPLETED,
    AgentEventKind.TOOL_STARTED: MultiAgentEventKind.SUBTASK_TOOL_STARTED,
    AgentEventKind.TOOL_COMPLETED: MultiAgentEventKind.SUBTASK_TOOL_COMPLETED,
    AgentEventKind.TOOL_FAILED: MultiAgentEventKind.SUBTASK_TOOL_FAILED,
    AgentEventKind.ANSWER_DELTA: MultiAgentEventKind.SUBTASK_ANSWER_DELTA,
}


class _SubtaskRuntimeEventObserver:
    """Forward a subtask's inner Agent runtime events into the run stream.

    The AgentRuntime calls ``observe`` synchronously, so each delta is
    scheduled as an async task against the multi-agent observer and drained
    before the next lifecycle event is emitted to preserve ordering.
    """

    def __init__(
        self,
        *,
        run_id: str,
        task_id: str,
        agent_role: AgentRole,
        observer: MultiAgentEventObserver,
        run_source: str = "supervisor",
    ) -> None:
        self._run_id = run_id
        self._task_id = task_id
        self._agent_role = agent_role
        self._observer = observer
        self._run_source = run_source
        self._pending: list[asyncio.Task[None]] = []

    def observe(self, event: AgentEvent) -> None:
        mapped_kind = _AGENT_TO_MULTI_EVENT.get(event.kind)
        if mapped_kind is None:
            return
        origin = event.tool_call or event.tool_result
        multi_event = MultiAgentEvent(
            run_id=self._run_id,
            kind=mapped_kind,
            sequence=0,
            task_id=self._task_id,
            agent_role=self._agent_role.value,
            output_summary=(_bounded_summary(event.message) if event.message else None),
            agent_event_kind=event.kind.value,
            step_index=event.step_index,
            tool_name=origin.name if origin is not None else None,
            call_id=origin.call_id if origin is not None else None,
            run_source=self._run_source,
        )
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._observer.on_event(multi_event))
        self._pending.append(task)
        task.add_done_callback(self._release)

    def _release(self, task: asyncio.Task[None]) -> None:
        if task in self._pending:
            self._pending.remove(task)

    async def drain(self) -> None:
        """Await all scheduled delta events before continuing the lifecycle."""
        while self._pending:
            pending = list(self._pending)
            await asyncio.gather(*pending, return_exceptions=True)


class Orchestrator:
    """Executes subtasks using AgentRuntime with concurrency and failure policies."""

    def __init__(
        self,
        runtime_factory: type[AgentRuntime] | None = None,
        chat_service: ChatService | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._chat_service = chat_service
        self._agent_service = agent_service

    async def execute(
        self,
        decision: SupervisorDecision,
        user_input: str,
        config: OrchestrationConfig | None = None,
        *,
        run_id: str | None = None,
        request_id: str | None = None,
        observer: MultiAgentEventObserver | None = None,
        cancel_event: asyncio.Event | None = None,
        context: RequestContext | None = None,
        api_key: APIKey | None = None,
        run_source: str = "supervisor",
    ) -> OrchestrationResult:
        """Execute all subtasks respecting dependencies and policies."""
        config = config or OrchestrationConfig()
        resolved_run_id = run_id or uuid.uuid4().hex
        start_time = time.monotonic()

        state = OrchestrationState(
            run_id=resolved_run_id,
            shared_context=SharedContext(user_input=user_input),
            subtasks=decision.subtasks,
            started_at=datetime.now(UTC),
        )

        # Build dependency graph
        task_map = {t.id: t for t in decision.subtasks}
        completed: set[str] = set()
        failed: set[str] = set()

        # Semaphore for concurrency control
        concurrency = min(config.max_concurrency, len(decision.subtasks))
        semaphore = asyncio.Semaphore(concurrency)

        # Execute tasks in topological order with limited parallelism
        try:
            await self._execute_dag(
                state=state,
                task_map=task_map,
                config=config,
                semaphore=semaphore,
                completed=completed,
                failed=failed,
                start_time=start_time,
                concurrency=concurrency,
                request_id=request_id,
                observer=observer,
                cancel_event=cancel_event,
                context=context,
                api_key=api_key,
                run_source=run_source,
            )
        except asyncio.CancelledError:
            state.status = OrchestrationStatus.CANCELLED
            state.error = "Orchestration cancelled externally"
            state.error_code = "cancelled"
        except Exception as exc:
            state.status = OrchestrationStatus.FAILED
            state.error = str(exc)
            state.error_code = "internal_error"

        state.completed_at = datetime.now(UTC)
        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Determine final output (last completed task's output, or error)
        final_output = ""
        if state.status == OrchestrationStatus.COMPLETED:
            # Find the last task in topological order that completed
            for task in reversed(decision.subtasks):
                result = state.results.get(task.id)
                if result and result.status == TaskStatus.COMPLETED:
                    final_output = result.output
                    break

        return OrchestrationResult(
            run_id=resolved_run_id,
            status=state.status,
            final_output=final_output,
            subtask_results=list(state.results.values()),
            total_token_usage=state.total_token_usage,
            error=state.error,
            error_code=state.error_code,
            duration_ms=duration_ms,
            started_at=state.started_at,
            completed_at=state.completed_at,
        )

    async def _execute_dag(
        self,
        state: OrchestrationState,
        task_map: Mapping[str, Subtask],
        config: OrchestrationConfig,
        semaphore: asyncio.Semaphore,
        completed: set[str],
        failed: set[str],
        start_time: float,
        concurrency: int,
        request_id: str | None,
        observer: MultiAgentEventObserver | None,
        cancel_event: asyncio.Event | None,
        context: RequestContext | None,
        api_key: APIKey | None,
        run_source: str = "supervisor",
    ) -> None:
        """Execute tasks respecting dependencies (DAG execution)."""
        pending = set(task_map.keys())
        in_progress: dict[str, asyncio.Task[SubtaskResult]] = {}

        while pending or in_progress:
            # Check external cancellation
            if cancel_event is not None and cancel_event.is_set():
                state.status = OrchestrationStatus.CANCELLED
                state.error = "Orchestration cancelled externally"
                state.error_code = "cancelled"
                for task in in_progress.values():
                    task.cancel()
                return
            # Check global timeout
            if config.total_timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= config.total_timeout:
                    state.status = OrchestrationStatus.TIMED_OUT
                    state.error = f"Global timeout exceeded ({config.total_timeout}s)"
                    state.error_code = "timeout"
                    # Cancel in-progress tasks
                    for task in in_progress.values():
                        task.cancel()
                    return

            # Check global token budget
            if (
                config.total_token_budget is not None
                and state.total_token_usage >= config.total_token_budget
            ):
                state.status = OrchestrationStatus.BUDGET_EXCEEDED
                state.error = f"Token budget exceeded ({config.total_token_budget})"
                state.error_code = "budget_exceeded"
                for task in in_progress.values():
                    task.cancel()
                return

            # Find tasks whose dependencies are all satisfied
            ready: list[Subtask] = []
            for task_id in list(pending):
                subtask = task_map[task_id]
                deps = set(subtask.depends_on)
                if deps.issubset(completed):
                    # Check if any dependency failed (and policy is fail_fast)
                    if (
                        deps & failed
                        and config.failure_policy == FailurePolicy.FAIL_FAST
                    ):
                        state.results[task_id] = SubtaskResult(
                            task_id=task_id,
                            status=TaskStatus.SKIPPED,
                            error="Skipped due to dependency failure",
                        )
                        if observer is not None:
                            await observer.on_event(
                                MultiAgentEvent(
                                    run_id=state.run_id,
                                    kind=MultiAgentEventKind.SUBTASK_SKIPPED,
                                    sequence=0,
                                    task_id=task_id,
                                    run_source=run_source,
                                )
                            )
                        pending.discard(task_id)
                        continue
                    ready.append(subtask)

            # Start ready tasks (up to concurrency limit)
            for subtask in ready:
                if len(in_progress) >= concurrency:
                    break
                pending.discard(subtask.id)
                in_progress[subtask.id] = asyncio.create_task(
                    self._execute_single_task(
                        state=state,
                        task=subtask,
                        config=config,
                        semaphore=semaphore,
                        request_id=request_id,
                        observer=observer,
                        cancel_event=cancel_event,
                        context=context,
                        api_key=api_key,
                        run_source=run_source,
                    )
                )

            # Wait for at least one task to complete
            if in_progress:
                done, _ = await asyncio.wait(
                    in_progress.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task_coro in done:
                    # Find which task this corresponds to
                    for task_id, task_obj in list(in_progress.items()):
                        if task_obj is task_coro:
                            result = task_coro.result()
                            state.results[task_id] = result
                            if result.status == TaskStatus.COMPLETED:
                                completed.add(task_id)
                                state.total_token_usage += result.token_usage
                            elif result.status == TaskStatus.CANCELLED:
                                state.status = OrchestrationStatus.CANCELLED
                                state.error = "Orchestration cancelled externally"
                                state.error_code = "cancelled"
                                for t in in_progress.values():
                                    if not t.done():
                                        t.cancel()
                                return
                            elif result.status == TaskStatus.FAILED:
                                failed.add(task_id)
                                if config.failure_policy == FailurePolicy.FAIL_FAST:
                                    state.status = OrchestrationStatus.FAILED
                                    state.error = (
                                        f"Task {task_id} failed: {result.error}"
                                    )
                                    state.error_code = "subtask_failed"
                                    for t in in_progress.values():
                                        t.cancel()
                                    return
                            del in_progress[task_id]
                            break
            else:
                # No tasks ready and none in progress — deadlock or done
                if pending:
                    state.status = OrchestrationStatus.FAILED
                    state.error = (
                        "Deadlock: pending tasks with unsatisfied dependencies"
                    )
                    state.error_code = "dependency_deadlock"
                    return
                break

        # All tasks completed
        if not failed:
            state.status = OrchestrationStatus.COMPLETED
        elif config.failure_policy == FailurePolicy.SKIP:
            state.status = OrchestrationStatus.COMPLETED
        else:
            state.status = OrchestrationStatus.FAILED
            state.error_code = "subtask_failed"

    async def _execute_single_task(
        self,
        state: OrchestrationState,
        task: Subtask,
        config: OrchestrationConfig,
        semaphore: asyncio.Semaphore,
        request_id: str | None,
        observer: MultiAgentEventObserver | None,
        cancel_event: asyncio.Event | None,
        context: RequestContext | None,
        api_key: APIKey | None,
        run_source: str = "supervisor",
    ) -> SubtaskResult:
        """Execute a single subtask using AgentRuntime."""
        start_time = time.monotonic()
        result = SubtaskResult(
            task_id=task.id,
            agent_role=task.agent_role,
            started_at=datetime.now(UTC),
        )

        # Get agent config for this role
        agent_config = config.agent_configs.get(task.agent_role)
        if agent_config is None:
            agent_config = _DEFAULT_AGENT_CONFIGS.get(task.agent_role)
        if agent_config is None:
            result.status = TaskStatus.FAILED
            result.error = f"No agent config for role {task.agent_role}"
            result.completed_at = datetime.now(UTC)
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            if observer is not None:
                await observer.on_event(
                    MultiAgentEvent(
                        run_id=state.run_id,
                        kind=MultiAgentEventKind.SUBTASK_FAILED,
                        sequence=0,
                        task_id=task.id,
                        agent_role=task.agent_role.value,
                        duration_ms=result.duration_ms,
                        error_code="subtask_failed",
                    )
                )
            return result

        # Build task input from template
        task_input = task.description
        if task.input_template and task.depends_on:
            prev_results = state.shared_context.get_prev_results(task.depends_on)
            try:
                task_input = task.input_template.format(prev_results=prev_results)
            except KeyError:
                task_input = f"{task.description}\n\nPrevious results:\n{prev_results}"

        # Execute with semaphore for concurrency control
        async with semaphore:
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                if observer is not None:
                    await observer.on_event(
                        MultiAgentEvent(
                            run_id=state.run_id,
                            kind=MultiAgentEventKind.SUBTASK_STARTED,
                            sequence=0,
                            task_id=task.id,
                            agent_role=task.agent_role.value,
                            run_source=run_source,
                        )
                    )
                if (
                    self._agent_service is not None
                    and context is not None
                    and api_key is not None
                ):
                    request = AgentRunRequest(message=task_input)
                    if task.agent_id is not None:
                        request = request.model_copy(update={"agent_id": task.agent_id})
                    else:
                        request = request.model_copy(
                            update={"system_prompt": agent_config.system_prompt}
                        )
                        if agent_config.model is not None:
                            request = request.model_copy(
                                update={"model": agent_config.model}
                            )
                        if agent_config.max_steps is not None:
                            request = request.model_copy(
                                update={"max_steps": agent_config.max_steps}
                            )
                        if agent_config.timeout is not None:
                            request = request.model_copy(
                                update={"timeout_seconds": agent_config.timeout}
                            )
                        if agent_config.token_budget is not None:
                            request = request.model_copy(
                                update={"token_budget": agent_config.token_budget}
                            )
                    runtime_observer: _SubtaskRuntimeEventObserver | None = None
                    if observer is not None:
                        runtime_observer = _SubtaskRuntimeEventObserver(
                            run_id=state.run_id,
                            task_id=task.id,
                            agent_role=task.agent_role,
                            observer=observer,
                            run_source=run_source,
                        )
                    try:
                        outcome = await self._agent_service.run(
                            request,
                            context=context,
                            api_key=api_key,
                            observer=runtime_observer,
                            cancel_event=cancel_event,
                            streaming=runtime_observer is not None,
                        )
                    finally:
                        if runtime_observer is not None:
                            await runtime_observer.drain()
                    result.output = outcome.result.answer or ""
                    result.steps_taken = len(outcome.result.state.steps)
                    result.tool_calls = [
                        call.name
                        for step in outcome.result.state.steps
                        for call in step.decision.tool_calls
                    ]
                    if (
                        outcome.prompt_tokens is not None
                        and outcome.completion_tokens is not None
                    ):
                        result.token_usage = (
                            outcome.prompt_tokens + outcome.completion_tokens
                        )
                    else:
                        result.token_usage = outcome.result.token_usage or 0
                    if outcome.result.status.value == "completed":
                        result.status = TaskStatus.COMPLETED
                    elif outcome.result.status.value == "cancelled":
                        result.status = TaskStatus.CANCELLED
                        result.error = "Subtask cancelled externally"
                    else:
                        result.status = TaskStatus.FAILED
                        result.error = (
                            "Subtask agent ended with "
                            f"{outcome.result.stop_reason.value}"
                        )
                elif self._chat_service is not None:
                    from app.schemas.chat import ChatRequest

                    chat_request = ChatRequest(
                        message=task_input,
                        model=agent_config.model,
                        system_prompt=agent_config.system_prompt,
                        history=[],
                    )
                    response = await self._chat_service.chat(chat_request)
                    result.output = response.message.content
                    result.token_usage = (response.prompt_tokens or 0) + (
                        response.completion_tokens or 0
                    )
                    result.steps_taken = 1
                    result.status = TaskStatus.COMPLETED
                else:
                    # Fallback: placeholder for testing
                    result.output = f"[{task.agent_role}] Completed: {task.description}"
                    result.status = TaskStatus.COMPLETED
                if observer is not None:
                    result.duration_ms = int((time.monotonic() - start_time) * 1000)
                    if result.status == TaskStatus.COMPLETED:
                        await observer.on_event(
                            MultiAgentEvent(
                                run_id=state.run_id,
                                kind=MultiAgentEventKind.SUBTASK_COMPLETED,
                                sequence=0,
                                task_id=task.id,
                                agent_role=task.agent_role.value,
                                duration_ms=result.duration_ms,
                                token_usage=result.token_usage,
                                output_summary=_bounded_summary(result.output),
                                run_source=run_source,
                            )
                        )
                    else:
                        await observer.on_event(
                            MultiAgentEvent(
                                run_id=state.run_id,
                                kind=MultiAgentEventKind.SUBTASK_FAILED,
                                sequence=0,
                                task_id=task.id,
                                agent_role=task.agent_role.value,
                                duration_ms=result.duration_ms,
                                error_code="subtask_failed",
                                run_source=run_source,
                            )
                        )
            except asyncio.CancelledError:
                result.status = TaskStatus.CANCELLED
                result.error = "Task cancelled"
                if observer is not None:
                    result.duration_ms = int((time.monotonic() - start_time) * 1000)
                    await observer.on_event(
                        MultiAgentEvent(
                            run_id=state.run_id,
                            kind=MultiAgentEventKind.SUBTASK_FAILED,
                            sequence=0,
                            task_id=task.id,
                            agent_role=task.agent_role.value,
                            duration_ms=result.duration_ms,
                            error_code="subtask_failed",
                        )
                    )
            except Exception as exc:
                result.status = TaskStatus.FAILED
                result.error = str(exc)
                logger.warning(
                    "multi_agent_task_failed task_id=%s error=%s", task.id, exc
                )
                if observer is not None:
                    result.duration_ms = int((time.monotonic() - start_time) * 1000)
                    await observer.on_event(
                        MultiAgentEvent(
                            run_id=state.run_id,
                            kind=MultiAgentEventKind.SUBTASK_FAILED,
                            sequence=0,
                            task_id=task.id,
                            agent_role=task.agent_role.value,
                            duration_ms=result.duration_ms,
                            error_code="subtask_failed",
                        )
                    )

        result.completed_at = datetime.now(UTC)
        result.duration_ms = int((time.monotonic() - start_time) * 1000)
        return result
