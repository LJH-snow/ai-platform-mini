"""Single-agent vs multi-agent comparison for M4 agentic evaluation.

Runs a small deterministic golden task set through the real ``AgentService``
(single-agent baseline) and ``MultiAgentService`` (research/writer workflow),
then persists the outcomes in the existing benchmark repository so the
comparison is tenant-scoped and replayable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.agent_config.service import AgentDefinitionService
from app.agents.models import RunStatus
from app.auth.models import APIKey
from app.core.context import RequestContext
from app.evals.agent_benchmark import _bounded_error
from app.evals.benchmark_repository import (
    BenchmarkRunRecord,
    BenchmarkRunRepository,
)
from app.multi_agent.models import FailurePolicy, OrchestrationConfig
from app.multi_agent.service import MultiAgentService
from app.schemas.agent import AgentRunRequest
from app.services.agent_service import AgentService

logger = logging.getLogger(__name__)

_TASK_SET = "multi_agent_compare"


@dataclass(frozen=True)
class CompareTask:
    """One golden prompt used in both single-agent and multi-agent lanes."""

    message: str
    expected_tool_calls: tuple[str, ...] = ()


GOLDEN_COMPARE_TASKS: tuple[CompareTask, ...] = (
    CompareTask(
        "Calculate 2+2 and 10*4, then summarize the results.",
        ("calculator",),
    ),
    CompareTask(
        (
            "Search the knowledge base for the project overview and "
            "produce a short report."
        ),
        ("knowledge_search",),
    ),
    CompareTask(
        (
            "The knowledge base is empty; produce a report that "
            "honestly reports no sources."
        ),
        (),
    ),
)


@dataclass
class CaseOutcome:
    """One lane's result for one task; safe to persist in metric_payload."""

    message: str
    status: str
    tool_calls: list[str] = field(default_factory=list)
    steps: int = 0
    token_usage: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    subtask_count: int = 0


class MultiAgentCompareRunner:
    """Persist a single-agent vs multi-agent comparison for one agent."""

    def __init__(
        self,
        agent_service: AgentService,
        multi_agent_service: MultiAgentService,
        agent_definition_service: AgentDefinitionService,
        run_repository: BenchmarkRunRepository,
    ) -> None:
        self._agent_service = agent_service
        self._multi_agent_service = multi_agent_service
        self._definition_service = agent_definition_service
        self._repository = run_repository

    async def run(
        self,
        agent_id: str,
        *,
        workspace_id: str,
        context: RequestContext,
        api_key: APIKey,
        max_steps: int | None = None,
    ) -> BenchmarkRunRecord:
        """Run the golden compare set and persist aggregated metrics."""
        agent = await self._definition_service.get_agent(
            agent_id, workspace_id=workspace_id
        )
        if agent is None:
            raise ValueError(
                f"Agent {agent_id} not found or not accessible in this workspace."
            )

        single: list[CaseOutcome] = []
        multi: list[CaseOutcome] = []
        for task in GOLDEN_COMPARE_TASKS:
            single.append(
                await self._run_single(
                    agent_id,
                    task,
                    max_steps=max_steps,
                    context=context,
                    api_key=api_key,
                )
            )
            multi.append(await self._run_multi(task, context=context, api_key=api_key))

        single_completed = sum(o.status == RunStatus.COMPLETED.value for o in single)
        multi_completed = sum(o.status == "completed" for o in multi)
        single_tool_accuracy = _tool_accuracy(single)
        multi_tool_accuracy = _tool_accuracy(multi)
        record = BenchmarkRunRecord(
            agent_id=agent_id,
            workspace_id=workspace_id,
            task_set=_TASK_SET,
            tool_call_accuracy=single_tool_accuracy,
            task_completion_rate=(multi_completed / len(multi) if multi else None),
            average_steps=None,
            average_latency_ms=None,
            task_count=len(multi),
            completed_count=multi_completed,
            metric_payload={
                "mode": "single_vs_multi",
                "single": [o.__dict__ for o in single],
                "multi": [o.__dict__ for o in multi],
                "judgement": {
                    "single_completed": single_completed,
                    "multi_completed": multi_completed,
                    "single_task_count": len(single),
                    "multi_task_count": len(multi),
                    "single_tool_call_accuracy": single_tool_accuracy,
                    "multi_tool_call_accuracy": multi_tool_accuracy,
                    "multi_tokens_below_plus_40_percent": (
                        _is_within_token_budget(single, multi)
                    ),
                },
            },
        )
        return await self._repository.save(record)

    async def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[BenchmarkRunRecord]:
        return await self._repository.list_runs(
            workspace_id,
            agent_id=agent_id,
            limit=limit,
        )

    async def _run_single(
        self,
        agent_id: str,
        task: CompareTask,
        *,
        max_steps: int | None,
        context: RequestContext,
        api_key: APIKey,
    ) -> CaseOutcome:
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
            return CaseOutcome(
                message=task.message,
                status=result.result.status.value,
                tool_calls=[
                    call.name
                    for step in result.result.state.steps
                    for call in step.decision.tool_calls
                ],
                steps=len(result.result.state.steps),
                token_usage=result.result.token_usage or 0,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            return CaseOutcome(
                message=task.message,
                status="error",
                duration_ms=(time.monotonic() - start) * 1000,
                error=_bounded_error(exc),
            )

    async def _run_multi(
        self,
        task: CompareTask,
        *,
        context: RequestContext,
        api_key: APIKey,
    ) -> CaseOutcome:
        start = time.monotonic()
        try:
            result = await self._multi_agent_service.run(
                user_input=task.message,
                config=OrchestrationConfig(
                    max_concurrency=3,
                    failure_policy=FailurePolicy.SKIP,
                    total_timeout=120.0,
                ),
                context=context,
                api_key=api_key,
            )
            return CaseOutcome(
                message=task.message,
                status=result.status.value,
                tool_calls=[
                    call
                    for subtask in result.subtask_results
                    for call in subtask.tool_calls
                ],
                steps=len(result.subtask_results),
                token_usage=result.total_token_usage or 0,
                duration_ms=(time.monotonic() - start) * 1000,
                subtask_count=len(result.subtask_results),
            )
        except Exception as exc:
            return CaseOutcome(
                message=task.message,
                status="error",
                duration_ms=(time.monotonic() - start) * 1000,
                error=_bounded_error(exc),
            )


def _tool_accuracy(outcomes: list[CaseOutcome]) -> float | None:
    if not outcomes:
        return None
    matched = sum(
        1
        for task, outcome in zip(GOLDEN_COMPARE_TASKS, outcomes, strict=True)
        if task.expected_tool_calls
        and set(task.expected_tool_calls).issubset(set(outcome.tool_calls))
    )
    total_expecting = sum(
        bool(task.expected_tool_calls) for task in GOLDEN_COMPARE_TASKS
    )
    return matched / total_expecting if total_expecting else None


def _is_within_token_budget(
    single: list[CaseOutcome],
    multi: list[CaseOutcome],
    threshold: float = 0.4,
) -> bool:
    single_total = sum(o.token_usage for o in single)
    multi_total = sum(o.token_usage for o in multi)
    if single_total == 0:
        return True
    return (multi_total - single_total) / single_total <= threshold
