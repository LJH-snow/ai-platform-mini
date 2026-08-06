from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import _admin_key_hashes, provide_api_key_service
from app.auth.hash import hash_api_key
from app.auth.memory_repository import InMemoryAPIKeyRepository
from app.auth.models import APIKeyRecord
from app.auth.service import APIKeyService
from app.core.container import provide_agent_run_record_service
from app.core.settings import get_settings
from app.db.models import AgentRunRecordTable
from app.main import app

_ADMIN_KEY = "sk-admin-test"
_ADMIN_HASH = hash_api_key(_ADMIN_KEY)
_REGULAR_KEY = "sk-regular-test"


def _make_service(*, extra_records: list[APIKeyRecord] | None = None) -> APIKeyService:
    records = [
        APIKeyRecord(key_hash=_ADMIN_HASH, name="admin", status="active"),
        APIKeyRecord(
            key_hash=hash_api_key(_REGULAR_KEY),
            name="regular",
            status="active",
        ),
    ]
    if extra_records:
        records.extend(extra_records)
    return APIKeyService(repository=InMemoryAPIKeyRepository(records))


client = TestClient(app)


class _FakeAgentRunRecordService:
    def __init__(self, rows: list[AgentRunRecordTable]) -> None:
        self.rows = rows

    async def list_runs(
        self, limit: int = 50, status: str | None = None
    ) -> list[AgentRunRecordTable]:
        rows = [row for row in self.rows if status is None or row.status == status]
        return rows[:limit]

    async def get_run(self, run_id: str) -> AgentRunRecordTable | None:
        return next((row for row in self.rows if row.run_id == run_id), None)


def _safe_record() -> AgentRunRecordTable:
    return AgentRunRecordTable(
        run_id="run-admin-test",
        request_id="req-admin-test",
        api_key_hash=hash_api_key(_REGULAR_KEY),
        api_key_name="regular",
        model="qwen3:4b-instruct",
        status="completed",
        stop_reason="direct_answer",
        payload={
            "run_id": "run-admin-test",
            "status": "completed",
            "answer": "年假政策见参考来源。",
            "steps": [
                {
                    "tool_calls": [
                        {
                            "name": "knowledge_search",
                            "rag": {
                                "status": "success_with_sources",
                                "references": [{"document_id": "hr-policy"}],
                            },
                        }
                    ]
                }
            ],
        },
    )


@pytest.fixture(autouse=True)
def _setup_admin(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("ADMIN_API_KEYS", _ADMIN_KEY)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()
    app.dependency_overrides[provide_api_key_service] = lambda: _make_service()
    yield
    app.dependency_overrides = {}
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()


def test_create_api_key_returns_raw_key_once() -> None:
    response = client.post(
        "/admin/api-keys",
        json={"name": "new-key"},
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "new-key"
    assert data["raw_key"].startswith("sk-")
    assert len(data["raw_key"]) > 10
    assert data["created_at"] is not None


def test_list_api_keys_excludes_hash() -> None:
    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    keys = response.json()
    assert len(keys) >= 2
    for k in keys:
        assert "key_hash" not in k
        assert "key_hash_prefix" in k
        assert "name" in k
        assert "status" in k


def test_list_api_keys_marks_configured_admin_key() -> None:
    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )

    assert response.status_code == 200
    keys = {item["key_hash_prefix"]: item for item in response.json()}
    assert keys[_ADMIN_HASH[:8]]["is_admin"] is True
    assert keys[hash_api_key(_REGULAR_KEY)[:8]]["is_admin"] is False


def test_revoke_configured_admin_key_is_forbidden() -> None:
    response = client.delete(
        f"/admin/api-keys/{_ADMIN_HASH[:8]}",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_ERROR"


def test_revoke_api_key_by_prefix() -> None:
    prefix = hash_api_key(_REGULAR_KEY)[:8]
    response = client.delete(
        f"/admin/api-keys/{prefix}",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["revoked"] is True


def test_revoke_invalid_prefix_returns_422() -> None:
    response = client.delete(
        "/admin/api-keys/abc",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_short_prefix_returns_422() -> None:
    response = client.delete(
        "/admin/api-keys/abcd123",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_uppercase_prefix_returns_422() -> None:
    prefix = hash_api_key(_REGULAR_KEY)[:8].upper()
    response = client.delete(
        f"/admin/api-keys/{prefix}",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 422


def test_revoke_nonexistent_prefix_returns_404() -> None:
    response = client.delete(
        "/admin/api-keys/00000000",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 404


def test_agent_run_records_expose_safe_rag_summary() -> None:
    fake_service = _FakeAgentRunRecordService([_safe_record()])
    app.dependency_overrides[provide_agent_run_record_service] = lambda: fake_service
    try:
        response = client.get(
            "/admin/agent-runs",
            headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
        )
        detail = client.get(
            "/admin/agent-runs/run-admin-test",
            headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
        )
    finally:
        app.dependency_overrides.pop(provide_agent_run_record_service, None)

    assert response.status_code == 200
    assert response.json()[0]["model"] == "qwen3:4b-instruct"
    assert response.json()[0]["tool_count"] == 1
    assert response.json()[0]["rag_reference_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["api_key_prefix"] == hash_api_key(_REGULAR_KEY)[:8]
    assert "api_key_hash" not in detail.json()
    assert detail.json()["response"]["steps"][0]["tool_calls"][0]["rag"][
        "references"
    ] == [{"document_id": "hr-policy"}]


def test_non_admin_key_gets_403() -> None:
    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_REGULAR_KEY}"},
    )
    assert response.status_code == 403


def test_no_admin_configured_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEYS", "")
    get_settings.cache_clear()
    _admin_key_hashes.cache_clear()

    response = client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )
    assert response.status_code == 403


def test_missing_auth_returns_401() -> None:
    response = client.get("/admin/api-keys")
    assert response.status_code == 401


@pytest.mark.parametrize("month", ["2026-8", "1-08", "0000-01"])
def test_monthly_usage_rejects_non_canonical_month(month: str) -> None:
    response = client.get(
        "/admin/usage/monthly",
        params={"key_hash_prefix": hash_api_key(_REGULAR_KEY)[:8], "month": month},
        headers={"Authorization": f"Bearer {_ADMIN_KEY}"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
