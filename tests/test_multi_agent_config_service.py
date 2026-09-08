"""Integration tests for multi-agent canvas config CRUD via API.

Requires INTEGRATION_TEST=1 with a running PostgreSQL instance.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_TEST"),
    reason="Set INTEGRATION_TEST=1 to run config API integration tests",
)

client = TestClient(app)
_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}

_SAMPLE_DAG = {
    "nodes": [
        {"id": "task_1", "role": "research", "description": "Search"},
        {"id": "task_2", "role": "writer", "description": "Write"},
    ],
    "edges": [{"id": "e1", "source": "task_1", "target": "task_2"}],
}


def test_create_config() -> None:
    resp = client.post(
        "/api/v1/multi-agent/configs",
        json={
            "name": "Research Pipeline",
            "description": "A test pipeline",
            "dag": _SAMPLE_DAG,
            "orchestration_config": {"failure_policy": "fail_fast"},
        },
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Research Pipeline"
    assert data["version"] == 1
    assert len(data["dag"]["nodes"]) == 2


def test_list_configs() -> None:
    client.post(
        "/api/v1/multi-agent/configs",
        json={"name": "List Test", "dag": _SAMPLE_DAG},
        headers=_AUTH_HEADERS,
    )
    resp = client.get("/api/v1/multi-agent/configs", headers=_AUTH_HEADERS)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_config() -> None:
    created = client.post(
        "/api/v1/multi-agent/configs",
        json={"name": "Get Test", "dag": _SAMPLE_DAG},
        headers=_AUTH_HEADERS,
    )
    config_id = created.json()["id"]
    resp = client.get(f"/api/v1/multi-agent/configs/{config_id}", headers=_AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["id"] == config_id


def test_update_config() -> None:
    created = client.post(
        "/api/v1/multi-agent/configs",
        json={"name": "Update Test", "dag": _SAMPLE_DAG},
        headers=_AUTH_HEADERS,
    )
    config_id = created.json()["id"]
    resp = client.put(
        f"/api/v1/multi-agent/configs/{config_id}",
        json={"name": "Updated Name"},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["version"] == 2


def test_delete_config() -> None:
    created = client.post(
        "/api/v1/multi-agent/configs",
        json={"name": "Delete Test", "dag": _SAMPLE_DAG},
        headers=_AUTH_HEADERS,
    )
    config_id = created.json()["id"]
    resp = client.delete(
        f"/api/v1/multi-agent/configs/{config_id}", headers=_AUTH_HEADERS
    )
    assert resp.status_code == 204
    get_resp = client.get(
        f"/api/v1/multi-agent/configs/{config_id}", headers=_AUTH_HEADERS
    )
    assert get_resp.status_code == 404


def test_export_import_config() -> None:
    created = client.post(
        "/api/v1/multi-agent/configs",
        json={"name": "Export Test", "dag": _SAMPLE_DAG},
        headers=_AUTH_HEADERS,
    )
    config_id = created.json()["id"]
    export_resp = client.get(
        f"/api/v1/multi-agent/configs/{config_id}/export",
        headers=_AUTH_HEADERS,
    )
    assert export_resp.status_code == 200
    export_data = export_resp.json()
    assert export_data["name"] == "Export Test"

    import_resp = client.post(
        "/api/v1/multi-agent/configs/import",
        json={"name": "Imported Config", "dag": export_data["dag"]},
        headers=_AUTH_HEADERS,
    )
    assert import_resp.status_code == 201
    assert import_resp.json()["name"] == "Imported Config"
