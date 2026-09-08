"""Multi-agent orchestration service.

Provides the application boundary for multi-agent runs: decompose user task
via Supervisor, execute via Orchestrator, and return structured results.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from app.multi_agent.decision_factory import DecisionFactoryError, from_dag
from app.multi_agent.events import (
    BestEffortObserver,
    ComposedObserver,
    MultiAgentEvent,
    MultiAgentEventKind,
    MultiAgentEventObserver,
    SequencedObserver,
    SubtaskResultSummary,
    SubtaskSummary,
    _bounded_result,
    _bounded_summary,
)
from app.multi_agent.models import (
    OrchestrationConfig,
    OrchestrationResult,
    OrchestrationStatus,
    SupervisorDecision,
    TaskStatus,
)
from app.multi_agent.orchestrator import Orchestrator
from app.multi_agent.supervisor import Supervisor

if TYPE_CHECKING:
    from app.auth.models import APIKey
    from app.core.context import RequestContext
    from app.services.agent_service import AgentService
    from app.services.chat_service import ChatService
    from app.services.multi_agent_config_service import MultiAgentConfigService

logger = logging.getLogger(__name__)

_TERMINAL_KIND_BY_STATUS: dict[OrchestrationStatus, MultiAgentEventKind] = {
    OrchestrationStatus.COMPLETED: MultiAgentEventKind.RUN_COMPLETED,
    OrchestrationStatus.FAILED: MultiAgentEventKind.RUN_FAILED,
    OrchestrationStatus.CANCELLED: MultiAgentEventKind.RUN_CANCELLED,
    OrchestrationStatus.TIMED_OUT: MultiAgentEventKind.RUN_TIMED_OUT,
    OrchestrationStatus.BUDGET_EXCEEDED: MultiAgentEventKind.RUN_BUDGET_EXCEEDED,
}


class MultiAgentService:
    """Application boundary for multi-agent orchestration."""

    def __init__(
        self,
        chat_service: ChatService,
        agent_service: AgentService | None = None,
        config_service: MultiAgentConfigService | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._agent_service = agent_service
        self._supervisor = Supervisor(chat_service)
        self._orchestrator = Orchestrator(
            chat_service=chat_service,
            agent_service=agent_service,
        )
        self._config_service = config_service

    async def run(
        self,
        user_input: str,
        *,
        config: OrchestrationConfig | None = None,
        config_id: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        supervisor_model: str | None = None,
        max_subtasks: int = 5,
        observer: MultiAgentEventObserver | None = None,
        event_recorder: MultiAgentEventObserver | None = None,
        cancel_event: asyncio.Event | None = None,
        context: RequestContext | None = None,
        api_key: APIKey | None = None,
    ) -> OrchestrationResult:
        """Execute a multi-agent run: decompose then orchestrate."""
        config = config or OrchestrationConfig()
        resolved_run_id = run_id or uuid.uuid4().hex
        run_source = "config" if config_id else "supervisor"

        logger.info(
            "multi_agent_run_started run_id=%s input_len=%d source=%s",
            resolved_run_id,
            len(user_input),
            run_source,
        )

        # Best-effort event emission so neither SSE nor persistence can break
        # the run.  Both consumers share one sequence stamp.
        sinks = tuple(sink for sink in (observer, event_recorder) if sink is not None)
        sink = BestEffortObserver(ComposedObserver(sinks)) if sinks else None
        sequenced = SequencedObserver(sink) if sink is not None else None

        async def emit(event: MultiAgentEvent) -> None:
            if sequenced is not None:
                await sequenced.on_event(event)

        await emit(
            MultiAgentEvent(
                run_id=resolved_run_id,
                kind=MultiAgentEventKind.RUN_STARTED,
                sequence=0,
                run_source=run_source,
            )
        )

        # Step 1: Obtain task decomposition — from canvas config or Supervisor
        if config_id is not None:
            decision = await self._load_decision_from_config(
                config_id,
                run_id=resolved_run_id,
                context=context,
            )
            if decision is None:
                return OrchestrationResult(
                    run_id=resolved_run_id,
                    status=OrchestrationStatus.FAILED,
                    error=f"Config {config_id} not found or invalid",
                    error_code="config_not_found",
                )
        else:
            decision = await self._decompose_via_supervisor(
                user_input,
                supervisor_model=supervisor_model,
                config=config,
                max_subtasks=max_subtasks,
                run_id=resolved_run_id,
                emit=emit,
            )

        logger.info(
            "supervisor_decomposed run_id=%s subtasks=%d reasoning=%s",
            resolved_run_id,
            len(decision.subtasks),
            decision.reasoning[:100] if decision.reasoning else "",
        )

        await emit(
            MultiAgentEvent(
                run_id=resolved_run_id,
                kind=MultiAgentEventKind.SUBTASKS_PLANNED,
                sequence=0,
                run_source=run_source,
                reasoning=(
                    _bounded_summary(decision.reasoning) if decision.reasoning else None
                ),
                subtasks=tuple(
                    SubtaskSummary(
                        id=subtask.id,
                        agent_role=subtask.agent_role.value,
                        agent_id=subtask.agent_id,
                        description=_bounded_summary(subtask.description),
                        depends_on=subtask.depends_on,
                    )
                    for subtask in decision.subtasks
                ),
            )
        )

        # Step 2: Orchestrator executes the subtasks
        result = await self._orchestrator.execute(
            decision=decision,
            user_input=user_input,
            config=config,
            run_id=resolved_run_id,
            request_id=request_id,
            observer=sequenced,
            cancel_event=cancel_event,
            context=context,
            api_key=api_key,
            run_source=run_source,
        )

        logger.info(
            "multi_agent_run_completed run_id=%s status=%s tokens=%d duration_ms=%s",
            result.run_id,
            result.status,
            result.total_token_usage,
            result.duration_ms,
        )

        # Emit the run-level terminal event matching the final status.
        terminal_kind = _TERMINAL_KIND_BY_STATUS.get(result.status)
        if terminal_kind is not None:
            await emit(
                MultiAgentEvent(
                    run_id=resolved_run_id,
                    kind=terminal_kind,
                    sequence=0,
                    run_source=run_source,
                    error_code=result.error_code,
                    final_output=(
                        _bounded_result(result.final_output)
                        if result.final_output
                        else None
                    ),
                    total_token_usage=result.total_token_usage,
                    duration_ms=result.duration_ms,
                    subtask_results=tuple(
                        SubtaskResultSummary(
                            task_id=r.task_id,
                            status=r.status.value,
                            agent_role=r.agent_role.value,
                            output=_bounded_summary(r.output) if r.output else "",
                            error_code=(
                                "subtask_failed"
                                if r.status is TaskStatus.FAILED
                                else None
                            ),
                            token_usage=r.token_usage,
                            duration_ms=r.duration_ms,
                        )
                        for r in result.subtask_results
                    ),
                )
            )

        return result

    async def _decompose_via_supervisor(
        self,
        user_input: str,
        *,
        supervisor_model: str | None,
        config: OrchestrationConfig,
        max_subtasks: int,
        run_id: str,
        emit: object,
    ) -> SupervisorDecision:
        """Decompose the user task via the Supervisor LLM."""
        return await self._supervisor.decompose(
            user_input,
            model=supervisor_model or config.supervisor_model,
            max_subtasks=max_subtasks,
        )

    async def _load_decision_from_config(
        self,
        config_id: str,
        *,
        run_id: str,
        context: RequestContext | None,
    ) -> SupervisorDecision | None:
        """Load a canvas config and convert it to a SupervisorDecision."""
        if self._config_service is None:
            logger.error("multi_agent_config_service_not_available run_id=%s", run_id)
            return None
        owner_scope = (
            context.identity.workspace_id if context and context.identity else None
        )
        cfg = await self._config_service.get_config(
            config_id,
            owner_scope=owner_scope,
        )
        if cfg is None:
            logger.error("config_not_found config_id=%s run_id=%s", config_id, run_id)
            return None
        try:
            return from_dag(cfg.dag_json)
        except DecisionFactoryError as exc:
            logger.error("config_dag_invalid error=%s run_id=%s", exc, run_id)
            return None
