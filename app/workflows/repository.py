"""Repository boundary for workflow run metadata."""

from __future__ import annotations

from typing import Protocol

from app.workflows.models import WorkflowRun, WorkflowRunStage, WorkflowRunStatus


class WorkflowRunRepository(Protocol):
    async def create(self, run: WorkflowRun) -> WorkflowRun: ...

    async def update(self, run: WorkflowRun) -> WorkflowRun | None: ...

    async def get(self, thread_id: str, owner_key_hash: str) -> WorkflowRun | None: ...

    async def update_status_if(
        self,
        thread_id: str,
        owner_key_hash: str,
        *,
        expected_status: WorkflowRunStatus,
        new_status: WorkflowRunStatus,
        new_stage: WorkflowRunStage,
    ) -> WorkflowRun | None: ...
