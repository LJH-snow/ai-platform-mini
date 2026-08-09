# 开发日志（Sprint Log）

本文件是 `README.md` 的配套开发日志，记录每个 Sprint 的交付内容、学习总结与
Code Review 沉淀。README 只保留面向使用者/面试官的当前能力概览；历史演进细节
统一归档在这里。

> 从 Sprint 1 到当前最新 Sprint 的逐条记录与「学习总结」均保留，供 Code Review
> 与面试复盘使用。

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
- API 只返回步骤和事件安全摘要，不暴露原始工具参数、原始工具输出或 Provider 原始响应
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

### 阶段 5 / Agent Console 收口与阶段 6 浏览器验收

- 完成键盘可访问性、单独低频 live region、非颜色状态表达、Step/Tool/RAG disclosure、Request ID/Run ID 复制反馈、Chat/Agent 错误恢复和旧 Run 隔离。
- 阶段 6 初始前端五项门禁通过：format、lint、typecheck、7 个测试文件中的 79 个测试和 build；后续收口扩展为 13 个测试文件、141 个测试；真实浏览器已通过开发期 Vite proxy 验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待后的“后端终态未知”、offline 后 `connection_lost`、恢复网络后的重试成功，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行。
- 真实浏览器已验证 320/375/768/1024/1440 五档无横向溢出，并核对 Agent 模式展示。`npm run a11y:smoke` 使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过：初始空态与真实 Agent/RAG 状态 axe `violations=0`；初始空态有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断），不是 violation，也不能写成 axe 完全无 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出通过。Ollama 已安装 `nomic-embed-text`，真实调用 `/api/embed` 返回 1 个 768 维向量；PostgreSQL/pgvector 空库的 Agent SSE 路径已观察到 `RAG loading` → `knowledge_base_empty` → `run_completed`。随后使用仓库已有 `docs/superpowers/specs/2026-08-04-agent-runtime-design.md` 真实 ingest 53 个 chunks，浏览器真实来源路径显示 `success_with_sources` 和 5 条真实来源，公开字段为 `document_id`、`chunk_id`、`chunk_index`、`distance`、`content` 的安全投影；该次 UI Run 后续因 `token_budget_exceeded` 停止。另一次直接真实 SSE 请求使用 `token_budget=8192`、`max_steps=3`，收到 `rag_started`、`tool_completed`（`success_with_sources`，5 条 refs）、多个真实 `answer_delta`，并以唯一 `run_timed_out`（`deadline_exceeded`）终止，因此不能把该次请求记录为 `run_completed`。当前默认 `RAG_ENABLED=false`；以上 RAG 浏览器验证使用显式启用的真实本地依赖，不能将测试或安全投影写成伪造来源。完整 VoiceOver/NVDA/Orca 仍未验证，浏览器 DOM、键盘、ARIA、live region 和五档响应式已验证。
- 保留阶段 2—5 的 Chat SSE、同步 Agent Trace、Tool 状态和 RAG 来源契约；阶段 6 已实现 Agent SSE、实时 Trace 和实时 RAG 状态投影，持久化查询和回答内精确引用仍未实现。

#### 阶段 5 学习总结

本阶段确认可访问性状态应与视觉增量渲染分离，避免 SSE 内容更新造成过度播报；真实浏览器验证也必须覆盖代理、断连、重试和停止等待等状态边界。开发期 Vite proxy 让浏览器能够在不把 key 注入 bundle 的前提下观察真实 `answer_delta`、Trace 和 Tool Call；RAG 已通过真实 embedding、空库查询、53 个 chunks ingest、5 条安全来源和真实超时终止路径验证。成功来源那次 UI Run 因 `token_budget_exceeded` 停止，直接 SSE 验证则以唯一 `run_timed_out(deadline_exceeded)` 终止，不能改写为成功完成。浏览器 DOM、键盘、ARIA、live region 和五档响应式已验证，但完整屏幕阅读器仍受环境限制未完成；阶段 7 未进入，当前等待人工 Code Review。

### 阶段 6 Review 修复收口（待人工 Code Review）

- 将 Agent SSE 的“仍在执行”状态与最后展示事件状态分离，`tool_completed`/`tool_failed` 后不会误启用输入或覆盖活动请求。
- 每次新的 Agent Run 都重置流式 reducer，保证 sequence、terminal、run_id、回答和 Trace 不跨 Run 污染。
- Run 启动后发生 prompt quota 扩展或 reservation 续期失败时，Runtime、Service 和 SSE 统一以唯一 `run_failed` 终态收口；setup failure 只保留给 Run 尚未启动的初始化失败。
- Agent SSE producer 发生未预期异常时，根据是否已观察到 `run_started` 选择 `stream_setup_failed` 或合成唯一 `run_failed`，避免已启动 Run 被错误伪装成 setup error。
- Agent SSE 显式把 `X-RateLimit-Limit`、`X-RateLimit-Remaining` 和 `X-RateLimit-Reset` 传入实际的 `StreamingResponse`。
- 第三轮阶段 6 修复后的基线为后端 478 passed、28 skipped，前端 7 个测试文件 83 passed；后续 RAG preset、预算和布局收口后，前端测试扩展为 13 个文件、141 个测试，另有 1 个既有 Starlette/httpx 弃用警告。

#### 阶段 6 Review 修复学习总结

这轮修复确认了 SSE 的生命周期真相不能由最后一个展示事件推导，必须单独维护流是否仍在执行。流式 reducer 的 terminal 状态属于单个 Run，下一次执行前必须显式初始化，而不能依赖上次终态自然覆盖。配额续期失败通过带有领域异常标记的任务取消传递给 Runtime，并由 Service 统一记录用量、释放 reservation 和输出唯一失败终态，同时保留普通 Chat SSE 的既有异常语义。实际响应 Header 必须写入最终返回的 `StreamingResponse`，不能只修改 FastAPI 注入的临时 `Response`。此外，SSE producer 的异常分类必须依赖已观察到的生命周期事件，而不能仅依赖是否已经观察到终止事件。

### 管理员后台与 HR RAG 演示

当前前后端已支持管理员控制台：管理员登录后可以创建普通用户 API Key（原始 Key 只在创建成功时显示）、查看普通 Key 状态、撤销普通 Key、按北京时间查看 Token 用量，以及查询 Agent Run、工具调用和 RAG 来源的安全摘要。普通用户可在前端“用户 API Key”区域粘贴普通 Key，不需要在前端启动时注入用户 Key；开发环境代理仍可通过 `AI_PLATFORM_DEV_API_KEY` 提供可选的本地 fallback。

认证 Key 使用 PostgreSQL 持久化时请设置 `AUTH_STORAGE=postgres`，RAG 演示请设置 `RAG_ENABLED=true` 并确保 PostgreSQL/pgvector 与 Ollama 可用。完整 HR 演示流程、提示词和常见问题见 [管理员、API Key 与 HR RAG 演示说明](docs/admin-rag-demo.md)。

### 产品化前端平台壳层（2026-08-06）

前端默认进入 **AI Platform Mini · 平台概览**，不再直接把工程控制台作为首页。平台导航现在包含：

- 平台概览：展示 API Gateway、Model Provider、Agent Runtime 和 RAG 的真实配置状态，以及四条 HR 演示路径。
- 对话工作台：保留真实 Chat SSE、Agent SSE、Agent Trace、Tool Call、RAG 来源、Request ID 和重试/停止行为。
- Prompt Studio：提供代码审查、技术总结、面试模拟和知识库问答模板；模板编辑和保存使用浏览器 `localStorage`，可一键带入真实对话。
- 模型目录：通过现有 `/api/v1/models` 读取真实模型列表，不伪造模型启停或删除能力。
- 管理员后台：继续复用 API Key、Token 用量和 Agent Run 审计页面。

本轮只扩展展示层，没有新增虚假统计和不存在的后端接口。默认首页不配置普通用户 Key 时会明确显示 `Key required`，模型目录也会提示先配置普通用户 Key。HR 演示建议按“平台概览 → Agent 工作流 → Trace/RAG 来源 → Prompt Studio → 管理员审计”的顺序进行。

### Sprint 12（PDF 文档入库已完成）

- 新增 `POST /api/v1/rag/documents`：接收 multipart PDF，执行签名校验、大小/页数/文本长度限制、`pypdf` 文本提取、分块、Ollama Embedding 和 pgvector 入库。
- 新增 `GET /api/v1/rag/documents`：只返回文档元数据、分块数、文本字符数、Embedding 模型和创建时间，不返回原文或向量。
- 上传接口返回 `202` 和任务状态；通过 `GET /api/v1/rag/tasks/{task_id}` 查询 queued/processing/completed/failed 状态。worker 仅在进程内暂存 PDF bytes，不保存原始 PDF 文件。
- 文档和 chunks 使用 UUID；文档按 API Key 的 SHA-256 hash 隔离。新增 `DELETE /api/v1/rag/documents/{document_id}` 和 `GET /api/v1/rag/documents/{document_id}/preview`，仅允许所属 Key 访问。
- 新增知识库页面：支持选择/拖拽 PDF、显示真实入库状态、列出已索引文档，并能跳转到 RAG 问答工作台。
- 新增稳定错误边界：无效 PDF 返回 `RAG_DOCUMENT_INVALID`，超出上传限制返回 `RAG_DOCUMENT_TOO_LARGE`，存储和 Embedding 故障继续返回 503 类错误。
- 新增 `pypdf` 和 `python-multipart` 依赖，以及上传大小、页数和文本字符数配置项。

### Sprint 12 学习总结

这次把 RAG 从“已有检索能力”延伸到“可演示的文档入库闭环”，前端展示的每个状态都对应真实后端阶段，没有伪造上传进度。PDF 解析只把受限的纯文本交给分块和 Embedding，API 返回安全元数据和有界文本预览，避免把向量或原始文件暴露给浏览器。文档列表复用 pgvector 元数据和分块聚合结果，并通过 API Key hash 实现租户隔离。

> 当前队列是单进程内存实现，重启后未完成任务不会恢复；文档数据本身按 API Key hash 隔离。同名 PDF 上传会返回冲突，不会静默覆盖已有文档。

### RAG readiness、Agent preset 与 HR 演示收口（2026-08-06）

- `/api/v1/ready` 现在返回稳定的顶层 `rag` 能力状态：是否启用、数据库状态、Embedding 状态、模型名和安全原因码；前端启动时读取真实状态，不再把缺失的 runtime config 误判为 RAG 关闭。
- 知识库页面区分检查中、已就绪、空知识库、数据库不可用、Embedding 不可用和健康检查失败；文档数量只来自当前 API Key，失败时显示“不可用”而不是猜测数量。
- `AgentRunRequest.preset="rag"` 是受限能力：只由知识库入口设置，要求先执行 `knowledge_search`；普通 Agent 和 Chat SSE 不受影响。没有来源时显示真实 `no_relevant_sources`，不把模型常识包装成知识库答案。
- Chat SSE 与 Agent Run 在 UI 上有独立模式标识。Chat 模式不展示 Tool/RAG Trace；Agent 模式展示真实步骤、工具、来源和终态。
- 桌面端使用固定视口和独立滚动容器，会话区、Trace 区和平台导航不会被长回答互相撑开；移动端恢复单列自然滚动并保持无横向溢出。

#### 本阶段学习总结

本阶段把“能调用模型”推进为“能解释模型如何完成任务”：通过受限 preset 将 RAG 约束放在服务端，而不是依赖前端提示词；通过 readiness 契约让前端展示真实基础设施状态；通过独立滚动容器解决长对话的可用性问题。验证覆盖普通 Chat、Agent Tool Call、真实 RAG 来源、无相关来源、页面滚动和移动端布局，并保留未验证的屏幕阅读器边界。

### Agent 预算与 RAG 回答修复（2026-08-06）

- 前端和后端统一安全默认值：`token_budget=8192`、`max_steps=4`、`timeout_seconds=60`，并保留服务端上限。
- Runtime 仍会真实执行 Token budget 检查；超限时返回 `stopped/token_budget_exceeded`，不伪造最终回答。SSE 增加真实累计 Token，前端能展示预算超限时的实际用量。
- RAG 工具输出有界截断，并保护 `document_id`、`chunk_id` 等来源标识；没有来源或回答时，UI 不生成假回答或假引用。
- 真实验证：知识库问题完成 `knowledge_search → final_answer`，返回真实来源和 `completed/direct_answer`；预算超限、超时和无相关来源路径也分别通过测试覆盖。

#### 本阶段学习总结

本阶段定位并修复了 RAG 检索成功但最终回答未生成的预算问题，同时没有通过删除预算检查来“修好”页面。通过显式请求参数、前后端边界校验、真实 SSE usage 和有界工具输出，解决了可用性问题并保留了 Agent 的失败真实性。后续仍可独立优化多轮预算语义和 Ollama `num_ctx`，不把它们隐藏成已完成能力。

### Sprint 13（LangGraph PDF Workflow API 化）

- 新增 `PDFReportWorkflowService`，封装 `PDFReportWorkflow` 的构建、执行、resume
  和状态读取；复用 `RAGService.prepare`、`ProviderRouter` 和
  `app/rag/pdf_extractor.py`，不复制业务逻辑。
- 新增 workflow API：上传创建、状态查询、approve、reject 四个端点；上传同步执行
  到第一个 interrupt 或完成，返回 `thread_id`、阶段、草稿摘要/报告与安全错误信息。
- 新增 PostgreSQL checkpointer（`langgraph-checkpoint-postgres`）和
  `workflow_runs` 运行元数据表；默认 `WORKFLOW_STORAGE=memory`，单进程本地开发
  无需 PostgreSQL；显式设置 `WORKFLOW_STORAGE=postgres` 后服务重启可恢复同一
  `thread_id` 并继续审批/生成。
- 鉴权与隔离：所有查询/审批沿用 Bearer API Key，按 `owner_key_hash` 隔离，
  缺失、跨租户、非法 id 统一返回 `404 WORKFLOW_NOT_FOUND`。
- 测试覆盖 service interrupt/approve/reject/max revisions、同一 store 新实例
  resume、API 鉴权与跨租户 404，以及 Testcontainers 真实 PostgreSQL 跨“重启”
  恢复；测试不调用真实 LLM、不访问外网。
- 本轮不改动 `AgentRuntime`、现有 Chat/Agent/OpenAI API 与 SSE 契约；任务队列、
  SSE 进度推送和历史任务列表留待后续。

#### Sprint 13 学习总结

把 LangGraph 工作流接成 API 的关键是把 checkpointer、运行元数据和服务边界拆开：
LangGraph 负责线程状态恢复，`workflow_runs` 负责租户归属与安全状态投影，Service
统一处理失败记录和 404 语义。PostgreSQL checkpointer 使用独立 psycopg 连接池，
避免与 SQLAlchemy 引擎生命周期混在一起，并通过 serde 定制解决
`RAGReference` 的 msgpack 序列化警告。同步执行到 interrupt 的模型让前端可以先
拿到 `thread_id` 再异步审批，同时把任务队列和 SSE 留给后续切片。

#### Sprint 13 Review 修复

- 默认 `WORKFLOW_STORAGE` 从 `postgres` 改为 `memory`，与 README Quick Start
  的“无需 PostgreSQL”保持一致；无 DB 环境可直接启动，不再卡在连接重试。
- `topic` 超过 `VARCHAR(1024)` 时先截断到 1000 字符，避免 DB 插入 500。
- `start()` 的 PDF 写入和 `repository.create()` 纳入同一个 `try/finally`，
  任何一步失败都会清理磁盘临时文件。
- `approve`/`reject` 引入 CAS 原子状态迁移（`pending_approval → running` 条件
  `UPDATE`），防止并发竞争导致重复生成或决策覆盖；已完成后再审批返回 409。
- README 四处默认值表述同步更新，新增 3 个自动化测试覆盖截断、清理和 CAS 路径。

#### Sprint 13 Review 学习总结

默认配置与文档不一致会让新用户直接踩坑，这是最容易被忽视但影响最大的 P1。
引入 CAS 原子状态迁移替代先读后写，是因为并发 approve/reject 会导致同一
checkpoint 被重复消费；PostgreSQL `UPDATE ... WHERE status = expected RETURNING`
天然支持这一点，内存实现也做了同样检查。PDF 清理必须和业务逻辑在同一个
`try/finally` 中，否则部分失败会留下磁盘垃圾。测试命名必须精确反映行为，
`concurrent` 和 `double` 在 async 代码里语义完全不同。

### Sprint 14（Workflow 前端面板）

- 新增 `frontend/src/workflow/client.ts`：封装 workflow API 调用，支持
  `AbortSignal` 以在面板卸载/重置时取消 in-flight 请求；错误处理覆盖
  401/403/404/409/413/429/5xx，统一 safeErrorMessage 不暴露内部字段。
- 新增 `frontend/src/workflow/WorkflowPanel.tsx`：PDF 上传、状态轮询、
  pending_approval 审批/拒绝、报告展示；轮询使用 setTimeout 链式调用 +
  `isFetchingRef` 锁避免重叠请求；可访问性包含 `aria-live`、`role=alert`、
  `aria-label`、`aria-describedby`。
- 接入 `App.tsx` 导航：复用现有 `effectiveApiKey` 与运行时配置，新增
  `'workflow'` 页面，与 dashboard/console/knowledge 等风格一致。
- 响应式样式：`900px` 以下双栏变单栏，`560px` 以下 meta 单列；状态标签
  颜色+文字并存，不依赖颜色作为唯一信息源。
- 新增 20 个前端自动化测试（8 client + 12 component），覆盖上传解析、
  状态流转、审批/拒绝/失败/网络错误/鉴权错误、轮询、重置面板；
  不访问真实后端和真实 LLM。

#### Sprint 14 学习总结

前端 client 必须支持 `AbortSignal`，否则组件卸载时的轮询请求会继续执行并
触发已卸载组件的 setState。`setTimeout` 链式调用比 `setInterval` 更适合
后端请求轮询，因为它天然防止重叠：上次响应回来后才排下次。可访问性不能
事后补，要从设计阶段就纳入：屏幕阅读器区域 (`aria-live`)、错误提示
(`role="alert"`) 和按钮语义 (`aria-label`) 缺一不可。测试命名要精确，
`concurrent` 和 `double` 在 async 代码里语义完全不同。

### Sprint 15（RAG 评估升级：CI 回归 + 报表持久化）

- 新增 `rag_evaluation_runs` 表：持久化每次评估的 dataset、retriever、模型、
  各项指标、用例数和 created_at；注册到 `_CORE_TABLES`，默认随 init_db 创建。
- 新增 `app/evals/repository.py` + `memory_repository.py` +
  `postgres_repository.py`：内存模式用于本地开发和测试，PostgreSQL 模式用于
  生产环境持久化；`list_recent` 支持分页查询最近 N 条记录。
- `scripts/evaluate_rag.py` 运行结束后自动写入一条 `RAGEvaluationRun` 记录；
  数据库不可用时降级为 `InMemoryRAGEvaluationRepository`，脚本不失败。
- 新增 `test_rag_ci_regression_meets_thresholds`：对 `rag_golden.jsonl` 用
  fake retriever 跑完整评估，断言 retrieval_success_rate >= 0.5、
  context_recall_at_k >= 0.4、answer_correctness_accuracy == 1.0 等阈值；
  离线、确定性、不调用真实 LLM。
- 新增 repository 测试：验证内存 save/list_recent 行为；脚本测试验证
  run 记录写入。
- **不默认引入 RAGAS**：LLM-as-a-judge 虽然能评估答案相关性和忠实度，但
  依赖外部模型调用、成本高、结果非确定性、引入额外依赖；当前评估以
  确定性指标（recall@k、retrieval success rate、latency）为主，
  answer_correctness 通过 `expected_answer_contains` 字符串匹配完成；
  后续如需 LLM judge，将以可选模块形式独立引入，不影响现有 CI 回归路径。

#### Sprint 15 学习总结

从命令行工具升级为可回归的平台能力，关键是把"评估结果"也当作持久化实体：
  `rag_evaluation_runs` 让团队能追踪每次评估的指标趋势，而不是每次都看
  本地 JSON 文件。CI 回归阈值不是越低越好，而是要贴近 fixture 的真实行为，
  过高会导致无害的波动触发失败，过低则失去回归意义。
  `InMemoryRAGEvaluationRepository` 降级策略保证了脚本在无 DB 环境也能跑完，
  这是工具类脚本和生产代码的重要区别——工具失败不应阻塞整个流程。

### Sprint 16（OpenTelemetry 指标、采样与 request_id 关联）

- 新增 `app/observability/metrics.py`：HTTP、LLM、Tool、RAG 四类服务的 counter
  与 histogram 指标，通过 `PeriodicExportingMetricReader` 每 5s 分批导出；提供
  `InMemoryMetricReader` 作为 test seam。
- 新增 `TELEMETRY_METRICS_ENABLED` 配置项，可独立关闭指标而保留 trace；
  OTLP endpoint 支持只写 base URL，trace exporter 自动补全 `/v1/traces`，
  metrics exporter 自动补全 `/v1/metrics`。
- 新增 `TELEMETRY_SAMPLING_RATIO`（0.0–1.0，默认 1.0）控制根 span 采样率；
  使用 `ParentBased(TraceIdRatioBased)`，子 span 跟随父 span 决策，保证
  同一请求要么完整出现、要么完全不出现。
- 新增 `app/observability/context.py`：基于 `contextvars.ContextVar` 的
  request_id 桥接层，在 LLM/Tool/RAG/Agent 子 span 上显式附加当前请求 ID；
  SSE/流式响应体迭代期间通过 `_instrument_stream` 重新绑定同一 request_id，
  修复 async generator 不继承创建时 contextvar 的问题。
- 新增 `AliasChoices` 支持标准环境变量 `OTEL_EXPORTER_OTLP_ENDPOINT`；
  `telemetry_sampling_ratio` 带 `[0.0, 1.0]` 范围校验。
- 修复 `_instrument_stream` 错误路径 status_code bug：取消和异常时 metrics
  分别记录 499 和 500，不再统一记录 200。
- 新增 662 个测试，覆盖 sampling ratio、metrics 指标、request_id 跨 span
  关联（含流式路径）、敏感字段不泄露、metrics 可独立关闭；`_instrument_stream`
  修复后全部通过。

#### Sprint 16 学习总结

OpenTelemetry metrics 与 traces 应共用同一配置入口（`TELEMETRY_ENABLED`），但允许
  `TELEMETRY_METRICS_ENABLED` 独立关闭指标——这在小规模部署中很实用，可以只保留
  trace 而不承担指标存储成本。`ParentBased` 采样对 LLM 可观测性至关重要：如果根
  span 被采样而子 span 独立决策，会导致 trace 中出现不完整的请求片段。contextvar
  桥接解决了 async generator 不继承创建时 context 的 Python 运行时限制，确保流式
  响应中的 LLM/Tool span 也能关联到正确的 request_id。`_instrument_stream` 的
  status_code bug 是典型的"正常路径和异常路径走同一 finally 分支但只用正常路径
  变量"的陷阱，修复方案用局部变量追踪有效状态，在 finally 中按条件分支。

### Sprint E2 P1（Workflow Builder 引擎）

- 新增 `app/workflows/engine/` 新引擎包（通用编排，与现有固定 PDF 工作流
  `app/workflows/` 平级且完全独立）：`models.py`（`NodeType`/`WorkflowNode`/
  `WorkflowEdge`/`WorkflowDefinition` + `from_dict`/`to_dict`、`NodeResult`、
  `WorkflowRunResult`、`truncate_summary` 摘要截断纯函数）、`validation.py`
  （`validate_definition` 中文错误消息、Kahn 拓扑无环、入边 ≤1、condition
  branches 校验、模板引用存在性 + 拓扑序早于引用者、条件表达式三字面形式
  正则校验；`render_template`/`evaluate_condition` 纯函数）、`executor.py`
  （`NodeExecutor` Protocol 注入边界、`WorkflowEngine` 拓扑序串行执行、
  condition 分支选择后仅执行选中目标、节点失败即停、output 节点值作为最终
  输出）。
- 分支边裁定：条件节点分支只由 `config.branches` 表达，不重复出现在
  `edges`；校验与拓扑排序把 branch target 计入图边，若分支边重复出现在
  edges 会因目标节点入边 >1 在校验期被拒绝——双源不一致在定义期拦截。
- 引擎零依赖：不 import 任何服务实现（ChatService/RAGService/ToolExecutor/
  AgentService 均不出现），所有外部能力经 `NodeExecutor` Protocol 注入；
  未注册节点类型 → failed run + 中文报错。
- 新增 `tests/test_workflow_engine.py` 30 个测试（14 校验 + 3 模板 + 5 条件 +
  7 引擎 + roundtrip/truncate），全套 941 passed 基线零变化；ruff/mypy 全绿。

#### Sprint E2 P1 学习总结

工作流编排的第一波重点是"把不确定性挡在校验期"：拓扑排序、模板引用存在性、
条件表达式白名单都在执行前 fail-fast，运行时只剩确定性执行路径。条件分支用
`branches` 单一事实源、edges 只放无条件边，靠入边 ≤1 校验天然拒绝"两处都写"
的不一致，比运行时检查更早暴露错误。引擎与服务的解耦通过 Protocol 注入完成，
fake executor 让全部执行语义（分支选择、失败停止、摘要截断）都可以无外部依赖
单测。摘要截断按字符而非字节计算，保证 CJK 文本 256 字符边界安全；截断标记
`...[truncated]` 让审计记录可读且不落原始大 payload。
