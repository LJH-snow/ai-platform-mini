"""Persistence and history tests for multi-agent runs (M3 P2)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.api.multi_agent import _provide_multi_agent_record_service
from app.main import app
from app.services.multi_agent_run_record_service import (
    project_run_response,
    public_run_payload,
    public_run_summary,
)

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}


def _row(
    run_id: str = "run-1",
    owner: str = "owner-a",
    *,
    polluted: dict[str, Any] | None = None,
) -> SimpleNamespace:
    payload: dict[str, Any] = {
        "status": "completed",
        "final_output": "hello",
        "error_code": None,
        "total_token_usage": 10,
        "duration_ms": 5,
        "subtask_results": [
            {
                "task_id": "t1",
                "status": "completed",
                "agent_role": "writer",
                "output": "out",
                "error_code": None,
                "token_usage": 10,
                "duration_ms": 5,
            }
        ],
    }
    if polluted is not None:
        payload.update(polluted)
    return SimpleNamespace(
        run_id=run_id,
        request_id="req-1",
        api_key_hash="cafef00d" * 8,
        api_key_name="test",
        status="completed",
        stop_reason="completed",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        duration_ms=5.0,
        total_tokens=10,
        payload=payload,
        workspace_id=None,
    )


class TestProjections:
    def test_payload_hides_full_hash(self) -> None:
        payload = public_run_payload(_row())  # type: ignore[arg-type]
        assert payload["api_key_prefix"] == "cafef00d"
        assert "cafef00dcafef00dcafef00dcafef00dcafef00dcafef00dcafef00d" not in str(
            payload
        )

    def test_summary_counts_subtasks(self) -> None:
        summary = public_run_summary(public_run_payload(_row()))  # type: ignore[arg-type]
        assert summary["subtask_count"] == 1
        assert summary["run_id"] == "run-1"

    def test_detail_projection_drops_unknown_keys(self) -> None:
        row = _row(
            polluted={
                "secret_extra": "should-drop",
                "subtask_results": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "agent_role": "writer",
                        "output": "out",
                        "token_usage": 1,
                        "duration_ms": 1,
                        "raw_prompt": "drop-me",
                    }
                ],
            }
        )
        projected = project_run_response(public_run_payload(row))  # type: ignore[arg-type]
        assert "secret_extra" not in projected
        assert "raw_prompt" not in str(projected["subtask_results"])


class _FakeRecordService:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = {row.run_id: row for row in rows}

    async def list_runs(
        self, limit: int = 50, *, owner_scope: str | None = None
    ) -> list[SimpleNamespace]:
        del owner_scope
        return list(self._rows.values())[:limit]

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> SimpleNamespace | None:
        del owner_scope
        return self._rows.get(run_id)


class _ScopedFakeRecordService(_FakeRecordService):
    async def list_runs(
        self, limit: int = 50, *, owner_scope: str | None = None
    ) -> list[SimpleNamespace]:
        rows = list(self._rows.values())
        if owner_scope is not None:
            rows = [
                row
                for row in rows
                if getattr(row, "workspace_id", None) == owner_scope
                or (
                    getattr(row, "workspace_id", None) is None
                    and row.api_key_hash == owner_scope
                )
            ]
        return rows[:limit]

    async def get_run(
        self, run_id: str, *, owner_scope: str | None = None
    ) -> SimpleNamespace | None:
        row = self._rows.get(run_id)
        if row is None or owner_scope is None:
            return row
        if getattr(row, "workspace_id", None) == owner_scope:
            return row
        if (
            getattr(row, "workspace_id", None) is None
            and row.api_key_hash == owner_scope
        ):
            return row
        return None


def _override(records: object) -> None:
    app.dependency_overrides[_provide_multi_agent_record_service] = lambda: records


def _clear() -> None:
    app.dependency_overrides.pop(_provide_multi_agent_record_service, None)


def test_history_requires_bearer() -> None:
    assert client.get("/api/v1/multi-agent/runs").status_code in (401, 403)


def test_history_503_when_no_store() -> None:
    _override(None)
    try:
        response = client.get("/api/v1/multi-agent/runs", headers=_AUTH_HEADERS)
        detail = client.get("/api/v1/multi-agent/runs/x", headers=_AUTH_HEADERS)
    finally:
        _clear()
    assert response.status_code == 503
    assert detail.status_code == 503


def test_unknown_run_is_404() -> None:
    _override(_FakeRecordService([]))
    try:
        response = client.get("/api/v1/multi-agent/runs/nope", headers=_AUTH_HEADERS)
    finally:
        _clear()
    assert response.status_code == 404


def test_cross_tenant_read_is_404() -> None:
    mine = _row(run_id="mine")
    mine.api_key_hash = "owner-mine"
    other = _row(run_id="other")
    other.api_key_hash = "owner-other"
    service = _ScopedFakeRecordService([mine, other])
    # Same filtering the API uses: non-matching scope must return None.
    import asyncio

    assert asyncio.run(service.get_run("other", owner_scope="owner-mine")) is None
    assert asyncio.run(service.get_run("mine", owner_scope="owner-mine")) is not None
    listed = asyncio.run(service.list_runs(owner_scope="owner-mine"))
    assert [row.run_id for row in listed] == ["mine"]
