import asyncio
import json

import httpx

from app.exceptions.ollama import OllamaModelNotFoundError
from app.providers.ollama import OllamaProvider
from app.schemas.chat import ChatMessage, ChatRequest
from app.schemas.models import ModelInfo
from app.services.chat_service import ChatService
from app.services.model_service import ModelService


def test_chat_service_calls_ollama_provider_and_returns_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://testserver/api/chat"

        payload = json.loads(request.content.decode("utf-8"))
        assert payload == {
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "assistant", "content": "How can I help?"},
                {"role": "user", "content": "Explain vectors."},
            ],
            "stream": False,
        }

        return httpx.Response(
            status_code=200,
            json={
                "model": "llama3.2",
                "created_at": "2026-07-31T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": "Vectors have magnitude and direction.",
                },
                "done": True,
                "done_reason": "stop",
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provider = OllamaProvider(
                base_url="http://localhost:11434",
                default_model="llama3.2",
                timeout_seconds=30.0,
                http_client=client,
            )
            service = ChatService(provider=provider)

            response = await service.chat(
                ChatRequest(
                    message="Explain vectors.",
                    system_prompt="Be concise.",
                    history=[ChatMessage(role="assistant", content="How can I help?")],
                )
            )

        assert response.model == "llama3.2"
        assert response.message.role == "assistant"
        assert response.message.content == "Vectors have magnitude and direction."
        assert response.done is True
        assert response.done_reason == "stop"

    asyncio.run(run_test())


def test_ollama_provider_surfaces_missing_model_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=404,
            json={"error": "model 'llama3.2' not found"},
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provider = OllamaProvider(
                base_url="http://localhost:11434",
                default_model="llama3.2",
                timeout_seconds=30.0,
                http_client=client,
            )

            try:
                await provider.chat(
                    {"model": "llama3.2", "messages": [], "stream": False}
                )
            except OllamaModelNotFoundError as exc:
                assert str(exc) == "model 'llama3.2' not found"
            else:
                raise AssertionError("Expected OllamaModelNotFoundError")

    asyncio.run(run_test())


def test_model_service_lists_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "http://testserver/api/tags"

        return httpx.Response(
            status_code=200,
            json={
                "models": [
                    {"name": "qwen3:4b"},
                    {"name": "deepseek-r1:8b"},
                ]
            },
        )

    async def run_test() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            provider = OllamaProvider(
                base_url="http://localhost:11434",
                default_model="qwen3:4b",
                timeout_seconds=30.0,
                http_client=client,
            )
            service = ModelService(provider=provider)
            response = await service.list_models()

        assert response.data == [
            ModelInfo(id="qwen3:4b"),
            ModelInfo(id="deepseek-r1:8b"),
        ]

    asyncio.run(run_test())
