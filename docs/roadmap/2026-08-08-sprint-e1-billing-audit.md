# Sprint E1：Billing/Plan + Audit Log（企业 SaaS 闭环）

状态：设计已批准（含 review 修正），进入实现
前置：quota（QUOTA_SCOPE + workspace_quotas）、usage 聚合（日/月 workspace 维度）、workspace 体系均已闭环（commit a5d6902/fffa748/41d78cd 起）

---

## 第 1 节：E1 设计文档（批准版，含 review 修正）

### A. Billing / Plan

#### A1. 数据模型

```sql
plans(
  id            UUID PK,
  name          text UNIQUE,                -- free / pro / enterprise
  version       INT NOT NULL DEFAULT 1,     -- review 修正：预留版本（修改计划不追溯历史订阅，backlog 语义）
  daily_token_limit    BIGINT NULL,         -- NULL = 继承全局默认
  monthly_token_limit  BIGINT NULL,
  max_agents    INT NULL,                   -- NULL = 不限
  max_documents INT NULL,                   -- MVP 只做文档数（max_storage_bytes 记 backlog）
  max_members   INT NULL,
  features      JSONB NOT NULL DEFAULT '{}',  -- {"reranker": false, "benchmark": false}
  created_at    timestamptz
)

subscriptions(
  id            UUID PK,
  workspace_id  UUID UNIQUE FK → workspaces.id (CASCADE),  -- 每 workspace 至多一个有效订阅
  plan_id       UUID FK → plans.id,
  status        text NOT NULL,              -- ACTIVE | TRIAL | EXPIRED | CANCELLED
  started_at    timestamptz,
  expired_at    timestamptz NULL
)
```

- features 用 JSONB（feature 数量个位数，行级表是过度设计）；`features->>'reranker'` 查询。
- **种子计划**（幂等 seed，启动时，与 prompt/tool 种子同模式）：
  - free：100k token/月、3 agents、5 文档、无 reranker/benchmark；
  - pro：10M token/月、50 agents、100 文档、全功能；
  - enterprise：无限额（NULL）、全功能。
- **兼容语义（关键，不可改）**：workspace **无订阅 = legacy 模式 = 不限**（与现状逐字节一致）——绝不默认给 free 计划（那是 silent breaking change）。订阅是显式收紧操作。

#### A2. 配额继承链（QuotaResolver 扩展）

```
workspace_quotas 显式覆盖（最高优先级）
      ↓
subscription plan 限额（仅 QUOTA_SCOPE=workspace 模式）
      ↓
settings 全局默认
```

- **review 修正（职责边界，不可混）**：resolver 必须显式分支，不允许偷偷 fallback：

```python
if quota_scope == "key":
    return resolve_key_quota()  # 现状路径，plan 不参与
if quota_scope == "workspace":
    return resolve_workspace_quota()  # override → plan → default
```

- plan 仅在 workspace 模式生效（key 模式判定按 key、plan 是 workspace 维度——混用会出现"key 各自 10M + workspace plan 20M"的语义歧义，必须隔离）。

#### A3. EntitlementService（职责：feature 能力 + 资源上限）

**review 修正（接口拆分，feature 与 limit 是两个概念）**：

```python
class EntitlementService:
    async def check_feature(self, workspace_id: str, feature: str) -> bool:
        """Feature capability: reranker / benchmark / workflow ..."""

    async def check_limit(
        self, workspace_id: str, resource: str, current_count: int
    ) -> bool:
        """Resource ceiling: agent / document / member ..."""
```

- 无订阅 → check_feature 返回 True（legacy 全开）、check_limit 返回 True（不限）——兼容语义；
- 检查点（仅三处，不扩）：`AgentDefinitionService.create_agent`（max_agents）、文档入库（max_documents，ingestion 入口）、成员邀请/添加（max_members）；
- 超限抛明确错误："已达 {plan_name} 计划上限（{resource} {limit}）。"——错误类型用 ValidationError（422）或平台约定（403），按现有异常体系选；
- **职责边界**：token/resource limit → QuotaResolver；feature capability → EntitlementService。未来 Workflow Builder：`workflow.enabled?` 走 EntitlementService，`workflow executions/month` 走 QuotaResolver——互不返工。

#### A4. API

| 端点 | 说明 |
|---|---|
| `GET /api/v1/billing` | 用户端点（owner/admin）：当前计划 + 本月 token 用量（复用 workspace 月度聚合）+ 资源使用数（agents/documents/members）+ features 清单 |
| `POST /api/v1/admin/workspaces/{id}/subscription` | 分配/变更计划（`{plan_id, status?}`）；admin key；无订阅 → 创建，有 → 更新 |
| `GET /api/v1/admin/subscriptions` | 订阅列表（admin，可过滤 plan/status） |
| `GET /api/v1/admin/plans` | 计划列表（admin/用户 billing 页可读？——用户端从 /billing 拿，admin 端全量） |

#### A5. 前端 Billing 页面

`frontend/src/platform/Billing.tsx`：当前计划卡片 + 月度用量进度条（`8.2M / 10M tokens`）+ 资源计数（Agents 12/50、文档数、成员数）+ features 清单（启用/未启用）。导航与 UsageDashboard 同级。无订阅时显示"无计划（legacy）"态。计划切换操作 MVP 只读展示（变更走 admin API，前端不做切换表单——除非成本低）。

### B. Audit Log

#### B1. 表

```sql
audit_events(
  id            BIGSERIAL PK,
  workspace_id  UUID NULL,               -- 无 workspace 的平台级操作为 NULL
  api_key_hash  text NULL,               -- review 修正：actor_key_hash → api_key_hash（F4 审计口径惯例）
  user_id       UUID NULL,
  action        text NOT NULL,           -- agent.create / agent.update / prompt.activate / ...
  resource_type text NOT NULL,           -- agent / prompt / tool / workspace / quota / subscription / member / benchmark / mcp
  resource_id   text NOT NULL,           -- 保持 text（agent UUID / prompt 版本串 / workspace 等混合类型）
  before        JSONB NULL,              -- 变更前关键字段快照（非全量对象）
  after         JSONB NULL,
  ip            text NULL,
  created_at    timestamptz DEFAULT now()
)
```

- 索引：`(workspace_id, created_at)`、`(action)`；
- before/after = 关键字段快照（如 agent.update 记录 temperature/max_steps/enabled/prompt_ref 前后值）。

#### B2. AuditService + 钩子点

`AuditService.record(workspace_id, api_key_hash, user_id, action, resource_type, resource_id, before, after, ip)`。

钩子点（服务层显式记录）：
- `AgentDefinitionService`：create_agent / update_agent（before=旧记录关键字段）/ delete_agent
- `PromptRegistryService`：create_version / activate（回滚也记录）
- `AgentDefinitionService.set_tool_enabled`（工具开关）
- **review 补充钩子**：`AgentBenchmarkRunner.run`（benchmark.execute：agent_id/task_set/结果指标摘要）；MCP 权限变更（mcp_permission_change——若当前无 MCP 权限修改入口则记 backlog，不强行造钩子）
- `WorkspaceService`：成员 invite / role change / remove
- 配额/订阅：set_workspace_quota、subscription 变更

**review 修正（降级语义）**：审计失败**业务不中断但必须可见**——`logger.exception("audit write failed")` 而非裸 `except: pass`（企业实践：有告警通道）。

#### B3. API + 前端

- `GET /api/v1/admin/audit-events?workspace_id=&action=&limit=`（admin key，时间倒序，limit 1-200，无分页游标 MVP）；
- 前端：Admin 面板审计表格（时间/操作/资源/actor/diff 展示）——**可降级**：时间不足时只做 API（Billing UI 优先）。

### C. 实施拆分

| 批 | 内容 | 验收要点 |
|---|---|---|
| **E1a**（Billing 后端） | plans/subscriptions 表 + 种子 + EntitlementService + QuotaResolver 继承链扩展 + 资源上限检查 + billing/subscription API | 继承链（override > plan > default）；无订阅=现状；key 模式不受 plan 影响；资源超限拒绝；种子幂等；check_feature/check_limit 职责分离 |
| **E1b**（Audit 后端） | audit_events 表 + AuditService + 钩子 + admin API | 关键操作记录断言（agent.update 前后值）；降级 logger.exception；过滤/排序 |
| **E1c**（前端） | Billing 页面（必须）+ 审计表格（可降级为 API only） | 渲染/交互测试；质量门全绿 |

E1a 与 E1b 独立可并行；E1c 依赖两者。

### D. 明确不做（backlog）

- 真实支付（Stripe）——status 字段已含 TRIAL/EXPIRED/CANCELLED，无支付回调；
- 超额自动断服务——软上限（超限展示+记录，不拦截现有体验）；
- plans.version 的历史订阅追溯语义（字段预留，不改订阅逻辑）；
- max_storage_bytes（文档数先行）；
- Organization 三层模型、计划自定义构建器。

---

## 第 2 节：E1a 派发提示词（Billing 后端）

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现 Sprint E1 的 E1a（Billing/Plan 后端）。
先读 docs/roadmap/2026-08-08-sprint-e1-billing-audit.md 第 1 节（设计文档，含 review 修正——
不可改）、仓库根 AGENTS.md，以及以下现有代码：app/quota/{models,service,repository,
postgres_repository,memory_repository}.py、app/db/models.py、app/db/init.py（migrate_* 模式）、
app/agent_config/service.py（create_agent 与资源检查点）、app/rag/ingestion.py（文档入库检查点）、
app/auth/workspace_service.py（成员检查点）、app/api/admin.py（admin 端点模式）、
app/api/usage.py（_owner_scope 与用户端点模式）、app/core/container.py（provide_* 模式）。

实现：
1. 表结构（app/db/billing_models.py 新增）：PlanTable + SubscriptionTable（按第 1 节 A1 SQL）；
   注册进 _CORE_TABLES；幂等迁移不需要（新表 create_all 即可——如项目惯例需要 migrate helper
   则按既有模式补）；种子计划（free/pro/enterprise）幂等 seed（启动时，与 prompt/tool 种子同位置
   同模式，见 app/main.py 的 _bootstrap_seeds）。
2. app/billing/（新包）：
   - models.py：Plan / Subscription / PlanLimits dataclass（或 pydantic，按项目惯例）；
   - repository.py + memory/postgres 双实现：list_plans / get_plan / create_subscription /
     update_subscription / get_subscription_for_workspace / list_subscriptions；
   - service.py：PlanService（订阅管理：assign_plan 的创建/更新/状态流转）；
   - entitlement.py：EntitlementService（第 1 节 A3 接口——check_feature 与 check_limit 分离，
     无订阅返回 True/True，legacy 全开）。
3. QuotaResolver 继承链扩展（app/quota/service.py）：
   - _resolve_limits 改为显式分支：quota_scope=="key" → 现状（settings 值，不查 plan）；
     quota_scope=="workspace" → workspace override → subscription plan 限额 → settings；
   - plan 解析依赖 QuotaService 构造注入（PlanService/EntitlementService 或直接注入 repository——
     按容器习惯选择，避免循环依赖：billing 不依赖 quota，quota 依赖 billing 的 repository 即可）；
   - 注意 subscription 状态：仅 ACTIVE/TRIAL 生效（EXPIRED/CANCELLED 跳过 plan 层）。
4. 资源上限检查：
   - AgentDefinitionService.create_agent：注入 EntitlementService，check_limit("agent",
     count(workspace 现有 agents)) 超限抛 ValidationError（消息含计划名与上限）；
   - 文档入库（RAGIngestionService 或上传 API 层）：check_limit("document", 现有文档数)；
   - 成员添加（WorkspaceService）：check_limit("member", 现有成员数)；
   - 三处均无订阅时不检查（legacy）。
5. API：
   - app/api/billing.py：GET /api/v1/billing（用户端点，require_rate_limit + _owner_scope 同款
     鉴权模式；响应：plan 名/状态/features/月度 token 用量（复用 workspace 月度聚合）/
     资源计数（agents/documents/members））；
   - app/api/admin.py 或新 admin 路由：POST /admin/workspaces/{id}/subscription
     （admin key；body {plan_id, status?}；workspace 存在性 404；无订阅创建/有订阅更新）、
     GET /admin/subscriptions（过滤 plan_id/status 可选）、GET /admin/plans；
   - main.py 注册路由。
6. 测试（tests/test_billing.py 等）：
   - 种子幂等（重复 seed 不重复插）；
   - 继承链：override > plan > default 三态断言；无订阅=settings（现状）；
     key 模式传 workspace_id 也不走 plan（显式分支测试）；
     EXPIRED 订阅跳过 plan 层；
   - check_feature/check_limit：无订阅 True/True；订阅后按 plan 值；超限拒绝（agent 创建、
     文档入库、成员添加三处各一个场景）；
   - API：GET /billing 形状（含用量与计数）；subscription assign/update/404/403（admin 鉴权）。

验收：
- 现有全部测试保持通过（无订阅=现状是硬验收——现有 quota/agent/rag 测试零变化）；
- 新增测试全绿；ruff format --check .、ruff check .、mypy app tests、pytest 全绿；
- README：能力清单补 Billing/Plan 一行 + 配置说明（无订阅=legacy）。

完成后不要继续；列出变更文件清单和关键设计说明，交给用户 Code Review。
```

---

## 第 3 节：E1b 派发提示词（Audit 后端）

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现 Sprint E1 的 E1b（Audit Log 后端）。
先读 docs/roadmap/2026-08-08-sprint-e1-billing-audit.md 第 1 节 B 部分（含 review 修正——
不可改）、仓库根 AGENTS.md，以及：app/services/agent_run_record_service.py（run 审计的
静默降级与日志模式——本批沿用其哲学但按修正加 logger.exception）、app/agent_config/service.py、
app/prompts/service.py、app/auth/workspace_service.py、app/api/admin.py（admin 端点模式）、
app/db/init.py（_CORE_TABLES 注册）。

实现：
1. 表结构（app/db/audit_models.py 新增）：AuditEventTable（第 1 节 B1 SQL，含索引：
   (workspace_id, created_at)、(action)）；注册 _CORE_TABLES。
2. app/audit/（新包）：
   - models.py：AuditEvent dataclass + AuditRecordInput（action/resource_type/resource_id/
     before/after/ip 等）；
   - repository.py + memory/postgres 双实现：record(event) / list_events(workspace_id?,
     action?, limit)（时间倒序）；
   - service.py：AuditService.record(...)；
3. 钩子接入（服务层显式调用，改以下文件）：
   - AgentDefinitionService：create_agent（after=关键字段快照 {name,model,prompt_ref,
     temperature,max_steps,enabled}）、update_agent（before=旧快照/after=新快照）、
     delete_agent（before=删除前快照）；
   - PromptRegistryService：create_version（after={version,content 摘要或长度}）、
     activate（before={旧 active 版本}/after={新版本}——回滚同样走 activate 自然覆盖）；
   - AgentDefinitionService.set_tool_enabled（before/after enabled 值）；
   - AgentBenchmarkRunner.run（benchmark.execute：after={task_set, task_count,
     completed_count, tool_call_accuracy, task_completion_rate} 摘要）；
   - WorkspaceService：成员 invite / role change / remove（before/after role 等）；
   - 配额与订阅（如 E1a 已完成则补：set_workspace_quota、subscription 变更）；
   - MCP 权限变更：当前若无修改入口则记 backlog，不强行造钩子。
   - actor 来源：调用方从 RequestContext（identity.user_id / api_key_hash）传，服务层
     不感知请求（与 run 审计一致——API 层解析 context 后传入，或服务方法签名加可选参数）；
   - 记录失败降级（修正版）：try/except Exception → logger.exception("audit write failed")
     业务不中断但可告警——绝不影响主操作返回值。
4. API：GET /api/v1/admin/audit-events（admin key；query：workspace_id?/action?/limit
   ge=1 le=200；时间倒序；响应 list[AuditEventResponse]）。
5. 测试（tests/test_audit_log.py）：
   - agent.update 记录断言（before/after 关键字段值）；
   - prompt.activate 记录（版本前后）；
   - benchmark.execute 记录（指标摘要）；
   - 降级路径：repository.record 抛错 → 主操作不受影响 + logger.exception 被调用
     （mock logger 断言）；
   - list_events 过滤（workspace_id/action）与排序；
   - admin 鉴权（非 admin 403）。

验收：
- 现有全部测试保持通过（钩子不改变任何业务行为）；新增测试全绿；
- ruff format --check .、ruff check .、mypy app tests、pytest 全绿；
- README：能力清单补 Audit Log 一行。

完成后不要继续；列出变更文件清单和关键设计说明，交给用户 Code Review。
```

---

## 第 4 节：E1c 派发提示词（前端 Billing + Audit）

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现 Sprint E1 的 E1c（前端）。
前置：E1a（Billing API）已完成；E1b（Audit API）已完成或至少 audit-events API 存在。
先读 docs/roadmap/2026-08-08-sprint-e1-billing-audit.md 第 1 节 A5/B3、仓库根 AGENTS.md，
以及 frontend/src/platform/{UsageDashboard.tsx, RunList.tsx}（页面模式）、
frontend/src/platform/config-client.ts（客户端模式）、frontend/src/admin/（admin client 模式）、
frontend/src/App.tsx（导航/路由模式）、frontend/src/platform/platform.test.tsx 与
frontend/src/admin/client.test.ts（测试模式）。

实现：
1. Billing 页面（必须）：frontend/src/platform/Billing.tsx——
   - 当前计划卡片（计划名/状态/features 清单启用与否）；
   - 月度用量进度条（已用/上限，上限为 null 时显示"不限"）；
   - 资源计数（Agents 12/50、文档数、成员数）；
   - 无订阅（legacy）显示"无计划"态；
   - config-client：getBilling() + 防御性归一化（空值/null 兜底）；
   - App.tsx 导航加「账单」入口。
2. Audit 表格（可降级）：若 E1b API 完成且有剩余时间——Admin 面板加审计表格
   （时间/操作/资源类型/资源 ID/actor 前缀/before-after 摘要展示）；admin client 加
   listAuditEvents；若时间不足只保证 API 存在、表格不做（交付说明里注明）。
3. 测试：
   - Billing 渲染（mock client：有订阅/无订阅两态、进度条计算、features 展示）；
   - client 契约（URL/method/归一化）；
   - Audit 表格（如做）：渲染 + 空态。

验收：
- 前端现有测试全绿 + 新增通过；typecheck、oxlint 全绿；
- 后端不受影响（纯前端批）。

完成后不要继续；列出变更文件清单，交给用户 Code Review。
```

---

## 派发说明

1. **顺序**：E1a 与 E1b 可并行（不同 bounded context：app/billing/ vs app/audit/），互不依赖（E1b 的"配额与订阅钩子"依赖 E1a——若无则跳过该项记 backlog，不阻塞）；E1c 依赖 E1a（必须）与 E1b（可降级）；
2. **每个模型开工前先读本文件第 1 节**（设计含修正，不可改）+ AGENTS.md；
3. 完成后按 AGENTS.md 流程停住交回 Code Review；
4. **硬验收**：现有测试零破坏（无订阅=现状、钩子不改变业务行为）。
