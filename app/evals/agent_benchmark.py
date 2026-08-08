"""Agent Benchmark — real golden-task execution with persisted metrics.

Runs a golden task set through ``AgentService.run`` (the same bounded
runtime used by ``POST /api/v1/agent/runs``) and derives four metrics
from the real run results:

* Tool Call Accuracy — share of tasks whose actual tool call set
  contains every expected tool (empty expectations always match)
* Task Completion Rate — share of tasks that finished COMPLETED
* Average Steps — mean agent steps across **completed** tasks
* Average Latency — mean wall-clock duration across **completed**
  tasks (ms)

Results are persisted to ``agent_benchmark_runs`` scoped by workspace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from app.agent_config.service import AgentDefinitionService
from app.agents.models import RunStatus
from app.audit.service import AuditActor, AuditService
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.evals.benchmark_repository import (
    BenchmarkRunRecord,
    BenchmarkRunRepository,
)
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_CHARS = 200


@dataclass(frozen=True)
class BenchmarkTask:
    """One golden prompt plus the tool calls the agent must make."""

    message: str
    expected_tool_calls: list[str] = field(default_factory=list)


# Built-in golden task sets.  Frozen so tests and callers cannot mutate
# the shared definition; extend by adding a named set here.
GOLDEN_TASKS: Mapping[str, list[BenchmarkTask]] = MappingProxyType(
    {
        "default": [
            BenchmarkTask(
                message="Calculate 2+2",
                expected_tool_calls=["calculator"],
            ),
            BenchmarkTask(
                message="What is the square root of 144?",
                expected_tool_calls=["calculator"],
            ),
            BenchmarkTask(
                message="Search the knowledge base for the latest report",
                expected_tool_calls=["knowledge_search"],
            ),
        ],
    }
)


@dataclass
class TaskOutcome:
    """One task's real execution result (kept in the persisted payload)."""

    message: str
    status: str
    tool_calls: list[str] = field(default_factory=list)
    steps: int = 0
    duration_ms: float = 0.0
    error: str | None = None


class AgentBenchmarkRunner:
    """Evaluate one agent against a golden task set through the real runtime."""

    def __init__(
        self,
        agent_service: AgentService,
        agent_definition_service: AgentDefinitionService,
        run_repository: BenchmarkRunRepository,
        audit: AuditService | None = None,
    ) -> None:
        self._agent_service = agent_service
        self._definition_service = agent_definition_service
        self._run_repository = run_repository
        self._audit = audit

    async def run(
        self,
        agent_id: str,
        task_set: str,
        *,
        workspace_id: str,
        context: RequestContext,
        api_key: APIKey,
        max_steps: int | None = None,
        actor: AuditActor | None = None,
    ) -> BenchmarkRunRecord:
        """Execute every task of the set and persist the aggregated metrics.

        ``max_steps`` is optional: when omitted the agent definition's
        configured step limit is used (identical to production requests);
        pass an explicit value to bound evaluation cost uniformly.
        """
        tasks = GOLDEN_TASKS.get(task_set)
        if tasks is None:
            raise ValueError(f"Unknown task set: {task_set}")
        agent = await self._definition_service.get_agent(
            agent_id, workspace_id=workspace_id
        )
        if agent is None:
            raise ValueError(
                f"Agent {agent_id} not found or not accessible in this workspace."
            )

        outcomes: list[TaskOutcome] = []
        for task in tasks:
            start = time.monotonic()
            try:
                request = AgentRunRequest(
                    message=task.message,
                    agent_id=agent_id,
                    timeout_seconds=60.0,
                )
                if max_steps is not None:
                    request = request.model_copy(update={"max_steps": max_steps})
                result = await self._agent_service.run(
                    request,
                    context=context,
                    api_key=api_key,
                )
                outcome = TaskOutcome(
                    message=task.message,
                    status=result.result.status.value,
                    tool_calls=[
                        call.name
                        for step in result.result.state.steps
                        for call in step.decision.tool_calls
                    ],
                    steps=len(result.result.state.steps),
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            except Exception as exc:  # task-level failure must not abort the set
                outcome = TaskOutcome(
                    message=task.message,
                    status="error",
                    duration_ms=(time.monotonic() - start) * 1000,
                    error=_bounded_error(exc),
                )
            outcomes.append(outcome)

        record = _aggregate(agent_id, workspace_id, task_set, tasks, outcomes)
        saved = await self._run_repository.save(record)
        if self._audit is not None and actor is not None:
            await self._audit.record(
                action="benchmark.execute",
                resource_type="benchmark",
                resource_id=agent_id,
                actor=actor,
                after={
                    "task_set": task_set,
                    "task_count": saved.task_count,
                    "completed_count": saved.completed_count,
                    "tool_call_accuracy": saved.tool_call_accuracy,
                },
            )
        logger.info(
            "benchmark_run agent_id=%s task_set=%s task_count=%d completed=%d",
            agent_id,
            task_set,
            saved.task_count,
            saved.completed_count,
        )
        return saved

    async def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[BenchmarkRunRecord]:
        """Return persisted runs scoped to one workspace."""
        return await self._run_repository.list_runs(
            workspace_id, agent_id=agent_id, limit=limit
        )


def _aggregate(
    agent_id: str,
    workspace_id: str,
    task_set: str,
    tasks: list[BenchmarkTask],
    outcomes: list[TaskOutcome],
) -> BenchmarkRunRecord:
    """Derive the four metrics from real per-task outcomes.

    Step/latency averages only count completed tasks so early failures
    cannot drag the means toward zero; tool call accuracy spans all
    tasks (failed tasks simply recorded no tool calls).
    """
    task_count = len(tasks)
    completed_outcomes = [
        outcome for outcome in outcomes if outcome.status == RunStatus.COMPLETED.value
    ]
    completed_count = len(completed_outcomes)
    matched_count = sum(
        1
        for task, outcome in zip(tasks, outcomes, strict=True)
        if set(task.expected_tool_calls).issubset(set(outcome.tool_calls))
    )
    average_steps = (
        sum(outcome.steps for outcome in completed_outcomes) / completed_count
        if completed_outcomes
        else None
    )
    average_latency_ms = (
        sum(outcome.duration_ms for outcome in completed_outcomes) / completed_count
        if completed_outcomes
        else None
    )
    return BenchmarkRunRecord(
        agent_id=agent_id,
        workspace_id=workspace_id,
        task_set=task_set,
        tool_call_accuracy=matched_count / task_count if task_count else None,
        task_completion_rate=completed_count / task_count if task_count else None,
        average_steps=average_steps,
        average_latency_ms=average_latency_ms,
        task_count=task_count,
        completed_count=completed_count,
        metric_payload={
            "task_outcomes": [outcome.__dict__ for outcome in outcomes],
        },
    )


def _bounded_error(exc: Exception) -> str:
    """Persist a bounded, type-prefixed error summary instead of raw text."""
    message = str(exc)
    if len(message) > _MAX_ERROR_MESSAGE_CHARS:
        message = message[:_MAX_ERROR_MESSAGE_CHARS] + "..."
    return f"{type(exc).__name__}: {message}"
