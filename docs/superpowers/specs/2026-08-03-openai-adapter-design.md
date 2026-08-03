# Sprint 7.3: OpenAIAdapter Boundary Extraction

## 1. 背景

Sprint 7.1 已新增 `OpenAIProvider`，负责调用上游 OpenAI Chat
Completions API；Sprint 7.2 已新增 `ProviderRouter`，根据模型名选择
OpenAI 或默认 Provider。

项目同时还提供公开的 OpenAI-compatible `/v1/chat/completions` API。
当前 `OpenAIService` 既负责编排 ChatService、Quota 和 Usage，又包含以下
协议转换逻辑：

- `OpenAIChatRequest` 转换为内部 `ChatRequest`。
- 内部 `ChatResponse` 转换为 `OpenAIChatResponse`。
- 内部流式结果转换为 OpenAI SSE chunk。

Sprint 7.3 按路线图提取 `OpenAIAdapter`，但只移动无状态、确定性的请求和
非流式响应转换。流式响应转换仍保留在 `OpenAIService`，避免在一次重构中
同时改变协议映射和流式生命周期。

## 2. 目标

Sprint 7.3 将完成以下能力：

1. 新增 `app/adapters/openai_adapter.py`。
2. 将 `_to_chat_request()` 从 `OpenAIService` 移入 `OpenAIAdapter`。
3. 将非流式 `_to_openai_response()` 移入 `OpenAIAdapter`。
4. 让 `OpenAIService` 通过组合方式使用 Adapter。
5. 为 Adapter 增加独立单元测试。
6. 保持公开 API、错误语义、Quota、Usage 和 SSE 输出完全兼容。

本 Sprint 是职责提取，不新增用户可见能力。

## 3. 术语边界

项目存在两个不同的 OpenAI 协议边界：

1. **公开 API 边界**：客户端调用本项目的 OpenAI-compatible API，由
   `OpenAIService` 和本 Sprint 的 `OpenAIAdapter` 处理。
2. **上游 Provider 边界**：本项目调用 OpenAI 官方 API，由
   `OpenAIProvider` 处理。

`OpenAIAdapter` 只属于公开 API 边界。它不接管 `OpenAIProvider` 的请求
构造、HTTP 错误映射、SSE 解析、usage-only 帧校验或终止状态机。

## 4. 非目标

本 Sprint 明确不包含：

- 提取流式响应转换或 SSE 文本组装。
- 修改 `OpenAIProvider`。
- 修改 `ProviderRouter` 或模型路由规则。
- 修改公开 OpenAI-compatible API Schema。
- 新增 Responses API、工具调用、多模态或其他 OpenAI 能力。
- 重构 Quota、UsageCollector 或 ReservationLifecycle。
- 引入通用 Adapter Protocol、注册表或 DI 容器级抽象。
- 改变现有消息转换规则或补充新的业务校验。

## 5. 设计原则

### 5.1 Adapter 保持无状态

`OpenAIAdapter` 不持有 HTTP 客户端、配置、数据库连接、logger 或请求上下文。
其公开方法为同步方法，相同输入产生相同协议字段映射。

### 5.2 Service 保留运行时编排

UUID、当前时间、异步流迭代、Quota 生命周期、Usage 记录和 SSE 输出顺序仍由
`OpenAIService` 管理。Adapter 不拥有资源，也不需要 `close()`。

### 5.3 重构不改变行为

Sprint 7.3 只移动已经存在的转换规则。发现与本次提取无关的协议扩展或校验
机会时，记录为后续工作，不在本 Sprint 顺带修改。

## 6. 总体架构

```text
POST /v1/chat/completions
            |
            v
      OpenAIService
       /         \
      v           v
OpenAIAdapter   ChatService
                    |
                    v
              ProviderRouter
               /          \
              v            v
      OllamaProvider   OpenAIProvider
```

`OpenAIService` 是应用用例协调者，`OpenAIAdapter` 是公开 API Schema 与内部
Schema 之间的纯转换组件，Provider 层继续负责上游供应商协议。

## 7. 组件设计

### 7.1 OpenAIAdapter

新建 `app/adapters/openai_adapter.py`：

```python
class OpenAIAdapter:
    def to_chat_request(self, request: OpenAIChatRequest) -> ChatRequest: ...

    def to_chat_response(
        self,
        response: ChatResponse,
        *,
        completion_id: str,
        fallback_created: int,
    ) -> OpenAIChatResponse: ...
```

Adapter 不生成 UUID，也不读取系统时间。`completion_id` 和
`fallback_created` 由调用方显式传入，使响应转换可以稳定、独立地测试。

#### 请求转换

`to_chat_request()` 保留当前行为：

- 最后一条 OpenAI message 的 `content` 成为内部 `message`。
- 最后一条消息之前遇到的第一条 `system` message 成为 `system_prompt`。
- 其他前置消息按原顺序进入 `history`。
- `model`、`temperature` 和 `max_tokens` 原样映射到内部字段。

本 Sprint 不改变“最后一条消息作为当前消息”的既有规则，也不新增角色限制。

#### 非流式响应转换

`to_chat_response()` 保留当前行为：

- 使用调用方传入的 `completion_id`。
- 将有效的内部 ISO 8601 `created_at` 转换为 Unix 时间戳。
- `created_at` 缺失或无法解析时使用 `fallback_created`。
- 将内部 message、model 和 finish reason 映射到 OpenAI Schema。
- `done_reason` 缺失时使用 `"stop"`。
- prompt 或 completion token 任一存在时返回 usage。
- usage 中缺失的一侧按 `0` 处理，`total_tokens` 为两侧之和。
- 两个 token 字段都缺失时不返回 usage。

Adapter 返回 Pydantic Schema 对象，不返回未验证的字典。

### 7.2 OpenAIService

`OpenAIService` 构造函数显式接收 `OpenAIAdapter`，并保存为
`self._adapter`。`get_openai_service()` 创建无状态 Adapter 后注入 Service；
Adapter 不需要缓存或生命周期管理。

非流式路径调整为：

1. 调用 `adapter.to_chat_request()`。
2. 执行现有 ReservationLifecycle、ChatService 和 UsageCollector 流程。
3. 生成 completion ID 和当前 Unix 时间。
4. 调用 `adapter.to_chat_response()`。

以下流式职责继续留在 `OpenAIService.chat_completions_stream()`：

- 生成共享的 completion ID 和 created 时间。
- 消费并关闭内部异步流。
- 生成首个 assistant role chunk。
- 将每个 `ProviderChatResult` 组装为 `OpenAIStreamChunk`。
- 处理 finish reason 和空流 fallback chunk。
- 输出 `data: ...\n\n` 和最终 `[DONE]`。
- 在断连、取消和异常路径中维持现有 Quota、Usage 和资源关闭语义。

### 7.3 OpenAIProvider

`OpenAIProvider` 不参与本次重构。以下逻辑继续保留在 Provider：

- Provider-neutral payload 到上游 OpenAI 请求的转换。
- 上游非流式响应到 Provider-neutral 响应的转换。
- OpenAI SSE 行解析和 JSON 校验。
- 终止 chunk、usage-only 帧和部分 usage 合并状态。
- HTTP、网络和模型不存在异常映射。

这些职责处理的是上游供应商协议，与公开 API Adapter 的方向不同。

## 8. 数据流

### 8.1 非流式请求

```text
OpenAIChatRequest
    -> OpenAIAdapter.to_chat_request()
    -> ChatRequest
    -> ChatService.chat()
    -> ChatResponse
    -> OpenAIAdapter.to_chat_response()
    -> OpenAIChatResponse
```

### 8.2 流式请求

```text
OpenAIChatRequest
    -> OpenAIAdapter.to_chat_request()
    -> ChatRequest
    -> ChatService.chat_stream()
    -> ProviderChatResult stream
    -> OpenAIService SSE assembly
    -> OpenAIStreamChunk / [DONE]
```

流式路径只复用请求转换，不新增 Adapter 的流式响应方法。

## 9. 错误处理

- Adapter 不捕获或包装 `ChatService`、Quota 或 Usage 异常。
- Adapter 不新增项目异常类型。
- 输入的 OpenAI request 已由 Pydantic Schema 验证。
- 内部 response 继续依赖现有 `ChatResponse` 类型约束。
- 无效或缺失的内部 `created_at` 保持当前 fallback 行为。
- 流式异常、客户端断连和取消仍由现有 Service、生命周期和 API 边界处理。
- `OpenAIProviderError` 及其子类完全不受本 Sprint 影响。

## 10. 依赖与生命周期

Adapter 是无状态普通对象：

- 不使用 `lru_cache`。
- 不进入 FastAPI lifespan。
- 不实现异步方法或 `close()`。
- 不依赖 `Settings` 或 FastAPI `Depends`。

只有 `get_openai_service()` 负责创建并注入 Adapter。直接构造
`OpenAIService` 的测试需要显式提供 `OpenAIAdapter`，让依赖关系保持可见。

## 11. 测试策略

### 11.1 Adapter 单元测试

新增 `tests/test_openai_adapter.py`，通过公开方法覆盖：

- 单消息请求转换。
- system prompt 与 history 顺序。
- model、temperature 和 max_tokens 映射。
- 非流式 message、model 和 finish reason 转换。
- completion ID 和 created 时间戳。
- 无 usage、完整 usage 和部分 usage。
- 无效或缺失 created_at 使用调用方 fallback。

测试使用固定 completion ID 和 fallback 时间，不 mock UUID 或系统时钟。

### 11.2 Service 回归测试

保留 `tests/test_openai_service.py` 中的公开路径测试，更新构造方式并验证：

- 非流式路径通过 Adapter 后响应行为不变。
- 流式 SSE chunk 顺序和内容不变。
- `[DONE]`、finish reason、空流 fallback 保持不变。
- Usage 记录、Quota settle/release、续租和断连清理保持不变。

Service 测试不直接测试 Adapter 私有实现。

### 11.3 Provider 回归测试

`tests/test_openai_provider.py` 不迁移到 Adapter 测试。其覆盖的是上游 OpenAI
协议和流式状态机，应继续作为独立 Provider 契约。

## 12. 文件变更范围

预计变更：

- 新建 `app/adapters/__init__.py`
- 新建 `app/adapters/openai_adapter.py`
- 修改 `app/services/openai_service.py`
- 新建 `tests/test_openai_adapter.py`
- 修改 `tests/test_openai_service.py`
- Sprint 收尾时更新 `README.md`
- Sprint 收尾时同步项目路线图和学习总结

不应修改：

- `app/providers/openai.py`
- `app/providers/router.py`
- `app/providers/factory.py`
- `app/schemas/openai.py`
- Provider 路由和应用生命周期代码

若实现需要修改上述非预期文件，应先回到设计审查，而不是扩大 Sprint 范围。

## 13. 风险与控制

### 13.1 同名协议边界混淆

风险：把 `OpenAIProvider` 的上游转换误移入公开 API Adapter。

控制：Adapter 的输入和输出只使用 `app.schemas.openai` 与
`app.schemas.chat` 类型，不接收 `httpx.Response`、SSE 文本或 Provider
payload 字典。

### 13.2 流式行为回归

风险：为了复用 Adapter 而改变 role chunk、终止 chunk 或 `[DONE]` 顺序。

控制：本 Sprint 不增加流式响应转换方法，现有流式实现只替换请求转换调用。

### 13.3 非确定性进入 Adapter

风险：Adapter 内部直接调用 `uuid.uuid4()` 或 `time.time()`，导致转换测试依赖
运行时状态。

控制：Service 生成 completion ID 和 fallback 时间，并通过参数传入 Adapter。

## 14. 备选方案

### 14.1 同时提取流式响应转换

该方案可以进一步缩短 `OpenAIService`，但 Adapter 将同时承担无状态字段映射
和有状态流式组装。改动会触及 SSE 顺序、取消清理、Quota 和 Usage 边界，超出
Sprint 7.3 的职责提取目标，因此不采用。

### 14.2 新增独立 OpenAIStreamAssembler

该方案能为流式逻辑建立清晰边界，但当前只有一个公开 SSE 协议实现，尚无第二
个调用方或独立变化压力。此时提取会增加接口和测试成本，留待流式逻辑出现新的
复用或复杂度需求后再评估。

## 15. 完成标准

Sprint 7.3 完成时必须满足：

1. `OpenAIAdapter` 只包含请求和非流式响应转换。
2. `OpenAIService` 显式组合 Adapter。
3. 流式 SSE 组装和上游 `OpenAIProvider` 状态机保持原位。
4. 公开 API 响应、SSE 帧顺序和异常语义无变化。
5. Adapter 与 Service 的测试边界清晰，不直接测试私有方法。
6. `ruff format --check .`、`ruff check .`、`mypy app tests` 和 `pytest`
   全部通过。
7. Uvicorn 可以正常启动并优雅关闭。
8. `git diff --check` 通过。
9. 完成 Code Review 后再提交 Sprint 实现并推送。
