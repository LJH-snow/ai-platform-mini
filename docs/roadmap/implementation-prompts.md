# 平台化实现提示词包（给其他编码模型使用）

> 用法：按顺序执行目标 1 → 2 → 3 → 4 → 5。每个目标交给一个模型实例，**完成并交回后**再派下一个。
> 每个模型开始前必须阅读：
> - 本文件对应目标的提示词
> - `docs/roadmap/2026-08-07-platform-hardening-plan.md` 对应 Sprint 章节 + 附录文件清单
> - 仓库根 `AGENTS.md`（项目规则）
>
> 通用规则（所有目标强制）：
> 1. 所有用户可见文案/回复用中文；代码注释用英文。
> 2. 完成定义 = 质量门全绿：`ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest`、`uvicorn app.main:app --reload` 能启动。
> 3. 完成后**不要自动进入下一个功能**，把变更代码展示给用户做 Code Review，等用户批准。
> 4. 遵守计划文档 1.5 节设计冻结 F1–F7（不可更改项）。
> 5. 禁止引入 Redis/Milvus/Alembic/新框架；禁止删除 `owner_key_hash`；禁止破坏 legacy Key 行为。
> 6. 提交信息用 conventional commit；更新 README 相关章节；写 ≤5 句学习总结。

---

## 目标 1：身份底座（Sprint A 阶段 1：A0 + A1 + A2）

**依赖**：无（仓库当前状态）。

### 提示词

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现"多租户身份底座"（Sprint A 阶段 1）。
先阅读 docs/roadmap/2026-08-07-platform-hardening-plan.md 的"1.5 设计冻结"（F1–F7）、
"Sprint A"章节、附录 S1 文件清单，以及仓库根 AGENTS.md。

严格按冻结决策执行，禁止自行更改以下设计：
1. Workspace 是所有未来资源的一级归属；Role 仅 OWNER/ADMIN/MEMBER/VIEWER 四档，不做 RBAC 表。
2. api_keys 表新增 id(UUID PK)、user_id(可空 FK)、workspace_id(可空 FK)，保留 key_hash 唯一；
   workspace_id IS NULL 表示 legacy Key（租户语义与现状完全一致）。
3. 资源表过渡期双写：保留 owner_key_hash，新增可空 workspace_id，不删除任何现有列。
4. 先做 IdentityContext 抽象，再改 API 层调用点；service 层与存储层签名不变。

按 A0 → A1 → A2 顺序实现：
- A0: app/core/context.py 的 RequestContext 增加 identity 字段；新增 app/auth/identity.py
  定义 IdentityContext(user_id, workspace_id, api_key_id, api_key_hash, role) 与
  tenant_scope 属性（workspace 存在时 sha256(workspace_id)，否则 api_key_hash）。
  密码哈希用标准库 hashlib.scrypt（app/auth/password.py），零新依赖。
- A1: app/db/user_models.py 新增 UserTable/WorkspaceTable/WorkspaceMemberTable
  （结构见计划文档 Sprint A 章节），注册进 app/db/init.py 的 _CORE_TABLES；
  app/auth/ 新增 users/workspaces 的 memory + postgres 双存储 repository 与 service；
  app/api/auth.py 提供 POST /auth/register、POST /auth/login、GET /auth/me；
  app/api/workspaces.py 提供 workspaces CRUD 与成员管理（owner/admin 才能管理成员），
  API 契约以计划文档 A3 为准。
- A2: app/db/models.py 的 APIKeyTable 增加 id/user_id/workspace_id 列，
  app/db/init.py 增加幂等 migrate helper（ALTER TABLE ... ADD COLUMN IF NOT EXISTS）；
  app/auth/service.py 的 create_key 支持绑定 user_id/workspace_id；validate 返回完整记录。
  register 流程：建用户 → 建默认 workspace → 建 owner 成员 → 签发绑定 Key（前端写入 sessionStorage）。

验收：
- 注册/登录/me/workspaces 成员管理单测全绿（tests/test_auth_users.py、tests/test_workspaces.py）。
- IdentityContext 单测覆盖 legacy（无 workspace）与 workspace 两种 tenant_scope
  （tests/test_identity.py）。
- 现有全部测试（含 test_rag_tenant_isolation.py）保持通过，legacy Key 行为不变。
- 质量门全绿：ruff format --check .、ruff check .、mypy app tests、pytest、
  uvicorn app.main:app --reload 可启动。

完成后不要继续下一目标；列出变更文件清单和关键设计说明，交给用户 Code Review。
```

---

## 目标 2：资源租户迁移 + 前端登录（Sprint A 阶段 2：A3 + A4 + A5）

**依赖**：目标 1（IdentityContext + users/workspaces + api_keys 绑定已存在）。

### 提示词

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中完成"资源租户迁移与前端登录"（Sprint A 阶段 2）。
前置：目标 1 已完成（RequestContext.identity、/auth/register、/auth/me、/workspaces、api_keys 绑定）。
先阅读 docs/roadmap/2026-08-07-platform-hardening-plan.md 的 1.5 设计冻结、
Sprint A 章节、附录 S0/S1，以及仓库根 AGENTS.md。

任务：
- A3: 在 app/auth/ 新增 tenant helper（resolve_tenant_scope(identity)），
  把 API 层 6 个文件（app/api/{rag,chat,openai,workflows,conversations,agent}.py）
  中约 33 处 owner_key_hash=api_key.key 替换为 identity.tenant_scope。
  只改 API 层；service/存储层签名不动。legacy Key 的 tenant_scope 必须等于 api_key 哈希，
  保证与现状逐字节一致。
- A4: 新增 tests/test_workspace_isolation.py（扩展自 test_rag_tenant_isolation.py）：
  两个 workspace 严格隔离 + 同 workspace 两用户共享 RAG 文档 + legacy Key 兼容三个场景。
  若存在并发风险点（如 key 解析缓存），用依赖覆盖方式隔离测试。
- A5: 前端登录/注册页（frontend/src/auth/LoginPage.tsx + RegisterPage.tsx），
  复用 frontend/src/App.tsx:530 的 sessionStorage['ai-platform-user-key'] 单一存储点
  （登录后写入服务端签发 Key）；顶栏 Workspace 切换器（GET /workspaces + X-Workspace-Id 头，
  legacy 忽略）；成员管理表格（role 下拉/移除）。参考现有 AdminDashboard 的 client 模式。

验收：
- 场景1：注册 → 建 workspace → 生成 Key → 调 Chat API → 查看 Conversation，全链路通。
- 场景2：workspace A/B 隔离测试通过；同 workspace 共享测试通过。
- 场景3：旧 Key（未绑定用户）访问 RAG/会话/Workflow 行为不变。
- 前端测试（vitest）补登录页与 workspace client 用例；质量门全绿。

完成后不要继续下一目标；列出变更文件清单，交给用户 Code Review。
```

---

## 目标 3：Prompt Registry + Agent Definition + Tool Center + Benchmark（Sprint B）

**依赖**：目标 1、2（IdentityContext 可用）。

### 提示词

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现"AI 平台配置层"（Sprint B）。
前置：身份底座与租户迁移已完成（RequestContext.identity、workspace 隔离可用）。
先阅读 docs/roadmap/2026-08-07-platform-hardening-plan.md 的 1.5 设计冻结、
Sprint B 章节、附录 S2，以及仓库根 AGENTS.md。

核心等式：Agent = Model + Prompt(版本) + Tools(白名单)。全部资源挂 workspace_id。

实现：
- app/db/prompt_models.py: PromptTemplateTable(workspace_id 可空=内置全局, name, version,
  content, variables JSON, is_active, created_by)；每 (workspace_id,name) 至多一个 active。
- app/prompts/: models/repository/memory_repository/postgres_repository/service/seeds。
  service 提供 render(name, variables, fallback)/create_version/activate(回滚=激活上一版)。
  种子：把 app/services/agent_service.py 的 _AGENT_PROTOCOL_PROMPT/_RAG_PRESET_PROMPT
  和 chat_service 的 system prompt 写入全局模板；registry 空时回退内置常量（离线可用）。
- app/db/agent_models.py: AgentTable(workspace_id, name, model, prompt_ref, temperature,
  max_steps, enabled) + AgentToolTable(agent_id, tool_name) + ToolTable(name PK,
  description, parameters_schema JSON, enabled_by_default, owner builtin|mcp|custom)。
- app/agent_config/: AgentDefinitionService（CRUD + 工具白名单校验，工具必须在
  ToolRegistry/MCP 可用集内）；ToolRegistry 升级：内置工具注册表为种子，DB 控制
  workspace 级启用。
- app/api/prompts.py: GET /prompts、GET /prompts/{name}/versions、
  POST /prompts/{name}/versions、POST /prompts/{name}/activate {version}。
- app/api/agents.py: agents CRUD + 工具勾选；app/api/tools.py: 工具列表 + enabled + JSON Schema。
- app/api/agent.py: POST /agent/run 支持 {agent_id}（解析 AgentTable → model/prompt/tools/
  max_steps）；未提供 agent_id 时行为与现状完全一致；audit payload 记录 prompt name+version。
- app/evals/agent_benchmark.py + app/db/benchmark_models.py: golden 任务集 JSON，
  指标 Tool Call Accuracy / Task Completion Rate / Average Steps / Latency，
  复用 run recorder 数据；POST /benchmarks/run {agent_id, task_set}、
  GET /benchmarks/runs；结果落 agent_benchmark_runs 表。
- 前端：PromptStudio 去 localStorage 改调 API（版本历史 + 设为当前版本）；
  新增 Agent Studio（模型下拉/Prompt 版本选择/工具勾选）与 Tool Center（开关 + Schema 展示）。
  参考 frontend/src/platform/ 现有 client 模式。

验收：
- Prompt 版本生命周期单测（create→list→activate→rollback）、单 active 不变式、
  变量渲染/缺省、fallback；workspace 间模板隔离。
- 创建 Research Agent（qwen3 + rag_answer@3 + knowledge_search/calculator）→
  POST /agent/run {agent_id} 跑通；无 agent_id 时旧行为不变。
- Benchmark 四项指标落库并有单测。
- 前端 vitest 覆盖 prompt/agent/tool client；质量门全绿。

完成后不要继续下一目标；列出变更文件清单，交给用户 Code Review。
```

---

## 目标 4：Hybrid Search + Reranker + Golden Set CI（Sprint C）

**依赖**：身份底座可用（owner 过滤不受影响）。

### 提示词

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现"企业级 RAG 检索"（Sprint C）。
先阅读 docs/roadmap/2026-08-07-platform-hardening-plan.md 的 Sprint C 章节、
附录 S3，以及仓库根 AGENTS.md。

关键决策（不可改）：
- 中文 keyword 用 jieba 分词 → to_tsvector('simple')；E10023 等错误码保留整 token。
  不引入 zhparser（需编译 SCWS）；pgvector 镜像零改动。
- 融合用 RRF：score = 1/(60 + semantic_rank) + 1/(60 + keyword_rank)，不调权重。
- RAG_SEARCH_MODE=vector 时行为与现状逐字节一致（兼容开关）。

实现：
- app/rag/tokenize.py: jieba 封装（+ 纯标准库降级路径）；pyproject 增加 jieba。
- app/db/rag_models.py: RagDocumentChunk 增加 search_vector(tsvector) + GIN 索引，
  幂等 migrate helper；app/rag/ingestion.py 入库时生成 tsvector。
- scripts/backfill_search_vectors.py: 存量数据幂等回填。
- app/rag/hybrid.py: HybridRetriever（实现现有 VectorStore 协议不变式，
  SearchResult.distance 语义保留；keyword 查询用 ts_rank + plainto_tsquery）。
- app/rag/reranker.py: Reranker Protocol；NoopReranker（默认）/ JinaReranker
  （RERANKER_API_KEY，无 Key 自动禁用）/ Cohere 预留。
- app/core/settings.py + .env.example: RAG_SEARCH_MODE=hybrid|vector|keyword、
  RAG_RRF_K=60、RERANKER_*。
- app/evals/rag_runner.py: 参数化 RAG_SEARCH_MODE；golden set 打分对比 hybrid vs vector-only。
- .github/workflows/ci.yml: 新增 Golden Set job——hybrid 分数低于 vector-only 基线阈值则失败。
- README Evaluation 记录两种模式分数；若 hybrid 无提升，保留实现但默认 vector。

验收：
- tests/test_tokenize.py（中文/错误码/英文混合）、tests/test_hybrid_search.py
  （RRF 排序、distance 语义、模式开关）。
- E10023 类查询 golden set 上 hybrid 命中 > vector-only（分数写入 README）。
- 回填脚本幂等可重跑；CI 含 Golden Set 门；质量门全绿。

完成后不要继续下一目标；列出变更文件清单，交给用户 Code Review。
```

---

## 目标 5：Replay UI + Usage Dashboard + E2E（Sprint D）

**依赖**：目标 1–4（身份、配置、RAG 均已可用）。

### 提示词

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现"展示增强"（Sprint D）。
先阅读 docs/roadmap/2026-08-07-platform-hardening-plan.md 的 Sprint D 章节、
附录 S4，以及仓库根 AGENTS.md。

实现：
- app/api/runs.py: GET /runs/{run_id} 详情契约——逐步事件（LLM decision → tool call →
  RAG result → final answer）+ prompt name/version + tool call/result 摘要；
  必须沿用现有安全投影/sanitize 逻辑（app/api/agent.py 的 _public_* / _sanitize_*），
  不暴露原始 prompt、tool 输入输出、provider 响应或内部错误。
- 前端 frontend/src/platform/RunDetail.tsx: 步骤时间线页，从现有 Trace 面板点击 run_id 进入。
- frontend/src/platform/UsageDashboard.tsx: token/请求数 7 日趋势 + 按模型/按 Key 排行
  （轻量 SVG/CSS 图表，不引新图表库）；后端在 app/api/admin.py 补 per-workspace 聚合
  （daily_usage 已有数据；Sprint A 后 workspace_id 可空列）。
- frontend/e2e/: Playwright 全链路"登录 → 上传 PDF → 审批 Workflow → 知识库问答"
  （测试专用 Mock Provider；本地 Ollama 可选）。frontend/package.json + CI 增加 e2e job
  （可标记 needs: [ci]）。

验收：
- 任意 run_id 可打开时间线页，字段安全投影（有单测断言不含原始敏感内容）。
- Dashboard 展示 7 日趋势 + 排行；前端单测覆盖 client/数据映射。
- Playwright 全链路本地可跑、CI 通过；质量门全绿。

完成后列出变更文件清单，交给用户 Code Review；并按 AGENTS.md 要求更新 README、
conventional commit、≤5 句学习总结。
```
