# AI Platform Mini

基于 **FastAPI + React** 的轻量级 LLM 应用平台：提供 OpenAI-compatible LLM
Gateway、有界 Agent Runtime（Tool Calling）、RAG 检索增强、长期记忆、多 Agent
编排、LangGraph/Workflow Builder 工作流、多租户（身份/工作空间/计费/审计）与完整可观测性。既是可用的产品原型，
也是可解释、可验证、经过 Code Review 的 Agent 工程演示。

## 目录

- [项目亮点](#项目亮点为什么这是一个强的-agent-工程演示)
- [技术栈](#技术栈)
- [架构总览](#架构总览)
- [核心能力](#核心能力)
- [前端平台](#前端平台)
- [快速开始](#快速开始)
- [Docker 演示](#docker-演示)
- [质量门禁与 CI](#质量门禁与-ci)
- [API 一览](#api-一览)
- [关键契约与设计文档](#关键契约与设计文档)
- [配置](#配置)
- [目录结构](#目录结构)
- [设计原则](#设计原则)
- [项目规则](#项目规则)
- [开发历程](#开发历程)

## 项目亮点（为什么这是一个强的 Agent 工程演示）

这个项目不是只调用一次大模型 API 的聊天页面，而是一个可解释、可验证的 Agent
应用平台：

- **Agent Runtime**：模型决策、有限步数循环、工具执行、结果回填、超时、取消、
  配额和 Token 预算终态都有明确边界。
- **多 Agent 编排**：`/api/v1/multi-agent/runs` 先由 Supervisor 拆分任务，再由
  Orchestrator 按依赖、并发、失败策略、超时和总 Token 预算执行子任务。
- **真实 Tool Calling**：内置 `calculator` 和 `knowledge_search`，通过 Tool
  Registry/Executor 做 Schema 校验、权限边界、超时和输出截断。
- **可观察性**：Agent SSE 实时发送步骤计划、Tool Call、RAG 状态、回答增量和
  终态；前端 Trace 展示真实事件，不补造时间、来源或回答。
- **RAG 闭环**：文档入库（PDF/TXT/Markdown/DOCX/XLSX/HTML）→ 注入安全评估 →
  分块 → Embedding → pgvector 检索（hybrid/RRF + 可选 rerank）→ 安全来源投影 →
  Agent 回答。
- **安全与多租户**：API Key 哈希存储、scrypt 密码哈希、限流、Token 配额、
  计费计划、审计日志；RAG 文档按租户隔离；Prompt、原始 Tool payload、Provider
  响应和敏感信息不公开。
- **工程质量**：后端 1009 个通过测试（87 个测试文件）+ 前端
  Vitest/Playwright/a11y 门禁、真实浏览器验证、失败/超时/断连回归、多 Python
  版本 CI 和 Code Review 记录。

### 面向 HR 的建议演示路径

1. 打开 **平台概览**，展示 Provider、Agent Runtime 和 RAG 的真实状态。
2. 进入 **Agent 工作流**，运行 calculator，展开右侧 Trace，说明
   `step_planned → tool_started → tool_completed → final_answer`。
3. 进入 **知识库** 上传一份 PDF，再点击“去知识库问答”；入口会自动进入
   **Agent Run + RAG Agent preset**，不会误用普通 Chat SSE。
4. 展示真实来源卡片、`success_with_sources`/`no_relevant_sources` 状态和安全
   投影字段。
5. 打开 **长期记忆** 保存一条偏好，再运行 Agent，说明记忆只经显式 CRUD 写入、
   按用户/工作空间隔离，并在 Agent 上下文准备阶段检索注入。
6. 调用 **多 Agent Run** 演示 Supervisor 拆分、Orchestrator 执行子任务和结构化汇总。
7. 打开 **Run 历史** 回放一次 Agent Run，再查看 **用量仪表盘** 和
   **Billing / 计划**。
8. 打开管理员后台，展示 API Key、Token 用量、Agent Run 审计摘要和 Audit 日志。

> 普通 Chat SSE 只负责普通对话，不执行工具调用；知识库问答使用 Agent Run，
> 先检索再回答。两条链路在 UI 和 API 层均明确区分。

## 技术栈

- **后端**：Python 3.12–3.14（默认 3.14）、FastAPI、Pydantic v2、
  SQLAlchemy 2（async）、asyncpg、pgvector
- **LLM/Agent**：Ollama、OpenAI、自研 Agent Runtime、MultiAgentService
  （Supervisor + Orchestrator）、LangGraph（PDF 报告工作流参考实现）、MCP
  （stdio 客户端）
- **前端**：React 19 + TypeScript + Vite，Vitest/Playwright/axe
- **可观测性**：OpenTelemetry（OTLP trace + metrics）、Jaeger、结构化 JSON 日志
- **基础设施**：Docker Compose（5 服务）、GitHub Actions CI（4 个 job）

## 架构总览

```mermaid
flowchart TB
    subgraph Client["客户端"]
        FE["React 前端
平台壳层 / Agent / Memory / Workflow Builder"]
        SDK["OpenAI-compatible SDK / curl"]
    end

    FE -->|"/api/*"| NGINX["nginx 同源代理"]
    SDK -->|"/v1/chat/completions"| API
    NGINX --> API["FastAPI 应用"]

    API --> MW["ContextMiddleware / RequestLoggingMiddleware<br/>(request_id)"]
    MW --> GATE["Auth / Rate Limit / Quota / Entitlement"]
    GATE --> ROUTES["Router 层（22 routers）"]

    ROUTES --> CHAT["ChatService / OpenAIService"]
    ROUTES --> AGENTAPI["AgentService"]
    ROUTES --> MULTI["MultiAgentService"]
    ROUTES --> MEM["MemoryService"]
    ROUTES --> WFB["Workflow Builder Service"]
    ROUTES --> WF["LangGraph PDF Workflow"]

    MULTI --> SUP["Supervisor
任务拆分"]
    MULTI --> ORCH["Orchestrator
依赖 / 并发 / 预算"]
    ORCH --> CHAT
    AGENTAPI --> RUNTIME["AgentRuntime（有界循环）"]
    RUNTIME --> TOOLS["ToolRegistry / ToolExecutor"]
    RUNTIME --> MCPMGR["MCP Manager"]
    AGENTAPI --> RAGSRV["RAGService"]
    CHAT --> ADAPTER["OpenAIAdapter"]
    MEM --> AGENTAPI

    RAGSRV --> VECTOR[("pgvector")]
    RAGSRV --> EMBED["Ollama Embedder"]
    WFB --> WFE["通用 DAG Engine"]
    WF --> CKPT[("Checkpointer
内存 / PostgreSQL")]

    ADAPTER --> ROUTER["ProviderRouter"]
    ROUTER --> OLLAMA["OllamaProvider"]
    ROUTER --> OPENAI["OpenAIProvider"]
    ROUTER --> MOCK["MockProvider（测试）"]

    GATE --> DB[("PostgreSQL
用户 / Key / 用量 / 配额
Billing / Audit / Agent / Memory / Workflow")]
    MEM --> DB
    WFB --> DB
    CHAT --> OBS["OpenTelemetry
trace + metrics"]
    RUNTIME --> OBS
    RAGSRV --> OBS
```

## 核心能力

> 可交互 HTML 架构图见
> [`docs/architecture/ai-platform-mini-architecture.html`](docs/architecture/ai-platform-mini-architecture.html)；
> 同目录保留 Archify JSON 规格、visual-check 截图和校验 receipt。

### LLM Gateway

- OpenAI-compatible `POST /v1/chat/completions`，支持普通响应与 SSE 流式响应
- 原生 Chat、Models、Health、Readiness 和 Usage API
- 可替换的 `LLMProvider` Protocol、共享 HTTP 连接池和统一异常映射
- `ProviderRouter` 按模型自动选择 OpenAI 或 Ollama，Service 和公开端点无需感知
  Provider（默认模型 → Ollama，`gpt-*` → OpenAI，Mock 用于测试）
- `OpenAIAdapter` 无状态协议转换，将请求和非流式响应映射从 Service 提取到独立
  Adapter

### Agent Runtime 与工具系统

- 独立于 FastAPI 的有界 Agent Runtime：模型决策、Tool 调用、结果回填和多步循环
- 支持 `max_steps`、deadline/timeout、外部取消和 provider-reported Token budget；
  未知 Token 用量不会被伪装成 0
- `POST /api/v1/agent/runs`（同步）与 `POST /api/v1/agent/runs/stream`（SSE）：
  复用现有鉴权、限流、配额、Usage 和统一异常边界
- Tool Registry/Executor：Schema 参数校验、超时、异常安全归一化、输出截断和
  工具 Schema 导出；内置低风险 `calculator`（AST 白名单，不使用 `eval()`）与
  `knowledge_search`（RAG 工具），默认不开放任意文件、网络或 Shell 能力
- MCP foundation：stdio JSON-RPC Client、工具发现、allowlist、
  `MCPToolAdapter`、生命周期 health/readiness 查询；不可用 Server 被隔离，
  不阻断其他 Server
- Agent 定义落库：`Agent = Model + Prompt(版本) + Tools(白名单)`，支持
  workspace 级工具启用开关、Prompt 版本激活/回滚/钉扎、Agent Benchmark
  （Tool Call Accuracy / Task Completion Rate / Average Steps / Latency）
- 长期记忆：显式保存事实/偏好/指令，按“用户 + 工作空间”严格隔离；Agent Run
  自动检索相关记忆并注入系统 Prompt，模型猜测不会自动写入永久记忆
- 多 Agent 编排：`POST /api/v1/multi-agent/runs` 先让 Supervisor 输出任务图，
  再由 Orchestrator 依据依赖、`max_concurrency`、`failure_policy`、
  `total_timeout` 和 `total_token_budget` 执行；子任务结果、错误、Token 用量和
  耗时以结构化响应返回

### RAG 检索增强与安全

- 文档管线：Parser 工厂按扩展名路由
  PDF / TXT / Markdown / DOCX / XLSX / HTML（确定性解码、YAML frontmatter
  剥离、script/style 跳过），`ingest_document` 统一主路径：
  parse → safety → chunk → embed → 入库
- 检索：pgvector 余弦距离 + jieba 中文关键词（`to_tsvector`）+ RRF
  （`1/(60+rank)`）混合融合；`RAG_SEARCH_MODE=vector|hybrid|keyword`；
  可选 Jina rerank（失败自动降级，Cohere 预留同一 Protocol）
- **Prompt Injection 防护**：入库前确定性规则评估（中英文注入模式 + 指令轰炸
  启发）；`malicious` 拒绝入库（422），`suspicious` 按 `RAG_SAFETY_MODE`
  处理（strict 隐藏 / flag 标记 / off 关闭）；`SafetyReviewer` Protocol 预留
  LLM 复核扩展点
- **安全来源投影**：Agent Run 只向客户端公开稳定标识、分块索引、截断片段和
  真实 distance；不暴露查询文本、Tool 原始输入/输出、Prompt、Provider 响应、
  堆栈、密钥或内部路径（详见[关键契约](#agent-run-rag-公开契约)）

### LangGraph PDF 报告工作流

- 参考实现：`parse_pdf → retrieve_context → analyze → request_approval`
  （人工审批 human-in-the-loop；拒绝可带反馈重新分析，超过 `max_revisions`
  终止）
- Checkpointer 默认内存，`WORKFLOW_STORAGE=postgres` 切换
  `langgraph-checkpoint-postgres` 持久化，支持跨重启恢复和多 worker 部署
- HTTP API：创建 / 状态查询 / 审批 / 拒绝 / **历史列表**（`GET
  /api/v1/workflows`，按租户隔离）；前端 PDF 工作流页支持历史任务查看、
  显式反馈与回退到当前任务
- 不替换、不修改现有 `AgentRuntime` 与 Chat/Agent/OpenAI API；主 Agent 链路
  仍由自研 `AgentRuntime` 负责

### Workflow Builder（通用可视化编排）

- 通用串行 DAG 编排，与固定 PDF 工作流命名空间隔离：`app/workflows/engine/`
  负责定义校验与执行引擎，`app/workflow_builder/` 提供 API/双存储/真实节点
  执行器，React Flow 前端负责画布交互
- 节点类型：`input` / `llm` / `knowledge` / `tool` / `condition` / `agent` /
  `output`；保存前校验 DAG 无环、入边 ≤1、条件表达式三字面形式、模板引用
  存在性与拓扑序；发布冻结版本，试运行落 run 快照
- 前端入口：左侧导航 `Workflow Builder`，支持新建/编辑节点、画布连线、节点
  配置表单、本地校验、保存/发布/取消发布/删除，以及 JSON 试运行并查看
  `node_results` 时间线

### 多租户平台化：身份 / 工作空间 / 计费 / 审计

- **身份**：`POST /api/v1/auth/register`、`POST /api/v1/auth/login`、
  `GET /api/v1/auth/me`；密码使用标准库 `hashlib.scrypt` 加盐哈希（
  `hmac.compare_digest` 抗计时攻击），登录返回绑定的 API Key；存储由
  `AUTH_STORAGE=memory|postgres` 控制
- **工作空间**：Workspace CRUD 与成员管理（owner/member 角色），API Key
  绑定 workspace；跨 workspace 访问统一 404/400（IDOR 保守策略）
- **配额**：日/月 Token 配额（key 或 workspace 共享维度），预占 + 续租 +
  结算生命周期，并发安全（PostgreSQL advisory lock），断连释放
- **Billing**：内置 free/pro/enterprise 计划种子，订阅继承链
  （workspace 显式覆盖 > 计划限额 > 全局默认）；无订阅 = legacy 模式，
  行为与现状完全一致，不隐式收紧
- **Entitlement**：feature 能力（reranker/benchmark）与资源上限
  （agent/document/member）职责分离，超限返回明确 422
- **Audit 审计**：`AuditService` 记录关键管理操作，`GET /admin/audit-events`
  支持 workspace/action 过滤与分页
- **Usage 仪表盘**：按日趋势 + 按模型/按 Key 排行（`GET
  /api/v1/usage/dashboard`），CSV/JSON 导出（`GET /api/v1/usage/export`）
- **Agent Run 回放**：`GET /api/v1/runs` 列表 + `GET /api/v1/runs/{run_id}`
  详情，按租户隔离，沿用安全投影；前端 Run 历史页展示步骤时间线

### 安全边界

- API Key 与 Admin Key 以 SHA-256 哈希存储，密钥字段均为 `SecretStr`，
  仅通过 `.env` 加载，绝不进入 Git
- 滑动窗口限流（Protocol 抽象，预留 Redis 切换）、Bearer 鉴权、租户隔离
  固化在 repository 查询条件中，避免 Service 层遗漏导致跨租户读取
- Agent 公开契约只暴露有界安全摘要；RAG 内容作为不可信参考材料渲染，
  不执行其中的指令或 HTML
- 文档上传有大小/页数/字符数上限校验；`malicious` 注入文档拒绝入库

### 可观测性

- 结构化 JSON 日志（dictConfig）、完整 UUID4 Request ID（支持
  `X-Request-ID`）、请求方法/路径/状态/耗时
- 可选 OpenTelemetry：HTTP / LLM / Tool / RAG / Agent Run 边界 span +
  四类指标（http、llm、tool、rag）；`ParentBased(TraceIdRatioBased)` 采样，
  SSE 流式响应 span 覆盖完整响应体生命周期
- span 属性与指标标签不写入原始 Prompt、API Key、完整文档或堆栈；
  `api_key_hash` 只暴露前 8 个字符

### 评估与评测

- **Evaluation Foundation**：离线、确定性的 golden data contract 与顺序执行
  runner（JSONL 用例，可注入的 `run_case`，不调用真实 LLM）；汇总任务成功率、
  tool selection accuracy、平均步骤、p95 延迟和 Token 用量
- **RAG 评估**：`context_recall_at_k` / `document_recall_at_k` /
  `chunk_recall_at_k`、检索成功率、p95 延迟；`evaluate_rag.py --compare`
  对 vector 与 hybrid 做相对门禁（CI `rag-golden` job 执行，防平凡真）
- **RAG MRR@k**：`context/document/content MRR@k`（TREC first-hit 语义，
  1/首个命中位置，未命中为 0）；`context_mrr_at_k` 落
  `rag_evaluation_runs` 表持久化
- **CI 回归 + 报表持久化**：`rag_evaluation_runs` 表持久化每次评估结果，
  CI 断言检索成功率/召回阈值；默认不引入 RAGAS（确定性优先，LLM judge
  留作可选扩展）
- **Agent Benchmark**：通过真实 `AgentService` 逐任务执行 golden 任务集，
  四项指标落 `agent_benchmark_runs` 表，前端 Agent Studio 可运行与查看历史

## 前端平台

前端位于 `frontend/`，基于 Vite、React 19 和 TypeScript，生产构建由 nginx
托管（同源代理 API，无 CORS 问题）。导航包含：

- **平台概览**：Provider / Agent Runtime / RAG 真实状态
- **对话工作台**：普通 Chat SSE（不执行工具调用）
- **PDF 工作流**：上传 PDF → 人工审批 → 报告生成；历史任务查看、显式反馈、
  回退到当前任务
- **知识库**：文档上传（6 种格式）、任务状态、文档列表/删除/预览、
  RAG readiness 状态机
- **Prompt Studio**：模板版本历史、保存即新版本、设为当前版本/回滚
- **Agent Studio**：Agent 定义（模型/Prompt 版本/工具勾选/步数/温度）、
  Benchmark 运行与历史
- **Tool Center**：workspace 级工具启用开关 + JSON Schema 展示
- **用量仪表盘**：按日 Token 趋势 + 按模型/按 Key 排行，CSV/JSON 导出
- **Billing / 计划**：当前计划、月度用量与资源计数
- **Run 历史**：Agent Run 列表与步骤时间线回放（沿用后端安全投影）
- **模型目录**、**管理后台**（API Key、用量、配额、Audit）、**成员管理**

Agent Console 关键设计：

- 领域适配层稳定排序并去重步骤/事件，组件不直接依赖后端原始 JSON
- 仅展示后端公开的安全字段；缺失字段显示“后端未提供”，不补造时间、来源、
  Token 分项或回答
- 前端 Abort 只停止等待，只有收到真实 `run_cancelled` 才显示后端取消；
  网络断连与格式错误分别独立显示
- 键盘操作、`aria-expanded`/`aria-controls`、低频 live region 播报、
  320px–1440px 五档响应式；`npm run a11y:smoke` 真实浏览器 axe
  `violations=0`
- 开发期 Vite proxy：`AI_PLATFORM_DEV_API_BASE_URL` +
  `AI_PLATFORM_DEV_API_KEY` 只由 Node proxy 注入，不进入浏览器 bundle；
  生产入口通过 `window.__AI_PLATFORM_RUNTIME_CONFIG__` 注入

前端验证命令：

```bash
cd frontend
npm install
npm run format:check
npm run lint
npm run typecheck
npm run test
npm run build
```

## 快速开始

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

启动前端开发服务器（可选，本地 Ollama 会话）：

```bash
cd frontend
AI_PLATFORM_DEV_API_BASE_URL=http://127.0.0.1:8000 \
AI_PLATFORM_DEV_API_KEY=sk-your-dev-key \
npm run dev
```

## Docker 演示

```bash
# One-command demo: builds frontend (nginx) + backend + Ollama + Jaeger + Postgres
export INITIAL_API_KEY=sk-your-initial-key
export ADMIN_API_KEYS=sk-your-admin-key
./scripts/demo.sh
```

Compose stack（5 服务）：**frontend**（nginx 静态 + `/api` proxy，`:5173`）、
**app**（FastAPI，`:8000`）、**ollama**（entrypoint 预拉取
`OLLAMA_PRELOAD_MODELS`，默认 `qwen3:4b nomic-embed-text`，`:11434`）、
**jaeger**（OTLP 接收 + UI，`:16686`/`:4318`）、**postgres**（pgvector，
`:5432`）。前端通过 nginx 同源访问 API（无 CORS 问题）；
`TELEMETRY_ENABLED=true` 默认开启 trace 到 Jaeger。`INITIAL_API_KEY` /
`ADMIN_API_KEYS` 必填（compose 拒绝启动），Docker 模式自动使用 PostgreSQL
认证与持久化。

## 质量门禁与 CI

```bash
ruff format --check .
ruff check .
mypy app tests
pytest
# Includes PostgreSQL integration tests (Docker required)
INTEGRATION_TEST=1 pytest
```

GitHub Actions（`.github/workflows/ci.yml`）4 个 job：

| Job | 内容 |
| --- | --- |
| `ci` | Python 3.14：ruff format/check + mypy + pytest（`INTEGRATION_TEST=1`） |
| `compatibility-312` | Python 3.12 兼容性：ruff + pytest |
| `rag-golden` | pgvector 服务 + mock embedder 跑 hybrid `--compare` 门禁（防平凡真） |
| `e2e` | Playwright 端到端（注册 → Chat/Agent/RAG 流程，依赖 `ci`） |

## API 一览

### 平台与健康

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/v1/health` | Liveness probe |
| GET | `/api/v1/ready` | Readiness probe（Provider + PostgreSQL 多资源检查） |
| GET | `/api/v1/health/mcp` | MCP lifecycle/readiness status（启用时） |
| GET | `/api/v1/models` | 列出可用 LLM 模型 |

### Chat / Agent

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/v1/chat/completions` | OpenAI-compatible chat completions（支持 SSE 流式） |
| POST | `/api/v1/chat` | 原生 chat，按模型 Provider 路由 |
| POST | `/api/v1/chat/rag` | RAG 增强 chat（需 `RAG_ENABLED=true`） |
| POST | `/api/v1/agent/runs` | 有界 Agent Run（同步 JSON） |
| POST | `/api/v1/agent/runs/stream` | Agent SSE 流（生命周期事件 + 回答增量） |
| POST | `/api/v1/multi-agent/runs` | Supervisor 拆分 + Orchestrator 编排的同步多 Agent Run |
| GET | `/api/v1/runs` | 租户 Agent Run 列表（可选 `agent_id` 过滤） |
| GET | `/api/v1/runs/{run_id}` | Run 安全回放详情（跨租户 404） |

### 会话记忆

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/v1/conversations` | 租户会话线程列表（按最近更新排序） |
| GET | `/api/v1/conversations/{thread_id}/messages` | 线程历史（安全字段） |

### 身份 / 工作空间

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/api/v1/auth/register` | 注册（创建 workspace + 绑定 API Key） |
| POST | `/api/v1/auth/login` | 登录（返回绑定 API Key） |
| GET | `/api/v1/auth/me` | 当前身份与工作空间列表 |
| POST | `/api/v1/workspaces` | 创建 workspace |
| GET | `/api/v1/workspaces` | 列出工作空间 |
| POST | `/api/v1/workspaces/{workspace_id}/members` | 添加成员 |
| GET | `/api/v1/workspaces/{workspace_id}/members` | 列出成员 |
| PUT | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 更新成员角色 |
| DELETE | `/api/v1/workspaces/{workspace_id}/members/{user_id}` | 移除成员 |

### Agent 配置 / Prompt / 工具 / Benchmark

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/v1/prompts` | Prompt 模板列表（含 active 版本） |
| GET | `/api/v1/prompts/{name}/versions` | 模板版本历史 |
| POST | `/api/v1/prompts/{name}/versions` | 创建新版本 |
| POST | `/api/v1/prompts/{name}/activate` | 激活版本（回滚 = 激活旧版本） |
| GET | `/api/v1/agents` | Agent 列表 |
| POST | `/api/v1/agents` | 创建 Agent |
| GET | `/api/v1/agents/{agent_id}` | Agent 详情 |
| PUT | `/api/v1/agents/{agent_id}` | 更新 Agent |
| DELETE | `/api/v1/agents/{agent_id}` | 删除 Agent（204） |
| GET | `/api/v1/tools` | 工具列表（含 workspace 生效状态） |
| PUT | `/api/v1/tools/{tool_name}` | 切换工具启用状态 |
| POST | `/api/v1/benchmarks/run` | 运行 golden 任务集（真实执行） |
| GET | `/api/v1/benchmarks/runs` | Benchmark 运行历史（可选 `agent_id`） |

### RAG / 工作流

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/api/v1/rag/documents` | 上传文档（202，异步任务；6 种格式） |
| GET | `/api/v1/rag/tasks/{task_id}` | 摄入任务状态 |
| GET | `/api/v1/rag/documents` | 文档元数据列表（不含原文/向量） |
| GET | `/api/v1/rag/documents/{document_id}/preview` | 文档预览 |
| DELETE | `/api/v1/rag/documents/{document_id}` | 删除文档 |
| POST | `/api/v1/workflows/pdf-report` | 创建 PDF 报告工作流（同步到首个 interrupt） |
| GET | `/api/v1/workflows` | 租户工作流历史（newest first） |
| GET | `/api/v1/workflows/{thread_id}` | 工作流状态 |
| POST | `/api/v1/workflows/{thread_id}/approve` | 批准并生成报告 |
| POST | `/api/v1/workflows/{thread_id}/reject` | 带反馈拒绝并重新分析 |
| POST | `/api/v1/workflow-builder/workflows` | 创建 Workflow Builder 草稿 |
| GET | `/api/v1/workflow-builder/workflows` | Workflow Builder 列表 |
| GET | `/api/v1/workflow-builder/workflows/{workflow_id}` | Workflow Builder 详情 |
| PUT | `/api/v1/workflow-builder/workflows/{workflow_id}` | 更新 Workflow Builder 草稿 |
| POST | `/api/v1/workflow-builder/workflows/{workflow_id}/publish` | 发布 Workflow Builder 版本 |
| POST | `/api/v1/workflow-builder/workflows/{workflow_id}/unpublish` | 取消发布 |
| POST | `/api/v1/workflow-builder/workflows/{workflow_id}/runs` | 试运行/运行工作流 |
| GET | `/api/v1/workflow-builder/workflows/{workflow_id}/runs` | Workflow Builder 运行历史 |
| GET | `/api/v1/workflow-builder/workflows/runs/{run_id}` | Workflow Builder 运行详情 |
| DELETE | `/api/v1/workflow-builder/workflows/{workflow_id}` | 删除草稿 |

### 长期记忆

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/v1/memory` | 检索/列表长期记忆（`q` 可选） |
| POST | `/api/v1/memory` | 显式保存长期记忆 |
| GET | `/api/v1/memory/{memory_id}` | 长期记忆详情 |
| PATCH | `/api/v1/memory/{memory_id}` | 更新长期记忆 |
| DELETE | `/api/v1/memory/{memory_id}` | 删除长期记忆（204） |

### Usage / Billing

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET | `/api/v1/usage` | Token 用量统计 |
| GET | `/api/v1/usage/dashboard` | 按日趋势 + 模型/Key 排行（`days=1..90`） |
| GET | `/api/v1/usage/export` | 用量导出（`format` 支持 csv/json，默认 csv） |
| GET | `/api/v1/billing` | 当前计划 + 月度用量 + 资源计数（legacy: plan=null） |

### Admin（管理员 API Key）

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST | `/admin/api-keys` | 创建 API Key |
| GET | `/admin/api-keys` | 列出 API Key |
| DELETE | `/admin/api-keys/{key_hash_prefix}` | 按 hash 前缀吊销 |
| GET | `/admin/usage/daily` | 按 Key 日用量 |
| GET | `/admin/usage/monthly` | 按 Key 月用量 |
| GET | `/admin/agent-runs` | Agent Run 审计列表 |
| GET | `/admin/agent-runs/{run_id}` | Agent Run 审计详情 |
| GET | `/admin/workspaces/{workspace_id}/quota` | 读取 workspace 配额覆盖 |
| PUT | `/admin/workspaces/{workspace_id}/quota` | 写入/清除配额覆盖 |
| GET | `/admin/audit-events` | Audit 日志（workspace/action 过滤，limit 1-200） |
| POST | `/admin/workspaces/{workspace_id}/subscription` | 分配计划订阅 |
| GET | `/admin/subscriptions` | 订阅列表 |
| GET | `/admin/plans` | 计划列表 |

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

## 关键契约与设计文档

### Agent SSE 事件契约

`POST /api/v1/agent/runs/stream` 按 `run_id`/`sequence` 发布真实事件：
`run_started`、`step_planned`（`MODEL_DECISION` 安全投影）、
`tool_started`/`tool_completed`、`rag_started`、`answer_delta`、终止事件
（`run_completed`/`run_stopped`/`run_cancelled`/`run_timed_out`/
`run_failed`）与可选的 `cumulative_token_usage`。

- 事件不公开原始 Tool payload、Prompt、Provider 响应或模型内部推理
- Tool 事件只公开有限的 `argument_count`、`input_summary`、
  `output_summary` 和 `result_chars`；calculator 的 expression/result 经过
  长度限制与敏感信息清洗
- 空流不生成补充文本；Provider 错误、超时和取消不被改写为成功
- 启动阶段 `stream_error` 可缺少 `run_id`/`sequence`，只表示流启动或连接
  边界错误，不代表 Run 终态

> 完整字段边界见
> [Agent SSE 事件契约](docs/superpowers/specs/2026-08-05-agent-sse-event-contract.md)
> 与
> [阶段 6 开发记录](docs/roadmap/2026-08-05-agent-sse-stage-6-record.md)。

### Agent Run RAG 公开契约

同步 Agent Run 在 `steps[].tool_calls[].rag` 下按 Tool Call 公开受限 RAG
来源摘要：

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

- `references` 只投影真实 `RAGReference` 的稳定标识、分块索引、片段
  （最多 1200 字符）和有限 distance；拒绝畸形标识、非法索引、布尔/非有限/
  越界 distance，忽略不安全来源字段
- `status` 包括 `success_with_sources`、`no_relevant_sources`、
  `knowledge_base_empty`、`rag_unavailable`、`embedding_failed`、
  `output_unavailable` 和 `failed`
- RAG 来源是**不可信参考材料**，不等同于回答中的精确引用；契约不返回查询
  文本、Tool 原始输入/输出、Prompt、模型推理、Provider 响应、堆栈、密钥、
  内部路径、文档名称或凭空生成的 rank/citation

> 详细字段边界见
> [Agent Run RAG public contract design](docs/superpowers/specs/2026-08-05-agent-rag-public-contract.md)。

### 服务端会话记忆

原生 Chat、Agent Run（同步与 SSE）和 OpenAI-compatible 端点均可传可选
`thread_id`；未传时自动创建线程，传入时按 API Key 校验归属并加载服务端历史。
服务端历史排在客户端 `history` 之前，运行结束后持久化本轮 user 与
assistant 消息。

- 配置：`CONVERSATION_STORAGE=memory|postgres`，默认 `memory`（仅单进程
  本地开发；多 worker/跨重启请用 `postgres`）
- 所有查询按 `owner_key_hash` 隔离，跨租户线程统一返回 404
  `CONVERSATION_NOT_FOUND`
- 前端刷新时从 `sessionStorage` 恢复 `thread_id` 并回填历史；404 表示线程
  失效，下一次提问自动新建线程

### 长期记忆

- `POST /api/v1/memory` 显式保存、`GET /api/v1/memory` 检索/列表、
  `GET/PATCH/DELETE /api/v1/memory/{memory_id}` 查看/更新/删除
- 记忆类型：`fact`（事实）、`preference`（偏好）、`instruction`（指令）；
  保留来源、置信度、最后使用时间与 JSON metadata
- 隔离键为 `sha256(workspace_id:user_id)`（工作空间用户）或 legacy API Key
  hash；跨用户/跨 Key/跨工作空间统一 404，Repository 层强制过滤
- Agent Service 在上下文准备阶段检索最多 `MEMORY_CONTEXT_ITEMS` 条记忆并
  注入 system prompt；默认不自动保存模型猜测，所有长期记忆来自显式写入口
- 配置：`MEMORY_STORAGE=memory|postgres`、`MEMORY_CONTEXT_ITEMS=5`、
  `MEMORY_CONTEXT_MAX_CHARS=3000`；前端左侧导航 `长期记忆` 提供管理入口

### Agent 定义与 Prompt Registry

- `Agent = Model + Prompt(版本) + Tools(白名单)` 落库；`POST
  /api/v1/agent/runs` 传 `{agent_id}` 时从定义解析，显式请求字段
  （Pydantic `model_fields_set`）覆盖定义
- Prompt 模板按 `(workspace_id, name)` 隔离，每名至多一个 active 版本，
  激活旧版本即回滚；`prompt_ref` 支持 `name@version` 版本钉扎——钉扎的
  agent 不受后续激活影响
- 启动时把内置常量与工具 schema 写入 `prompt_templates`/`tools` 表，
  seed 与运行时注册表零漂移；MCP 工具同样注册进定义校验注册表

### Agent Token Budget 语义

前端默认显式发送 `token_budget=8192`、`max_steps=4`、`timeout_seconds=60`，
后端 `AgentRunRequest` 使用相同默认值。服务端边界：
`token_budget <= 16384`、`max_steps <= 20`、`timeout_seconds <= 120`。
Runtime 把每轮模型调用的 `prompt_tokens + completion_tokens` 累加到
`state.token_usage`（多轮会重复发送完整 transcript，RAG 结果回填后下一轮
prompt 变长并再次计入）；真正超限仍返回 `stopped / token_budget_exceeded`
与真实 usage，不伪造回答或终态。

## 配置

复制 `.env.example` 为 `.env` 后按需调整：

```
APP_NAME=AI Platform Mini
DEBUG=false
LOG_LEVEL=INFO
LOG_FORMAT=json
# LOG_FILE=/var/log/ai-platform/app.log  (optional rotating file output)

LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=120

# OpenAI Provider (non-default `gpt-*` models route here)
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_DEFAULT_MODEL=gpt-4.1-mini
OPENAI_TIMEOUT_SECONDS=60

# API Key 鉴权与用户存储（memory 单进程开发；postgres 生产持久化）
API_KEYS=sk-test-key-1:development,sk-admin-key-1:admin
ADMIN_API_KEYS=sk-admin-key-1
AUTH_ENABLED=true
AUTH_STORAGE=memory

# 会话 / 工作流存储
CONVERSATION_STORAGE=memory
WORKFLOW_STORAGE=memory

# Rate limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60

# Token quota (0 = disabled)
QUOTA_DAILY_TOKENS=0
QUOTA_MONTHLY_TOKENS=0
# key = per-key limits (default); workspace = workspace-shared limits
QUOTA_SCOPE=key
QUOTA_RESERVATION_TTL_SECONDS=600
QUOTA_RESERVATION_RENEWAL_SECONDS=60

# Bootstrap (Docker/Postgres only)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aiplatform
INITIAL_API_KEY=

# RAG (Retrieval-Augmented Generation)
RAG_ENABLED=false
RAG_EMBEDDING_MODEL=nomic-embed-text
RAG_EMBEDDING_DIMENSIONS=768
RAG_MAX_UPLOAD_BYTES=10000000
RAG_MAX_PDF_PAGES=100
RAG_MAX_DOCUMENT_CHARACTERS=1000000
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=5
RAG_MAX_CONTEXT_CHARS=10000
RAG_MAX_DISTANCE=0.35
RAG_EMBEDDING_TIMEOUT_SECONDS=60
# Hybrid search: vector-only by default (legacy byte-identical behavior)
RAG_SEARCH_MODE=vector
RAG_RRF_K=60
# Prompt-injection safety: strict hides suspicious docs; flag marks them; off disables
RAG_SAFETY_MODE=strict
# Reranker: set RERANKER_API_KEY in .env (never committed) to enable Jina reranking
RERANKER_API_KEY=
RERANKER_MODEL=jina-reranker-v2-base-multilingual
RERANKER_TIMEOUT_SECONDS=10

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

> 注：用户注册/登录的存储由 `AUTH_STORAGE` 控制（与 API Key 同一开关）；
> 项目**不引入 JWT/bcrypt**——密码使用标准库 scrypt 加盐哈希，登录返回绑定
> 的 API Key。

### 关键配置说明

- `API_KEYS` 格式：`sk-xxx:name,sk-yyy:name2`（逗号分隔，name 可选）
- `ADMIN_API_KEYS` 必须也出现在 `API_KEYS`（或经 bootstrap 注册）
- `INITIAL_API_KEY` 启动时自动注册（幂等，`ON CONFLICT DO NOTHING`）
- `QUOTA_DAILY_TOKENS`/`QUOTA_MONTHLY_TOKENS`：`0` 禁用，必须 ≥ 0
- 配额使用 reserve/settle 模式：调用前预占、实际用量持久化后结算；
  `ReservationLifecycle` 对非流式与流式请求续租，续租失败或客户端断连时释放
- Billing：只有 `ACTIVE`/`TRIAL` 订阅参与限额解析（`EXPIRED`/`CANCELLED`
  回退到全局设置）；无订阅的 workspace 是 legacy 模式、完全开放

### RAG 配置说明

- `RAG_ENABLED=true` 需要 PostgreSQL + pgvector 扩展 + 可访问的 Ollama
  Embedding
- `RAG_EMBEDDING_DIMENSIONS` 当前锁定 `768`（MVP 固定 schema，变更需要
  数据库迁移）
- `RAG_MAX_DISTANCE` 使用余弦距离（0 = 完全相同，2 = 完全相反），超过阈值
  的结果被排除
- `RAG_TOP_K` 控制距离过滤前的候选数（≤ 50）；`RAG_MAX_CONTEXT_CHARS` 限制
  注入上下文总长度（≤ 100,000）
- 离线摄入：`python scripts/ingest.py <path-to-txt-file>`（TXT；需
  `RAG_ENABLED=true` + Ollama）；上传 API 与前端支持
  `.pdf/.txt/.md/.markdown/.docx/.xlsx/.html/.htm`
- 空知识库 → `KnowledgeBaseEmptyError`（404）；检索结果全部超过
  `RAG_MAX_DISTANCE` → `NoRelevantContextError`（404）

### MCP 配置说明

- `MCP_SERVERS_JSON` 必须是 JSON 数组，每项需要 `name` 与非空 `command`
  数组；可配置 `allowed_tools`、`max_risk_level`、超时和字符串环境变量
- MCP 工具启动时发现、关闭时清理；不可用 Server 被隔离，不阻断其他 Server
- 工具需要 `mcp:server:<server_name>` 权限，只授予成功发现的显式配置 Server，
  永不来自模型输出或用户输入
- 真实 stdio 工具必须提供显式 read-only/destructive 注解；未知风险元数据
  fail-closed 拒绝，重名工具隔离受影响 Server

### OpenTelemetry 说明

- `TELEMETRY_ENABLED=true` 启用 trace + metrics；关闭时全部为 no-op，
  不依赖外部 Collector
- `TELEMETRY_SAMPLING_RATIO`（0.0–1.0，默认 1.0）使用
  `ParentBased(TraceIdRatioBased)`，保证同一请求要么完整出现、要么完全不出现
- 指标：`http.requests`/`http.duration_ms`、`llm.calls`/`llm.duration_ms`、
  `llm.prompt_tokens`/`llm.completion_tokens`、`tool.executions`/
  `tool.duration_ms`、`rag.retrievals`/`rag.duration_ms`
- request_id 关联：HTTP 根 span 携带 `app.request_id`；流式响应体迭代期间
  重新绑定同一 request_id（async generator 不继承创建时的 contextvar）
- 本地查看：`TELEMETRY_ENABLED=true TELEMETRY_EXPORTER=console
  .venv/bin/uvicorn app.main:app --reload`；Docker 栈中 Jaeger 直接接收
  OTLP

## 目录结构

```
app/
├── adapters/        # 无状态协议转换（OpenAI-compatible 请求/响应映射）
├── agents/          # 框架无关的 Agent Runtime（state、protocols、loop、events）
├── agent_config/    # Agent 定义落库（model + prompt_ref + 工具白名单）
├── api/             # Router 层（chat/agent/openai/admin/health/models/...）
├── audit/           # Audit 审计服务
├── auth/            # 鉴权（API Key / 用户 / workspace / 租户隔离）
├── billing/         # 计费计划 / 订阅 / entitlement
├── core/            # 基础设施（settings/logging/exceptions/container/context）
├── conversations/   # 服务端会话记忆（memory/postgres）
├── db/              # 数据库（模型/session/init）
├── evals/           # Golden + RAG 评估（runner/JSONL/报告/基准）
├── exceptions/      # Provider 特定 + 领域异常
├── memory/          # 长期记忆（owner 隔离/repository/service/检索）
├── mcp/             # MCP stdio 客户端 / 工具适配 / 管理器
├── middleware/      # Context 中间件（request_id）
├── multi_agent/     # 多 Agent 编排（Supervisor / Orchestrator / shared context）
├── observability/   # OpenTelemetry（tracing + metrics + middleware）
├── prompts/         # Prompt Registry（版本/激活/回滚/seeds）
├── providers/       # LLM Provider 层（Protocol + Router + 实现）
├── quota/           # Token 配额（reserve/settle、repository、service）
├── rag/             # RAG（embedder/vector store/chunker/parsers/service/safety）
├── ratelimit/       # 限流（Protocol + memory 实现 + dependencies）
├── runs/            # Agent Run 记录与安全回放投影
├── schemas/         # Pydantic 请求/响应模型
├── services/        # 业务逻辑
├── tools/           # Tool Protocol/Registry/Executor + 内置工具
├── usage/           # Token 用量（repository/service/collector）
├── workflow_builder/ # 通用 Workflow Builder API/双存储/真实节点执行器
├── workflows/       # 固定 PDF 工作流；engine/ 为通用编排引擎
└── main.py

frontend/
├── src/             # React 19 + TS（platform/admin/auth/agent/chat/workflow/
│                    #   workflow-builder + memory 长期记忆管理）
├── e2e/             # Playwright 端到端测试
└── scripts/         # a11y smoke（真实 Chromium + axe）

scripts/             # ingest / evaluate_rag / demo / 回填脚本
docs/                # 路线图、设计文档、开发日志
tests/               # 后端测试（87 个文件、1009 个通过测试）
```

## 设计原则

- **Pydantic schemas**（`app/schemas/`）— 类型化请求/响应，无原始 dict
- **Service 层** — Router 不直接调用 Provider；换 Provider 只改配置/Factory
- **Agent 边界** — `AgentRuntime` 只依赖类型化领域模型与 Protocol；
  `AgentService` 负责 Chat/Quota/Usage 集成
- **Adapter 层** — 无状态协议转换，纯职责提取不改变既有行为
- **Settings**（`app/core/settings.py`）— pydantic-settings 读取 `.env`，
  不硬编码配置；密钥字段均为 `SecretStr`
- **日志**（`app/core/logging.py`）— dictConfig JSON 日志 + request_id +
  方法/路径/状态/耗时
- **异常**（`app/core/exceptions.py`）— 全局错误处理，Router 内无 try/except
- **Lifespan**（`app/main.py`）— 启动初始化与关闭回滚；外部取消信号不被吞掉
- **租户隔离** — 固化在 repository 查询条件中，Service 层遗漏也会被约束
- **安全投影** — 公开契约只暴露有界安全字段，缺失显示“后端未提供”，
  不补造值

## 项目规则

- Python 版本：支持 `3.12`–`3.14`，默认 `3.14`（`.python-version` 与
  `pyproject.toml` 对齐，CI 验证所有支持版本）
- 风格：PEP 8，提交前通过 `ruff format --check .` 与 `ruff check .`
- 类型注解：新增/修改的生产函数、方法、类必须显式类型标注；
  提交前通过 `mypy app tests`
- Sprint 完成定义：`ruff format --check .`、`ruff check .`、
  `mypy app tests`、`pytest` 全绿，应用可通过
  `uvicorn app.main:app --reload` 启动
- Code Review：每个功能写完后展示给用户评审，修复评审意见后才能进入
  下一个任务
- Git：conventional commit、小而有意义的提交、push 到 GitHub 保留完整历史

## 开发历程

Sprint 1–M2 的逐条交付、学习总结与 Code Review 沉淀见
[开发日志（Sprint Log）](docs/DEVELOPMENT_LOG.md)。演进主线：

1. **Sprint 1–5**：FastAPI + Ollama 聊天端点 → 鉴权/限流/配额/Usage →
   可观测性与技术债清理
2. **Sprint 7**：Multi-LLM Gateway（OpenAIProvider、ProviderRouter、
   OpenAIAdapter）
3. **Sprint 8+**：RAG MVP → Agent Runtime → Tool Calling → MCP →
   Agent 配置层 → Agent SSE + 前端 Console
4. **Hardening B**：Prompt Injection 防护、Document Pipeline 6 格式、
   Docker 演示栈、Benchmark 前端
5. **Sprint D/E**：Run 回放与用量仪表盘、共享配额、Billing/计划/Audit、
   工作流历史、用量导出
6. **Sprint E2（已完成）**：Workflow Builder——P1 通用编排引擎、P2
   `/api/v1/workflow-builder` API + InMemory/Postgres 双存储 + 真实节点执行器、
   P3 React Flow 可视化编排全部完成（画布、节点配置、保存/发布/试运行、
   `node_results` 时间线）
7. **Sprint M1（已完成）**：长期记忆——内存/Postgres 双存储、显式 CRUD、
   用户/工作空间隔离、Agent 上下文检索注入、前端管理入口
8. **Sprint M2（已完成）**：多 Agent 编排——Supervisor JSON 任务拆分、
   Orchestrator 依赖/并发/失败策略/预算执行、`/api/v1/multi-agent/runs` 公开端点
