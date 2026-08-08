from collections.abc import AsyncIterator
from typing import Any

_AGENT_PROTOCOL_MARKER = "decision model"
_MOCK_FINAL_ANSWER = "这是 Mock Provider 的最终回答。"
_MOCK_CHAT_TEXT = "Hello from Mock Provider"


class MockProvider:
    """Deterministic LLM stand-in for tests and E2E.

    Routes on the Agent protocol marker: prompts containing
    "decision model" get a valid JSON Agent decision (final answer);
    everything else (plain chat, workflow report generation) gets the
    fixed chat text.  Streaming mirrors the same routing.
    """

    def __init__(self, default_model: str = "mock-model") -> None:
        self._default_model = default_model

    @property
    def default_model(self) -> str:
        return self._default_model

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        content = (
            f'{{"type":"final_answer","answer":"{_MOCK_FINAL_ANSWER}"}}'
            if self._is_agent_decision(payload)
            else _MOCK_CHAT_TEXT
        )
        return {
            "model": payload.get("model", self._default_model),
            "created_at": "2026-08-02T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": content,
            },
            "done": True,
            "done_reason": "stop",
            # Deterministic token counts keep the Agent usage pipeline
            # complete (missing counts would flag tool-call decisions as
            # budget-incomplete and stop the run).
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        if self._is_agent_decision(payload):
            tokens = [_MOCK_FINAL_ANSWER]
        else:
            tokens = ["Hello ", "from ", "Mock ", "Provider"]
        for token in tokens:
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
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    @staticmethod
    def _is_agent_decision(payload: dict[str, Any]) -> bool:
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return False
        return any(
            isinstance(message, dict)
            and message.get("role") == "system"
            and _AGENT_PROTOCOL_MARKER in str(message.get("content", ""))
            for message in messages
        )

    async def list_models(self) -> dict[str, Any]:
        return {
            "models": [
                {
                    "name": self._default_model,
                    "model": self._default_model,
                    "modified_at": "2026-08-02T00:00:00Z",
                    "size": 0,
                    "digest": "mock",
                    "details": {"parameter_size": "0B"},
                }
            ]
        }

    async def close(self) -> None:
        return None


def get_mock_provider() -> MockProvider:
    """Factory entry used by ``app.providers.factory``."""
    return MockProvider()
