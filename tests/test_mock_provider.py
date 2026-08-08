"""MockProvider contract tests — Agent routing, token counts, streaming.

Locks the deterministic mock behaviour in a fast feedback loop so the
E2E chain never silently depends on unverified mock semantics.
"""

from __future__ import annotations

from app.providers.mock import MockProvider


def _payload(system_prompt: str | None = None) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": "hello"})
    return {"model": "mock-model", "messages": messages, "stream": False}


async def test_agent_prompt_returns_json_decision_with_token_counts() -> None:
    provider = MockProvider()
    data = await provider.chat(
        _payload("You are the decision model for a bounded agent runtime.")
    )

    import json

    message = data["message"]
    assert isinstance(message, dict)
    content = str(message["content"])
    decision = json.loads(content)
    assert decision["type"] == "final_answer"
    assert decision["answer"]
    # Token counts keep the Agent usage pipeline complete.
    assert data["prompt_eval_count"] == 10
    assert data["eval_count"] == 5
    assert data["done"] is True


async def test_plain_chat_returns_fixed_text() -> None:
    provider = MockProvider()
    data = await provider.chat(_payload())

    message = data["message"]
    assert isinstance(message, dict)
    assert message["content"] == "Hello from Mock Provider"
    assert data["prompt_eval_count"] == 10


async def test_stream_routes_agent_and_plain_chat() -> None:
    provider = MockProvider()

    agent_chunks = [
        chunk
        async for chunk in provider.chat_stream(
            _payload("You are the decision model for a bounded agent runtime.")
        )
    ]
    # Streaming agent answers carry the final-answer text, not JSON.
    assert (
        "".join(
            str(chunk.get("message", {}).get("content", "")) for chunk in agent_chunks
        )
        == "这是 Mock Provider 的最终回答。"
    )
    assert agent_chunks[-1]["done"] is True

    chat_chunks = [chunk async for chunk in provider.chat_stream(_payload())]
    contents = [
        str(chunk.get("message", {}).get("content", "")) for chunk in chat_chunks
    ]
    assert "".join(contents) == "Hello from Mock Provider"
    assert chat_chunks[-1]["done"] is True
    assert chat_chunks[-1]["eval_count"] == 5


async def test_unrelated_system_prompt_is_not_routed_as_agent() -> None:
    provider = MockProvider()
    data = await provider.chat(_payload("You are a helpful assistant."))

    message = data["message"]
    assert isinstance(message, dict)
    assert message["content"] == "Hello from Mock Provider"


async def test_protocol_members() -> None:
    provider = MockProvider()

    models = await provider.list_models()
    assert models["models"][0]["name"] == "mock-model"
    await provider.close()
    assert provider.default_model == "mock-model"
