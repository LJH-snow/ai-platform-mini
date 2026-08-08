"""In-memory workflow run repository for tests and local development."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.workflows.models import WorkflowRun, WorkflowRunStage, WorkflowRunStatus


class InMemoryWorkflowRunRepository:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], WorkflowRun] = {}

    async def create(self, run: WorkflowRun) -> WorkflowRun:
        now = datetime.now(UTC)
        saved = replace(run, created_at=now, updated_at=now)
        self._runs[(run.thread_id, run.owner_key_hash)] = saved
        return saved

    async def update(self, run: WorkflowRun) -> WorkflowRun | None:
        key = (run.thread_id, run.owner_key_hash)
        if key not in self._runs:
            return None
        saved = replace(run, updated_at=datetime.now(UTC))
        self._runs[key] = saved
        return saved

    async def get(self, thread_id: str, owner_key_hash: str) -> WorkflowRun | None:
        return self._runs.get((thread_id, owner_key_hash))

    async def list_by_owner(
        self, owner_key_hash: str, *, limit: int = 20
    ) -> list[WorkflowRun]:
        runs = [
            run for (_, owner), run in self._runs.items() if owner == owner_key_hash
        ]
        runs.sort(key=lambda run: run.created_at or datetime.min, reverse=True)
        return runs[:limit]

    async def update_status_if(
        self,
        thread_id: str,
        owner_key_hash: str,
        *,
        expected_status: WorkflowRunStatus,
        new_status: WorkflowRunStatus,
        new_stage: WorkflowRunStage,
    ) -> WorkflowRun | None:
        key = (thread_id, owner_key_hash)
        existing = self._runs.get(key)
        if existing is None or existing.status != expected_status:
            return None
        updated = replace(
            existing,
            status=new_status,
            stage=new_stage,
            updated_at=datetime.now(UTC),
        )
        self._runs[key] = updated
        return updated
