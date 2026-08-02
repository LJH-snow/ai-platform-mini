from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def default_model(self) -> str: ...

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...

    async def list_models(self) -> dict[str, Any]: ...
