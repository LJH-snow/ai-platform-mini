# ai-platform-mini

Minimal FastAPI scaffold for an AI platform backend.

## Project rules

- Python version: support `3.12` to `3.14`, with `3.14` as the default local version
- Style: follow PEP 8 and keep formatting/linting green with Ruff
- Type hints: add type hints early; all new or edited production code should be annotated
- Sprint rule: every Sprint must end with a runnable app and passing checks
- Code Review: every code change goes through user review before moving to the next feature
- Git workflow: push to GitHub from day one with small, meaningful commits

## Quick start

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## Quality gate

```bash
ruff format --check .
ruff check .
mypy app tests
pytest
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions (supports SSE streaming) |
| GET | `/api/v1/models` | List available LLM models |
| POST | `/api/v1/chat` | Generate a chat completion using the configured LLM provider |

### Chat request example

```json
{
  "message": "Hello",
  "model": null,
  "system_prompt": null,
  "history": []
}
```

### Chat response example

```json
{
  "model": "qwen3:4b",
  "created_at": "2026-08-02T00:00:00Z",
  "message": {"role": "assistant", "content": "Hi there!"},
  "done": true,
  "done_reason": "stop"
}
```

## Architecture

```
               Client
                  │
                  ▼
        RequestIdMiddleware
                  │
                  ▼
       LoggingMiddleware
                  │
                  ▼
           FastAPI Router
                  │
                  ▼
           Service Layer
                  │
                  ▼
         LLMProvider Protocol
             ┌────┴────┐
        OllamaProvider  MockProvider
             │
             ▼
         Ollama Server
```

### Directory structure

```
app/
├── api/            # Router layer
├── core/           # Infrastructure (settings, logging, exceptions)
├── exceptions/     # Provider-specific exceptions
├── middleware/     # Request ID
├── providers/      # LLM Provider layer (Protocol + implementations)
├── schemas/        # Pydantic request/response models
├── services/       # Business logic
└── main.py
```

### Design principles

- **Pydantic schemas** (`app/schemas/`) — typed request/response, no raw dicts
- **Service layer** — Router never calls Ollama directly; swap provider by changing only the service
- **Settings** (`app/core/settings.py`) — pydantic-settings reads `.env`, never hardcode configs
- **Logging** (`app/core/logging.py`) — structured request logs with method, path, status, latency
- **Exception handlers** (`app/core/exceptions.py`) — global error handling, no try/except in Router
- **Middleware** (`app/middleware/`) — request ID tracing, supports client-provided `X-Request-ID`

## Configuration

Copy `.env.example` to `.env` and adjust:

```
APP_NAME=AI Platform Mini
DEBUG=false
LOG_LEVEL=INFO
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=60
```

No code changes needed when switching environments or models.

## Sprint log

### Sprint 1 (Day 1–3)

- FastAPI scaffold with health check and Ollama chat endpoint
- Pydantic schemas (`ChatRequest`, `ChatResponse`, `ChatMessage`)
- Service layer (`OllamaService`) with dependency injection via `Depends`
- API versioned under `/api/v1`
- Full test suite (6 tests) + ruff + mypy green
- Code Review flow established; deferred optimizations documented in AGENTS.md

### Sprint 1 (Day 4)

- Configuration management: `config.py` → `settings.py` with pydantic-settings + `.env`
- Structured logging: `RequestLoggingMiddleware` + `setup_logging()` in core layer
- `@lru_cache` on `get_settings()` to avoid re-reading `.env` per request
- `.env.example` committed; `.env` gitignored for secret safety

### Sprint 1 (Day 5)

- Global exception handlers: `register_exception_handlers()` with `@app.exception_handler`
- Standard error response: `ErrorCode` (StrEnum) + `ErrorResponse` (Pydantic) in `schemas/error.py`
- Request ID middleware: `X-Request-ID` header support, auto-generates 8-char ID
- Router simplified: removed try/except, exceptions handled globally
- Logging middleware: 500 errors also logged with traceback via try/except/raise
- Middleware order verified: RequestId → Logging → Router

### Sprint 2 (Day 1)

- Models API: `GET /api/v1/models` listing available LLM models
- Protocol translation: Ollama `/api/tags` → unified `ModelsResponse` format
- `ModelInfo` schema with `object="model"` for future OpenAI compatibility
- `list_models()` + `_get_json()` in OllamaService
- Warning log for skipped non-dict model entries

### Sprint 2 (Day 2)

- Extract HTTP logic from Service to Provider Layer (`app/providers/`)
- OllamaProvider: pure HTTP + error handling, no business logic
- ChatService/ModelService: own payload construction, response parsing, schema building
- Add `ProviderChatResult`/`ProviderModelEntry` dataclasses (frozen) replacing magic-string dicts
- Move exceptions to `app/exceptions/ollama.py`, removing Provider→Service reverse dependency
- Unify `_request()` method replacing `_get_json/_post_json`

### Sprint 2 (Day 3)

- Add `LLMProvider` Protocol defining Provider interface (chat, list_models, default_model)
- Add `MockProvider` for architecture validation — zero HTTP, zero Service changes
- Add `get_llm_provider()` factory with `LLM_PROVIDER` env switch (ollama/mock)
- Service layer depends on abstraction (Protocol), not concrete OllamaProvider
- Dependency Inversion: high-level modules depend on abstractions, not implementations

### Sprint 2 (Day 4)

- OpenAI-compatible API: `POST /v1/chat/completions`
- Bidirectional protocol translation: OpenAI Request ⇄ ChatRequest ⇄ ChatResponse ⇄ OpenAI Response
- OpenAIService wraps ChatService — protocol layer separated from business layer
- `model` in response uses actual provider model (not request model name)
- `created` parsed from Ollama `created_at` ISO8601 timestamp
- `stream=true` returns 501 (Streaming support coming in Day 5)

### Sprint 2 (Day 5)

- OpenAI-compatible streaming (SSE): `POST /v1/chat/completions?stream=true`
- Provider layer: OllamaProvider.chat_stream() yielding NDJSON chunks, MockProvider.chat_stream() yielding tokens
- ChatService.chat_stream(): async generator converting Provider chunks → ProviderChatResult
- OpenAIService.chat_completions_stream(): ProviderChatResult → OpenAI SSE format (role chunk → content chunks → [DONE])
- Stream chunk schemas: OpenAIStreamDelta, OpenAIStreamChoice, OpenAIStreamChunk
- Router: stream=true returns StreamingResponse with text/event-stream
- _parse_stream_chunk: lenient parsing (invalid chunks skipped with None), distinct from strict _parse_chat_response
