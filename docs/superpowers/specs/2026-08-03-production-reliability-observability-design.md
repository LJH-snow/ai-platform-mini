# Sprint 5: Production Reliability & Observability Foundation

## 1. 背景

AI Platform Mini 已具备 Provider 抽象、依赖注入、API Key 鉴权、Usage 统计、限流、PostgreSQL 持久化 Quota、OpenAI Compatible API、SSE Streaming 和 CI 测试体系。

Sprint 5 不再扩展业务能力，而是提升服务的部署可靠性与故障可诊断性。当前主要缺口包括：

- 日志仍以格式化字符串为主，缺少稳定的机器可读字段。
- 敏感配置缺少统一的日志保护边界。
- `lifespan` 已管理数据库和 Provider，但部分启动失败后的资源回滚行为没有形成明确契约。
- readiness 只检查 Provider，不能完整反映服务是否可以接收需要数据库的流量。

## 2. 目标

Sprint 5 将完成以下能力：

1. 使用 `logging.config.dictConfig()` 建立 JSON 结构化日志。
2. 在请求日志中统一输出请求 ID、方法、路径、状态码和耗时。
3. 防止 API Key、认证头和数据库凭据进入日志。
4. 在现有 FastAPI `lifespan` 上建立可测试的资源启动、回滚和关闭流程。
5. 让 readiness 同时反映数据库和 LLM Provider 的实际状态。
6. 为日志、脱敏、生命周期和 readiness 补充自动化测试。

完成后，服务应从“功能完整且可运行”提升为“具备基础生产部署可靠性”。

## 3. 非目标

本 Sprint 明确不包含：

- Prometheus 指标与 Grafana 仪表盘。
- OpenTelemetry tracing。
- Redis 或分布式限流实现。
- 新增 LLM Provider。
- 日志采集平台集成。
- 默认写入本地日志文件或日志轮转。
- Kubernetes 探针、部署清单或基础设施配置。
- 为尚不存在的资源设计通用插件系统。

日志默认写入标准输出，由部署环境负责采集和留存。

## 4. 总体架构

Sprint 5 保留现有分层，不改变 Router、Service、Repository 和 Provider 的职责。新增或调整的边界如下：

```text
Application Settings
        |
        +--> Logging Configuration
        |      +--> JSON Formatter
        |      +--> Sensitive Data Filter
        |
FastAPI Lifespan
        |
        +--> Application Lifecycle
               +--> Database
               +--> LLM Provider
               +--> rollback on startup failure
               +--> reverse-order shutdown

Readiness Endpoint
        |
        +--> Database Check
        +--> Provider Check
```

生命周期协调器只编排当前已有资源。未来增加 Redis 时可以添加新的资源步骤，但本 Sprint 不为其预留抽象接口。

## 5. 结构化日志

### 5.1 配置

`app/core/logging.py` 使用 `logging.config.dictConfig()` 配置根 logger 和 console handler。日志级别继续由现有配置控制。日志配置是 `lifespan` 的第一个启动步骤；现有 `create_app()` 中的主动日志初始化将被移除，避免应用构造和资源生命周期分别持有初始化责任。

默认日志格式为一行一个 JSON 对象，至少包含：

- `timestamp`：UTC ISO 8601 时间。
- `level`：日志级别。
- `logger`：logger 名称。
- `message`：日志消息。

当调用方通过 `extra` 提供上下文时，formatter 应保留以下已知字段：

- `request_id`
- `method`
- `path`
- `status_code`
- `latency_ms`
- `model`
- `prompt_tokens`
- `completion_tokens`

异常日志应保留异常类型、异常消息和堆栈信息。formatter 不依赖第三方日志包，避免仅为基础 JSON 序列化增加运行时依赖。

### 5.2 请求日志

`RequestLoggingMiddleware` 不再把字段拼接进消息字符串，而是使用稳定消息名称和结构化 `extra` 字段。

请求完成日志语义：

```json
{
  "message": "request_completed",
  "request_id": "abc123",
  "method": "POST",
  "path": "/v1/chat/completions",
  "status_code": 200,
  "latency_ms": 532
}
```

未处理异常仍由现有异常边界转换为响应。请求日志记录失败状态和耗时，但不得重复记录完整异常堆栈；异常堆栈由负责处理异常的边界记录一次。

日志字段名称形成稳定契约，测试通过解析 JSON 验证字段，而不是比较完整字符串。

## 6. 敏感信息保护

### 6.1 敏感数据范围

以下内容视为敏感数据：

- API Key 和管理 API Key。
- `Authorization`、`Proxy-Authorization` 等认证头。
- 数据库 URL 中的用户名和密码。
- 字段名包含 `password`、`secret`、`token`、`api_key` 或 `authorization` 的值。

普通 `OLLAMA_BASE_URL` 不属于密钥；如果 URL 内嵌用户名、密码或认证查询参数，则只隐藏凭据部分。

### 6.2 防护策略

防护采用两层策略：

1. 生产代码不得主动记录完整敏感值。
2. 日志 handler 安装敏感数据过滤器，作为意外记录时的最后防线。

`app/core/security.py` 提供小而明确的脱敏函数：

- `mask_secret(value)`：短值完全隐藏；长值只保留有限前后字符。
- `sanitize_url(value)`：保留协议、主机、端口和数据库名，隐藏凭据。
- `sanitize_mapping(value)`：递归处理结构化字段中的敏感键。

日志过滤器处理结构化字段，并替换当前配置中已知敏感值的精确匹配。它不承诺识别任意未知密钥格式，因此“禁止主动记录敏感值”仍是首要规则。

脱敏后的日志不得影响程序控制流，也不得修改原始请求对象或配置对象。

## 7. 生命周期可靠性

### 7.1 设计原则

现有 FastAPI `lifespan` 继续作为应用资源的唯一所有者。`app/core/lifecycle.py` 新增 `ApplicationLifecycle`，内部使用 `contextlib.AsyncExitStack` 登记清理回调，负责：

- 按顺序初始化真实存在的资源。
- 记录已经成功初始化的资源。
- 启动中途失败时，按相反顺序释放已成功初始化的资源。
- 正常关闭时，按相反顺序释放资源。
- 单个资源关闭失败时继续关闭其他资源，并记录结构化异常日志。

协调器不得承担业务依赖解析，也不取代现有 DI 容器。

### 7.2 启动顺序

当使用 PostgreSQL 后端时：

1. 配置结构化日志。
2. 初始化数据库 engine 和 session factory。
3. 创建必要数据库结构。
4. 执行初始 API Key bootstrap。
5. 初始化或取得 LLM Provider。
6. 将就绪资源暴露给应用。

不使用 PostgreSQL 时，配置日志后跳过数据库步骤，再初始化 Provider。

### 7.3 回滚与关闭

如果数据库初始化成功但 Provider 初始化失败：

1. Provider 失败被记录。
2. 已初始化的数据库资源被释放。
3. 容器中与本次生命周期相关的缓存引用被清理。
4. 原始启动异常继续抛出，使 Uvicorn 启动失败。

正常关闭时，资源按 Provider、数据库的顺序释放。关闭操作应具备幂等性，避免测试清理或重复 shutdown 导致二次异常。

本 Sprint 不创建虚构的 PostgreSQL Pool 或 Redis 资源；协调器调用项目当前真实的数据库和 Provider 初始化、关闭接口。

## 8. Health 与 Readiness

### 8.1 Liveness

`GET /health` 仅表示应用进程可以响应请求。它不访问数据库或 Provider，避免外部依赖抖动导致进程被错误重启。

成功响应保持轻量，并返回 HTTP `200`。

### 8.2 Readiness

`GET /ready` 表示当前实例可以接收正常业务流量。

检查项：

- `database`：当启用 PostgreSQL 时执行轻量查询；未启用时标记为 `not_configured`。
- `provider`：调用 Provider 的轻量可用性检查。

Quota 与 Usage 共享当前数据库后端时，不重复执行独立数据库检查。

全部必要检查成功时返回 HTTP `200`：

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "provider": "ok"
  }
}
```

任一必要检查失败时返回 HTTP `503`：

```json
{
  "status": "not_ready",
  "checks": {
    "database": "failed",
    "provider": "ok"
  }
}
```

响应不得包含异常消息、数据库地址、凭据或 Provider 内部响应正文。详细故障原因只写入经过脱敏的服务端日志。

数据库和 Provider 检查并发执行。每个检查使用统一的 `readiness_timeout_seconds` 配置，默认值为 `2.0` 秒，防止探针请求长期占用连接。

## 9. 错误处理

- 日志配置失败属于启动失败，不能静默退回不可预测的配置。
- 资源启动失败必须保留原始异常链，并完成已初始化资源回滚。
- 资源关闭失败记录为 error，但不阻止其他资源继续关闭。
- readiness 的单项检查异常被转换为 `failed`，不会把内部异常直接返回客户端。
- 脱敏函数遇到未知对象时应安全地转换或忽略，不得导致原始日志调用失败。
- JSON formatter 无法序列化扩展字段时，将该字段转换为安全字符串，保证日志记录本身不会破坏请求。

## 10. 测试策略

### 10.1 日志测试

覆盖：

- 每行日志是有效 JSON。
- 标准字段存在且类型稳定。
- 请求完成日志包含 `request_id`、路径、状态码和耗时。
- 异常日志保留异常信息和堆栈。
- 扩展字段不可序列化时日志仍可输出。

### 10.2 敏感信息测试

覆盖：

- 长短 API Key 的脱敏行为。
- `Authorization` 和结构化敏感键被隐藏。
- PostgreSQL URL 凭据被隐藏，主机和数据库名仍可诊断。
- 已知 API Key 即使被误写入消息也不会出现在最终日志中。
- 非敏感 URL 和普通字段不被过度修改。

### 10.3 生命周期测试

覆盖：

- 全部资源启动成功。
- 数据库启动失败时不启动后续资源。
- Provider 启动失败时释放数据库。
- 正常关闭使用反向顺序。
- 一个资源关闭失败时其他资源仍被释放。
- 重复关闭不会重复释放或抛出非预期异常。

### 10.4 Readiness 测试

覆盖：

- 数据库和 Provider 正常时返回 `200`。
- 数据库失败时返回 `503`。
- Provider 失败时返回 `503`。
- 两者同时失败时正确报告所有检查结果。
- 非 PostgreSQL 配置不会执行数据库查询。
- 响应和日志均不泄露敏感连接信息。

### 10.5 回归验证

Sprint 完成前必须通过：

```text
ruff format --check .
ruff check .
mypy app tests
pytest
INTEGRATION_TEST=1 pytest
```

同时验证：

- Python 3.12 和 3.14 CI。
- Uvicorn 正常启动和关闭。
- PostgreSQL 集成测试。
- 现有非流式、流式、鉴权、限流、Usage 和 Quota 行为无回归。

## 11. 交付物

预计涉及：

- `app/core/logging.py`
- `app/core/security.py`
- `app/core/lifecycle.py`
- 请求日志 middleware
- health/readiness router 或 service
- Settings 中与日志和检查超时有关的最小配置
- 对应单元测试和 PostgreSQL 集成测试
- `.env.example`
- `README.md`

最终文件名和函数名可以根据现有模块边界微调，但不得扩大本设计定义的功能范围。

## 12. 完成标准

Sprint 5 只有在以下条件全部满足时才算完成：

1. 日志为稳定、可解析的 JSON，关键请求字段可直接检索。
2. 自动化测试证明 API Key 和数据库凭据不会出现在最终日志中。
3. 部分启动失败会释放此前已初始化的资源。
4. readiness 能准确区分数据库和 Provider 故障，并返回正确状态码。
5. 全部静态检查、默认测试、PostgreSQL 集成测试和启动验证通过。
6. README 记录 Sprint 5 内容和不超过五句话的学习总结。
7. 变更完成 Code Review 后，以 conventional commit 提交并推送。
