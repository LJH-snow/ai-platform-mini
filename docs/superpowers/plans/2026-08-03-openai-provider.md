# OpenAIProvider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `LLMProvider` implementation that translates the project's
provider-neutral chat payloads to the OpenAI Chat Completions API and translates
OpenAI responses back to the shapes already consumed by `ChatService`.

**Architecture:** `OpenAIProvider` owns one shared `httpx.AsyncClient`,
authentication headers, HTTP error mapping, response validation, and protocol
translation. The rest of the application continues to consume the existing
Ollama-compatible provider boundary, so Sprint 7.1 does not change routing,
services, dependency injection, or public API behavior.

**Tech Stack:** Python 3.14, httpx, pytest, pydantic-settings, OpenAI Chat
Completions REST API.

---

## Provider Contract

### Input

`ChatService` supplies a provider-neutral payload with:

```python
{
    "model": "gpt-4.1-mini",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": False,
    "options": {
        "temperature": 0.2,
        "num_predict": 256,
    },
}
```

`OpenAIProvider` maps `options.temperature` to `temperature` and
`options.num_predict` to `max_completion_tokens`. Provider-only keys are not
forwarded to OpenAI.

### Non-streaming Output

The provider returns the shape already parsed by `ChatService`:

```python
{
    "model": "gpt-4.1-mini",
    "created_at": "2026-08-03T00:00:00Z",
    "message": {"role": "assistant", "content": "Hello"},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 8,
    "eval_count": 3,
}
```

Sprint 7.1 supports text-only chat output. A string `message.content` is used
directly. When `message.content` is `null` and `message.refusal` is a string,
the refusal text is mapped to the internal `message.content` field. Responses
that contain neither text nor refusal text, including tool-call-only output,
or contain empty non-streaming text, raise `OpenAIProviderError`. A refusal
fallback is allowed only when `content` is explicitly `null`; non-null values
of unsupported types and responses that omit `content` before providing
`refusal` are rejected.

### Streaming Output

The provider parses OpenAI SSE frames and yields provider-neutral chunks:

```python
{
    "model": "gpt-4.1-mini",
    "created_at": "2026-08-03T00:00:00Z",
    "message": {"role": "assistant", "content": "Hello"},
    "done": False,
}
```

The terminal chunk is buffered until the usage-only SSE frame or `[DONE]`
arrives. This lets the final chunk include `prompt_eval_count` and `eval_count`
without changing `ChatService` or `UsageCollector`.

Only a frame with `choices=[]` and a valid `usage` object is treated as a
usage-only frame. `prompt_tokens`, `completion_tokens`, and `total_tokens` must
all be non-negative integers, excluding booleans. Any token field present in a
regular streaming or non-streaming `usage` object follows the same validation;
omitted fields are allowed, but explicitly `null` fields are invalid. Partial
usage from regular streaming frames is merged field by field so later omissions
do not clear previously reported counts. Other parsed JSON frames must contain
a valid `model`, `created`, non-empty `choices`, and `delta`; invalid structures
raise `OpenAIProviderError` instead of being silently discarded. Normal
completion also requires a terminal chunk with a non-empty string
`finish_reason`; a stream that reaches `[DONE]` or EOF without one is rejected.
After the terminal chunk, only a valid usage-only frame, `[DONE]`, or EOF is
accepted. A usage-only frame received before the terminal chunk is rejected.
Streaming refusal text is mapped from `delta.refusal` using the same text-only
boundary.

### Errors

- Network and timeout failures raise `OpenAIUnavailableError`, which is also a
  `ProviderUnavailableError`.
- A chat HTTP 404 with structured error code `model_not_found` raises
  `OpenAIModelNotFoundError`, which is also a `ModelNotFoundError`.
- Other HTTP 404 responses, including failures from the Models endpoint, raise
  `OpenAIRequestError` so route and Base URL failures are not misreported as
  missing models.
- Other HTTP failures raise `OpenAIRequestError`, which is also a
  `ProviderRequestError`.
- Invalid JSON and invalid success response shapes raise `OpenAIProviderError`.
- Exception messages never contain the configured API key or raw response body.

## Task 1: Configuration Contract

**Files:**
- Modify: `app/core/settings.py`
- Modify: `.env.example`
- Test: `tests/test_openai_provider.py`

- [x] Add `openai_api_key: SecretStr`, `openai_base_url`,
  `openai_default_model`, and `openai_timeout_seconds`.
- [x] Verify the API key stays masked in `repr(Settings())`.
- [x] Verify environment variables override all OpenAI defaults.

## Task 2: Non-streaming Chat And Models

**Files:**
- Create: `app/providers/openai.py`
- Create: `app/exceptions/openai.py`
- Test: `tests/test_openai_provider.py`

- [x] Write failing tests for authorization, request translation, response
  translation, token usage, and Unix timestamp conversion.
- [x] Implement `default_model`, `chat()`, `list_models()`, and `close()`.
- [x] Verify model listing skips malformed entries and returns
  `{"models": [{"name": "..."}]}`.

## Task 3: Streaming Chat

**Files:**
- Modify: `app/providers/openai.py`
- Test: `tests/test_openai_provider.py`

- [x] Write failing tests for SSE role/content chunks, `[DONE]`, blank/comment
  frames, usage-only chunks, and a terminal finish reason.
- [x] Send `stream=true` and `stream_options.include_usage=true`.
- [x] Buffer the terminal chunk until usage is available or the stream ends.
- [x] Ignore malformed SSE data without logging raw response content.
- [x] Reject parsed JSON frames with invalid streaming response structures.
- [x] Preserve text refusals while rejecting unsupported non-text output.
- [x] Require a terminal chunk before accepting `[DONE]` or EOF as completion.
- [x] Strictly validate usage-only token fields.
- [x] Reject regular usage objects containing negative or invalid token values.
- [x] Reject explicitly null token fields while allowing omitted regular fields.
- [x] Merge partial regular streaming usage without clearing prior counts.
- [x] Accept usage-only frames only after the terminal chunk.
- [x] Reject every post-terminal data frame except valid usage-only frames.
- [x] Require a non-empty string streaming `finish_reason`.

## Task 4: Error Mapping And Verification

**Files:**
- Modify: `app/providers/openai.py`
- Modify: `app/exceptions/openai.py`
- Test: `tests/test_openai_provider.py`

- [x] Write failing tests for network failure, 404, another HTTP error, invalid
  JSON, and invalid response shape.
- [x] Implement typed exception mapping without exposing secrets or raw bodies.
- [x] Restrict model-not-found mapping to structured chat errors with code
  `model_not_found`.
- [x] Reject empty non-streaming text at the Provider boundary.
- [x] Require `content` to be explicitly `null` before using refusal text.
- [x] Run `ruff format --check .`, `ruff check .`, `mypy app tests`, and
  `pytest`.
- [x] Stop for user Code Review without starting Sprint 7.2 or committing.
