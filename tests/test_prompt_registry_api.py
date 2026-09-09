"""Prompt registry API tests for workspace-scoped listing."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.auth import _clear_auth_service_caches
from app.api.prompts import PromptSummaryResponse
from app.auth.dependencies import provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.container import provide_prompt_registry
from app.main import app
from app.prompts.repository import InMemoryPromptRepository
from app.prompts.service import PromptRegistryService

_WORKSPACE_KEY = "sk-prompt-ws-test"
_EMPTY_WORKSPACE_KEY = "sk-prompt-empty-test"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides.clear()
    _clear_auth_service_caches()
    key_records = [
        APIKeyRecord(
            key_hash=hash_api_key(_WORKSPACE_KEY),
            name="prompt-ws",
            status="active",
            user_id="u-ws",
            workspace_id="ws-1",
        ),
        APIKeyRecord(
            key_hash=hash_api_key(_EMPTY_WORKSPACE_KEY),
            name="prompt-empty",
            status="active",
            user_id="u-empty",
            workspace_id="ws-2",
        ),
    ]
    app.dependency_overrides[provide_api_key_service] = lambda: APIKeyService(
        repository=InMemoryAPIKeyRepository(key_records)
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
    _clear_auth_service_caches()


def _use_prompt_registry(service: PromptRegistryService) -> None:
    app.dependency_overrides[provide_prompt_registry] = lambda: service


def _registry() -> PromptRegistryService:
    return PromptRegistryService(repository=InMemoryPromptRepository())


def _auth_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _summary_by_name(response: object) -> dict[str, PromptSummaryResponse]:
    assert isinstance(response, list)
    result: dict[str, PromptSummaryResponse] = {}
    for item in response:
        summary = PromptSummaryResponse.model_validate(item)
        result[summary.name] = summary
    return result


async def test_list_includes_active_template(client: TestClient) -> None:
    registry = _registry()
    await registry.seed(name="tpl_active", content="global v1", workspace_id=None)
    await registry.create_version("tpl_active", "workspace v1", workspace_id="ws-1")
    await registry.activate("tpl_active", 1, workspace_id="ws-1")
    _use_prompt_registry(registry)

    response = client.get("/api/v1/prompts", headers=_auth_header(_WORKSPACE_KEY))

    assert response.status_code == 200
    by_name = _summary_by_name(response.json())
    assert by_name["tpl_active"].active_version == 1
    assert {v.version for v in by_name["tpl_active"].versions} == {1}


async def test_list_includes_name_without_active_version(
    client: TestClient,
) -> None:
    registry = _registry()
    await registry.create_version("tpl_draft", "workspace v1", workspace_id="ws-1")
    _use_prompt_registry(registry)

    response = client.get("/api/v1/prompts", headers=_auth_header(_WORKSPACE_KEY))

    assert response.status_code == 200
    by_name = _summary_by_name(response.json())
    assert by_name["tpl_draft"].active_version is None
    assert {v.version for v in by_name["tpl_draft"].versions} == {1}


async def test_list_excludes_workspace_without_prompts(
    client: TestClient,
) -> None:
    _use_prompt_registry(_registry())

    response = client.get("/api/v1/prompts", headers=_auth_header(_EMPTY_WORKSPACE_KEY))

    assert response.status_code == 200
    assert response.json() == []


async def test_list_includes_all_versions_for_active_name(
    client: TestClient,
) -> None:
    registry = _registry()
    await registry.seed(name="tpl_rollback", content="v1", workspace_id="ws-1")
    await registry.create_version("tpl_rollback", "v2", workspace_id="ws-1")
    await registry.activate("tpl_rollback", 2, workspace_id="ws-1")
    _use_prompt_registry(registry)

    response = client.get("/api/v1/prompts", headers=_auth_header(_WORKSPACE_KEY))

    assert response.status_code == 200
    by_name = _summary_by_name(response.json())
    versions = [item.version for item in by_name["tpl_rollback"].versions]
    assert by_name["tpl_rollback"].active_version == 2
    assert set(versions) == {1, 2}


async def test_list_includes_inactive_global_fallback_for_workspace(
    client: TestClient,
) -> None:
    registry = _registry()
    await registry.create_version("tpl_global", "global v1", workspace_id=None)
    _use_prompt_registry(registry)

    response = client.get("/api/v1/prompts", headers=_auth_header(_WORKSPACE_KEY))

    assert response.status_code == 200
    by_name = _summary_by_name(response.json())
    assert by_name["tpl_global"].active_version is None
    assert {v.version for v in by_name["tpl_global"].versions} == {1}
