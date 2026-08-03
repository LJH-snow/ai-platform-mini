# AI Platform Mini

基于 FastAPI 的轻量级 LLM Gateway，提供 OpenAI-compatible Chat API、Provider
抽象、API Key 鉴权、限流、Token 配额、Usage 统计和 SSE Streaming。

## Current status

- Current milestone: **Sprint 7.2 implemented; awaiting Code Review**
- Version: `0.1.0`
- Runtime: Python `3.12`–`3.14`（默认 `3.14`）
- Active routing: 默认模型 → Ollama，其余 `gpt-*` → OpenAI，其他模型 → Ollama；Mock 用于测试
- OpenAIProvider: 已接入 ProviderRouter、DI 和应用生命周期
- Storage: Memory 或 PostgreSQL
- Verification baseline（2026-08-03）:
  - Default suite: `198 passed, 21 skipped`
  - PostgreSQL integration suite: `160 passed`
  - Ruff format/lint and mypy: passed

## Core capabilities

- OpenAI-compatible `POST /v1/chat/completions`，支持普通响应与 SSE 流式响应
- 原生 Chat、Models、Health、Readiness 和 Usage API
- 可替换的 `LLMProvider` Protocol、共享 HTTP 连接池和统一异常映射
- ProviderRouter 按模型自动选择 OpenAI 或 Ollama，Service 和公开端点无需感知 Provider
- Bearer API Key 鉴权、Admin Key 管理及 SHA-256 哈希存储
- 按 API Key 的滑动窗口限流，以及日/月 Token 配额
- 配额预占、续租、结算和断连释放，支持并发及长时间流式请求
- PostgreSQL Usage 聚合、API Key 持久化和 Testcontainers 集成测试
- JSON 结构化日志、完整 UUID4 Request ID、敏感配置脱敏和多资源 Readiness

## Project rules

- Python version: support `3.12` to `3.14`, with `3.14` as the default local version
- Style: follow PEP 8 and keep formatting/linting green with Ruff
- Type hints: add type hints early; all new or edited production code should be annotated
- Sprint rule: every Sprint must end with a runnable app and passing checks
- Code Review: every code change goes through user review before moving to the next feature
- Git workflow: push to GitHub from day one with small, meaningful commits

## Quick start

前置条件：Python `3.14` 和一个可访问的 Ollama 实例。默认配置使用内存存储，
无需 PostgreSQL。

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

启动后可访问：

- API documentation: `http://localhost:8000/docs`
- Liveness: `http://localhost:8000/api/v1/health`
- Readiness: `http://localhost:8000/api/v1/ready`

## Docker

```bash
# Set required secrets first
export INITIAL_API_KEY=sk-your-initial-key
export ADMIN_API_KEYS=sk-your-admin-key

docker compose up
```

This starts the app on `:8000`, Ollama on `:11434`, and PostgreSQL on `:5432`.
Both `INITIAL_API_KEY` and `ADMIN_API_KEYS` must be set (compose will refuse to start otherwise).
Docker mode automatically uses PostgreSQL-backed authentication and persistence.

## Quality gate

```bash
ruff format --check .
ruff check .
mypy app tests
pytest
# Includes PostgreSQL integration tests (Docker required)
INTEGRATION_TEST=1 pytest
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Liveness probe |
| GET | `/api/v1/ready` | Readiness probe (checks downstream) |
| GET | `/api/v1/usage` | Token usage statistics |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions (supports SSE streaming) |
| GET | `/api/v1/models` | List available LLM models |
| POST | `/api/v1/chat` | Generate a chat completion with model-based provider routing |
| POST | `/admin/api-keys` | Create a new API key (admin only) |
| GET | `/admin/api-keys` | List all API keys (admin only) |
| DELETE | `/admin/api-keys/{prefix}` | Revoke an API key by hash prefix (admin only) |
| GET | `/admin/usage/daily` | Get daily token usage for an API key (admin only) |
| GET | `/admin/usage/monthly` | Get monthly token usage for an API key (admin only) |

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
        ContextMiddleware (request_id)
                  │
                  ▼
       RequestLoggingMiddleware
                  │
                  ▼
      Auth / Rate Limit / Quota
          FastAPI dependencies
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
         │                  │
    InMemory/Postgres  ProviderRouter
                         ┌───┴────┐
                         ▼        ▼
                  OpenAIProvider  OllamaProvider
                         │        │
                         ▼        ▼
                    OpenAI API  Ollama Server

              MockProvider（测试模式）
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
├── quota/          # Token quota (reserve/settle, repository, service)
├── ratelimit/      # Rate limiting (Protocol + memory impl + dependencies)
├── schemas/        # Pydantic request/response models
├── services/       # Business logic
├── usage/          # Token usage tracking (repository, service, collector)
└── main.py
```

### Design principles

- **Pydantic schemas** (`app/schemas/`) — typed request/response, no raw dicts
- **Service layer** — Router never calls Ollama directly; swap provider by changing only the service
- **Settings** (`app/core/settings.py`) — pydantic-settings reads `.env`, never hardcode configs
- **Logging** (`app/core/logging.py`) — dictConfig JSON logs with request ID, method, path, status, and latency
- **Exception handlers** (`app/core/exceptions.py`) — global error handling, no try/except in Router
- **Middleware** (`app/middleware/`) — full UUID4 request tracing, supports client-provided `X-Request-ID`
- **Lifespan** (`app/main.py`) — initializes and closes Provider/PostgreSQL resources with startup rollback

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

# OpenAI Provider (non-default `gpt-*` models route here)
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_DEFAULT_MODEL=gpt-4.1-mini
OPENAI_TIMEOUT_SECONDS=60

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

# Token quota (0 = disabled)
QUOTA_DAILY_TOKENS=0
QUOTA_MONTHLY_TOKENS=0
QUOTA_RESERVATION_TTL_SECONDS=600
QUOTA_RESERVATION_RENEWAL_SECONDS=60
```

### Key configuration notes

- `API_KEYS` format: `sk-xxx:name,sk-yyy:name2` (comma-separated, name optional)
- `ADMIN_API_KEYS` must also be present in `API_KEYS` (or registered via bootstrap)
- `INITIAL_API_KEY` auto-registers on startup (idempotent, `ON CONFLICT DO NOTHING`)
- Docker Compose requires both `INITIAL_API_KEY` and `ADMIN_API_KEYS` to be set
- `QUOTA_DAILY_TOKENS`/`QUOTA_MONTHLY_TOKENS`: set to `0` to disable, must be ≥ 0
- `QUOTA_RESERVATION_TTL_SECONDS`: lifespan of an active quota reservation; must be positive
- `QUOTA_RESERVATION_RENEWAL_SECONDS`: reservation renewal interval; must be positive and shorter than its TTL
- Quota uses a reserve/settle pattern: tokens are reserved before an LLM call and settled only after actual usage is persisted. `ReservationLifecycle` renews active reservations for both non-streaming and streaming requests, and releases them if renewal fails or a client disconnects.

> [完整路线图](docs/superpowers/specs/2026-08-03-project-roadmap.md)

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
- Request ID middleware: `X-Request-ID` header support, auto-generates full UUID4 hex
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
- Tests: no key → 401, wrong key → 401, valid key → pass, no keys configured → 401 (fail-closed), auth disabled → pass, health/ready bypass

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

### Sprint 4 (Day 1)

- Usage persistence: `UsageRepository` Protocol + `InMemoryUsageRepository` + `PostgresUsageRepository`
- `DailyUsageTable` with `ON CONFLICT DO UPDATE` upsert (unique constraint on api_key_hash, usage_date, model)
- `UsageService` refactored to accept `UsageRepository`, all methods now async
- `UsageCollector.record_chat()` / `record_stream()` now async (await service.record)
- Token quota: `QuotaConfig` (daily_token_limit, monthly_token_limit, default_reserve_tokens)
- Independent `quota_reservations` table tracks active reservations separately from persisted `DailyUsageTable` usage
- `QuotaService.reserve()`: atomic pre-check + token reservation before LLM calls in Chat/OpenAI routes
- `QuotaService.settle()`: removes a reservation only after actual usage is persisted
- `QuotaExceededError` with computed `retry_after`: daily → seconds until next UTC day, monthly → seconds until next month
- Chat/OpenAI routes: Auth → RateLimit → route-level reservation before LLM call
- `QUOTA_DAILY_TOKENS` / `QUOTA_MONTHLY_TOKENS` settings with `Field(ge=0)` validation
- InMemory `_daily` cleanup: prunes entries older than 90 days
- Usage query API: `GET /admin/usage/daily`, `GET /admin/usage/monthly` (admin only)

### Sprint 4 (Day 2)

- Shared `UsageRepository` singleton prevents memory-mode quota from losing persisted usage.
- PostgreSQL reservation creation uses a same-API-key transaction advisory lock; CI runs its Testcontainers integration suite with `INTEGRATION_TEST=1`.
- `ReservationLifecycle` renews reservations for native Chat, OpenAI non-streaming, and OpenAI streaming calls; renewal failure cancels the active operation and returns a 503 `QUOTA_UNAVAILABLE` error where an HTTP response is still possible.
- Streaming usage is persisted before settling; client disconnects and renewal failures explicitly release the reservation.
- Quota reservations include a conservative prompt-token estimate plus maximum completion tokens, and repositories report the exact daily or monthly limit that rejected a request.
- Settled reservations are deleted, authenticated usage summaries are isolated by API key, and admin month parameters require canonical `YYYY-MM` values.
- Request cancellation propagates to the active provider operation, while streaming model and token state is saved before yielding the final provider result.

### Sprint 4 学习总结

配额预留口径必须覆盖输入与最大输出，否则并发请求仍可突破按总 token 计费的限制。累计用量与在途预留应分开存储，但必须共享同一按 API Key 隔离的用量视图。异步生成器可能在 `yield` 后被调用方关闭，因此结算所需状态必须在交出结果前保存。将续租、取消、结算和释放集中在生命周期对象后，所有调用路径可以采用同一一致性规则。并发、事务和流式收尾逻辑需要通过真实 PostgreSQL 与服务层消费测试持续验证。

### Sprint 5

- 结构化日志：`dictConfig` + JSON Formatter，`RequestLogger` adapter 注入 `request_id`、`latency_ms`、`status_code` 为独立字段
- 敏感信息保护：`api_keys`、`admin_api_keys`、`initial_api_key`、`database_url` 迁移为 `SecretStr`，所有调用点适配 `.get_secret_value()`
- 生命周期加固：provider 在 try 块内赋值，`db_initialized` flag 守卫关闭顺序，启动失败正确回滚
- Readiness 多资源检查：provider ping + PostgreSQL `SELECT 1`，返回 `{"status":"ready","checks":{"provider":"ok","database":"ok"}}`
- 内存泄漏修复：`MemorySlidingWindowLimiter._queues` 旧 key 自然耗尽后删除条目；`APIKeyService._touch_cache` revoke 后清理
- UsageCollector DI 统一：`provide_usage_collector()` 注入，消除 `chat.py` 中内联 `UsageCollector(usage_service)` 构造
- `import time` 提升到模块级
- 测试：新增 3 个 readiness probe 测试（provider OK / provider 失败 / memory 模式无 DB 检查）

### Sprint 5 学习总结

敏感配置使用 `SecretStr` 的代价是调用点需要适配 `.get_secret_value()`，但相比手写 `mask_secret` 函数的"依赖人记得调用"模式，编译器/类型检查器强制保护更可靠。结构化日志的价值不在于 JSON 格式本身，而在于让 `latency_ms`、`status_code` 成为独立可查询字段——这要求在 middleware 中使用 `extra={}` 而非字符串拼接。内存泄漏的修复陷阱在于：简单的 `del` 方案可能因为保留本地引用而导致新请求拿到全新计数器，正确做法是用 `is_new` 标志区分两条路径。

### Sprint 6

- Request ID 使用完整 UUID4 hex，避免大规模跨实例日志聚合中的实际碰撞风险
- Token usage 解析显式拒绝布尔值，避免 Python `bool` 是 `int` 子类带来的错误计数
- Ollama 流式响应将非 JSON 行按流汇总为单条 warning，仅记录模型、数量和最大行长度
- 新增回归测试，覆盖完整 UUID4、公开 token 解析路径、日志限量和敏感内容保护

### Sprint 6 学习总结

防御性类型检查需要考虑 Python 类型继承关系，而不能只依赖直观语义。流式协议中的坏行可以跳过，但诊断日志必须同时控制敏感内容和放大风险。跨实例追踪标识符应按系统生命周期内的累计请求量评估碰撞概率，而不是只看单实例流量。

### Sprint 7.1

- 新增 OpenAIProvider，支持非流式 Chat Completions、SSE 流式响应和模型列表
- OpenAI API Key 使用 `SecretStr`，共享 `httpx.AsyncClient` 负责连接复用和生命周期关闭
- 新增 OpenAI 类型化异常，区分网络故障、模型不存在、HTTP 请求错误和协议错误
- 流式解析使用显式终止状态，严格限制 terminal、usage-only、`[DONE]` 和 EOF 的顺序
- 所有 usage token 字段统一执行非负整数校验，并按字段合并跨帧部分统计
- Sprint 7.1 保持现有 DI、路由和公开 API 不变，ProviderRouter 将在 Sprint 7.2 实现

### Sprint 7.1 学习总结

OpenAI SSE 转换不仅需要字段映射，还需要状态机验证终止帧与 usage-only 帧的顺序。统一校验 token 字段可以阻止负数、布尔值和显式 `null` 污染用量与配额统计。部分 usage 必须逐字段合并，避免后续帧的字段缺失清除已有计数。将文本边界和类型化异常收敛在 Provider 层，可以让上游协议错误在进入业务层前被明确识别。

### Sprint 7.2

- 新增 ProviderRouter，默认模型优先使用 Ollama，其余 `gpt-*` 模型路由至 OpenAI
- Factory 在 Ollama 模式下创建 Router，现有 FastAPI DI 无需改动即可获得多 Provider 路由
- Router 实现完整 `LLMProvider` Protocol，非流式和流式请求都保持原始 payload
- Models 与 Readiness 继续使用默认 Provider，避免未配置 OpenAI 时影响默认 Ollama 部署
- Router 生命周期在异常或取消时仍会关闭全部唯一 Provider，并通过异常组保留多项关闭失败
- 新增默认模型优先级、路由、Factory、Protocol、取消和多关闭失败回归测试

### Sprint 7.2 学习总结

将路由实现为 `LLMProvider` 可以在不修改 Service 和端点的情况下接入多模型选择。默认模型的精确匹配必须优先于名称前缀，避免本地模型名触发意外外部请求。生命周期聚合对象必须在异常或取消时继续清理全部资源，并保留所有关闭故障。通过 Factory 返回 Router，现有缓存和 FastAPI 依赖注入边界可以保持稳定。
