# AI Platform Mini

基于 FastAPI 的轻量级 LLM Gateway，提供 OpenAI-compatible Chat API、Provider
抽象、API Key 鉴权、限流、Token 配额、Usage 统计和 SSE Streaming。

## Current status

- Current milestone: **Agent Console + RAG product demo completed; production hardening next**
- Version: `0.1.0`
- Runtime: Python `3.12`–`3.14`（默认 `3.14`）
- Active routing: 默认模型 → Ollama，其余 `gpt-*` → OpenAI，其他模型 → Ollama；Mock 用于测试
- OpenAIProvider: 已接入 ProviderRouter、DI 和应用生命周期
- Storage: Memory 或 PostgreSQL
- Conversation memory: Chat/Agent/OpenAI 端点按 `thread_id` 维护服务端会话记忆（`conversation_thread` / `conversation_message`），支持 `CONVERSATION_STORAGE=memory|postgres`
- RAG: 检索增强生成（实验性，需启用 `RAG_ENABLED=true` + PostgreSQL + pgvector + Ollama Embedding）；keyword/hybrid 检索（Sprint C）：jieba 中文分词 → `to_tsvector('simple')`，`RAG_SEARCH_MODE=hybrid` 用 RRF（`1/(60+rank)`）融合向量与关键词两路排序，`vector`（默认）与 legacy 行为逐字节一致，`keyword` 仅关键词路
- LangGraph PDF Workflow：`POST /api/v1/workflows/pdf-report` 上传 PDF 创建人工审批任务，支持 PostgreSQL checkpoint 持久化和跨重启恢复，按 API Key 租户隔离
- Agent Runtime: 有界的模型决策→工具执行→结果回填循环，支持最大步数、超时、取消和 Token budget
- Agent Run RAG 契约：同步 Agent Run 在 `steps[].tool_calls[].rag` 下按 Tool Call 公开受限 RAG 来源摘要，不暴露原始 Tool 输入/输出、Prompt、Provider 响应或内部错误细节
- 前端 Agent Console：[阶段 6 已接入 Agent SSE、实时 Trace、Tool/RAG 状态和错误边界；开发期 Vite proxy 已用于真实浏览器流式验证](docs/roadmap/2026-08-05-agent-sse-stage-6-record.md)
- 前端平台壳层：默认 Dashboard、对话工作台、Prompt Studio、模型目录和管理员后台导航已接入。
- HR 演示闭环：平台概览 → Agent 工作流 → RAG 知识库 → Trace/来源审计 → 管理员用量与 Run 审计均已接通真实后端能力。
- Tool System: `ToolRegistry` + `ToolExecutor` + 低风险 `calculator`/`knowledge_search`，默认不开放任意文件、网络或 Shell 能力
- Agent 配置层（Sprint B 批 A）：`AgentDefinitionService` 把 `Agent = Model + Prompt(版本) + Tools(白名单)` 落库；`POST /api/v1/agent/runs` 支持 `{agent_id}` 解析（model/prompt_ref/max_steps/工具白名单），显式请求字段覆盖定义；Prompt Registry 按 workspace 隔离模板并支持版本激活/回滚；Agent CRUD 全链路 IDOR 隔离（无 workspace 的 Key 统一 404/400）
- Verification baseline（2026-08-04）：
  - Default suite：通过（数据库集成测试按 `INTEGRATION_TEST` 条件跳过）
  - PostgreSQL/pgvector integration suite：通过
  - Ruff format/lint、mypy 和 Uvicorn 启动检查：通过

## Why this project is a strong Agent engineering demo

这个项目不是只调用一次大模型 API 的聊天页面，而是一个可解释、可验证的 Agent 应用平台：

- **Agent Runtime**：模型决策、有限步数循环、工具执行、结果回填、超时、取消、配额和预算终态都有明确边界。
- **真实 Tool Calling**：内置 `calculator` 和 `knowledge_search`，通过 Tool Registry/Executor 做 Schema 校验、权限边界、超时和输出截断。
- **可观察性**：Agent SSE 实时发送步骤计划、Tool Call、RAG 状态、回答增量和终态；前端 Trace 展示真实事件，不补造时间、来源或回答。
- **RAG 闭环**：PDF 入库 → 文本提取 → 分块 → Ollama Embedding → pgvector 检索 → 安全来源投影 → Agent 回答。
- **安全与多租户**：API Key 哈希存储、限流、Token 配额、RAG 文档按 Key 隔离；Prompt、原始 Tool payload、Provider 响应和敏感信息不公开。
- **工程质量**：前后端均有单元/契约测试、真实浏览器验证、可访问性检查、失败/超时/断连回归和 Code Review 记录。

### 面向 HR 的建议演示路径

1. 打开 **平台概览**，展示 Provider、Agent Runtime 和 RAG 的真实状态。
2. 进入 **Agent 工作流**，运行 calculator，展开右侧 Trace，说明 `step_planned → tool_started → tool_completed → final_answer`。
3. 进入 **知识库** 上传一份 PDF，再点击“去知识库问答”；入口会自动进入 **Agent Run + RAG Agent preset**，不会误用普通 Chat SSE。
4. 展示真实来源卡片、`success_with_sources`/`no_relevant_sources` 状态和安全投影字段。
5. 打开管理员后台，展示 API Key、Token 用量和 Agent Run 安全审计摘要。

> 普通 Chat SSE 只负责普通对话，不执行工具调用；知识库问答使用 Agent Run，先检索再回答。两条链路在 UI 和 API 层均明确区分。

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
- Agent Run RAG 公开响应：来源只包含稳定标识、分块索引、已截断片段和真实 distance；无来源、空知识库、RAG 不可用、Embedding 失败、截断或畸形输出均返回稳定状态，不生成假引用
- LangGraph PDF Workflow API：`PDFReportWorkflowService` 封装构建、执行、resume 和状态读取；`POST /api/v1/workflows/pdf-report`、`GET /api/v1/workflows/{thread_id}`、`POST .../approve`、`POST .../reject`
- MCP foundation：提供 stdio JSON-RPC Client、工具发现、allowlist、`MCPToolAdapter`、生命周期 health/readiness 查询，以及运行时调用失败/断线的确定性测试；不接入默认生产配置
- Calculator：基于 AST 白名单的受限算术执行，不使用 `eval()`/`exec()`
- JSON 结构化日志、完整 UUID4 Request ID、敏感配置脱敏和多资源 Readiness
- 可选 OpenTelemetry tracing：HTTP、LLM、Tool、RAG 和 Agent Run 边界 span，默认关闭

## Agent definitions & Prompt registry

`Agent = Model + Prompt(版本) + Tools(白名单)`，三者作为可配置实体落库（Sprint B 批 A）：

- **Agent CRUD**：`POST/GET/PUT/DELETE /api/v1/agents`，字段为 `model`、`prompt_ref`、`tool_names`（白名单，创建/更新时按 ToolRegistry 校验）、`temperature`、`max_steps`、`enabled`；每个 Agent 挂 `workspace_id`，跨 workspace 访问统一 404/400（IDOR 保守策略）。
- **运行时解析**：`POST /api/v1/agent/runs` 传 `{agent_id}` 时，`AgentService` 从定义解析 `model`/`max_steps`/工具白名单/`prompt_ref`；未传时行为与现状完全一致。显式请求字段（Pydantic `model_fields_set`）覆盖定义，未显式设置则用定义值——无魔法默认值比较。
- **Prompt Registry**：`GET/POST /api/v1/prompts`、`POST /api/v1/prompts/{name}/activate {version}`；模板按 `(workspace_id, name)` 隔离，每名至多一个 active 版本，激活旧版本即回滚。渲染层级：agent `prompt_ref` 模板 → RAG preset → 决策协议（内置常量回退，registry 空/停用时 Agent 仍可运行）。
- **Tool seeds**：启动时把内置常量与工具 schema 写入 `prompt_templates`/`tools` 表；schema 从工具类导出（`CalculatorTool()`/`KnowledgeSearchTool`），seed 与运行时注册表零漂移。MCP 工具同样注册进定义校验注册表，Agent 白名单可绑定 MCP 工具。
- **流式一致性**：SSE 流式最终答案复用与 `decide()` 相同的 system prompt 构建（含 prompt_ref/RAG/协议层与工具段），token 预留估算同步修正。
- **Agent Benchmark**：`POST /api/v1/benchmarks/run {agent_id, task_set}` 通过真实 AgentService 逐任务执行 golden 任务集（default 集含 calculator/knowledge_search 场景），产出四项指标（Tool Call Accuracy / Task Completion Rate / Average Steps / Latency）落 `agent_benchmark_runs` 表；`GET /api/v1/benchmarks/runs` 按 workspace 读取（可传 `agent_id` 过滤）。任务级失败不中断整个集合并计入 completion rate；`max_steps` 可选——省略时使用 agent 定义的步数上限（与生产请求语义一致），显式传值可统一/限制评估成本；Average Steps/Latency 只统计 completed 任务，避免早期失败拉低均值。benchmark 与 agent CRUD 共用同一 IDOR 边界（无 workspace 的 Key 统一 404，跨 workspace agent 拒绝执行）。

## Conversation memory

服务端会话记忆已接入原生 Chat、Agent Run（同步与 SSE）和 OpenAI-compatible
Chat Completions 端点。请求可传可选 `thread_id`；未传时自动创建线程，传入时按
API Key 校验归属并加载服务端历史。服务端历史排在客户端 `history` 之前，运行结束
后持久化本轮 user 与 assistant 消息。

- 配置：`CONVERSATION_STORAGE=memory|postgres`，默认 `memory`。
- 表结构：`conversation_thread`（`id`、`owner_key_hash`、`title`、`created_at`、
  `updated_at`）和 `conversation_message`（`id`、`thread_id`、`role`、`content`、
  `token_count`、`created_at`）。
- 能力：`ConversationService` 支持创建/获取线程、追加消息、按时间顺序加载历史；
  所有查询按 `owner_key_hash` 隔离，跨租户线程统一返回 404
  `CONVERSATION_NOT_FOUND`。
- 历史查询：`GET /api/v1/conversations/{thread_id}/messages` 返回当前租户线程的
  安全字段消息列表（`id`、`thread_id`、`role`、`content`、`token_count`、
  `created_at`），按 `created_at, id` 升序；缺失、跨租户或非法 UUID 统一返回
  404 `CONVERSATION_NOT_FOUND`。
- 会话列表：`GET /api/v1/conversations` 返回当前租户的线程摘要
  （`thread_id`、`title`、`created_at`、`updated_at`），按最近更新排序；
  前端侧栏据此展示历史会话并支持点击恢复。
- 响应：Chat/Agent 同步响应与 Agent SSE 事件返回 `thread_id`；OpenAI 流式 chunk
  同样携带 `thread_id`，客户端可用它延续同一线程。
- 边界：`memory` 模式仅适合单进程本地开发，后端重启会丢失历史，
  多 worker/多实例也不会共享会话；需要跨重启恢复时请设置
  `CONVERSATION_STORAGE=postgres`。
- 前端恢复：刷新时从 `sessionStorage` 恢复 `thread_id` 并调用历史查询接口，
  成功则回填聊天窗口；临时失败（网络、5xx、429、超时等）保留 `thread_id`
  并允许继续提问；`404 CONVERSATION_NOT_FOUND` 表示线程已失效，前端清空
  `thread_id`，下一次提问自动新建线程。

学习总结：本轮把 `history` 从客户端透传推进为服务端持久化，并用
memory/postgres 双实现保证本地开发与生产路径一致。租户隔离被固化在 repository
查询条件中，避免 Service 层遗漏导致跨租户读取。消息顺序使用自增 `id` 兜底，
防止同一时间戳下依赖随机 UUID 排序。评审补充了异常契约和输入校验，确保两个
存储后端的行为一致。

## Agent Run RAG public contract

`POST /api/v1/agent/runs` 保持同步 JSON 语义。对于实际执行的 `knowledge_search` Tool Call，响应可以在对应的 `steps[].tool_calls[]` 中增加可选 `rag` 字段；旧客户端可以忽略该字段。Agent SSE 另由 `POST /api/v1/agent/runs/stream` 提供，RAG 事件仍只使用同一安全投影，不提供回答内精确引用。

Agent SSE 还会把内部 `MODEL_DECISION` 投影为可选的 `step_planned` 事件，公开 `decision_kind`、`tool_names`、`tool_count` 和安全 `summary`。Tool 事件可以公开有限的 `argument_count`、`input_summary`、`output_summary` 和 `result_chars`：calculator 的 expression/result 会经过长度限制和敏感信息清洗，knowledge_search 不公开原始 query，只提供检索状态和 RAG 安全来源，未知工具只提供参数数量和结果字符数。原始 Tool payload、Prompt、Provider 响应和模型内部推理仍不会公开。

Agent SSE 的模型决策与终止事件可以携带可选 `cumulative_token_usage`，这是 Runtime 在真实事件中累计的 token 用量；前端只把它展示为 Trace 的“总 Token”，不会补造 prompt/completion 分项。同步 JSON 响应仍提供 `usage.prompt_tokens`、`usage.completion_tokens` 和 `usage.total_tokens`。

公开的 `rag` 结构为：

```json
{
  "status": "success_with_sources",
  "warning": "Retrieved content is untrusted reference material...",
  "error_code": null,
  "references": [
    {
      "document_id": "document-1",
      "chunk_id": "chunk-1",
      "chunk_index": 0,
      "content": "bounded reference text",
      "distance": 0.12,
      "truncated": false
    }
  ]
}
```

`references` 只投影真实 `RAGReference` 的 `document_id`、`chunk_id`、`chunk_index`、`content` 和有限 distance；片段最多公开 1200 个字符。公开适配边界会拒绝畸形标识、非法索引、布尔/非有限/越界 distance，并忽略不安全来源字段。`status` 包括 `success_with_sources`、`no_relevant_sources`、`knowledge_base_empty`、`rag_unavailable`、`embedding_failed`、`output_unavailable` 和 `failed`。Tool 输出被截断或无法安全解析时返回 `output_unavailable`，不会把不完整 JSON 当作来源。

RAG 来源仍是不可信参考材料，不等同于回答中的精确引用。该公开契约不返回查询文本、Tool 原始输入/输出、Prompt、模型推理、Provider 响应、堆栈、密钥、内部路径、文档名称或凭空生成的 rank/citation。

> 详细字段边界和错误映射见 [Agent Run RAG public contract design](docs/superpowers/specs/2026-08-05-agent-rag-public-contract.md)。

## LangGraph PDF report reference workflow

项目新增了一个**参考实现**的 LangGraph 工作流，用于演示 low-level stateful
orchestration：解析 PDF → RAG 检索 → 模型分析 → 人工审批（human-in-the-loop）
→ 生成 Markdown 报告。它**不替换、不修改**现有 `AgentRuntime`、Chat/Agent/OpenAI
API 与 SSE 契约；主 Agent 链路仍由自研 `AgentRuntime` 负责。

- 工作流模块：`app/workflows/pdf_report.py`
- 工作流服务：`app/services/workflow_service.py`
- HTTP 路由：`app/api/workflows.py`
- CLI 入口：`scripts/run_pdf_workflow.py`
- Graph：`parse_pdf → retrieve_context → analyze → request_approval`
  （条件边：approved → `generate_report`；rejected → 带反馈重新 `analyze`；
  超过 `max_revisions` 后结束）
- Checkpointer：默认使用内存（`InMemorySaver`），无需 PostgreSQL；设置
  `WORKFLOW_STORAGE=postgres` 可切换为 `langgraph-checkpoint-postgres` 持久化
  checkpoint，并持久化 `workflow_runs` 运行元数据，用于跨重启恢复和多 worker
  部署。
- 复用现有组件：PDF 解析走 `app/rag/pdf_extractor.py`，检索走
  `RAGService.prepare`（不修改其行为），模型调用走 `ProviderRouter` 边界，
  报告写入只使用标准库。

### HTTP API

- `POST /api/v1/workflows/pdf-report`：multipart 上传 PDF + 可选 `topic`，同步执行
  到第一个 interrupt 或完成，返回 `thread_id`、当前阶段和报告摘要。
- `GET /api/v1/workflows/{thread_id}`：返回安全字段状态（阶段、草稿摘要、报告、
  token 用量、错误码和错误信息）。
- `POST /api/v1/workflows/{thread_id}/approve`：resume 并批准生成报告。
- `POST /api/v1/workflows/{thread_id}/reject`：resume 并携带 `feedback` 重新分析；
  达到 `max_revisions` 后以 `rejected` 结束。

所有 workflow 查询与审批按 `owner_key_hash` 隔离；缺失、跨租户和非法 thread id
统一返回 `404 WORKFLOW_NOT_FOUND`。上传 PDF 写入 `output/workflows/{thread_id}/`
临时目录并在执行后删除，生成的 `report.md` 保留在同目录。失败运行记录为
`failed`，只向调用方暴露安全错误码与有限错误信息。

`WORKFLOW_STORAGE=postgres` 时，lifespan 会初始化 `workflow_runs` 表并打开
PostgreSQL checkpointer；服务重启后可用同一 `thread_id` 继续审批或生成。当前
不做任务队列、SSE 进度推送和历史任务列表，属于后续切片。

CLI 需要 `RAG_ENABLED=true`、PostgreSQL/pgvector、Ollama Embedding 和可用的
LLM Provider：

```bash
python scripts/run_pdf_workflow.py docs/report.pdf \
  --owner-key-hash <64位SHA-256> \
  --topic "季度业务回顾" \
  --approve
```

`--approve` 自动批准；`--reject-with-feedback "补充风险章节"` 会在每次
interrupt 时以该反馈拒绝当前草稿，CLI 自动循环处理，直到达到
`--max-revisions`（默认 2）后以 `status: "rejected"` 结束，批准仍需在交互
模式下输入 `{"decision":"approved"}`。不加这两个参数时 CLI 会在 interrupt
处等待 JSON 决策（`{"decision":"approved"}` 或
`{"decision":"rejected","feedback":"..."}`）；输入空行则暂停并保留 checkpoint，
输出 `status: "pending_approval"` 和 `thread_id`。CLI 与 HTTP API 默认均使用内存存储（`WORKFLOW_STORAGE=memory`）；需要跨进程
恢复时，在 `.env` 中显式设置 `WORKFLOW_STORAGE=postgres`。

可通过 `--max-document-characters`、`--max-reference-characters` 和
`--max-reference-total-characters` 控制喂给模型的 PDF 文本和引用区大小；
单条引用先按 content 上限截断，引用区再按总上限截断。成功或暂停时输出 JSON
运行摘要，包含报告路径、页数、引用数、模型、token 用量和 `thread_id`。

离线测试使用 fake extractor / fake retriever / fake model，覆盖 graph compile、
节点执行、interrupt 后 resume、rejected 后带反馈重跑、max revisions 终止、
service 跨实例恢复、API 鉴权/租户隔离和 PostgreSQL checkpoint 跨“重启”恢复，
不访问外网、不调用真实 LLM。该 workflow 是参考实现，生产默认路径仍然是
`AgentRuntime`；HTTP 层通过独立 `PDFReportWorkflowService` 边界执行，不改动
Chat/Agent/OpenAI 路由与 SSE 契约。

## Agent token budget 与 RAG 回填

Agent 前端默认显式发送 `token_budget=8192`、`max_steps=4` 和
`timeout_seconds=60`，后端 `AgentRunRequest` 使用相同安全默认值；未发送这些字段的
旧客户端也会得到同一组默认值。后端边界为 `token_budget <= 16384`、
`max_steps <= 20`、`timeout_seconds <= 120`，前端在发请求前做同样的整数/边界校验，
不发送无限预算。

预算语义保持真实：Runtime 会把每一轮模型调用的 `prompt_tokens + completion_tokens`
累加到 `state.token_usage`，多轮 Agent 会重复发送完整 transcript，因此 RAG 工具结果
回填后会放大下一轮 prompt，并再次计入累计用量。这就是旧默认 `2048` 会在 RAG 检索完成、
最终回答尚未生成时提前触发 `token_budget_exceeded` 的原因。本项目当前默认已提升为
`8192`；本轮不改变该语义，也不删除
预算检查；真正超限仍返回 `stopped / token_budget_exceeded` 和真实 usage，不会伪造
`answer`。前端对预算终态显示“Agent 达到 token 预算，已完成检索但未生成最终回答”，
并与超时、取消和 RAG 失败分开展示。

RAG 回填已有有界保护：`RAGService` 使用 `RAG_MAX_CONTEXT_CHARS`（默认 10000）限制
检索上下文；`ToolExecutor` 默认把结构化工具输出限制在 8192 字符，并保护
`document_id`、`chunk_id`、`call_id`、`id` 等稳定标识不被截断；公开来源投影仍只返回
最多 1200 字符的脱敏片段。提高默认 token budget 不会让回填 prompt 无限放大。

Ollama Provider 目前没有 `num_ctx` 配置入口，本轮不为此重构 Provider；后续任务需要为
Ollama 请求增加可配置的上下文窗口，并确保 Agent/RAG 提示词不超过模型上下文限制。

## Evaluation Foundation

Evaluation Foundation 提供离线、确定性的 golden data contract 与顺序执行 runner：评测用例通过 JSONL 保存，runner 接受可注入的异步 `run_case`，不会调用真实 LLM 或外网。单用例结果记录状态、成功与否、答案/工具判定、工具序列、步骤、延迟、Token 用量和错误；汇总提供任务成功率、声明工具期望用例的 tool selection accuracy、平均步骤、p95 延迟和 Token 总量/均值。`tests/fixtures/evals/agent_golden.jsonl` 是 30 条本地契约 fixture，覆盖 direct-answer、calculator 和 knowledge_search，它明确不是线上模型结果，也不包含密钥。RAG 离线评测入口与指标已实现（见下方 RAG 评估），RAG 评估已支持 CI 回归阈值断言和 `rag_evaluation_runs` 数据库持久化报告（Sprint 15）；Agent 评估的 CI 回归和数据库报表待后续实现。

### RAG 评估

在现有 `EvalCase`/`EvalExecution` 契约之外，新增独立的 `RAGEvalCase`、`RAGEvaluationRunner` 和确定性检索指标，不修改既有字段语义：

- `context_recall_at_k`：优先按 golden 声明的期望 chunk id 计算 `|期望 ∩ 检索结果前 k 个| / |期望|`；未声明 chunk 期望时回退到 document id。
- `document_recall_at_k` / `chunk_recall_at_k`：分别统计文档级和分块级召回，未声明的层级返回 `None`。
- `answer_correctness`：复用 `answer_matches_expected` 做片段包含判定，只在评测方提供模型回答时计算；真实检索脚本默认不调用 LLM。
- 报告只输出稳定标识、指标和有限错误类别，不输出密钥、完整文档内容或内部堆栈。

真实检索入口：

```bash
python scripts/evaluate_rag.py tests/fixtures/evals/rag_golden.jsonl \
  --owner-key-hash <64位SHA-256十六进制> \
  --retriever embedding \
  --output output/rag_eval_report
```

需要 `RAG_ENABLED=true`、PostgreSQL/pgvector 与 Ollama。`--retriever embedding` 直接注入生产 `Embedder` + `VectorStore`，评估原始检索质量；`--retriever service` 复用 `RAGService.prepare` 路径，保留生产环境的分块/上下文截断语义。脚本输出 `.json` 和 `.md` 报告。离线测试使用 `tests/fixtures/evals/rag_golden.jsonl` 与 fake retriever，不调用真实模型。

当前覆盖指标：`context_recall_at_k`、`document_recall_at_k`、`chunk_recall_at_k`、检索成功率、平均检索分块数、p95 延迟，以及可选 answer fragment 正确率；CI 回归阈值断言和数据库持久化报告已实现（Sprint 15）。后续可扩展：`precision@k`/MRR/nDCG、LLM-as-judge 的 faithfulness/answer correctness、Agent 评估的 CI 回归和数据库报表。

默认不引入 RAGAS：当前指标都是确定性、离线可复现的检索召回指标；RAGAS 的 LLM-as-judge 指标需要真实模型调用、对模型版本敏感且结果不可完全复现，适合后续作为可选扩展而不是基础依赖。

学习总结：本 Sprint 学到应先固定可序列化的评测数据契约，再通过依赖注入让 runner 保持离线和可重复。将答案包含判断与完整有序工具序列判断拆开，使失败原因和聚合指标更清晰。p95 对空集返回 `0.0`，tool accuracy 在没有声明 expected_tools 时返回 `None`，避免制造误导性统计。通过 JSON 标准库解析而不是 `eval`，并用异常隔离保证单个 case 不会阻断整批评测。

## Frontend Agent Console

前端位于 `frontend/`，基于 Vite、React 和 TypeScript。阶段 2 的普通 Chat SSE 保持不变；阶段 3 新增独立的 Agent Run 模式，阶段 4 在此基础上接入同步 `POST /api/v1/agent/runs` 的 Tool 级 RAG 来源卡片。前端只消费对应 Tool Call 下的公开契约 `steps[].tool_calls[].rag.references`，不维护回答级全局来源。

阶段 3 当前能力：

- 普通模式继续解析 `POST /v1/chat/completions?stream=true` 的真实 SSE 增量，不把普通聊天伪装成 Agent Trace；
- Agent 模式现在使用 Agent SSE 实时更新；同步 JSON 客户端仍保留用于兼容和回归；
- 左侧最终回答与右侧 Run 状态联动，支持 `completed`、`stopped`、`failed`、`cancelled`、`timed_out`，并区分浏览器中止等待与后端取消终态；
- 领域适配层稳定排序并去重步骤/事件，组件不直接依赖后端原始 JSON；
- 步骤卡片展示序号、决策类型、状态、工具名称和后端提供的安全摘要；后端公开契约没有时间戳与耗时，因此显示“后端未提供”，不会补零或伪造；
- Tool Call 卡片支持 `calculator` 与未知工具的成功、失败、超时、取消和未知状态；后端现在提供有界安全摘要：calculator 展示脱敏后的 expression/result，knowledge_search 隐藏原始 query 并展示 RAG 状态，未知工具只展示参数数量和结果字符数；原始 payload 仍显示为“后端未提供”；
- 仅在后端响应包含 `run_id` 时展示 Run ID；Token 仅在公开 `usage` 字段非空时展示实际值；
- 支持步骤与工具摘要展开/收起、停止本地请求、失败后重新运行、新建会话和清空会话；窄屏布局避免 Trace 横向溢出；
- 前端对 HTTP、网络、Abort 和异常响应进行安全归一化，不渲染堆栈、内部路径、API Key、Provider 原始响应或模型思维链。

阶段 4 当前能力：

- `knowledge_search` 的来源只在对应 ToolCallCard 内展示，沿用真实的 `stepIndex` 和 `callId` 关联，不提升为回答级全局来源；
- 读取公开契约 `steps[].tool_calls[].rag.references`，展示真实的稳定来源标识、分块索引、片段摘要和 distance；缺失字段显示“后端未提供”，不生成文档名、URL、rank、引用编号或回答内精确引用；
- 支持有来源、来源缺失、空来源、无相关来源、知识库为空、RAG 服务不可用、Embedding 失败、输出不可用和其他失败状态；服务故障不会伪装成无相关来源；
- 来源片段遵循后端公开边界，并在前端显示截断/安全提示；RAG 内容始终作为不可信参考材料以普通文本渲染，不执行其中的指令或 HTML。

阶段 5 当前能力：

- 核心输入、发送、停止、新建、清空、重试以及 Step/Tool/RAG disclosure 均支持键盘操作，并提供正确的 `aria-expanded`、`aria-controls` 和动态 accessible name；多行输入保留 Enter，使用 `Ctrl/⌘ + Enter` 发送或运行；
- 通过单独的低频 live region 播报 Chat、Agent、RAG 和重试关键状态，SSE 增量不会逐条触发播报；状态同时提供文字和结构表达，不依赖颜色；
- Request ID 与真实 Run ID 提供复制反馈；Chat/Agent 错误使用安全文案并支持重试，重试和会话切换隔离旧回答、Trace、来源、错误及晚到回调；
- 响应式目标覆盖 320px、375px、768px、1024px 和 1440px，长回答与长标识支持换行或安全截断；
- Agent SSE 解析真实的 `run_started`、Step、Tool、RAG、回答和终止事件；事件按 `run_id`/`sequence` 隔离，重复或乱序事件安全忽略；
- 前端五项门禁已通过：格式检查、Oxlint、TypeScript 类型检查、Vitest（13 个测试文件、141 个测试全部通过）和生产构建。真实浏览器已通过开发期 Vite proxy 验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待后的“后端终态未知”、offline 后 `connection_lost`、恢复网络后的重试成功，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行；320px、375px、768px、1024px、1440px 五档均无横向溢出。`npm run a11y:smoke` 已使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过：初始空态与真实 Agent/RAG 状态均为 axe `violations=0`；初始空态另有 1 个 `incomplete` 的 color-contrast（`.emptyIcon` 内容过短，axe 无法判断），不能表述为 axe 完全没有 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、键盘 Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出均通过；完整 VoiceOver/NVDA/Orca 仍未验证。

当前边界：Agent SSE 的 final answer 支持真实文本 `answer_delta`，其 `delta` 来自显式 Agent final-answer `ChatService.chat_stream()` 的 provider chunks；Runtime 按 `sequence` 发布并累计完整答案。`assistant_message` 作为 legacy/非 streaming 兼容事件保留，也用于 Runtime 直接以工具结果完成（例如 calculator 捷径）时传递完整真实回答，同步 Agent API 仍保持非流式。空流不生成补充文本，Provider 错误、超时和取消不被改写为成功；增量沿用安全敏感字段清洗，不暴露模型 JSON、Prompt、工具原始输入输出、Provider 原始响应、堆栈、密钥或内部路径。SSE 通过真实 `cumulative_token_usage` 提供累计总 Token，同步 JSON 提供分项 usage；前端不补造缺失的分项或事件时间。前端 Abort 只停止等待，只有收到真实 `run_cancelled` 才显示后端取消；网络断连和格式错误分别独立显示。启动阶段 `stream_error` 可以缺少 `run_id`/`sequence`，前端会归一化并将其归类为 `AgentNetworkError`；它只表示流启动或连接边界错误，不代表 Run 终态。开发期 Vite proxy 的 key 只由 Node proxy 注入，不进入浏览器 bundle；真实浏览器已验证空库 `RAG loading` → `knowledge_base_empty` → `run_completed`，并在真实 ingest 53 个 chunks 后验证 `success_with_sources` 和 5 条安全来源；该次 UI Run 后续因 `token_budget_exceeded` 停止。独立真实 SSE 请求收到多个 `answer_delta`，并以唯一 `run_timed_out`（`deadline_exceeded`）终止，不能写成 `run_completed`。当前默认 `RAG_ENABLED=false`，上述验证使用显式真实依赖；RAG 安全投影和状态仅由后端/组件测试及真实验证覆盖，不能伪造来源。完整屏幕阅读器仍未验证；事件历史回放、持久化 Trace 查询、回答内精确引用、MCP UI 和复杂多 Agent 编排仍不在阶段 6 范围。

> [Agent SSE 事件契约](docs/superpowers/specs/2026-08-05-agent-sse-event-contract.md) 和 [阶段 6 开发记录](docs/roadmap/2026-08-05-agent-sse-stage-6-record.md) 记录真实事件、字段、顺序、终止与取消边界。

启动前端开发服务器：

```bash
cd frontend
npm install
npm run dev
```

连接本地后端和开发 API Key：

```bash
cd frontend
AI_PLATFORM_DEV_API_BASE_URL=http://127.0.0.1:8000 \
AI_PLATFORM_DEV_API_KEY=sk-your-dev-key \
npm run dev
```

### 前端鉴权与跨源边界

开发期 Vite dev server 会把同源 `/api` 和 `/v1` 转发到 `AI_PLATFORM_DEV_API_BASE_URL`（默认 `http://127.0.0.1:8000`），并从 Node 进程环境变量 `AI_PLATFORM_DEV_API_KEY` 注入后端请求的 `Authorization` header。它们不使用 `VITE_` 前缀，不进入 `import.meta.env`，也不会写入浏览器源码、Git、默认配置或生产构建；普通 Chat SSE 和 Agent SSE 都通过标准 Vite proxy 保持流式。

生产入口仍通过运行时配置读取 Chat/Agent API 地址和 Bearer API Key。开发时如需绕过 dev proxy，也可以在页面加载前注入运行时配置：

```html
<script>
  window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
    apiBaseUrl: "http://localhost:8000",
    apiKey: "<runtime-injected-key>",
  };
</script>
```

生产构建不启用 Vite dev proxy；浏览器跨源直连仍需后端允许对应 Origin。浏览器中的 Bearer Key 不属于生产级密钥保护方案，生产部署应优先使用同源 BFF/服务端代理或其他受控鉴权边界。本阶段不修改后端，也不通过前端绕过 CORS 或鉴权。

前端验证命令：

```bash
cd frontend
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
```

阶段 3 测试覆盖空 Trace、calculator 成功/失败/超时/取消、未知工具、重复步骤与事件去重、长摘要截断与敏感内容清理、异常响应、Trace 展开/收起、回答与 Trace 状态一致、本地取消和重新运行，并保留阶段 2 的 Chat SSE 回归测试。

阶段 3 学习总结：同步 Agent API 必须与实时 Chat SSE 在交互上明确分离，避免把请求等待状态伪装成实时 Trace。领域适配层负责去重、稳定排序、缺失字段和安全摘要，使组件只消费可审计数据。浏览器 Abort 只能证明前端停止等待，不能声称后端 Runtime 已取消。公开契约缺少时间、耗时和工具载荷时，清晰展示“后端未提供”比填充假值更可靠。

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

| Method | Path                       | Description                                                         |
| ------ | -------------------------- | ------------------------------------------------------------------- |
| GET    | `/api/v1/health`           | Liveness probe                                                      |
| GET    | `/api/v1/ready`            | Readiness probe (checks downstream)                                 |
| GET    | `/api/v1/health/mcp`       | MCP lifecycle/readiness status (when enabled)                       |
| GET    | `/api/v1/usage`            | Token usage statistics                                              |
| GET    | `/api/v1/conversations`    | List the tenant's conversation threads                              |
| GET    | `/api/v1/conversations/{thread_id}/messages` | List a thread's tenant-scoped history |
| POST   | `/v1/chat/completions`     | OpenAI-compatible chat completions (supports SSE streaming)         |
| GET    | `/api/v1/models`           | List available LLM models                                           |
| POST   | `/api/v1/chat`             | Generate a chat completion with model-based provider routing        |
| POST   | `/api/v1/chat/rag`         | RAG-enhanced chat completion (requires `RAG_ENABLED=true`)          |
| POST   | `/api/v1/agent/runs`       | Bounded Agent Runtime run (model decision and controlled tool loop) |
| POST   | `/admin/api-keys`          | Create a new API key (admin only)                                   |
| GET    | `/admin/api-keys`          | List all API keys (admin only)                                      |
| DELETE | `/admin/api-keys/{prefix}` | Revoke an API key by hash prefix (admin only)                       |
| GET    | `/admin/usage/daily`       | Get daily token usage for an API key (admin only)                   |
| GET    | `/admin/usage/monthly`     | Get monthly token usage for an API key (admin only)                 |

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
  "message": { "role": "assistant", "content": "Hi there!" },
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
├── evals/          # Golden + RAG evaluation models, runner, JSONL, report
├── middleware/     # Context middleware (request_id)
├── observability/  # Optional OpenTelemetry setup, tracer and HTTP middleware
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
- **Observability** (`app/observability/`) — optional OTel spans for HTTP, LLM, Tool, RAG and Agent Run boundaries
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
OLLAMA_DEFAULT_MODEL=qwen3:4b-instruct
OLLAMA_TIMEOUT_SECONDS=120

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

# User Auth (registration/login with bcrypt)
USER_AUTH_ENABLED=true
USER_AUTH_STORAGE=postgres
JWT_SECRET=change-me-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=1440
BCRYPT_ROUNDS=12

# Bootstrap (Docker/Postgres only)
INITIAL_API_KEY=
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aiplatform
CONVERSATION_STORAGE=postgres
WORKFLOW_STORAGE=postgres

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

# Agent Runtime
# Frontend defaults: token_budget=8192, max_steps=4, timeout_seconds=60.
# Server limits: token_budget<=16384, max_steps<=20, timeout_seconds<=120.
RAG_EMBEDDING_MODEL=nomic-embed-text
RAG_EMBEDDING_DIMENSIONS=768
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=5
RAG_MAX_CONTEXT_CHARS=10000
RAG_MAX_DISTANCE=0.35
RAG_EMBEDDING_TIMEOUT_SECONDS=60
# Hybrid search: vector-only by default (legacy byte-identical behavior);
# switch to hybrid after the golden-set gate confirms hybrid >= vector.
RAG_SEARCH_MODE=vector
RAG_RRF_K=60

# MCP (disabled by default; JSON array of explicitly allowlisted servers)
MCP_ENABLED=false
MCP_SERVERS_JSON=

# OpenTelemetry (disabled by default; no collector dependency when off)
TELEMETRY_ENABLED=false
TELEMETRY_SERVICE_NAME=ai-platform-mini
TELEMETRY_EXPORTER=otlp
# TELEMETRY_SAMPLING_RATIO=1.0        # 0.0 (drop all) ~ 1.0 (keep all)
# TELEMETRY_METRICS_ENABLED=true
# OTLP endpoint may also be set via OTEL_EXPORTER_OTLP_ENDPOINT
TELEMETRY_OTLP_ENDPOINT=http://localhost:4318/v1/traces
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
- To evaluate retrieval quality against golden JSONL, run `python scripts/evaluate_rag.py` (see the RAG 评估 section)
- Empty knowledge base → `KnowledgeBaseEmptyError` (404); all retrieved results exceeding `RAG_MAX_DISTANCE` → `NoRelevantContextError` (404)

### MCP configuration notes

- `MCP_ENABLED=false` keeps MCP disabled and does not parse `MCP_SERVERS_JSON`
- `MCP_SERVERS_JSON` must be a JSON array; each entry requires `name` and a non-empty `command` array
- Each server can configure `allowed_tools`, `max_risk_level`, startup/request timeouts and string environment variables
- MCP tools are discovered during application startup and closed during shutdown; an unavailable server is isolated and does not block other configured servers
- Discovered tools require the `mcp:server:<server_name>` permission; the application grants it only to the Agent runtime for successfully discovered, explicitly configured servers, never from model output or user input
- Real stdio tools must provide explicit read-only/destructive annotations; unknown risk metadata is rejected (fail-closed), and duplicate tool names isolate the affected Server

### OpenTelemetry notes

- `TELEMETRY_ENABLED=true` 启用 OpenTelemetry（trace + metrics）；默认关闭时 `setup_telemetry()`/`setup_metrics()` 都是 no-op，不依赖外部 Collector
- `TELEMETRY_EXPORTER=otlp` 使用 OTLP/HTTP exporter（默认 `http://localhost:4318/v1/traces`），`console` 则把 span 输出到 stdout，便于本地查看
- `TELEMETRY_SAMPLING_RATIO`（0.0–1.0，默认 `1.0`）控制根 span 采样率；使用 `ParentBased(TraceIdRatioBased)`，子 span（LLM/Tool/RAG/Agent）跟随父 span 的采样决策，保证同一请求要么完整出现、要么完全不出现
- `TELEMETRY_METRICS_ENABLED=false` 可单独关闭指标，保留 trace；OTLP endpoint 也可通过标准环境变量 `OTEL_EXPORTER_OTLP_ENDPOINT` 配置
- 指标（默认随 `TELEMETRY_ENABLED` 开启，`PeriodicExportingMetricReader` 每 5s 导出一次，关闭时释放干净）：
  - `http.requests` counter / `http.duration_ms` histogram（method、endpoint、status_code）
  - `llm.calls` counter / `llm.duration_ms` histogram（model、stream、status）
  - `llm.prompt_tokens` / `llm.completion_tokens` counters（model）
  - `tool.executions` counter / `tool.duration_ms` histogram（name、status）
  - `rag.retrievals` counter / `rag.duration_ms` histogram（status）
- 覆盖的边界 span：HTTP 根 span（`request_id`、脱敏后的 `api_key_hash` 前缀、endpoint、状态码、耗时）、LLM `chat`/`chat_stream`（模型、耗时、`llm.usage.prompt_tokens`/`llm.usage.completion_tokens`）、Tool 执行（工具名、风险等级、状态、耗时）、RAG 检索（`top_k`、向量库原始返回数、进入上下文的引用数、耗时）、Agent Run（`run_id`、`stop_reason`、总 token）
- request_id 关联：HTTP 根 span 携带 `app.request_id`；`ContextMiddleware` 通过 contextvar 把当前请求 ID 显式附加到 LLM/Tool/RAG/Agent span（`app.request_id`），SSE/流式响应体迭代期间会重新绑定同一 request_id（async generator 不继承创建时的 contextvar，见 `_instrument_stream`），同时这些子 span 通过 span context 嵌套在 HTTP 根 span 之下，两种关联机制都可用
- span 属性与指标标签不会写入原始 prompt、API Key、完整文档或堆栈；`api_key_hash` 只暴露前 8 个字符；客户端取消会记录 `*_cancelled` 属性且不标记为错误
- SSE/Streaming 响应的 HTTP 根 span 会覆盖整个响应体生命周期，而不是在响应头返回时提前结束
- 本地快速查看：

```bash
TELEMETRY_ENABLED=true TELEMETRY_EXPORTER=console .venv/bin/uvicorn app.main:app --reload
```

在 Jaeger/OTLP Collector 端查看时，配置 `TELEMETRY_OTLP_ENDPOINT`（或标准 `OTEL_EXPORTER_OTLP_ENDPOINT`）指向 Collector 的 OTLP HTTP 端点；可以只写 base（如 `http://localhost:4318`），trace 与 metrics exporter 会自动补全 `/v1/traces` 和 `/v1/metrics` 路径，也可以直接写完整路径。采集端建议将 OTLP/Prometheus 数据接入 Grafana 查看指标，Jaeger/Tempo 查看 trace。

### Auth 平台加固（用户注册、登录、身份与工作空间）

- `POST /auth/register` / `POST /auth/login`：用户注册与登录，密码经 bcrypt（`BCRYPT_ROUNDS` 可配置）哈希存储，登录返回 JWT（`JWT_SECRET`/`JWT_ALGORITHM`/`JWT_EXPIRATION_MINUTES`）。
- `GET /auth/me`：返回当前用户信息及所属工作空间列表，通过 `require_api_key` 鉴权。
- `IdentityContext`：`_update_context` 为异步方法，通过 `_resolve_role()` 查询工作空间 membership 填充 `role`（owner/member），`api_key_id` 由 `APIKeyMetadata.id` 真实填充。
- `POST /workspaces` / `POST /workspaces/{id}/members`：创建工作空间及添加成员。memory 与 Postgres 实现的 `add_member` 统一在重复加入时抛 `ConflictError`。
- 安全加固：密码验证使用 `hmac.compare_digest`（抗计时攻击）；6 处 `assert identity.*` 改为显式 `if … raise AuthenticationError`（避免 `-O` 优化被剥离）。
- `migrate_auth_schema` 执行顺序修复：置于 `create_all` 之后，并添加 `_table_exists` 守卫避免重复迁移。
- 测试：`test_auth_users.py`（注册/登录/me）、`test_identity.py`（role 解析）、`test_workspaces.py`（CRUD + 成员管理），全量 692 passed。

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
