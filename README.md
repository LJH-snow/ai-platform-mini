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

## Docker

```bash
# Set required secrets first
export INITIAL_API_KEY=sk-your-initial-key
export ADMIN_API_KEYS=sk-your-admin-key

docker compose up
```

This starts the app on `:8000`, Ollama on `:11434`, and PostgreSQL on `:5432`.
Both `INITIAL_API_KEY` and `ADMIN_API_KEYS` must be set (compose will refuse to start otherwise).

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
| GET | `/api/v1/health` | Liveness probe |
| GET | `/api/v1/ready` | Readiness probe (checks downstream) |
| GET | `/api/v1/usage` | Token usage statistics |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions (supports SSE streaming) |
| GET | `/api/v1/models` | List available LLM models |
| POST | `/api/v1/chat` | Generate a chat completion using the configured LLM provider |
| POST | `/admin/api-keys` | Create a new API key (admin only) |
| GET | `/admin/api-keys` | List all API keys (admin only) |
| DELETE | `/admin/api-keys/{prefix}` | Revoke an API key by hash prefix (admin only) |

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
        ContextMiddleware (request_id + auth)
                  │
                  ▼
       LoggingMiddleware
                  │
                  ▼
           FastAPI Router
                  │
         ┌────────┼────────┐
         ▼        ▼        ▼
     Admin API  LLM API  Health
         │        │
         ▼        ▼
     APIKeyService  ChatService / OpenAIService
         │        │
         ▼        ▼
   APIKeyRepository  LLMProvider Protocol
         │          ┌────┴────┐
    InMemory/Postgres  OllamaProvider  MockProvider
                        │
                        ▼
                    Ollama Server
```

### Directory structure

```
app/
├── api/            # Router layer (chat, openai, admin, health, models)
├── auth/           # Authentication & key management (service, repository, dependencies)
├── core/           # Infrastructure (settings, logging, exceptions, container, context)
├── db/             # Database (models, session, init)
├── exceptions/     # Provider-specific + domain exceptions
├── middleware/     # Context middleware (request_id)
├── providers/      # LLM Provider layer (Protocol + implementations)
├── ratelimit/      # Rate limiting (Protocol + memory impl + dependencies)
├── schemas/        # Pydantic request/response models
├── services/       # Business logic
├── usage/          # Token usage tracking
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
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=60

# Auth
API_KEYS=sk-test-key-1:development,sk-admin-key-1:admin
ADMIN_API_KEYS=sk-admin-key-1
AUTH_ENABLED=true
AUTH_STORAGE=memory

# Bootstrap (Docker/Postgres only)
INITIAL_API_KEY=
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aiplatform

# Rate limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60
```

### Key configuration notes

- `API_KEYS` format: `sk-xxx:name,sk-yyy:name2` (comma-separated, name optional)
- `ADMIN_API_KEYS` must also be present in `API_KEYS` (or registered via bootstrap)
- `INITIAL_API_KEY` auto-registers on startup (idempotent, `ON CONFLICT DO NOTHING`)
- Docker Compose requires both `INITIAL_API_KEY` and `ADMIN_API_KEYS` to be set

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

### Sprint 2 (Day 6)

- Dependency Injection refactor: FastAPI `Depends()` manages Service/Provider lifecycle
- Provider Container (`app/core/container.py`): `provide_llm_provider()` with `@lru_cache` singleton
- Provider Factory: `create_llm_provider()` with clear semantics, unsupported provider raises ValueError
- ChatService/ModelService/OpenAIService: factory functions use `Depends(provide_llm_provider)` injection
- OllamaProvider: owns shared `httpx.AsyncClient` (connection reuse), `close()` for graceful shutdown
- FastAPI lifespan: calls `provider.close()` on shutdown
- LLMProvider Protocol: added `close()` method
- Readiness probe: `GET /api/v1/ready` — checks downstream availability, returns 503 on failure
- Bugfix: temperature/max_tokens now passed through ChatRequest → Ollama options (`num_predict`)
- Bugfix: stream first chunk uses `result.model` (actual provider model, not request model)
- Bugfix: OllamaProvider stream catches `httpx.HTTPStatusError`
- Bugfix: SSE response includes `Cache-Control: no-cache` and `Connection: keep-alive`
- Bugfix: `OpenAIChatRequest.model` defaults to `None` (provider decides default model)

### Sprint 2 (Day 7)

- Test suite: 25 tests covering ChatService, OpenAIService, Provider Factory, Exception Handlers, and API endpoints
- Async tests with pytest-asyncio (`asyncio_mode=auto`)
- MockProvider-based integration tests (no Ollama dependency)
- Provider factory tests: mock/ollama switch, unsupported provider ValueError, singleton guarantee
- Exception handler tests: ProviderUnavailable→502, ProviderError→502, ModelNotFound→404, validation→422
- Parameter validation: temperature `ge=0, le=2`, max_tokens `gt=0, le=32768`
- Exception hierarchy refactor: `AppError → ProviderError → ProviderUnavailableError/ModelNotFoundError/ProviderRequestError`
- Ollama exceptions inherit Provider base classes (multi-inheritance for catchability)
- ErrorCode: `OLLAMA_ERROR` → `PROVIDER_ERROR` (provider-agnostic)
- Stream fallback: if Provider yields zero tokens, emit role+finish chunk before [DONE]
- ChatService.default_model property for stream fallback model name
- OpenAPI descriptions on all endpoints
- Docker: Dockerfile + docker-compose (app + Ollama) + .dockerignore

### Sprint 3 (Day 1)

- API Key authentication: `Authorization: Bearer sk-xxx` on all LLM endpoints
- `app/auth/` module: models (APIKey dataclass), service (APIKeyService), dependencies (require_api_key)
- API keys configured via `API_KEYS` env var (format: `sk-xxx:name,sk-yyy:name2`)
- `AUTH_ENABLED` env var: set `false` to disable auth (development mode)
- health/ready endpoints exempt from auth
- `AuthenticationError(AppError)` → 401 + `WWW-Authenticate: Bearer`
- `ErrorCode.AUTHENTICATION_ERROR`
- `APIKeyService.validate()` raises `AuthenticationError` (unified exception hierarchy)
- Tests: no key → 401, wrong key → 401, valid key → pass, no keys configured → anonymous, auth disabled → pass, health/ready bypass

### Sprint 3 (Day 2)

- Token usage tracking: `ProviderChatResult` now includes `prompt_tokens`/`completion_tokens`
- ChatService parses Ollama `prompt_eval_count`/`eval_count` → token fields
- ChatResponse schema: added `prompt_tokens`/`completion_tokens`
- OpenAI response `usage` now populated with real token counts from provider
- `app/usage/` module: models (UsageRecord, UsageSummary), service (UsageService), middleware (UsageMiddleware)
- UsageMiddleware records request_id, model, tokens, latency_ms, api_key_name per request
- UsageService keeps last 1000 records in memory, aggregates by model
- `GET /api/v1/usage` endpoint returns aggregated usage statistics (requires auth)
- Router layer writes `request.state.usage_data` and `request.state.api_key_name` for middleware

### Sprint 3 (Day 3)

- API Key management: Admin CRUD for creating, listing, and revoking keys
- `POST /admin/api-keys` — create key, raw_key returned only once
- `GET /admin/api-keys` — list keys, returns `APIKeyMetadata` (no key_hash exposed)
- `DELETE /admin/api-keys/{key_hash_prefix}` — revoke key (soft delete, status → "revoked")
- Admin authentication: `ADMIN_API_KEYS` env var, `require_admin_key` dependency
- `require_admin_rate_limit` — admin-specific rate limiting, no double auth validation
- Bootstrap: `INITIAL_API_KEY` and `ADMIN_API_KEYS` auto-registered on startup via `ensure_initial_key()`
- PostgreSQL upsert: `ON CONFLICT DO NOTHING` for concurrent-safe bootstrap
- Prefix query safety: 8-char lowercase hex validation, conflict detection on multiple matches
- Error responses: `ValidationError→422`, `ConflictError→409`, `AuthorizationError→403`, `AuthenticationError→401`
- Docker Compose: `INITIAL_API_KEY` and `ADMIN_API_KEYS` required via `${VAR:?message}`
- `APIKeyMetadata` public model with `key_hash_prefix` (never full hash)
- `APIKeyRecord` audit record retained after revoke (status tracking)
- 6 PostgreSQL integration tests (testcontainers, `INTEGRATION_TEST=1`)

### Sprint 3 (Day 4)

- Unified error protocol: `ValidationError(422)`, `ConflictError(409)` use `ErrorResponse` with `code` + `request_id`
- Prefix validation consolidated into Service layer (`find_hash_by_prefix` raises domain exceptions)
- Admin route simplified: no redundant regex validation, relies on Service exceptions
- PostgreSQL integration test suite: create, find, ensure_key idempotency, revoke, prefix query, touch_last_used
- `testcontainers` integration for real PostgreSQL verification
- Configuration documentation in `.env.example` and README
