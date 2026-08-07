"""Agent Benchmark — real golden-task execution with persisted metrics.

Runs a golden task set through ``AgentService.run`` (the same bounded
runtime used by ``POST /api/v1/agent/runs``) and derives four metrics
from the real run results:

* Tool Call Accuracy — share of tasks whose actual tool call set
  contains every expected tool (empty expectations always match)
* Task Completion Rate — share of tasks that finished COMPLETED
* Average Steps — mean agent steps across tasks
* Average Latency — mean wall-clock duration across tasks (ms)

Results are persisted to ``agent_benchmark_runs`` scoped by workspace.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.agent_config.service import AgentDefinitionService
from app.agents.models import RunStatus
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.evals.benchmark_repository import (
    BenchmarkRunRecord,
    BenchmarkRunRepository,
)
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BenchmarkTask:
    """One golden prompt plus the tool calls the agent must make."""

    message: str
    expected_tool_calls: list[str] = field(default_factory=list)


# Built-in golden task sets.  Extendable via JSON files in a later
# sprint; the default set exercises both built-in tools.
GOLDEN_TASKS: dict[str, list[BenchmarkTask]] = {
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
    ) -> None:
        self._agent_service = agent_service
        self._definition_service = agent_definition_service
        self._run_repository = run_repository

    async def run(
        self,
        agent_id: str,
        task_set: str,
        *,
        workspace_id: str,
        context: RequestContext,
        api_key: APIKey,
    ) -> BenchmarkRunRecord:
        """Execute every task of the set and persist the aggregated metrics."""
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
                result = await self._agent_service.run(
                    AgentRunRequest(
                        message=task.message,
                        agent_id=agent_id,
                        max_steps=5,
                        timeout_seconds=60.0,
                    ),
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
                    error=str(exc),
                )
            outcomes.append(outcome)

        record = _aggregate(agent_id, workspace_id, task_set, tasks, outcomes)
        saved = await self._run_repository.save(record)
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
    """Derive the four metrics from real per-task outcomes."""
    task_count = len(tasks)
    completed_count = sum(
        1 for outcome in outcomes if outcome.status == RunStatus.COMPLETED.value
    )
    matched_count = sum(
        1
        for task, outcome in zip(tasks, outcomes, strict=True)
        if set(task.expected_tool_calls).issubset(set(outcome.tool_calls))
    )
    step_counts = [outcome.steps for outcome in outcomes]
    durations = [outcome.duration_ms for outcome in outcomes]
    average_steps = sum(step_counts) / len(step_counts) if step_counts else 0.0
    average_latency_ms = sum(durations) / len(durations) if durations else 0.0
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
