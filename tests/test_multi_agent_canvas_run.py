"""Tests for running multi-agent from a canvas config (skips Supervisor)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.multi_agent.models import (
    OrchestrationStatus,
)
from app.multi_agent.service import MultiAgentService


def _chat_service() -> object:
    return SimpleNamespace(
        chat=AsyncMock(side_effect=AssertionError("Supervisor should not be called"))
    )


def _agent_service() -> object:
    return SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                result=SimpleNamespace(
                    answer="done",
                    status=SimpleNamespace(value="completed"),
                    state=SimpleNamespace(steps=[]),
                    stop_reason=SimpleNamespace(value="end"),
                    token_usage=10,
                ),
                prompt_tokens=5,
                completion_tokens=5,
            )
        )
    )


def _config_service(config_id: str, dag_json: dict) -> object:
    return SimpleNamespace(
        get_config=AsyncMock(
            return_value=SimpleNamespace(
                id=config_id,
                workspace_id="ws-1",
                dag_json=dag_json,
                orchestration_config={},
            )
        )
    )


def _identity(workspace_id: str = "ws-1") -> object:
    return SimpleNamespace(
        workspace_id=workspace_id, user_id="user-1", api_key_hash="k"
    )


def _context(workspace_id: str = "ws-1") -> object:
    return SimpleNamespace(
        identity=_identity(workspace_id),
        request_id="req-1",
    )


def _api_key() -> object:
    return SimpleNamespace(key="k", name="k")


_SAMPLE_DAG = {
    "nodes": [
        {"id": "task_1", "role": "research", "description": "Search"},
        {"id": "task_2", "role": "writer", "description": "Write"},
    ],
    "edges": [{"id": "e1", "source": "task_1", "target": "task_2"}],
}


class _CaptureObserver:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def on_event(self, event: object) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_run_from_config_skips_supervisor() -> None:
    """When config_id is given, Supervisor.decompose must not be called."""
    service = MultiAgentService(
        chat_service=_chat_service(),  # type: ignore[arg-type]
        agent_service=_agent_service(),  # type: ignore[arg-type]
        config_service=_config_service("cfg-1", _SAMPLE_DAG),  # type: ignore[arg-type]
    )
    result = await service.run(
        "user input",
        config_id="cfg-1",
        context=_context(),  # type: ignore[arg-type]
        api_key=_api_key(),  # type: ignore[arg-type]
    )
    assert result.status == OrchestrationStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_from_config_emits_config_source() -> None:
    """Events emitted during a config run should carry run_source='config'."""
    observer = _CaptureObserver()

    service = MultiAgentService(
        chat_service=_chat_service(),  # type: ignore[arg-type]
        agent_service=_agent_service(),  # type: ignore[arg-type]
        config_service=_config_service("cfg-2", _SAMPLE_DAG),  # type: ignore[arg-type]
    )
    await service.run(
        "user input",
        config_id="cfg-2",
        observer=observer,
        context=_context(),  # type: ignore[arg-type]
        api_key=_api_key(),  # type: ignore[arg-type]
    )
    assert len(observer.events) > 0
    assert all(getattr(e, "run_source", None) == "config" for e in observer.events)


@pytest.mark.asyncio
async def test_run_from_config_not_found() -> None:
    """If config_id does not exist, run fails with config_not_found."""
    service = MultiAgentService(
        chat_service=_chat_service(),  # type: ignore[arg-type]
        agent_service=_agent_service(),  # type: ignore[arg-type]
        config_service=SimpleNamespace(get_config=AsyncMock(return_value=None)),  # type: ignore[arg-type]
    )
    result = await service.run(
        "user input",
        config_id="missing",
        context=_context(),  # type: ignore[arg-type]
        api_key=_api_key(),  # type: ignore[arg-type]
    )
    assert result.status == OrchestrationStatus.FAILED
    assert result.error_code == "config_not_found"


@pytest.mark.asyncio
async def test_run_without_config_uses_supervisor() -> None:
    """Without config_id, Supervisor decomposition is used (run_source=supervisor)."""
    observer = _CaptureObserver()
    subtask_json = (
        '{"reasoning": "test", "subtasks": '
        '[{"id": "t1", "description": "d", "agent_role": "writer", "depends_on": []}]}'
    )
    chat = AsyncMock(
        return_value=SimpleNamespace(
            message=SimpleNamespace(content=subtask_json),
            prompt_tokens=1,
            completion_tokens=1,
        )
    )
    chat_service = SimpleNamespace(chat=chat)
    agent_service = _agent_service()

    service = MultiAgentService(
        chat_service=chat_service,  # type: ignore[arg-type]
        agent_service=agent_service,  # type: ignore[arg-type]
    )
    await service.run(
        "user input",
        observer=observer,
        context=_context(),  # type: ignore[arg-type]
        api_key=_api_key(),  # type: ignore[arg-type]
    )
    assert chat.await_count == 1
    assert all(getattr(e, "run_source", None) == "supervisor" for e in observer.events)
