from collections.abc import AsyncIterator
from typing import Any


class MockProvider:
    def __init__(self, default_model: str = "mock-model") -> None:
        self._default_model = default_model

    @property
    def default_model(self) -> str:
        return self._default_model

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": payload.get("model", self._default_model),
            "created_at": "2026-08-02T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": "Hello from Mock Provider",
            },
            "done": True,
            "done_reason": "stop",
        }

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        for token in ["Hello ", "from ", "Mock ", "Provider"]:
            yield {
                "model": payload.get("model", self._default_model),
                "created_at": "2026-08-02T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": token,
                },
                "done": False,
            }
        yield {
            "model": payload.get("model", self._default_model),
            "created_at": "2026-08-02T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done": True,
            "done_reason": "stop",
        }

    async def list_models(self) -> dict[str, Any]:
        return {
            "models": [
                {"name": "mock-model"},
            ]
        }

    async def close(self) -> None:
        pass


def get_mock_provider() -> MockProvider:
    return MockProvider()
