# 多租户 AI Agent Platform MVP — 平台化增强 Sprint 计划（2026-08-07 第二轮修订）

> 项目定位（第二轮评审结论）：
> **自研轻量级 AI Agent Platform（类似 Dify/Coze 核心架构），而非 RAG + Agent Demo。**
> 当前不缺功能、不缺架构，缺的是把既有抽象层产品化，补齐闭环：
> **Identity（用户）→ Configuration（配置）→ Execution（运行）→ Evaluation（评估）**。
> 每个 Sprint 结束必须通过质量门：`ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest`、Uvicorn 启动检查，并更新 README + 学习总结 + Git 提交。

## 0. 现状核实（两轮评审对照）

| 能力 | 现状 | 差距 |
| --- | --- | --- |
| 模型层 | ProviderRouter + OpenAI Compatible + Ollama + Mock | 无（架构已解决，更多模型只是数据） |
| Agent 层 | AgentRuntime + Tool Calling + MCP + SSE + Run Recorder | 无 Agent 定义实体 |
| Workflow 层 | LangGraph + Interrupt + Approval + Postgres checkpointer | 无 |
| Knowledge 层 | pgvector + RAG Pipeline + Eval + 按 Key 租户隔离 | 仅 PDF、无 keyword 检索 |
| Platform 层 | API Key 租户 + PromptStudio(localStorage) + Trace + Admin | 无用户/Workspace、Prompt 非后端化 |

## 1. 第二轮评审的关键调整（相对第一版计划）

1. **API Key 租户不废弃，升级为层级**：

   ```
   User → Workspace → API Key → Resources
   ```

   迁移后 `owner_key_hash` 语义：
   - legacy Key（未绑定用户）：租户 = `sha256(api_key)`（与今天逐字节一致）
   - 绑定用户的 Key：租户 = `sha256(workspace_id)`（同 workspace 多用户/多 Key 共享资源）
   - 审计口径 `api_key_hash`（quota/usage/run records）始终记录**出示的 Key**，不随租户切换变化
2. **Agent 配置化 + Tool Center 与 Prompt Registry 合并为一个 Sprint**：
   `Agent = Model + Prompt(版本) + Tools(白名单)`，三者一个闭环，拆开做反而两头不讨好。
3. **Agent Replay UI 提前**：后端数据已齐（run records + trace events），投入小、面试展示价值极高。
4. **新增 Agent Benchmark**：项目核心已不是纯 RAG，Agent 也应评估（Tool Call Accuracy / Task Completion Rate / Average Steps / Latency）。
5. **明确不做**：Redis（memory/postgres 已覆盖）、Milvus（pgvector 足够）、更多 Provider（纯数据）、Alembic（沿用 migrate helper）。

---

## 1.5 设计冻结（2026-08-07 已批准，B/C/D 不得推翻）

> 来源：Sprint A 开工前的设计评审。以下决策为不可更改项，后续 Sprint 必须遵守。

### F1. Workspace 是所有资源的一级归属

```
User
 └─ WorkspaceMember
      └─ Workspace
           ├─ Members
           ├─ API Keys
           ├─ Agents
           ├─ Prompts
           ├─ Knowledge Bases
           ├─ Workflows
           ├─ Conversations
           ├─ Runs
           └─ Usage Records
```

禁止混合归属（如 User 直属 Agent + Workspace 直属 KnowledgeBase）。所有未来资源表必须有 `workspace_id`（或通过所属链可达）。

### F2. Role 模型最小化（第一版不做 RBAC）

`OWNER > ADMIN > MEMBER > VIEWER`，权限表：

| 角色 | 创建 Agent | 管理成员 | 查看 Run |
| --- | --- | --- | --- |
| OWNER | ✓ | ✓ | ✓ |
| ADMIN | ✓ | ✓ | ✓ |
| MEMBER | ✓ | × | 仅自己的 |
| VIEWER | × | × | 只读 |

未来需要细粒度权限时再引入 Permission 表，本轮不加。

### F3. API Key 层级保留 + legacy 兼容

```
User → Workspace → API Key → Resources
```

- `api_keys` 表新增 `id`（UUID PK）、`user_id`（可空 FK）、`workspace_id`（可空 FK），保留 `key_hash` 唯一。
- `workspace_id IS NULL` = **legacy Key**：租户语义与今天完全一致（`owner_key_hash = sha256(api_key)`）。
- 绑定用户的 Key：租户 = `sha256(workspace_id)`，同 workspace 多用户/多 Key 共享资源。
- 审计口径 `api_key_hash`（quota/usage/run records）始终记录出示的 Key，不随租户切换变化。

### F4. owner_key_hash 双写过渡，不删除

资源表过渡期同时保留 `workspace_id`（可空）与 `owner_key_hash`：

- 便于数据迁移验证、回滚、审计。
- `owner_key_hash` 继续作为查询过滤条件（Sprint A 阶段），`workspace_id` 先落库验证。
- Sprint A 稳定运行、迁移验证通过后，再单独评估清理（后续 Sprint，非本轮）。

### F5. IdentityContext 认证中间层（A0 优先）

把"取 Key"升级为"取身份"，统一供 B/C/D 使用：

```python
@dataclass(frozen=True)
class IdentityContext:
    user_id: str | None
    workspace_id: str | None
    api_key_id: str | None       # api_keys.id（新增列）
    api_key_hash: str            # 出示 Key 哈希（审计口径）
    role: str | None             # workspace role；legacy Key = None

    @property
    def tenant_scope(self) -> str:
        # workspace 哈希；legacy = api_key 哈希
```

`RequestContext` 持有 `identity: IdentityContext`；API 层通过 `resolve_tenant_scope(identity)` 取租户过滤值。

### F6. Sprint A 执行顺序（A0 → A5）

```
A0 IdentityContext 抽象
 ↓
A1 User / Workspace / Member（表 + 服务 + API）
 ↓
A2 API Key 绑定 user_id + workspace_id（迁移 helper + 服务扩展）
 ↓
A3 Resource owner 迁移（API 层替换为 tenant_scope，33 处；service/存储层不动）
 ↓
A4 全量隔离测试（workspace 隔离 + legacy 兼容 + 同 workspace 共享）
 ↓
A5 前端登录/Workspace UI
```

先打 IdentityContext 底座，再改 33 处调用点；不要一开始就改 API 文件。

### F7. Sprint A 验收场景（在 F1–F6 之上）

- 场景 1 新用户完整流程：注册 → 创建 Workspace → 生成 API Key → 调用 Chat API → 查看 Conversation。
- 场景 2 隔离测试：`test_workspace_isolation.py`（A 不能访问 B，扩展自 `test_rag_tenant_isolation.py`）。
- 场景 3 Legacy Key：旧 Key 可继续调用、owner 语义不变、不影响新 workspace。

## Sprint A：用户 + Workspace 多租户（平台基础，"像产品"）

### A1. 表结构（新增，进 `_CORE_TABLES`）

```python
class UserTable(Base):
    __tablename__ = "users"
    id: Mapped[str]  # Uuid as_uuid=False, PK
    email: Mapped[str]  # String(255), unique, lower-cased
    display_name: Mapped[str]  # String(128)
    password_salt: Mapped[str]  # String(64)
    password_hash: Mapped[str]  # String(128), scrypt(hex)
    status: Mapped[str]  # "active" | "disabled"
    created_at / updated_at


class WorkspaceTable(Base):
    __tablename__ = "workspaces"
    id: Mapped[str]  # Uuid as_uuid=False, PK
    name: Mapped[str]  # String(128)
    created_by_user_id: Mapped[str]  # FK users.id
    created_at / updated_at


class WorkspaceMemberTable(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[int]  # BigInteger PK autoincrement
    workspace_id: Mapped[str]  # FK workspaces.id, ondelete=CASCADE
    user_id: Mapped[str]  # FK users.id, ondelete=CASCADE
    role: Mapped[str]  # "owner" | "admin" | "member"
    created_at
```

`api_keys` 表新增可空列（迁移 helper，不用 create_all）：

```sql
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
```

### A2. 租户解析（核心设计）

`RequestContext` 扩展（构造点全仓仅 2 处：`app/core/context.py`、`app/auth/dependencies.py`）：

```python
@dataclass(frozen=True)
class RequestContext:
    request_id: str
    api_key: str | None = None  # 出示 Key 哈希（审计语义不变）
    api_key_name: str | None = None
    user_id: str | None = None
    workspace_id: str | None = None
    tenant_scope: str | None = None  # sha256(workspace_id)，否则 = api_key
```

- 新增 `app/auth/tenant.py:resolve_tenant_scope(...)`，API 层 33 处 `owner_key_hash=api_key.key` 统一替换（详见附录 S1）。
- service 层与存储层只接收字符串，**零改动**；`validate_owner_key_hash` 校验语义不变（workspace 哈希同为 64 位 hex）。
- 密码哈希用标准库 `hashlib.scrypt`（`app/auth/password.py`，零新依赖）。

### A3. API 契约

```
POST /api/v1/auth/register  {email, display_name, password}
     -> {user, workspace:{id,name,role:"owner"}, api_key}   # 绑定用户，前端写入 sessionStorage
POST /api/v1/auth/login     {email, password}
     -> {user, workspaces:[{id,name,role}], api_key}        # 签发绑定 Key；AUTH_STORAGE!=postgres 时 503
GET  /api/v1/auth/me        -> {user, workspaces}
POST /api/v1/workspaces     {name} -> {workspace, role:"owner"}
GET  /api/v1/workspaces     -> [{id,name,role,member_count}]
POST /api/v1/workspaces/{id}/members  {email, role}         # owner/admin
GET  /api/v1/workspaces/{id}/members  -> [{user_id,email,role,created_at}]
DELETE /api/v1/workspaces/{id}/members/{user_id}
```

请求头：`Authorization: Bearer <api_key>`（不变）+ `X-Workspace-Id`（可选；缺省取用户第一个 workspace；legacy Key 忽略）。
前端复用 `frontend/src/App.tsx:530` 的 `sessionStorage['ai-platform-user-key']` 单一存储点，全站鉴权自动生效。

### A4. 验收

- [ ] 注册 → 自动建默认 workspace → 拿绑定 Key → 前端登录进入平台
- [ ] 同 workspace 两用户：A 上传 PDF，B 立即可见/可问答（共享知识库）
- [ ] 跨 workspace 严格隔离（隔离测试双跑）
- [ ] legacy Key 访问 RAG/会话/Workflow 行为与今天完全一致
- [ ] `pytest` 全绿（含现有 `test_rag_tenant_isolation.py`）

---

## Sprint B：Prompt Registry + Agent Definition + Tool Center（"像 Dify"）

**核心等式：`Agent = Model + Prompt(版本) + Tools(白名单)`**

### B1. 表结构（新增）

```python
class PromptTemplateTable(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "name", "version", name="uq_prompt_ws_name_version"
        ),
    )
    id: Mapped[int]  # BigInteger PK
    workspace_id: Mapped[str | None]  # NULL = 内置全局模板
    name: Mapped[str]  # "rag_answer" | "agent_planner" | "chat_system"
    version: Mapped[int]  # 自增
    content: Mapped[str]  # Text，{variable} 占位
    variables: Mapped[list]  # JSON [{name, default, description}]
    is_active: Mapped[
        bool
    ]  # 每 (workspace_id, name) 至多一个 true（service 保证 + 部分唯一索引兜底）
    created_by: Mapped[str | None]  # user_id
    created_at / updated_at


class AgentTable(Base):
    __tablename__ = "agents"
    id: Mapped[str]  # Uuid PK
    workspace_id: Mapped[str]  # FK workspaces.id, ondelete=CASCADE
    name: Mapped[str]  # String(128)
    model: Mapped[str]  # String(128)
    prompt_ref: Mapped[str]  # "rag_answer@3" 或 name（激活版本解析）
    temperature: Mapped[float]
    max_steps: Mapped[int]
    enabled: Mapped[bool]
    created_by / created_at / updated_at


class AgentToolTable(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("agent_id", "tool_name"),)
    id: Mapped[int]
    agent_id: Mapped[str]  # FK agents.id, ondelete=CASCADE
    tool_name: Mapped[str]  # FK tools.name


class ToolTable(Base):
    __tablename__ = "tools"
    name: Mapped[str]  # PK
    description: Mapped[str]
    parameters_schema: Mapped[dict]  # JSON Schema
    enabled_by_default: Mapped[bool]
    owner: Mapped[str]  # "builtin" | "mcp" | "custom"
    created_at
```

### B2. 服务层

```
app/prompts/       PromptRegistryService：render(name, variables, fallback) / create_version / activate(回滚=激活上一版)
app/agent_config/  AgentDefinitionService：CRUD + 工具白名单校验（工具必须在 ToolRegistry/MCP 可用集内）
app/tools/         ToolRegistry 升级：内置工具注册表（代码）为种子，DB 表控制 workspace 级启用状态
```

- Prompt 接入点：`app/services/agent_service.py` 的 `_AGENT_PROTOCOL_PROMPT`/`_RAG_PRESET_PROMPT`、`chat_service.py` system prompt 改为 `registry.render(...)`，registry 空时回退内置常量（离线可用性不变）。
- `POST /agent/run` 扩展支持 `{agent_id}`：解析 AgentTable → model/prompt/tools/max_steps，未提供时行为与今天完全一致。
- 种子：首次启动将内置常量写入 `prompt_templates`（workspace_id NULL）；tools 表种子 calculator/knowledge_search。
- `POST /api/v1/prompts/{name}/activate {version}` 即回滚；A/B 灰度（rollout_percent）预留字段，本轮只做激活切换。

### B3. 前端

- PromptStudio：去 localStorage，调 `/api/v1/prompts`；版本历史列表 + "设为当前版本"。
- 新增 Agent Studio：创建/编辑 Agent（模型下拉、Prompt 版本选择、工具勾选）。
- 新增 Tool Center：工具列表 + enabled 开关 + JSON Schema 展示（可折叠）。

### B4. Agent Benchmark（新）

- `app/evals/agent_benchmark.py`：golden 任务集（JSON，如"查询 PDF 并总结，必须调用 knowledge_search"）。
- 指标：Tool Call Accuracy / Task Completion Rate / Average Steps / Latency，按 agent_id 归档到 `agent_benchmark_runs` 表。
- `POST /api/v1/benchmarks/run {agent_id, task_set}` + `GET /api/v1/benchmarks/runs`。
- 复用现有 run recorder 数据（tool call 序列、终态、步数、耗时），不重复埋点。

### B5. 验收

- [ ] PromptStudio 保存即新版本；"设为当前版本"生效、回滚可点；workspace 间模板隔离
- [ ] 改 `rag_answer` 后同一 RAG 问题回答变化（golden set 可复现）
- [ ] registry 空/停用时 Agent 仍可运行（fallback）
- [ ] 创建 Research Agent（qwen3 + rag_answer@3 + knowledge_search/calculator）→ `POST /agent/run {agent_id}` 跑通，audit payload 记录 prompt name+version
- [ ] Benchmark 跑出四项指标并落库

---

## Sprint C：Hybrid Search + Reranker + Golden Set CI（"像企业知识库"）

### C1. 中文 keyword 方案（关键决策）

Postgres 默认 `english` parser 对中文无效；zhparser 需编译 SCWS，pgvector 镜像不带。
**方案：jieba（纯 Python）分词 → `to_tsvector('simple', ...)`**，`E10023` 等错误码保留为整 token，镜像零改动。

```sql
ALTER TABLE rag_document_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector;
CREATE INDEX IF NOT EXISTS ix_rag_chunk_search_vector
    ON rag_document_chunks USING GIN (search_vector);
```

- 新入库：chunk 后 `tokenize_keywords(chunk)` 生成 tsvector；存量数据脚本幂等回填。
- 查询：`ts_rank(search_vector, plainto_tsquery('simple', query_tokens))` + 语义距离，**RRF 融合（1/(60+rank)）不调权重**。

### C2. 代码与配置

- `app/rag/hybrid.py`：`HybridRetriever`（实现现有 VectorStore 协议不变式，`SearchResult.distance` 语义保留）。
- `app/rag/reranker.py`：`Reranker` Protocol；`NoopReranker`（默认）/ `JinaReranker`（`RERANKER_API_KEY`，无 Key 自动禁用）/ Cohere 预留。
- 配置：`RAG_SEARCH_MODE=hybrid|vector|keyword`（默认 hybrid，`vector` 时行为与今天逐字节一致）、`RAG_RRF_K=60`。

### C3. Golden Set CI 质量门

- `app/evals/rag_runner.py` 参数化 `RAG_SEARCH_MODE`；golden set 打分对比 hybrid vs vector-only。
- `.github/workflows/ci.yml` 新增 job：跑 eval，hybrid 分数低于 vector-only 基线阈值则 CI 失败（防回归）。
- README Evaluation 记录两种模式分数；若 hybrid 无提升，保留实现但默认 `vector`（不吹效果）。

### C4. 验收

- [ ] `E10023 错误码` 类查询 hybrid 命中 > vector-only（分数写入 README）
- [ ] `RAG_SEARCH_MODE=vector` 时与今天逐字节一致
- [ ] 存量回填脚本幂等可重跑；CI 含 Golden Set 回归门

---

## Sprint D：Agent Replay UI + Usage Dashboard + E2E（"面试 Demo 炸裂"）

### D1. Agent Replay 时间线

- 后端：run records + trace events 已齐；补 `GET /api/v1/runs/{run_id}` 详情契约（admin 审计已有摘要，扩展逐步事件 + prompt name/version + tool call/result 摘要，沿用现有安全投影/sanitize 逻辑，不暴露原始 payload）。
- 前端 `RunDetail.tsx`：步骤时间线（Step1 LLM decision → Step2 Tool call → Step3 RAG result → Step4 Final answer），从 Trace 面板点击 run_id 进入。

### D2. Usage Dashboard

- 数据已在 Postgres（`daily_usage` 按 key/date/model 聚合）；后端补 per-workspace 维度（Sprint A 后 `workspace_id` 可空列）。
- 前端 `UsageDashboard.tsx`：token/请求数按日趋势 + 按模型/按 Key 排行（无新依赖，轻量 SVG/CSS 图表即可）。

### D3. E2E Playwright

- `frontend/e2e/`：关键链路"登录 → 上传 PDF → 审批 Workflow → 知识库问答"（Playwright + 测试专用 Mock Provider / 本地 Ollama 可选）。
- CI 增加前端 e2e job（可标记 `needs: [ci]`，本地起 compose 或 mock）。

### D4. 验收

- [ ] 任意 run_id 可打开时间线页，步骤/工具调用/RAG 来源完整且字段安全投影
- [ ] Dashboard 展示 7 日 token 趋势 + 模型/Key 排行
- [ ] Playwright 全链路在 CI 通过

---

## Backlog（后续按需排期）

1. **Document Pipeline 抽象**：`app/rag/parsers/` Parser Factory（PDF/Markdown/TXT/DOCX/XLSX/HTML）→ `NormalizedDocument` → 复用 chunker。
2. **Prompt Injection 防护**：`app/rag/safety.py` 规则 + 可配 LLM 复核，入库前过滤，`RagDocument.safety_verdict` 落库。
3. **Docker Compose 完善**：+frontend（nginx）、+jaeger（TELEMETRY 指向）、Ollama 模型预拉取、一键演示脚本。

## 明确不做（本期）

- **Redis**：memory/postgres 双存储已覆盖当前需求；cache/queue/rate-limit 出现真实瓶颈再引入。
- **Milvus**：pgvector 足够，引入即运维成本爆炸。
- **更多 Model Provider**：ProviderRouter 已解决架构，增加模型只是配置数据。
- **Alembic**：`create_all` + `migrate_*` helper 已跑通两个版本演进；schema 变更频率上升再评估。

---

## 附录：实施规格（第二轮修订）

### S0. 改造点地图（全仓证据，第一轮调研结果保持不变）

- `owner_key_hash` 消费点 33 处全部在 API 层 6 个文件（`app/api/{rag,chat,openai,workflows,conversations,agent}.py`），service/存储层零改动。
- `RequestContext` 构造点仅 2 处；前端 key 存储点仅 1 处（`frontend/src/App.tsx:530`）。
- 审计口径 `api_key_hash` 文件（quota/usage/run records/middleware）保留不动。

### S1. Sprint A 文件级清单

新增：`app/auth/{password,tokens,tenant}.py`、`app/auth/users_repository.py`、`app/auth/workspaces_repository.py`、`app/auth/user_service.py`、`app/auth/workspace_service.py`、`app/api/auth.py`、`app/api/workspaces.py`、`app/db/user_models.py`、`tests/{test_auth_users,test_workspaces,test_tenant_scope,test_workspace_rag_shared}.py`
修改：`app/db/models.py`、`app/db/init.py`（+3 表 + migrate helper）、`app/core/context.py`、`app/auth/dependencies.py`、API 层 6 文件（33 处替换）、`frontend/src/App.tsx`、新增 `frontend/src/auth/`

### S2. Sprint B 文件级清单

新增：`app/db/{prompt_models,agent_models}.py`、`app/prompts/`（models/repository/memory_repository/postgres_repository/service/seeds）、`app/agent_config/`（同构）、`app/api/{prompts,agents,tools,benchmarks}.py`、`app/evals/agent_benchmark.py`、`app/db/benchmark_models.py`、前端 `frontend/src/platform/{AgentStudio,ToolCenter}.tsx` + `frontend/src/platform/prompt-client.ts`、对应测试
修改：`app/db/init.py`、`app/services/agent_service.py`（prompt 走 registry + `agent_id` 解析）、`app/api/agent.py`（`/run` 支持 `agent_id`）、`frontend/src/platform/PromptStudio.tsx`（去 localStorage）

### S3. Sprint C 文件级清单

新增：`app/rag/hybrid.py`、`app/rag/reranker.py`、`app/rag/tokenize.py`（jieba 封装 + 纯标准库降级路径）、`scripts/backfill_search_vectors.py`、`tests/test_hybrid_search.py`、`tests/test_tokenize.py`
修改：`app/db/rag_models.py`（search_vector + GIN index + migrate helper）、`app/rag/ingestion.py`（入库写 tsvector）、`app/rag/pg_vector_store.py`（keyword 查询 + RRF）、`app/core/settings.py`（RAG_SEARCH_MODE/RAG_RRF_K/RERANKER_*）、`.env.example`、`.github/workflows/ci.yml`（Golden Set job）、pyproject（+jieba）

### S4. Sprint D 文件级清单

新增：`app/api/runs.py`（run 详情契约）、`frontend/src/platform/{RunDetail,UsageDashboard}.tsx`、`frontend/e2e/`（Playwright 全链路）、对应测试
修改：`app/api/admin.py`（workspace 维度聚合可选）、`frontend/src/App.tsx`（路由）、`frontend/package.json`（+Playwright）、CI

### S5. 验收清单总表

| Sprint | 一句话目标 | 关键验收 |
| --- | --- | --- |
| A | 像产品 | 注册/登录/workspace 共享与隔离/legacy 兼容 |
| B | 像 Dify | Agent = Model+Prompt+Tools 可配置可运行可评估 |
| C | 像企业知识库 | hybrid 检索提升有数据支撑 + CI 回归门 |
| D | Demo 炸裂 | Replay 时间线 + 用量看板 + E2E 全链路 |
