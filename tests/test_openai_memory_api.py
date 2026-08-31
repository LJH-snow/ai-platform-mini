import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response

from app.adapters.openai_adapter import OpenAIAdapter
from app.auth.hash import hash_api_key
from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.service import ConversationService
from app.core.container import provide_conversation_service
from app.main import app
from app.providers.mock import MockProvider
from app.services.chat_service import ChatService
from app.services.openai_service import OpenAIService, get_openai_service
from app.usage.collector import UsageCollector
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.service import UsageService

client = TestClient(app)

_AUTH_HEADERS = {"Authorization": "Bearer sk-test-integration"}
_TEST_OWNER = hash_api_key("sk-test-integration")


class RecordingProvider(MockProvider):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, Any]] = []

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return await super().chat(payload)


def _openai_service() -> OpenAIService:
    usage_service = UsageService(repository=InMemoryUsageRepository())
    return OpenAIService(
        chat_service=ChatService(provider=MockProvider()),
        usage_collector=UsageCollector(usage_service),
        adapter=OpenAIAdapter(),
    )


def _openai_service_with_provider(provider: MockProvider) -> OpenAIService:
    return OpenAIService(
        chat_service=ChatService(provider=provider),
        usage_collector=UsageCollector(
            UsageService(repository=InMemoryUsageRepository())
        ),
        adapter=OpenAIAdapter(),
    )


def _conversation_service() -> ConversationService:
    return ConversationService(repository=InMemoryConversationRepository())


def _sse_data_lines(response: Response) -> list[str]:
    return [
        line
        for line in response.iter_lines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_openai_chat_creates_thread_and_persists_turn() -> None:
    conversation_service = _conversation_service()
    openai_service = _openai_service()
    app.dependency_overrides[get_openai_service] = lambda: openai_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": "client old"},
                    {"role": "user", "content": "Hello"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_openai_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"]
    history = asyncio.run(
        conversation_service.load_history(_TEST_OWNER, body["thread_id"])
    )
    assert [(message.role, message.content) for message in history] == [
        ("user", "Hello"),
        ("assistant", "Hello from Mock Provider"),
    ]


def test_openai_chat_stream_returns_thread_id_and_persists_after_done() -> None:
    conversation_service = _conversation_service()
    openai_service = _openai_service()
    app.dependency_overrides[get_openai_service] = lambda: openai_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Stream hello"}],
                "stream": True,
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_openai_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    data_lines = _sse_data_lines(response)
    assert data_lines
    first_payload = json.loads(data_lines[0][6:])
    thread_id = first_payload["thread_id"]
    assert thread_id
    history = asyncio.run(conversation_service.load_history(_TEST_OWNER, thread_id))
    assert [(message.role, message.content) for message in history] == [
        ("user", "Stream hello"),
        ("assistant", "Hello from Mock Provider"),
    ]


def test_openai_chat_retry_does_not_duplicate_current_user() -> None:
    provider = RecordingProvider()
    conversation_service = _conversation_service()
    openai_service = _openai_service_with_provider(provider)
    app.dependency_overrides[get_openai_service] = lambda: openai_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        first = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello"}]},
            headers=_AUTH_HEADERS,
        )
        thread_id = first.json()["thread_id"]
        second = client.post(
            "/v1/chat/completions",
            json={
                "thread_id": thread_id,
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hello from Mock Provider"},
                    {"role": "user", "content": "Hello"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_openai_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert provider.payloads[1]["messages"] == [{"role": "user", "content": "Hello"}]


def test_openai_chat_keeps_system_messages_at_front() -> None:
    provider = RecordingProvider()
    conversation_service = _conversation_service()
    openai_service = _openai_service_with_provider(provider)
    app.dependency_overrides[get_openai_service] = lambda: openai_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "system-one"},
                    {"role": "user", "content": "hello"},
                    {"role": "system", "content": "system-two"},
                    {"role": "user", "content": "final"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_openai_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    assert provider.payloads[0]["messages"] == [
        {"role": "system", "content": "system-one"},
        {"role": "system", "content": "system-two"},
        {"role": "user", "content": "hello"},
        {"role": "user", "content": "final"},
    ]


def test_openai_chat_trims_history_and_injects_summary_into_system_message() -> None:
    provider = RecordingProvider()
    conversation_service = ConversationService(
        repository=InMemoryConversationRepository(),
        context_limit=2,
        context_max_prompt_tokens=1000,
        context_summary_max_chars=500,
    )
    openai_service = _openai_service_with_provider(provider)
    app.dependency_overrides[get_openai_service] = lambda: openai_service
    app.dependency_overrides[provide_conversation_service] = lambda: (
        conversation_service
    )

    try:
        response = client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "Stay brief."},
                    {"role": "user", "content": "first question about billing"},
                    {"role": "assistant", "content": "first answer about billing"},
                    {"role": "user", "content": "second question about memory"},
                    {"role": "assistant", "content": "second answer about memory"},
                    {"role": "user", "content": "final question"},
                ],
            },
            headers=_AUTH_HEADERS,
        )
    finally:
        app.dependency_overrides.pop(get_openai_service, None)
        app.dependency_overrides.pop(provide_conversation_service, None)

    assert response.status_code == 200
    payload = provider.payloads[0]
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"].startswith(
        "Stay brief.\n\nEarlier conversation summary (2 messages omitted):"
    )
    assert "first question about billing" in payload["messages"][0]["content"]
    assert payload["messages"][1:] == [
        {"role": "user", "content": "second question about memory"},
        {"role": "assistant", "content": "second answer about memory"},
        {"role": "user", "content": "final question"},
    ]
