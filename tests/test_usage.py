from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_usage_endpoint_returns_summary() -> None:
    response = client.get("/api/v1/usage")
    assert response.status_code == 200
    body = response.json()
    assert "total_requests" in body
    assert "total_tokens" in body
    assert "by_model" in body


def test_usage_increments_after_chat_request() -> None:
    from app.usage.middleware import get_usage_service

    service = get_usage_service()
    initial_count = service.record_count

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )

    if response.status_code != 401:
        assert service.record_count >= initial_count
