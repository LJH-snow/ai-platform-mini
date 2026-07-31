import asyncio
import json

import httpx

from app.schemas.chat import ChatMessage, ChatRequest
from app.services.ollama_service import OllamaModelNotFoundError, OllamaService


def test_ollama_service_calls_chat_endpoint_and_parses_response() -> None:
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
            service = OllamaService(
                base_url="http://localhost:11434",
                default_model="llama3.2",
                timeout_seconds=30.0,
                http_client=client,
            )

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


def test_ollama_service_surfaces_missing_model_errors() -> None:
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
            service = OllamaService(
                base_url="http://localhost:11434",
                default_model="llama3.2",
                timeout_seconds=30.0,
                http_client=client,
            )

            try:
                await service.chat(ChatRequest(message="Hello"))
            except OllamaModelNotFoundError as exc:
                assert str(exc) == "model 'llama3.2' not found"
            else:
                raise AssertionError("Expected OllamaModelNotFoundError")

    asyncio.run(run_test())
