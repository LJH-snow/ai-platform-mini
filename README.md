# AI Platform Mini

基于 FastAPI 的轻量级 LLM Gateway，提供 OpenAI-compatible Chat API、Provider
抽象、API Key 鉴权、限流、Token 配额、Usage 统计和 SSE Streaming。

## Current status

- Current milestone: **Sprint 11 MCP foundation completed; production hardening next**
- Version: `0.1.0`
- Runtime: Python `3.12`–`3.14`（默认 `3.14`）
- Active routing: 默认模型 → Ollama，其余 `gpt-*` → OpenAI，其他模型 → Ollama；Mock 用于测试
- OpenAIProvider: 已接入 ProviderRouter、DI 和应用生命周期
- Storage: Memory 或 PostgreSQL
- RAG: 检索增强生成（实验性，需启用 `RAG_ENABLED=true` + PostgreSQL + pgvector + Ollama Embedding）
- Agent Runtime: 有界的模型决策→工具执行→结果回填循环，支持最大步数、超时、取消和 Token budget
- 前端 Agent Console：[规划完成，代码尚未实现](docs/roadmap/2026-08-05-frontend-agent-console-development-roadmap.md)
- Tool System: `ToolRegistry` + `ToolExecutor` + 低风险 `calculator`/`knowledge_search`，默认不开放任意文件、网络或 Shell 能力
- Verification baseline（2026-08-04）：
  - Default suite：通过（数据库集成测试按 `INTEGRATION_TEST` 条件跳过）
  - PostgreSQL/pgvector integration suite：通过
  - Ruff format/lint、mypy 和 Uvicorn 启动检查：通过

## Core capabilities

- OpenAI-compatible `POST /v1/chat/completions`，支持普通响应与 SSE 流式响应
- 原生 Chat、Models、Health、Readiness 和 Usage API
- 可替换的 `LLMProvider` Protocol、共享 HTTP 连接池和统一异常映射
- ProviderRouter 按模型自动选择 OpenAI 或 Ollama，Service 和公开端点无需感知 Provider
- OpenAIAdapter 无状态协议转换，将请求和非流式响应映射从 Service 提取到独立 Adapter
- Bearer API Key 鉴权、Admin Key 管理及 SHA-256 哈希存储
- 按 API Key 的滑动窗口限流，以及日/月 Token 配额
- 配额预占、续租、结算和断连释放，支持并发及长时间流式请求
- PostgreSQL Usage 聚合、API Key 持久化和 Testcontainers 集成测试
- Agent Runtime 核心状态、事件、Tool Protocol 和 `POST /api/v1/agent/runs` 应用层
- Tool Registry/Executor：Schema 参数校验、超时、异常安全归一化、输出截断和工具 Schema 导出
- RAG Tool：`knowledge_search` 复用 RAG prepare 阶段，返回带来源、距离和安全提示的结构化检索结果；Agent 可在回答前自主调用知识库
- MCP foundation：提供 stdio JSON-RPC Client、工具发现、allowlist、`MCPToolAdapter`、生命周期 health/readiness 查询，以及运行时调用失败/断线的确定性测试；不接入默认生产配置
- Calculator：基于 AST 白名单的受限算术执行，不使用 `eval()`/`exec()`
- JSON 结构化日志、完整 UUID4 Request ID、敏感配置脱敏和多资源 Readiness

## Evaluation Foundation

Evaluation Foundation 提供离线、确定性的 golden data contract 与顺序执行 runner：评测用例通过 JSONL 保存，runner 接受可注入的异步 `run_case`，不会调用真实 LLM 或外网。单用例结果记录状态、成功与否、答案/工具判定、工具序列、步骤、延迟、Token 用量和错误；汇总提供任务成功率、声明工具期望用例的 tool selection accuracy、平均步骤、p95 延迟和 Token 总量/均值。`tests/fixtures/evals/agent_golden.jsonl` 是 30 条本地契约 fixture，覆盖 direct-answer、calculator 和 knowledge_search，它明确不是线上模型结果，也不包含密钥。当前尚未接入真实模型 CI、数据库报表或 RAG Recall@K。

学习总结：本 Sprint 学到应先固定可序列化的评测数据契约，再通过依赖注入让 runner 保持离线和可重复。将答案包含判断与完整有序工具序列判断拆开，使失败原因和聚合指标更清晰。p95 对空集返回 `0.0`，tool accuracy 在没有声明 expected_tools 时返回 `None`，避免制造误导性统计。通过 JSON 标准库解析而不是 `eval`，并用异常隔离保证单个 case 不会阻断整批评测。

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
| GET | `/api/v1/health/mcp` | MCP lifecycle/readiness status (when enabled) |
| GET | `/api/v1/usage` | Token usage statistics |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions (supports SSE streaming) |
| GET | `/api/v1/models` | List available LLM models |
| POST | `/api/v1/chat` | Generate a chat completion with model-based provider routing |
| POST | `/api/v1/chat/rag` | RAG-enhanced chat completion (requires `RAG_ENABLED=true`) |
| POST | `/api/v1/agent/runs` | Bounded Agent Runtime run (model decision and controlled tool loop) |
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
          │        │              │
          ▼        ▼              ▼
    APIKeyRepository  LLMProvider Protocol  OpenAIAdapter
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
├── adapters/        # Protocol adaptation (OpenAI-compatible request/response mapping)
├── agents/         # Framework-independent Agent Runtime (state, protocols, loop, events)
├── tools/          # Tool Protocol, Registry, Executor and safe built-in tools
├── api/            # Router layer (chat, agent, openai, admin, health, models)
├── auth/           # Authentication & key management (service, repository, dependencies)
├── core/           # Infrastructure (settings, logging, exceptions, container, context)
├── db/             # Database (models, session, init)
├── exceptions/     # Provider-specific + domain exceptions
├── middleware/     # Context middleware (request_id)
├── providers/      # LLM Provider layer (Protocol + implementations)
├── quota/          # Token quota (reserve/settle, repository, service)
├── rag/            # Retrieval-Augmented Generation (embedder, vector store, chunker, service)
├── ratelimit/      # Rate limiting (Protocol + memory impl + dependencies)
├── schemas/        # Pydantic request/response models
├── services/       # Business logic
├── usage/          # Token usage tracking (repository, service, collector)
└── main.py
```

### Design principles

- **Pydantic schemas** (`app/schemas/`) — typed request/response, no raw dicts
- **Service layer** — Router never calls Ollama directly; swap provider by changing only the service
- **Agent boundary** — `AgentRuntime` only depends on typed domain models and Protocols; `AgentService` owns Chat/Quota/Usage integration
- **Adapter layer** — stateless protocol conversion between public API schemas and internal schemas
- **Settings** (`app/core/settings.py`) — pydantic-settings reads `.env`, never hardcode configs
- **Logging** (`app/core/logging.py`) — dictConfig JSON logs with request ID, method, path, status, and latency
- **Exception handlers** (`app/core/exceptions.py`) — global error handling, no try/except in Router
- **Middleware** (`app/middleware/`) — full UUID4 request tracing, supports client-provided `X-Request-ID`
- **Lifespan** (`app/main.py`) — initializes and closes Provider/PostgreSQL resources with startup rollback
- **MCP health boundary** — reports configured Server lifecycle/discovery state only; no active ping, reconnect or HTTP/SSE transport is implied

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

# RAG (Retrieval-Augmented Generation)
RAG_ENABLED=false
RAG_EMBEDDING_MODEL=nomic-embed-text
RAG_EMBEDDING_DIMENSIONS=768
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=5
RAG_MAX_CONTEXT_CHARS=10000
RAG_MAX_DISTANCE=0.35
RAG_EMBEDDING_TIMEOUT_SECONDS=60

# MCP (disabled by default; JSON array of explicitly allowlisted servers)
MCP_ENABLED=false
MCP_SERVERS_JSON=
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

### RAG configuration notes

- `RAG_ENABLED=true` **requires** PostgreSQL with the `pgvector` extension and an accessible Ollama instance for embeddings
- `RAG_EMBEDDING_DIMENSIONS` is currently locked to `768` (MVP fixed schema); changing it requires a database migration
- `RAG_MAX_DISTANCE` uses cosine distance (0 = identical, 2 = opposite); results with distance > threshold are excluded
- `RAG_TOP_K` controls how many candidate chunks are retrieved before distance filtering (max 50)
- `RAG_MAX_CONTEXT_CHARS` limits the total character length of injected context (max 100,000)
- To ingest documents, run: `python scripts/ingest.py <path-to-txt-file>` (requires `RAG_ENABLED=true` and running Ollama)
- Empty knowledge base → `KnowledgeBaseEmptyError` (404); all retrieved results exceeding `RAG_MAX_DISTANCE` → `NoRelevantContextError` (404)

### MCP configuration notes

- `MCP_ENABLED=false` keeps MCP disabled and does not parse `MCP_SERVERS_JSON`
- `MCP_SERVERS_JSON` must be a JSON array; each entry requires `name` and a non-empty `command` array
- Each server can configure `allowed_tools`, `max_risk_level`, startup/request timeouts and string environment variables
- MCP tools are discovered during application startup and closed during shutdown; an unavailable server is isolated and does not block other configured servers
- Discovered tools require the `mcp:server:<server_name>` permission; the application grants it only to the Agent runtime for successfully discovered, explicitly configured servers, never from model output or user input
- Real stdio tools must provide explicit read-only/destructive annotations; unknown risk metadata is rejected (fail-closed), and duplicate tool names isolate the affected Server

> [完整路线图](docs/roadmap/2026-08-04-agent-runtime-development-roadmap.md)
> [Sprint 8 设计说明](docs/superpowers/specs/2026-08-04-agent-runtime-design.md)

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

### Sprint 7.3

- 新增 `app/adapters/openai_adapter.py`，将请求转换和非流式响应转换从 `OpenAIService` 提取到无状态 `OpenAIAdapter`
- Adapter 不持有运行时依赖：`completion_id` 和 `fallback_created` 由 Service 生成并显式传入
- `OpenAIService` 通过构造函数注入 Adapter，`get_openai_service()` 负责创建和注入
- 流式 SSE 组装和上游 `OpenAIProvider` 状态机保持原位，不在本次提取范围内
- 新增 18 个 Adapter 单元测试，覆盖请求映射、响应映射、usage 边界和时间解析
- 增强流式回归测试：完整 SSE 帧序列验证、公共字段一致性、空流 fallback
- 增强 history 顺序测试：多前置消息断言完整 role/content 顺序
- 三轮 Code Review 发现并修正了时间戳语义变更和取消信号泄漏问题

### Sprint 7.3 学习总结

纯职责提取必须严格保持既有行为，即使是"改进"也应拆分为独立 Sprint。本次最初将 naive `created_at` 强制解释为 UTC，改变了非 UTC 部署环境的公开 API 输出，违反了"重构不改变行为"原则。`BaseExceptionGroup` 在混合 `CancelledError` 时不会降级为 `ExceptionGroup`，lifespan 的 `except Exception` 无法捕获——但用 `except BaseException` 修复会吞掉取消信号，破坏编排平台的取消传播语义。正确的做法是保持 `except Exception`，将 `BaseExceptionGroup` 泄漏问题留给专门的生命周期修复 Sprint。

### Sprint 7.4

- 修复 `ProviderRouter.close()` 的 `BaseExceptionGroup` 泄漏：Provider 内部 `CancelledError` 包装为 `RuntimeError`，确保所有非外部取消异常都是 `Exception` 子类
- 区分外部取消和 Provider 内部取消：通过 `current_task().cancelling() > 0` 检测外部取消，保存原始 `CancelledError` 并在所有 Provider 关闭后重新抛出
- 外部取消信号继续传播到 Uvicorn/编排平台，不被 `except Exception` 吞掉
- Provider 内部取消与普通关闭异常并存时，外部取消优先传播，其他异常记入日志
- 新增外部取消回归测试：真实 `task.cancel()` 场景验证取消传播和 Provider 全部关闭
- 新增双 CancelledError Provider 测试、重复关闭测试、lifespan 捕获 ExceptionGroup 回归测试

### Sprint 7.4 学习总结

`asyncio.CancelledError` 既是 Provider 可能主动抛出的异常，也是外部任务取消的信号，两种来源不能无差别处理。`current_task().cancelling()` 是 Python 3.11+ 提供的可靠方式区分当前任务是否正在被外部取消。资源清理代码必须尝试关闭所有组件，但外部取消应优先传播，其他关闭异常至少通过日志保留。`BaseExceptionGroup` 在全部子异常都是 `Exception` 时自动降级为 `ExceptionGroup`，混合 `CancelledError` 时则不会降级——包装为 `RuntimeError` 可以避免类型泄漏。

### Sprint 7.5

- RAG MVP：检索增强生成端点 `POST /api/v1/chat/rag`
- `app/rag/` 模块：OllamaEmbedder（调用 Ollama `/api/embed`）、PgVectorStore（pgvector 余弦距离检索）、Chunker（固定窗口 + 重叠切分）、RAGService（两阶段 prepare/answer）
- 相似度阈值 `RAG_MAX_DISTANCE`：过滤余弦距离超过阈值的检索结果，区分空知识库（`KnowledgeBaseEmptyError`）与全部不相关（`NoRelevantContextError`）
- 上下文注入使用随机 UUID 边界标记 + 内容净化，防止恶意文档伪造边界或注入指令
- 配置上限：`RAG_TOP_K ≤ 50`、`RAG_MAX_CONTEXT_CHARS ≤ 100000`、`RAG_MAX_DISTANCE ≤ 2.0`
- `scripts/ingest.py`：离线文档摄入脚本，支持 SHA-256 去重、同路径文档替代、事务级 advisory lock
- 数据库模型：`rag_documents`（文档元信息）+ `rag_document_chunks`（分块 + pgvector embedding 列）
- RAG 路由：认证→限流→RAG 服务→配额预占→LLM 调用→结算，配额估算包含 RAG 上下文 token
- `RAG_ENABLED=false` 时 RAG 端点返回 503，不暴露给未认证调用者

### Sprint 7.5 学习总结

RAG 两阶段设计（prepare/answer）将检索与生成解耦，允许在配额预占前获得完整的 token 估算——包括注入的上下文。余弦距离阈值需要区分两种"无结果"语义：知识库为空 vs 全部不相关，否则调用方无法判断是应该补充文档还是调整查询。pgvector 检索不应在 SQL 层硬编码距离上限，应返回原始距离交给服务层按配置阈值过滤，否则阈值变更需要同时修改应用代码和 SQL 查询。

### Sprint 8

- 新增 `app/agents/` 领域层：`AgentState`、`AgentDecision`、`AgentStep`、`AgentEvent`、`AgentRunResult` 及 `AgentModel`/`AgentTool` Protocol
- 实现独立于 FastAPI 的有界 Agent Runtime：模型决策、Tool 调用、结果回填和多步循环
- 支持 `max_steps`、deadline/timeout、外部取消和 provider-reported Token budget；未知 Token 用量不会被伪装成 0
- 新增 `POST /api/v1/agent/runs`，通过 `AgentService` 复用现有 ChatService、鉴权、限流、Quota、Usage 和统一异常边界
- API 只返回步骤和事件摘要，不暴露工具参数、工具输出或 Provider 原始响应
- 本 Sprint 不声称已经完成通用 Tool Registry、RAG Tool、MCP、Memory、Multi-Agent、Agent SSE 或前端 Agent UI

### Sprint 8 学习总结

Agent Runtime 不应直接依赖 FastAPI 或具体模型客户端，而应通过 `AgentModel` 和 `AgentTool` Protocol 保持领域层可独立测试。应用层适配现有 `ChatService` 时，模型输出采用受限 JSON 决策协议，解析失败必须进入受控失败路径。Token 用量缺失时保留 `None` 并显式标记估算状态，避免把未知数据误报为精确统计。最大步数、deadline、取消和工具输出边界共同保证 Agent 不会以不可观测的无限循环运行。

### Sprint 9

- 新增 `app/tools/`，建立 `Tool` Protocol、`ToolDescriptor`、`ToolRegistry` 和 `ToolExecutor`。
- `ToolRegistry` 负责工具注册、重名拒绝、查询和稳定的模型函数 Schema 导出。
- `ToolExecutor` 在工具实现前执行对象 Schema 校验，并统一处理超时、普通异常、未知工具和输出截断。
- `AgentService` 默认只注册低风险 `calculator`，`AgentRuntime` 支持 `ToolExecutor` 注入，同时保留 Sprint 8 的 Mapping 工具兼容路径。
- `calculator` 使用 AST 白名单实现 `+ - * / % **`，不提供任意文件、网络、Shell、MCP 或 RAG Tool。

### Sprint 9 学习总结

Tool Registry 解决“有哪些工具”，Tool Executor 解决“能否安全执行”，Agent Runtime 继续负责 Run/Step 循环，三者分工比把所有逻辑塞进 Chat API 更容易测试和演进。参数 Schema 必须在工具实现前校验，异常和输出也要经过统一边界，避免把内部细节直接暴露给模型。Calculator 使用 AST 白名单而不是 `eval()`，在保留演示价值的同时把执行面控制在低风险范围内。通过保留原有 Mapping 工具注入路径，本 Sprint 可以增量引入治理能力而不破坏 Sprint 8 Runtime。

> [Sprint 9 Tool System 设计说明](docs/superpowers/specs/2026-08-04-tool-system-design.md)

### Sprint 10

- 新增 `KnowledgeSearchTool`，通过现有 `ToolRegistry`/`ToolExecutor` 接入 Agent Runtime。
- Tool 只调用 `RAGService.prepare`，不直接依赖 Agent Runtime，也不重复实现 embedding、pgvector 检索、距离过滤或上下文截断。
- `PreparedRAGRequest` 新增结构化 `RAGReference`，向 Agent 返回实际纳入上下文的内容、文档/分块来源、距离和不可信内容提示。
- 空知识库、无相关上下文、RAG 存储不可用和 embedding 失败映射为稳定错误码，未知异常仍由 ToolExecutor 安全归一化。
- 容器仅在 RAG 服务可用时注册 `knowledge_search`；RAG 关闭时 Agent 继续保持 `calculator` 默认能力。
- 新增普通 Agent + Knowledge Search 集成测试，并保留 `/api/v1/chat/rag` 兼容链路。


### Sprint 11（MCP foundation 已完成，生产化切片待后续）

本轮完成 MCP foundation 的最小验收闭环：

- 增加基于 stdio 的 JSON-RPC MCP Client，支持 initialize、tools/list 和 tools/call；
- 增加 `MCPToolManager`，负责 Server 生命周期、工具发现、allowlist 和不可用 Server 隔离；
- 增加 `MCPToolAdapter`，将 MCP Tool 映射为现有内部 Tool Protocol；
- 默认通过 `mcp:server:<server_name>` 权限拒绝未授权调用；应用容器仅向 Agent runtime 授予已发现 Server 的服务端权限；
- 真实 stdio 工具必须声明只读/破坏性风险元数据，未知风险 fail-closed；重复工具名会隔离对应 Server；
- 已覆盖 fake client、不可用 Server、权限边界、真实子进程协议和 Agent 端到端调用测试。

本轮已完成受控 Settings 配置、FastAPI lifespan 接入、只读 MCP Agent 调用链、MCP Server 生命周期健康/就绪边界，以及发现完成后的运行时失败归一化测试。`/api/v1/health` 保持原有语义，`/api/v1/ready` 复用 MCP Manager 的就绪状态，并新增 `/api/v1/health/mcp`；测试 fixture 不依赖外网或第三方 MCP SDK，也没有注册到生产默认配置。当前仍未承诺 HTTP/SSE、重连、主动远端探活、生产部署、指标和追踪等能力，这些属于后续生产化切片。

#### Sprint 11 当前切片学习总结

MCP foundation 的关键是把外部协议限制在 Client 和 Adapter 边界内，Agent Runtime 继续只依赖内部 Tool Protocol。健康边界复用 Manager 生命周期状态，既能表达启动失败、部分 Server 可用和关闭状态，也不在本 Sprint 引入主动探活或重连。通过不依赖外网的 stdio fixture 验证运行时断线和单 Tool 失败归一化，确认失败不会污染其他工具或应用关闭流程；生产化仍需补充真实部署、探活、观测和恢复策略。

### Sprint 10 学习总结

RAG Tool 化的关键不是复制一套检索代码，而是把现有 `RAGService.prepare` 作为唯一检索入口，再通过结构化引用把结果交给 Agent。将来源、距离和清洗后的内容一起返回，既便于模型使用，也为后续引用展示和评测保留证据。容器根据 RAG 能力是否可用动态注册工具，使功能开关不会改变默认 Agent 的安全边界。通过区分可预期的知识库、存储和 embedding 错误与未知异常，Tool 层可以给模型稳定反馈，同时避免暴露内部实现细节。

> [Sprint 10 RAG Tool 化设计说明](docs/superpowers/specs/2026-08-04-rag-tool-design.md)

### Run Trace Foundation（当前切片）

本切片新增 `app/runs/`，基于现有 `AgentEvent` 和 `AgentRunResult` 生成安全的 Run Trace，当前仅支持**单 run 的脱敏内存 Recorder**以及 JSONL 导出/读取。Trace 会保留 run_id、可选 request_id/model、状态、停止原因、token usage、步数、工具摘要、耗时和经过截断的错误/消息摘要；默认不保存完整 prompt、API key、原始 tool arguments、完整 tool output、RAG 原文或 MCP 原文。

`AgentService` 的 Recorder 注入边界是可选的 `recorder_factory`：工厂必须为每次 `runtime.run()` 返回新的单 run Recorder；未配置时保持原有 Agent 行为。单个 `InMemoryRunTraceRecorder` 不得跨 run 复用，同一个 `AgentRuntime` 并发执行多个 run 时应使用 `recorder_factory` 隔离各自 trace，避免 request_id、事件和终态互相污染。

当前切片已覆盖直接回答、工具成功/失败、max steps、timeout/cancel、model error、Recorder 异常隔离、脱敏截断、JSONL round-trip 以及并发 request_id 隔离测试。后续 Sprint 13 的 PostgreSQL 持久化、SSE 推送和公开查询 API 均未实现，本切片也不提供这些能力。

#### Run Trace Foundation 学习总结

Run Trace 应该从 Runtime 已有事件和终态结果派生，而不是复制一套 Agent Loop。单 run Recorder 加上显式 `recorder_factory` 边界，可以在保持简单的同时避免并发状态污染。脱敏和截断必须位于记录边界，默认不保存 prompt、工具参数和外部检索原文。持久化、实时推送和查询接口应作为后续 Sprint 的独立能力演进。
