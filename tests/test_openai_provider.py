import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.exceptions.base import (
    ModelNotFoundError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from app.exceptions.openai import (
    OpenAIModelNotFoundError,
    OpenAIProviderError,
    OpenAIRequestError,
    OpenAIUnavailableError,
)
from app.providers.openai import OpenAIProvider


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "sk-test-value",
) -> OpenAIProvider:
    return OpenAIProvider(
        api_key=api_key,
        base_url="https://gateway.example/v1",
        default_model="gpt-4.1-mini",
        timeout_seconds=30.0,
        transport=httpx.MockTransport(handler),
    )


def test_openai_settings_have_secure_defaults() -> None:
    settings = Settings(
        openai_api_key=SecretStr("sk-sensitive-value"),
    )

    assert settings.openai_api_key.get_secret_value() == "sk-sensitive-value"
    assert "sk-sensitive-value" not in repr(settings)
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openai_default_model == "gpt-4.1-mini"
    assert settings.openai_timeout_seconds == 60.0


def test_openai_settings_can_be_overridden_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-environment-value")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "gpt-test")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "15")

    settings = Settings()

    assert settings.openai_api_key.get_secret_value() == "sk-environment-value"
    assert settings.openai_base_url == "https://gateway.example/v1"
    assert settings.openai_default_model == "gpt-test"
    assert settings.openai_timeout_seconds == 15.0


@pytest.mark.asyncio
async def test_openai_provider_translates_chat_request_and_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://gateway.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer sk-test-value"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
            "temperature": 0.2,
            "max_completion_tokens": 128,
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello from OpenAI",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                },
            },
        )

    provider = _make_provider(handler)
    try:
        response = await provider.chat(
            {
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 128,
                },
            }
        )
    finally:
        await provider.close()

    assert response == {
        "model": "gpt-4.1-mini",
        "created_at": "2026-08-03T00:00:00Z",
        "message": {
            "role": "assistant",
            "content": "Hello from OpenAI",
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 8,
        "eval_count": 4,
    }


@pytest.mark.asyncio
async def test_openai_provider_uses_refusal_when_content_is_null() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": "I cannot help with that request.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    try:
        response = await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()

    assert response["message"] == {
        "role": "assistant",
        "content": "I cannot help with that request.",
    }


@pytest.mark.asyncio
async def test_openai_provider_rejects_refusal_without_explicit_null_content() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "refusal": "I cannot help with that request.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="omitted content",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_non_null_chat_content() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": 123,
                            "refusal": "I cannot help with that request.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid content type",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_negative_non_streaming_usage() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": -100,
                    "completion_tokens": 4,
                    "total_tokens": -96,
                },
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid usage",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_null_non_streaming_usage() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hello",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": None,
                    "completion_tokens": 4,
                    "total_tokens": 4,
                },
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid usage",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {
            "role": "assistant",
            "content": "",
        },
        {
            "role": "assistant",
            "content": None,
            "refusal": "",
        },
    ],
)
async def test_openai_provider_rejects_empty_non_streaming_text(
    message: dict[str, object],
) -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "stop",
                    }
                ],
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="empty text",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_non_text_chat_response() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "refusal": None,
                            "tool_calls": [{"id": "call-test"}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="text content or refusal",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_lists_only_valid_model_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://gateway.example/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4.1-mini", "object": "model"},
                    {"id": 123, "object": "model"},
                    "invalid",
                    {"id": "gpt-4.1", "object": "model"},
                ],
            },
        )

    provider = _make_provider(handler)
    try:
        response = await provider.list_models()
    finally:
        await provider.close()

    assert response == {
        "models": [
            {"name": "gpt-4.1-mini"},
            {"name": "gpt-4.1"},
        ]
    }


@pytest.mark.asyncio
async def test_openai_provider_streams_sse_and_attaches_usage() -> None:
    frames = [
        ": keep-alive",
        "event: message",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-test",
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": None},
                        "finish_reason": None,
                    }
                ],
            }
        ),
        "",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-test",
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Hello"},
                        "finish_reason": None,
                    }
                ],
            }
        ),
        "",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-test",
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        ),
        "",
        "data: "
        + json.dumps(
            {
                "id": "chatcmpl-test",
                "created": 1785715200,
                "model": "gpt-4.1-mini",
                "choices": [],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            }
        ),
        "",
        "data: [DONE]",
        "",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        payload = json.loads(request.content)
        assert payload == {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        return httpx.Response(200, text="\n".join(frames))

    provider = _make_provider(handler)
    try:
        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                {
                    "model": "gpt-4.1-mini",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            )
        ]
    finally:
        await provider.close()

    assert chunks == [
        {
            "model": "gpt-4.1-mini",
            "created_at": "2026-08-03T00:00:00Z",
            "message": {"role": "assistant", "content": ""},
            "done": False,
        },
        {
            "model": "gpt-4.1-mini",
            "created_at": "2026-08-03T00:00:00Z",
            "message": {"role": "assistant", "content": "Hello"},
            "done": False,
        },
        {
            "model": "gpt-4.1-mini",
            "created_at": "2026-08-03T00:00:00Z",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 5,
            "eval_count": 2,
        },
    ]


@pytest.mark.asyncio
async def test_openai_provider_rejects_stream_without_terminal_chunk() -> None:
    partial_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {"content": "Partial response"},
                "finish_reason": None,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(partial_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="without a terminal chunk",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "post_terminal_frame",
    [
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {"content": "after"},
                    "finish_reason": None,
                }
            ],
        },
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "length",
                }
            ],
        },
    ],
)
async def test_openai_provider_rejects_events_after_terminal_chunk(
    post_terminal_frame: dict[str, object],
) -> None:
    content_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {"content": "before"},
                "finish_reason": None,
            }
        ],
    }
    terminal_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            f"data: {json.dumps(content_frame)}\n\n"
            f"data: {json.dumps(terminal_frame)}\n\n"
            f"data: {json.dumps(post_terminal_frame)}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="after terminal chunk",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_json_after_terminal_chunk() -> None:
    terminal_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            f"data: {json.dumps(terminal_frame)}\n\ndata: not-json\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="after terminal chunk",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_usage_only_before_terminal_chunk() -> None:
    usage_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 4,
            "total_tokens": 13,
        },
    }
    regular_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {"content": "Hello"},
                "finish_reason": None,
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "total_tokens": 2,
        },
    }
    terminal_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            f"data: {json.dumps(usage_frame)}\n\n"
            f"data: {json.dumps(regular_frame)}\n\n"
            f"data: {json.dumps(terminal_frame)}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="before terminal chunk",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        {
            "prompt_tokens": True,
            "completion_tokens": 2,
            "total_tokens": 2,
        },
        {
            "prompt_tokens": 1,
            "total_tokens": 1,
        },
        {
            "prompt_tokens": "1",
            "completion_tokens": 2,
            "total_tokens": 3,
        },
    ],
)
async def test_openai_provider_rejects_invalid_usage_only_frame(
    usage: dict[str, object],
) -> None:
    terminal_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    usage_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [],
        "usage": usage,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            f"data: {json.dumps(terminal_frame)}\n\n"
            f"data: {json.dumps(usage_frame)}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid streaming usage",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_negative_streaming_usage() -> None:
    content_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {"content": "Hello"},
                "finish_reason": None,
            }
        ],
        "usage": {
            "prompt_tokens": -100,
            "completion_tokens": 2,
            "total_tokens": -98,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(content_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid usage",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_null_streaming_usage() -> None:
    content_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {"content": "Hello"},
                "finish_reason": None,
            }
        ],
        "usage": {
            "prompt_tokens": None,
            "completion_tokens": 2,
            "total_tokens": 2,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(content_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid usage",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_merges_partial_streaming_usage() -> None:
    frames = [
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "total_tokens": 5,
            },
        },
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {"content": " world"},
                    "finish_reason": None,
                }
            ],
            "usage": {
                "completion_tokens": 2,
                "total_tokens": 7,
            },
        },
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        content = "\n\n".join(f"data: {json.dumps(frame)}" for frame in frames)
        return httpx.Response(200, text=f"{content}\n\ndata: [DONE]\n\n")

    provider = _make_provider(handler)
    try:
        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                {
                    "model": "gpt-4.1-mini",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            )
        ]
    finally:
        await provider.close()

    assert chunks[-1]["prompt_eval_count"] == 5
    assert chunks[-1]["eval_count"] == 2


@pytest.mark.asyncio
async def test_openai_provider_streams_refusal_when_content_is_null() -> None:
    frames = [
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": None,
                        "refusal": "I cannot help with that request.",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        content = "\n\n".join(f"data: {json.dumps(frame)}" for frame in frames)
        return httpx.Response(200, text=f"{content}\n\ndata: [DONE]\n\n")

    provider = _make_provider(handler)
    try:
        chunks = [
            chunk
            async for chunk in provider.chat_stream(
                {
                    "model": "gpt-4.1-mini",
                    "messages": [{"role": "user", "content": "Hello"}],
                }
            )
        ]
    finally:
        await provider.close()

    assert chunks[0]["message"]["content"] == "I cannot help with that request."
    assert chunks[-1]["done"] is True


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_non_null_stream_content() -> None:
    invalid_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {
                    "content": ["invalid"],
                    "refusal": "I cannot help with that request.",
                },
                "finish_reason": None,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(invalid_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="invalid content type",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_stream_refusal_without_null_content() -> None:
    invalid_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {
                    "refusal": "I cannot help with that request.",
                },
                "finish_reason": None,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(invalid_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="omitted content",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_empty_stream_finish_reason() -> None:
    invalid_frame = {
        "created": 1785715200,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "delta": {},
                "finish_reason": "",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        content = f"data: {json.dumps(invalid_frame)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="finish_reason",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("description", "event"),
    [
        (
            "missing model",
            {
                "created": 1785715200,
                "choices": [
                    {
                        "delta": {"content": "Hello"},
                        "finish_reason": None,
                    }
                ],
            },
        ),
        (
            "missing created",
            {
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "delta": {"content": "Hello"},
                        "finish_reason": None,
                    }
                ],
            },
        ),
        (
            "missing choices",
            {
                "model": "gpt-4.1-mini",
                "created": 1785715200,
            },
        ),
        (
            "missing delta",
            {
                "model": "gpt-4.1-mini",
                "created": 1785715200,
                "choices": [{"finish_reason": None}],
            },
        ),
    ],
)
async def test_openai_provider_rejects_structurally_invalid_stream_frames(
    description: str,
    event: dict[str, object],
) -> None:
    del description

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="unexpected streaming response shape",
        ):
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_stream_logs_bad_frames_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_frame = "sk-sensitive-upstream-content"
    valid_frame = json.dumps(
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        }
    )
    terminal_frame = json.dumps(
        {
            "created": 1785715200,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            f"data: {sensitive_frame}\n\n"
            f"data: {valid_frame}\n\n"
            f"data: {terminal_frame}\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=content)

    provider = _make_provider(handler)
    try:
        with caplog.at_level(logging.WARNING, logger="app.providers.openai"):
            chunks = [
                chunk
                async for chunk in provider.chat_stream(
                    {
                        "model": "gpt-4.1-mini",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                )
            ]
    finally:
        await provider.close()

    assert len(chunks) == 2
    warnings = [
        record
        for record in caplog.records
        if record.name == "app.providers.openai" and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.getMessage() == "openai_stream_invalid_json"
    assert warning.__dict__["model"] == "gpt-4.1-mini"
    assert warning.__dict__["invalid_json_line_count"] == 1
    assert warning.__dict__["max_invalid_json_line_length"] == len(sensitive_frame)
    assert sensitive_frame not in caplog.text


@pytest.mark.asyncio
async def test_openai_provider_closes_shared_http_client() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))

    await provider.close()

    assert provider._client.is_closed


@pytest.mark.asyncio
async def test_openai_provider_maps_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    provider = _make_provider(handler, api_key="sk-sensitive-value")
    try:
        with pytest.raises(OpenAIUnavailableError) as exc_info:
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()

    assert isinstance(exc_info.value, ProviderUnavailableError)
    assert "sk-sensitive-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_maps_not_found_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "raw upstream model error",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            },
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(OpenAIModelNotFoundError) as exc_info:
            await provider.chat({"model": "gpt-missing", "messages": []})
    finally:
        await provider.close()

    assert isinstance(exc_info.value, ModelNotFoundError)
    assert "raw upstream model error" not in str(exc_info.value)
    assert "404" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_maps_generic_chat_not_found_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "raw upstream route error",
                    "type": "invalid_request_error",
                    "code": None,
                }
            },
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(OpenAIRequestError) as exc_info:
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()

    assert "raw upstream route error" not in str(exc_info.value)
    assert "404" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_maps_models_not_found_to_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "message": "raw upstream models route error",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(OpenAIRequestError) as exc_info:
            await provider.list_models()
    finally:
        await provider.close()

    assert "raw upstream models route error" not in str(exc_info.value)
    assert "404" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_maps_streaming_model_not_found_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            stream=httpx.ByteStream(
                json.dumps(
                    {
                        "error": {
                            "message": "raw upstream streaming model error",
                            "type": "invalid_request_error",
                            "param": "model",
                            "code": "model_not_found",
                        }
                    }
                ).encode()
            ),
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(OpenAIModelNotFoundError) as exc_info:
            _ = [
                chunk
                async for chunk in provider.chat_stream(
                    {"model": "gpt-missing", "messages": []}
                )
            ]
    finally:
        await provider.close()

    assert "raw upstream streaming model error" not in str(exc_info.value)
    assert "404" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_maps_other_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"message": "raw upstream rate limit error"}},
        )

    provider = _make_provider(handler)
    try:
        with pytest.raises(OpenAIRequestError) as exc_info:
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()

    assert isinstance(exc_info.value, ProviderRequestError)
    assert "raw upstream rate limit error" not in str(exc_info.value)
    assert "429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_json() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, content=b"not-json"))
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="OpenAI returned invalid JSON",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_openai_provider_rejects_invalid_chat_response_shape() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "model": "gpt-4.1-mini",
                "created": 1785715200,
                "choices": [],
            },
        )
    )
    try:
        with pytest.raises(
            OpenAIProviderError,
            match="unexpected chat response shape",
        ):
            await provider.chat({"model": "gpt-4.1-mini", "messages": []})
    finally:
        await provider.close()
