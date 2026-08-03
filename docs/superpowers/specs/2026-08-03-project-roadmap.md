# AI Platform Mini 项目路线图（Sprint 6–10）

## 1. 项目当前状态

AI Platform Mini 是一个基于 FastAPI 构建的轻量级 LLM Gateway，提供 OpenAI Compatible API、多 Provider 抽象、API Key 鉴权、Rate Limit、Token Quota、Usage 统计和 SSE Streaming。

已完成能力：

- **API Layer**：`/v1/chat/completions`（OpenAI Compatible），`/api/v1/chat`（原生），`/api/v1/models`，`/api/v1/health`，`/api/v1/ready`，`/api/v1/usage`
- **Admin API**：`/admin/api-keys`（CRUD），`/admin/usage/daily`，`/admin/usage/monthly`
- **Auth**：API Key 鉴权（Bearer Token），SHA-256 哈希存储，Admin Key 分离，启动自举
- **Rate Limit**：滑动窗口算法，Memory 实现，Protocol 抽象预留 Redis 切换
- **Quota**：日/月 Token 配额，预占 + 续租 + 结算生命周期，并发安全（PostgreSQL advisory lock）
- **Usage**：按 API Key + 模型聚合，PostgreSQL 持久化，Streaming 延迟记录
- **Provider**：Protocol 抽象，OllamaProvider + MockProvider，Factory 模式
- **Streaming**：SSE（text/event-stream），Role chunk + Content chunks + [DONE]
- **Infrastructure**：Docker Compose（app + Ollama + PostgreSQL），CI（ruff + mypy + pytest），Testcontainers 集成测试
- **Observability**：dictConfig JSON 结构化日志，Request ID 追踪，SecretStr 脱敏，多资源 Readiness

## 2. 路线总览

| Sprint | 主题                | 核心交付                                                             | 依赖                |
| ------ | ------------------- | -------------------------------------------------------------------- | ------------------- |
| 6      | 技术债清理          | request_id 扩展、\_extract\_int bool 守卫、Ollama 日志增强           | —                   |
| 7      | Multi-LLM Gateway   | OpenAIProvider、ProviderRouter、Adapter 层提取                       | Provider 抽象（已完成） |
| 8      | RAG MVP             | TXT 文件 → Chunk → Embedding → pgvector → 检索增强对话              | pgvector 扩展       |
| 9      | Agent System        | Tool Calling、MCP 集成、Agent Workflow                               | Provider + RAG      |
| 10     | 平台化              | Admin Dashboard、API Key 可视化管理、Usage 图表                      | —                   |

## 3. Sprint 6：技术债清理

### 3.1 背景

Sprint 6 不做新功能，而是清理 Code Review 中发现的 deferred 问题。所有改动均为单文件、单函数级别的修复，回归风险极低。

### 3.2 具体任务

1. **request_id 扩展**（`app/middleware/context.py`）

   将 `uuid.uuid4().hex[:8]` 改为完整的 `uuid.uuid4().hex`。截取后的短 ID 在大规模跨实例日志聚合中仍存在实际碰撞风险；完整 UUID4 hex 提供足够的随机空间，并保持无连字符格式便于日志检索。

2. **`_extract_int` bool 守卫**（`app/services/chat_service.py`）

   `bool` 是 `int` 的子类，`isinstance(True, int)` 返回 `True`。虽然 Ollama 不太可能把 `prompt_eval_count` 返回为布尔值，但按防御性编程原则应加 `not isinstance(value, bool)` 守卫。

3. **Ollama 流式非 JSON 行日志**（`app/providers/ollama.py`）

   `chat_stream` 中 `json.loads(line)` 失败时静默 `continue`。如果 Ollama 返回格式异常数据，调用方可能永远收不到 `done=True`。每个流结束时最多记录一条 warning，只包含模型、坏行数量和最大行长度，避免响应内容泄露与逐行日志放大。

### 3.3 非目标

- ChatService 消息构造去重（涉及 token estimator 接口签名变更，收益不够大）
- lru_cache DI 重构（架构债，不影响当前功能）
- ProviderError 502 vs 503 语义调整
- 任何新增业务功能

### 3.4 完成标准

```text
ruff format --check .  ✅
ruff check .           ✅
mypy app tests         ✅
pytest                 ✅
```

### 3.5 交付物

- 修改：`app/middleware/context.py`（1 行）
- 修改：`app/services/chat_service.py`（1 行）
- 修改：`app/providers/ollama.py`（约 3 行）
- Git commit（conventional commit message）

## 4. Sprint 7：Multi-LLM Gateway

### 4.1 目标

这是项目从"单 Provider LLM 代理"升级为"多 Provider LLM Gateway"的关键 Sprint。核心价值在于**验证已完成的 Provider 抽象设计的正确性**——通过新增第二个 Provider 实现，证明 Protocol + Factory + DI 的架构不是过度设计。

完成后，同一个 `/v1/chat/completions` 端点可以按 model name 自动路由到 Ollama（`qwen3:4b`）或 OpenAI（`gpt-4.1-mini`），无需客户端感知 Provider 差异。

### 4.2 具体交付

#### 4.2.1 OpenAIProvider（新建：`app/providers/openai.py`）

实现 `LLMProvider` Protocol。关键细节：

- 通过 `httpx.AsyncClient` 调用 OpenAI `/v1/chat/completions` 端点
- `chat()` 返回格式与 Ollama 对齐的 dict（`model`、`message.role`、`message.content`、`done`、`done_reason`），确保 `ChatService._parse_chat_response` 无需修改
- `chat_stream()` 返回 SSE chunk dict，每个 chunk 包含 `model`、`message.role`、`message.content`、`done` 字段
- `list_models()` 调用 OpenAI `/v1/models` 并转换为统一的 `{"models": [{"name": "..."}]}` 格式
- `default_model` 返回配置的默认模型（如 `gpt-4.1-mini`）
- `close()` 关闭 `httpx.AsyncClient`
- 异常处理：HTTP 4xx/5xx → `ProviderError`（可新增 `OpenAIProviderError` 子类）；网络错误 → `ProviderUnavailableError`

#### 4.2.2 配置扩展（修改：`app/core/settings.py`）

新增字段：

```python
openai_api_key: SecretStr = SecretStr("")
openai_base_url: str = "https://api.openai.com/v1"
openai_default_model: str = "gpt-4.1-mini"
```

`llm_provider` 字段保留，其语义从"全局唯一 Provider"变为"默认 Provider fallback"。

#### 4.2.3 ProviderRouter（新建：`app/providers/router.py`）

核心路由逻辑：

```python
def route_provider(model: str) -> LLMProvider:
    """根据 model 名选择 Provider。

    路由规则（按优先级）：
    1. model 等于默认模型 → 默认 Provider（当前为 OllamaProvider）
    2. 其余 model 以 "gpt-" 开头 → OpenAIProvider
    3. 其他所有 → 默认 Provider（当前为 OllamaProvider）
    """
```

不是在 `create_llm_provider()` 里加 if-else，而是新建独立的 `route_provider()`，让 Factory 和 Router 各司其职。

#### 4.2.4 Adapter 层提取（新建：`app/adapters/openai_adapter.py`）

此时有两个协议方向（OpenAI Chat API ↔ 内部 ChatRequest/ChatResponse），Adapter 接口由两个方向自然驱动。从 `OpenAIService` 现有私有方法提取：

```python
class OpenAIAdapter:
    def to_chat_request(self, request: OpenAIChatRequest) -> ChatRequest: ...
    def to_chat_response(self, chat_response: ChatResponse) -> OpenAIChatResponse: ...
```

`OpenAIService` 改为组合 Adapter，`_to_chat_request` → `self._adapter.to_chat_request`，`_to_openai_response` → `self._adapter.to_chat_response`。

### 4.3 非目标

- Anthropic / Gemini Provider
- 基于 API key 的 Provider 路由
- Provider 健康检查和自动故障转移
- 负载均衡
- Streaming 中的 Provider 切换
- 任何 DI 容器级别的重构

### 4.4 完成标准

```text
ruff format --check .  ✅
ruff check .           ✅
mypy app tests         ✅
pytest                 ✅
# 手动验证：
#   curl POST /v1/chat/completions -d '{"model":"gpt-4.1-mini",...}' → 走 OpenAI
#   curl POST /v1/chat/completions -d '{"model":"qwen3:4b",...}'    → 走 Ollama
```

## 5. Sprint 8：RAG MVP

### 5.1 目标

为平台增加知识库检索能力，让 LLM 回答可以基于用户上传的私有文档。这是企业 AI 平台的核心差异化能力。

### 5.2 严格的范围约束

- **只做 TXT 文件**。不做 PDF、Markdown、Word、HTML 解析。
- **固定大小切片**。不做语义分块、句子边界检测、重叠窗口调参。
- **不做 reranking**。检索结果按向量相似度排序，不做二次精排。
- **不做多轮 RAG**。不维护对话历史中的检索上下文。
- **不做文档管理 API**。先用手动脚本 ingest，不接上传接口。

### 5.3 具体交付

#### 5.3.1 Chunker（新建：`app/rag/chunker.py`）

```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """按字符数固定切片，相邻 chunk 之间 overlap 个字符重叠。"""
```

简单实现：`for i in range(0, len(text), chunk_size - overlap)` 切片，不做句子边界检测。

#### 5.3.2 Embedder（新建：`app/rag/embedder.py`）

```python
class OllamaEmbedder:
    def __init__(self, base_url: str, model: str = "nomic-embed-text"): ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

调用 Ollama `/api/embed` 端点。`nomic-embed-text` 是 Ollama 生态中最常用的轻量 embedding 模型（768 维）。

#### 5.3.3 Vector Store（新建：`app/rag/vector_store.py` + `app/db/rag_models.py`）

使用 pgvector 扩展：

```python
class PgVectorStore:
    async def add(
        self, chunks: list[str], embeddings: list[list[float]], doc_id: str
    ) -> None: ...
    async def search(
        self, query_embedding: list[float], top_k: int = 5
    ) -> list[SearchResult]: ...
```

在 `app/db/init.py` 中确保 pgvector 扩展已启用（`CREATE EXTENSION IF NOT EXISTS vector`）。`DocumentChunk` SQLAlchemy 模型包含 `id`、`doc_id`、`chunk_index`、`content`、`embedding`（`Vector(768)`）。

#### 5.3.4 Ingest 脚本（新建：`scripts/ingest.py`）

独立脚本，不接入 API：

```bash
python scripts/ingest.py data/knowledge/sample.txt
```

流程：读取文件 → `chunk_text()` → `embed()` → `vector_store.add()`。

#### 5.3.5 RAG Chat 端点

新增 `POST /api/v1/chat/rag`：

```json
{"message": "什么是 AI Platform？", "model": null}
```

内部流程：`embed_query(message)` → `vector_store.search(embedding, top_k=5)` → 构造增强 prompt → `ChatService.chat(增强后的 ChatRequest)`。

### 5.4 非目标

- PDF / Markdown / Word 解析
- 语义或递归分块策略
- Reranking（Cross-encoder）
- 多轮 RAG 对话
- 文档上传 API
- 混合检索（BM25 + 向量）
- Embedding 模型切换

### 5.5 完成标准

```text
ruff format --check .  ✅
ruff check .           ✅
mypy app tests         ✅
pytest                 ✅
# 手动验证：
#   python scripts/ingest.py data/knowledge/sample.txt
#   curl POST /api/v1/chat/rag → 返回基于文档的回答
```

## 6. Sprint 9：Agent System

### 6.1 目标

为平台增加 Tool Calling 和 Agent Workflow 能力，让 LLM 可以主动调用外部工具完成任务。

### 6.2 方向性描述

> 注：Sprint 8 结束后根据实际状态细化此 Sprint 的设计。

- **Tool 定义**：支持 `function` 类型 tool，JSON Schema 参数描述
- **Tool 执行**：LLM 返回 tool_call → 平台执行对应函数 → 结果注入对话 → LLM 继续推理
- **MCP 集成**：支持通过 MCP 协议接入外部工具服务器
- **Workflow**：支持多步推理循环（ReAct 模式：Thought → Action → Observation → Thought → ...）

### 6.3 非目标

- 多 Agent 协作
- 长期记忆
- 沙箱化工具执行

## 7. Sprint 10：平台化

### 7.1 目标

为平台增加面向管理员的轻量可视化界面。

### 7.2 方向性描述

- **Admin Dashboard**：HTML 页面，展示 API Key 列表（创建/撤销）、Usage 图表（日/月）、Quota 使用率
- **技术选型**：纯静态 HTML + 内嵌 JS，通过已存在的 Admin API 获取数据。不引入 React/Vue 框架，保持项目定位为后端/AI Infra 项目
- **Usage 图表**：Chart.js CDN 引入，折线图或柱状图

## 8. 设计决策记录

以下决策在 Sprint 路线讨论中达成共识，记录于此以避免后续反复争论。

### 8.1 Adapter 层延后到 Sprint 7

当前只有一个协议（OpenAI Compatible），`OpenAIService._to_chat_request` 和 `_to_openai_response` 是两个私有方法，没有造成实际问题。Adapter 接口应该在第二个协议（Sprint 7 的 OpenAIProvider 带来内部格式差异）自然驱动出来，而非凭预期设计。过早提取会增加文件数、import 链和测试量，但不增加能力。

### 8.2 RAG 收窄为 MVP

完整的 RAG 系统包含 Document Loader、Chunker、Embedding、Vector Store、Retriever、Reranker、Prompt Builder、Evaluation 八个子系统。Sprint 8 只做 TXT → Chunk → Embedding → pgvector → 单轮检索增强对话这条最小闭环。PDF 解析是独立领域问题（需要 pdfplumber/PyMuPDF 等库），不应混入 MVP。多 chunking 策略和 reranking 在数据量和质量要求提升时才有价值。

### 8.3 Provider 路由按 model name 而非 API key

模型名路由（默认模型优先使用默认 Provider，其余 `gpt-*` → OpenAI、其他 → Ollama）简单且符合 OpenAI Compatible API 的惯例。基于 API key 的路由（不同用户走不同 Provider）在企业场景中有价值但复杂度高，留给后续 Sprint。

### 8.4 不做 Admin Dashboard 先行

Admin Dashboard 让项目"看起来完整"但对后端能力验证没有帮助。优先级应在 Provider 路由和 RAG 这类核心 AI 能力之后。Dashboard 用纯 HTML + Chart.js，不引入前端框架，保持项目定位为后端/AI Infra 项目。

### 8.5 不做 WebSocket Streaming

SSE（Server-Sent Events）已是 OpenAI 标准，满足当前所有流式需求。WebSocket 的双向通信在 LLM 对话场景中没有必要——客户端发送请求用 HTTP POST，服务端返回流用 SSE，职责分离更清晰。

### 8.6 不做完整 ResourceManager

当前只有两个资源（Provider + PostgreSQL），完整 ResourceManager（资源 DAG、回滚顺序、注册机制）在当前规模下属于过度设计。在 Sprint 5 的 lifespan 加固后，`db_initialized` flag + `provider is not None` 守卫已足够安全。ResourceManager 在资源种类超过 3 个时再抽象。
