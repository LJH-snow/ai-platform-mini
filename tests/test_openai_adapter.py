from app.adapters.openai_adapter import OpenAIAdapter
from app.schemas.chat import ChatMessage, ChatResponse
from app.schemas.openai import OpenAIChatMessage, OpenAIChatRequest

_FIXTURE_COMPLETION_ID = "chatcmpl-a1b2c3d4e5f6"
_FIXTURE_FALLBACK_CREATED = 1722648000


class _AdapterFixture:
    def __init__(self) -> None:
        self.adapter = OpenAIAdapter()


def _single_user_request(content: str = "Hi") -> OpenAIChatRequest:
    return OpenAIChatRequest(
        messages=[OpenAIChatMessage(role="user", content=content)],
    )


def _chat_response(
    *,
    model: str = "mock-model",
    content: str = "Hello",
    done_reason: str | None = "stop",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    created_at: str | None = "2026-08-03T12:00:00Z",
) -> ChatResponse:
    return ChatResponse(
        model=model,
        created_at=created_at,
        message=ChatMessage(role="assistant", content=content),
        done=True,
        done_reason=done_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def test_single_user_message_becomes_message_field() -> None:
    fixture = _AdapterFixture()
    request = _single_user_request("Hello there")
    result = fixture.adapter.to_chat_request(request)

    assert result.message == "Hello there"
    assert result.system_prompt is None
    assert result.history == []


def test_first_system_message_becomes_system_prompt() -> None:
    fixture = _AdapterFixture()
    request = OpenAIChatRequest(
        messages=[
            OpenAIChatMessage(role="system", content="Be brief."),
            OpenAIChatMessage(role="user", content="Hi"),
        ],
    )
    result = fixture.adapter.to_chat_request(request)

    assert result.system_prompt == "Be brief."
    assert result.message == "Hi"


def test_pre_system_messages_enter_history() -> None:
    fixture = _AdapterFixture()
    request = OpenAIChatRequest(
        messages=[
            OpenAIChatMessage(role="user", content="First"),
            OpenAIChatMessage(role="assistant", content="Reply"),
            OpenAIChatMessage(role="system", content="System"),
            OpenAIChatMessage(role="user", content="Last"),
        ],
    )
    result = fixture.adapter.to_chat_request(request)

    assert result.system_prompt == "System"
    assert result.message == "Last"
    assert len(result.history) == 2
    assert result.history[0].role == "user"
    assert result.history[0].content == "First"
    assert result.history[1].role == "assistant"
    assert result.history[1].content == "Reply"


def test_second_system_message_enters_history() -> None:
    fixture = _AdapterFixture()
    request = OpenAIChatRequest(
        messages=[
            OpenAIChatMessage(role="system", content="First"),
            OpenAIChatMessage(role="system", content="Second"),
            OpenAIChatMessage(role="user", content="Hi"),
        ],
    )
    result = fixture.adapter.to_chat_request(request)

    assert result.system_prompt == "First"
    assert len(result.history) == 1
    assert result.history[0].role == "system"
    assert result.history[0].content == "Second"


def test_model_temperature_max_tokens_mapped() -> None:
    fixture = _AdapterFixture()
    request = OpenAIChatRequest(
        model="gpt-4o",
        messages=[OpenAIChatMessage(role="user", content="Hi")],
        temperature=0.7,
        max_tokens=256,
    )
    result = fixture.adapter.to_chat_request(request)

    assert result.model == "gpt-4o"
    assert result.temperature == 0.7
    assert result.max_tokens == 256


def test_null_optional_fields() -> None:
    fixture = _AdapterFixture()
    request = _single_user_request()
    result = fixture.adapter.to_chat_request(request)

    assert result.model is None
    assert result.temperature is None
    assert result.max_tokens is None


def test_response_maps_basic_fields() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(model="gpt-4o", content="World", done_reason="length")
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.id == _FIXTURE_COMPLETION_ID
    assert result.object == "chat.completion"
    assert result.model == "gpt-4o"
    assert len(result.choices) == 1
    assert result.choices[0].index == 0
    assert result.choices[0].message.role == "assistant"
    assert result.choices[0].message.content == "World"
    assert result.choices[0].finish_reason == "length"


def test_response_uses_caller_completion_id() -> None:
    fixture = _AdapterFixture()
    response = _chat_response()
    result = fixture.adapter.to_chat_response(
        response,
        completion_id="chatcmpl-custom",
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.id == "chatcmpl-custom"


def test_done_reason_defaults_to_stop() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(done_reason=None)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.choices[0].finish_reason == "stop"


def test_created_at_parsed_from_iso8601() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(created_at="2026-08-03T00:00:00Z")
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.created == 1785715200


def test_missing_created_at_uses_fallback() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(created_at=None)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=999,
    )

    assert result.created == 999


def test_invalid_created_at_uses_fallback() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(created_at="not-a-date")
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=1234,
    )

    assert result.created == 1234


def test_naive_created_at_uses_host_timezone() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(created_at="2026-08-03T00:00:00")
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    from datetime import datetime

    expected = int(datetime.fromisoformat("2026-08-03T00:00:00").timestamp())
    assert result.created == expected


def test_z_suffix_created_at_uses_utc() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(created_at="2026-08-03T00:00:00Z")
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.created == 1785715200


def test_no_usage_when_both_tokens_missing() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(prompt_tokens=None, completion_tokens=None)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.usage is None


def test_full_usage() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(prompt_tokens=10, completion_tokens=20)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.usage is not None
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 20
    assert result.usage.total_tokens == 30


def test_partial_usage_prompt_only() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(prompt_tokens=5, completion_tokens=None)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.usage is not None
    assert result.usage.prompt_tokens == 5
    assert result.usage.completion_tokens == 0
    assert result.usage.total_tokens == 5


def test_partial_usage_completion_only() -> None:
    fixture = _AdapterFixture()
    response = _chat_response(prompt_tokens=None, completion_tokens=8)
    result = fixture.adapter.to_chat_response(
        response,
        completion_id=_FIXTURE_COMPLETION_ID,
        fallback_created=_FIXTURE_FALLBACK_CREATED,
    )

    assert result.usage is not None
    assert result.usage.prompt_tokens == 0
    assert result.usage.completion_tokens == 8
    assert result.usage.total_tokens == 8
