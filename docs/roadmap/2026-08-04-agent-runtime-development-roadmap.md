# Mini Agent Runtime 开发规划

> 版本日期：2026-08-04  
> 项目仓库：`ai-platform-mini`  
> 求职目标：Agent 开发工程师 / LLM 应用开发工程师

## 1. 项目重新定位

### 1.1 对外名称

**Mini Agent Runtime：可观测、可扩展的 LLM Agent 执行平台**

保留仓库名 `ai-platform-mini`，暂不进行无业务价值的大规模重命名。项目对外介绍从“LLM Gateway”升级为：

> 基于 FastAPI、Ollama/OpenAI、PostgreSQL/pgvector 与 MCP 构建的轻量级 Agent Runtime，支持模型路由、工具调用、RAG、记忆、运行追踪及多 Agent 工作流。

### 1.2 核心求职卖点

项目重点不再是“能与模型聊天”，而是证明以下能力：

1. 理解并能独立实现 Agent 执行循环和状态机。
2. 能设计类型安全、可扩展、可治理的 Tool Calling 系统。
3. 能将 RAG、Memory、MCP 作为 Agent 能力接入，而不是散落在 API 中。
4. 能处理流式输出、取消、超时、重试、资源生命周期和故障传播。
5. 能用评测、追踪和安全策略证明系统不仅可演示，而且可维护。

## 2. 当前基础与规划起点

当前项目已经具备一个可复用的 LLM 应用基础，不需要按“backend / llm / frontend”重新拆仓库，也不需要迁移到 LangChain 或 LangGraph。

已有能力包括：

- FastAPI 与 OpenAI-compatible Chat API；
- Ollama/OpenAI 多 Provider 路由；
- 非流式及 SSE 流式响应；
- API Key 鉴权、限流、Token 配额和 Usage 统计；
- PostgreSQL 持久化、结构化日志、Request ID 和 Readiness；
- `api → service → provider → repository` 的分层边界；
- Adapter、生命周期管理、单元测试和 PostgreSQL 集成测试基础；
- 当前工作区已经有 `app/rag/` 的 RAG MVP 实现，正在完成收口。

因此，本规划的核心不是重写，而是**在现有 LLM Gateway 上增加 Agent 领域层**。

### 2.1 不变的部分

以下能力继续保留并作为 Agent 的基础设施：

- 现有 FastAPI 应用和公开 Chat API；
- 现有 `ChatService`、`LLMProvider`、`ProviderRouter` 和 Ollama/OpenAI 实现；
- 现有 SSE、鉴权、限流、配额、Usage 和 Request ID；
- 现有 PostgreSQL、Docker Compose、配置和生命周期管理；
- 现有测试、CI 质量门禁和 README 结构。

### 2.2 新增的部分

新增 Agent Runtime、Tool Runtime、Memory、MCP 和 Run Trace。它们通过接口调用现有 Service/Provider，不直接复制一套模型客户端。

### 2.3 兼容迁移策略

第一阶段不要强行改变原有 `/api/v1/chat` 的行为：

```text
现有 Chat API  ───────────────> ChatService ──> ProviderRouter

新增 Agent API ──> AgentService ──> AgentRuntime
                                      ├──> ChatService
                                      └──> ToolExecutor
```

这样可以做到：

1. 现有 Chat API 继续稳定工作；
2. Agent Runtime 可以独立测试；
3. 前端可以逐步从 Chat API 切换到 Agent Run API；
4. Agent 的失败不会破坏普通聊天链路；
5. 后续再决定是否将默认交互完全切换到 Agent 模式。

## 3. 架构演进目标

```text
                         用户 / 现有 Web UI
                                  |
                                  v
                 FastAPI Router + Auth / Quota / Rate Limit
                         |                    |
                         |                    +--> 现有 Chat API
                         |                              |
                         |                         ChatService
                         |                              |
                         |                         ProviderRouter
                         |
                         +--> Agent Run API
                                  |
                             AgentService
                                  |
                            Agent Runtime
                  state / loop / policy / events / stop rules
                         /                    \
                        v                      v
                 ChatService              Tool Runtime
                                              |
                                  +-----------+-----------+
                                  |           |           |
                            Calculator   RAG Tool    MCP Adapter
                                              |           |
                                         app/rag/     MCP Server
                                              |
                                       PostgreSQL/pgvector

                         Memory 与 Run Trace 作为横切能力
```

### 3.1 现有模块到新增模块的映射

| 现有能力 | 演进方式 |
|---|---|
| `app/api/chat.py` | 保持兼容，新增 Agent Run 路由，不立即删除 Chat 路由 |
| `app/services/chat_service.py` | 继续作为模型调用边界，Agent Runtime 通过它调用 LLM |
| `app/providers/` | 保留，不新增重复的 `llm/ollama.py` |
| `app/rag/` | 保留现有 RAG Pipeline，增加 `KnowledgeSearchTool` 适配层 |
| `app/core/container.py` | 增加 Agent、Tool、Memory 的依赖组装和生命周期清理 |
| `app/schemas/` | 增加 Agent Run、Event、Tool Call 的类型化 Schema |
| `app/db/` | 增加 Run、Step、Tool Call、Memory 数据模型 |
| 现有 Usage/Quota | Agent Run 复用，不另建一套计费逻辑 |

### 3.2 建议新增目录

```text
app/
├── agents/       # Agent 配置、状态、循环、停止策略和事件
├── tools/        # Tool Protocol、Registry、Executor、内置工具
├── mcp/          # MCP 连接管理、工具发现和 Tool Adapter
├── memory/       # 会话记忆、摘要和长期记忆
├── runs/         # Run/Step/ToolCall 持久化与查询
└── evals/        # 评测数据、运行器和指标
```

不新增 `backend/`、`llm/`、`database/` 等重复顶层目录。当前项目已经有 `app/providers/`、`app/db/`、`app/services/`，应沿用已有命名和依赖方向。

## 4. 八阶段开发路线

## Sprint 8：Agent Runtime 核心（2026-08-04—2026-08-10）

### 目标

在现有 ChatService 之上增加最小 Agent Core，让项目从“请求一次、回答一次”演进为“模型决策、执行动作、继续推理”的运行时。

### 交付内容

- `AgentState`、`AgentStep`、`AgentEvent` 和 `RunStatus`；
- Agent loop：模型决策 → 判断工具调用 → 执行 → 回填结果 → 继续推理；
- `AgentService` 作为 API 与 Runtime 之间的应用层；
- Agent Runtime 通过现有 `ChatService`/`LLMProvider` 调用模型；
- `max_steps`、deadline、Token 上限和停止原因；
- 新增 Agent API，但不改变现有 `/api/v1/chat` 行为；
- 使用假的测试 Tool 验证循环，不先接入高风险能力。

### 验收标准

- 覆盖直接回答、单次工具、多次工具、工具失败、超步数、超时和取消；
- Agent Runtime 不依赖 FastAPI；
- 不出现不可观测的无限 `while True`；
- 普通 Chat API 的现有测试全部保持通过。

### 简历价值

> 在既有 LLM Gateway 上增量实现 Agent Runtime，通过状态机管理模型决策、工具执行、停止条件、超时与取消传播。

---

## Sprint 9：Tool Registry 与 Executor（2026-08-11—2026-08-17）

### 目标

将工具调用从 Agent loop 中抽离为独立的 Tool System，形成稳定扩展点。

### 交付内容

- `Tool` Protocol；
- `ToolRegistry`：注册、去重、查询和模型 Schema 导出；
- `ToolExecutor`：参数校验、超时、异常归一化和输出截断；
- 类型化输入、输出 Schema；
- 风险等级、权限要求和工具执行上下文；
- 首批低风险工具：`calculator`、测试 Echo Tool；
- Tool Call 事件和失败结果回填 Agent State。

### 验收标准

- 非法参数不会进入工具实现；
- 工具超时不会卡住整个 Agent；
- 工具异常可被模型理解并决定重试或结束；
- Registry 不依赖具体 FastAPI 路由；
- 不使用 Python `eval()` 实现计算器。

### 暂不做

不提供无限制终端、任意写文件和任意网络访问。

### 实际状态

已实现并通过 Code Review。Tool Registry、Tool Executor、Calculator 及 Agent Runtime/Service 集成已提交，commit 为 `50af61e`。

---

## Sprint 10：RAG Tool 化与当前 MVP 收口（2026-08-18—2026-08-24）

### 目标

完成当前 `app/rag/` MVP，并将知识库检索接入 Tool Runtime，而不是继续把 RAG 逻辑堆进 Chat API。

### 交付内容

- TXT ingest、切片、Ollama Embedding 和 pgvector Top-K 检索收口；
- `KnowledgeSearchTool`，统一封装查询输入、检索结果和来源元数据；
- Agent 可自主判断是否调用知识库；
- 保留 `/api/v1/chat/rag` 作为兼容入口，但内部逐步复用 RAG Tool；
- Embedding、数据库和知识库为空时的明确错误边界；
- 检索片段的文档来源、距离和截断策略。

### 验收标准

- 普通 Chat、RAG Chat 和 Agent Run 三条链路均有测试；
- RAG 代码不直接依赖 Agent Runtime；
- Agent 能完成“提问 → 调用知识库 → 回填上下文 → 回答”；
- 一份 TXT 文档可以完成 ingest、检索和回答；
- 不在本 Sprint 同时引入 FAISS、Qdrant、Rerank 或复杂文档平台。

### 实际状态

已实现并通过第三轮 Code Review。`KnowledgeSearchTool`、`RAGReference`、容器注册和 Agent 集成测试已经落地；完整质量门禁及 Uvicorn 启动检查全部通过。

### 简历价值

> 将已有 RAG Pipeline 以 Tool Adapter 形式接入 Agent Runtime，实现知识检索、来源追踪和上下文增强生成。

---

## Sprint 11：MCP 工具接入（2026-08-25—2026-08-31）

### 目标

将外部 MCP Server 的工具映射为内部统一 Tool，不让 MCP 协议细节渗透到 Agent Core。

### 交付内容

- MCP Client 与连接生命周期管理；
- Server 配置、连接、工具发现和 Schema 缓存；
- `MCPToolAdapter`：MCP Tool → 内部 Tool Protocol；
- 调用超时、断线和错误映射；
- 首个只读 MCP 场景；
- Server/Tool allowlist，默认拒绝未知高风险能力。

### 验收标准

- Agent 不需要知道工具来自本地还是 MCP；
- MCP Server 不可用时只影响相关 Tool Call；
- 应用关闭时正确释放连接和子进程资源；
- 完成“发现工具 → 模型选择 → MCP 执行 → 继续回答”的端到端测试。

---

## Sprint 12：Session 与 Memory（2026-09-01—2026-09-07）

### 目标

先解决可靠的会话上下文，再实现受控的长期记忆。

### 交付内容

- Session、Message、Memory 数据模型；
- 短期记忆：消息窗口、Token 预算和历史摘要；
- 长期记忆：显式保存、检索、更新和删除；
- 用户级隔离、记忆来源、创建时间、最后使用时间和置信度；
- Memory 作为 Agent 上下文准备步骤接入。

### 验收标准

- 超长对话不会无限增长 Prompt；
- 用户可以查看和删除长期记忆；
- 默认不把模型猜测自动写成永久事实；
- 不同 API Key 或用户之间严格隔离。

---

## Sprint 13：Run Trace、评测与 Agentic RAG（2026-09-08—2026-09-14）

### 目标

让 Agent 的效果、成本和故障可以量化。

### 交付内容

- Run、Step、ToolCall 持久化；
- Agent SSE Event Stream；
- Run 查询、事件查询和取消 API；
- 记录 request_id、run_id、模型、Token、总耗时、Step 耗时和 Tool 错误；
- 建立 30—50 条黄金评测集；
- 评估任务成功率、工具选择准确率、平均步数、P95 延迟和 Token 消耗；
- 评估 RAG 的 Recall@K、来源命中率和回答引用正确性。

### 验收标准

- 每次 Agent Run 都能还原完整执行轨迹；
- 代码变更前后可以运行同一批评测；
- RAG 优化由指标驱动，而不是只增加组件；
- 日志不记录 API Key、完整敏感 Prompt 或未经处理的隐私数据。

---

## Sprint 14：多 Agent 工作流（2026-09-15—2026-09-21）

### 进入条件

只有单 Agent 的 Tool Runtime、Run Trace 和黄金评测集稳定后才进入本阶段。若多 Agent 没有相对单 Agent 的量化收益，则保留为实验，不作为核心卖点。

### 推荐场景

“调研并生成带来源报告”：

1. Supervisor 拆分任务；
2. Research Agent 使用搜索或 RAG 工具收集材料；
3. Writer Agent 生成结构化报告；
4. Reviewer Agent 检查来源覆盖和结论一致性。

### 交付内容

- Workflow 状态、节点、边和共享上下文；
- 子任务输入输出 Schema；
- 最大并发数、失败策略和总预算；
- 顺序与有限并行执行；
- 与单 Agent 基线进行成功率、延迟和 Token 成本对比。

---

## Sprint 15：安全、部署与作品集打磨（2026-09-22—2026-09-28）

### 目标

将架构演进结果整理成面试官能够快速运行、理解和验证的作品。

### 交付内容

- 文件根目录、网络域名、写操作和高风险操作确认策略；
- Prompt Injection 与工具输出污染的基础防护；
- Docker Compose 一键启动应用、PostgreSQL/pgvector 和 Ollama；
- Python 3.12、3.13、3.14 CI；
- 架构图、时序图、ADR、API 示例和故障处理说明；
- 保留现有 Web UI，并只增加 Agent Run/Step/Tool Call 展示所需的最小页面；
- 3—5 分钟 Demo 视频和面试讲解稿。

### Demo 场景

1. 文档知识问答：Agent 调用 `knowledge_search` 并展示来源；
2. MCP 工具：Agent 自动发现并调用只读外部工具；
3. 长期记忆：用户显式保存偏好，后续任务检索并应用；
4. 运行追踪：展示每个 Step、Tool 参数、耗时、Token 和停止原因；
5. 多 Agent 报告：Supervisor 协调 Research、Writer 和 Reviewer。

### 验收标准

- 新环境按照 README 可在 15 分钟内启动；
- 所有质量门禁和 CI 通过；
- Demo 不依赖手工修改数据库或隐藏步骤；
- 所有简历描述都能在代码、测试或 Demo 中找到证据。

## 5. 功能优先级

| 优先级 | 能力 | 原因 |
|---|---|---|
| P0 | Agent Runtime | 岗位核心，决定项目是否真正属于 Agent 项目 |
| P0 | Tool Runtime | 展示工具调用、Schema、安全和扩展能力 |
| P0 | Evaluation | 证明效果可衡量，显著区别于普通 Demo |
| P0 | Run Trace / Observability | 证明具备调试和生产治理意识 |
| P1 | RAG as Tool | 复用当前投入，形成真实 Agent 场景 |
| P1 | MCP | 与现代 Agent 工具生态连接 |
| P1 | Memory | 展示上下文工程和持久化设计 |
| P1 | Security / Lifecycle | 体现企业工程能力 |
| P2 | Multi-Agent | 有评测收益时才有价值 |
| P2 | Minimal Web UI | 用于展示，不应抢占后端核心时间 |
| 暂缓 | 任意 Terminal、浏览器自动化 | 风险和复杂度高，先完成权限与沙箱设计 |
| 暂缓 | 微服务拆分、Kubernetes | 当前规模下会稀释核心能力，收益有限 |

## 6. 每个 Sprint 的统一完成定义

每个 Sprint 必须完成：

1. 写设计说明，明确目标、非目标和职责边界；
2. 新增或修改的生产代码具有完整类型标注；
3. 覆盖正常、失败、超时、取消和边界场景；
4. 运行：
   - `ruff format --check .`
   - `ruff check .`
   - `mypy app tests`
   - `pytest`
   - 需要 PostgreSQL 时运行集成测试；
5. 更新 README 和架构说明；
6. 展示变更，等待用户 Code Review；
7. Review 通过后使用 conventional commit 提交并推送；
8. 写不超过 5 句话的学习总结。

## 7. 技术选型原则

- **Agent Runtime 第一版自行实现**：核心循环、状态和工具执行不直接交给框架，便于面试讲清原理。
- **LangGraph 作为对照或后续适配**：只有需要复杂图工作流、持久化检查点或人工审批节点时再引入；不为了技术栈关键词提前增加依赖。
- **继续使用 PostgreSQL/pgvector**：当前项目已有数据库基础，不再同时引入 FAISS、Qdrant 等重复存储。
- **MCP 通过 Adapter 接入**：内部 Tool Protocol 保持稳定，避免 Runtime 与外部协议强耦合。
- **模块化单体优先**：先证明边界正确、测试充分，再考虑拆服务。
- **安全默认拒绝**：高风险工具、未知 MCP Server、任意路径和任意网络访问默认不可用。

## 8. 完成后的简历项目描述

### 项目名称

**Mini Agent Runtime｜可观测、可扩展的 LLM Agent 执行平台**

### 项目简介

基于 FastAPI、Ollama/OpenAI、PostgreSQL/pgvector 与 MCP 构建轻量级 Agent Runtime，在现有 LLM Gateway、鉴权、配额和 Usage 基础上，实现工具调用、RAG、会话与长期记忆、运行追踪及多 Agent 工作流。

### 核心工作

1. 独立实现 Agent 状态机和执行循环，支持多步工具调用、停止策略、超时、取消传播及流式事件输出。
2. 设计类型安全的 Tool Registry 与 Executor，统一管理参数校验、权限、超时、错误和调用轨迹，并将 RAG 与 MCP 工具通过 Adapter 接入。
3. 基于 PostgreSQL/pgvector 构建知识检索与长期记忆能力，实现用户级隔离、Token 预算、来源追踪和记忆治理。
4. 建立 Run/Step/ToolCall 可观测模型及离线评测集，量化任务成功率、工具调用准确率、延迟和 Token 成本。
5. 完成 Docker Compose、Python 3.12—3.14 CI、结构化日志、安全策略、集成测试和可复现 Demo。

> 注意：简历只写已经完成并能演示、测试或量化的内容；未完成的 LangGraph、多 Agent、MCP 或前端能力不提前写入技术栈。

## 9. 最终作品集清单

- 可公开访问的 GitHub 仓库和清晰提交历史；
- 一页架构总览；
- Agent Run 与 Tool Call 时序图；
- 30—50 条评测集及结果表；
- 3—5 分钟 Demo 视频；
- README 快速启动和四个核心场景；
- 一篇技术文章：如何从 LLM Gateway 演进为 Agent Runtime；
- 一份面试讲解提纲：架构取舍、失败处理、评测、安全和未来优化。

## 10. 执行原则

接下来不要同时开发 RAG、MCP、Memory、多 Agent 和前端。严格按照“先建立中间层，再逐步接入能力”的主线推进：

```text
保留现有 Chat 链路
  -> Agent Runtime
  -> Tool Registry / Executor
  -> RAG Tool 化
  -> MCP Adapter
  -> Memory
  -> Run Trace + Evaluation
  -> Multi-Agent（有收益才做）
  -> 安全、部署与展示
```

这条路线的目标不是把项目重写成另一个框架，而是让现有 LLM Application 逐步演进为 Agent Platform：保留现有 API、Provider、SSE、Docker 和测试基础，只增加可解释、可测试、可扩展的 Agent 中间层。
