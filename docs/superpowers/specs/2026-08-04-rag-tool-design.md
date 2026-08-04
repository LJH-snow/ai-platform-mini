# Sprint 10 RAG Tool 化设计说明

- **日期**：2026-08-04
- **状态**：已实现，待 Code Review
- **对应路线图**：Sprint 10：RAG Tool 化与当前 MVP 收口
- **前置条件**：Sprint 7.5 RAG MVP、Sprint 8 Agent Runtime、Sprint 9 Tool System 已通过质量门禁

## 1. 背景与目标

现有 RAG 已经能够完成 `prepare/answer` 两阶段流程，但只能通过 `/api/v1/chat/rag` 触发。Sprint 10 不重写 RAG，也不让 Agent Runtime 直接理解向量库，而是增加一个 `KnowledgeSearchTool` 适配器，将检索结果以结构化、可追踪、可治理的 Tool 输出回填给 Agent。

目标链路：

```text
Agent model decision
        ↓
ToolExecutor → KnowledgeSearchTool
        ↓
RAGService.prepare（embedding + pgvector + 距离过滤）
        ↓
references + source metadata + untrusted-content warning
        ↓
Agent state tool message
        ↓
Agent model final answer
```

## 2. 非目标

- 不引入 LangChain、LangGraph、FAISS、Qdrant 或 Rerank。
- 不修改现有 `/api/v1/chat/rag` 的鉴权、配额和两阶段回答语义。
- 不让 `app/rag/` 依赖 `app/agents/` 或 `AgentRuntime`。
- 不在本 Sprint 引入 MCP、Memory、Run Trace 或 Multi-Agent。
- 不把原始 Provider、数据库异常和内部配置细节暴露给模型。

## 3. 设计决策

### 3.1 复用 `RAGService.prepare`

`KnowledgeSearchTool` 只调用 `RAGService.prepare(ChatRequest(...))`，不调用 `answer`。这样 embedding、Top-K、`max_distance`、内容边界净化和上下文截断仍然只有一份实现；兼容 RAG Chat 路由继续调用 `prepare` 后再调用 `answer`。

### 3.2 增加结构化引用

`PreparedRAGRequest` 新增 `references`，每个 `RAGReference` 包含 `document_id`、`chunk_id`、`chunk_index`、清洗后的 `content` 和 `distance`。只记录实际纳入上下文的片段，避免 Tool 输出与 RAG Prompt 使用了不同的结果集合。

### 3.3 工具输出安全边界

工具结果返回 `ok`、查询、结果列表、来源元数据和固定 warning。文档内容被视为不可信参考资料，不能被模型当作系统指令执行。空知识库、无相关上下文、存储不可用和 embedding 失败分别映射为稳定错误码；其他未预期异常继续由 `ToolExecutor` 统一归一化。

### 3.4 容器组合

`provide_agent_service()` 默认注册低风险 `calculator`。只有 `provide_rag_service()` 返回可用实例时，才额外注册 `knowledge_search`，从而保持 `RAG_ENABLED=false` 时的既有 Agent 行为。

## 4. 验收与验证

- `KnowledgeSearchTool` 单元测试覆盖成功、参数边界、来源元数据、空知识库、无相关上下文、embedding 和存储错误。
- Agent Runtime 集成测试覆盖“模型决策 → 知识检索 → 工具消息回填 → 最终回答”。
- RAG 服务测试验证引用元数据与实际上下文一致。
- 质量门禁：`ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest -q`。
- 启动检查：`uvicorn app.main:app` 能正常完成 startup/shutdown。
