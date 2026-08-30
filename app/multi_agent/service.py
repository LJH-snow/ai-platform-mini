"""Multi-agent orchestration service.

Provides the application boundary for multi-agent runs: decompose user task
via Supervisor, execute via Orchestrator, and return structured results.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from app.multi_agent.models import (
    OrchestrationConfig,
    OrchestrationResult,
    OrchestrationStatus,
)
from app.multi_agent.orchestrator import Orchestrator
from app.multi_agent.supervisor import Supervisor

if TYPE_CHECKING:
    from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)


class MultiAgentService:
    """Application boundary for multi-agent orchestration."""

    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service
        self._supervisor = Supervisor(chat_service)
        self._orchestrator = Orchestrator(chat_service=chat_service)

    async def run(
        self,
        user_input: str,
        *,
        config: OrchestrationConfig | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
        supervisor_model: str | None = None,
        max_subtasks: int = 5,
    ) -> OrchestrationResult:
        """Execute a multi-agent run: decompose then orchestrate."""
        config = config or OrchestrationConfig()
        resolved_run_id = run_id or uuid.uuid4().hex

        logger.info(
            "multi_agent_run_started run_id=%s input_len=%d",
            resolved_run_id,
            len(user_input),
        )

        # Step 1: Supervisor decomposes the task
        try:
            decision = await self._supervisor.decompose(
                user_input,
                model=supervisor_model or config.supervisor_model,
                max_subtasks=max_subtasks,
            )
        except Exception as exc:
            logger.error("supervisor_decompose_failed error=%s", exc)
            return OrchestrationResult(
                run_id=resolved_run_id,
                status=OrchestrationStatus.FAILED,
                error=f"Supervisor decomposition failed: {exc}",
            )

        logger.info(
            "supervisor_decomposed run_id=%s subtasks=%d reasoning=%s",
            resolved_run_id,
            len(decision.subtasks),
            decision.reasoning[:100] if decision.reasoning else "",
        )

        # Step 2: Orchestrator executes the subtasks
        result = await self._orchestrator.execute(
            decision=decision,
            user_input=user_input,
            config=config,
            run_id=resolved_run_id,
            request_id=request_id,
        )

        logger.info(
            "multi_agent_run_completed run_id=%s status=%s tokens=%d duration_ms=%s",
            result.run_id,
            result.status,
            result.total_token_usage,
            result.duration_ms,
        )

        return result
