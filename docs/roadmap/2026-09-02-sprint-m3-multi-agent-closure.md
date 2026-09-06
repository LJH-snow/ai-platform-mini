# Sprint M3：多 Agent 编排闭环（SSE + 持久化 + 前端）

日期：2026-09-02
状态：已完成（2026-09-06）

## 背景

Sprint M2 交付了多 Agent 编排后端同步 MVP（Supervisor 拆分 + Orchestrator 执行，
`POST /api/v1/multi-agent/runs`），并明确不承诺 SSE、持久化 Trace 与前端编排。
M3 收口这三件事，并对齐平台既有模式（Agent SSE 事件契约、Run 记录租户隔离、
前端实时时间线）。

调研发现的额外缺口（本轮一并修复）：

- **鉴权缺口**：`app/api/multi_agent.py` 的 `POST /runs` 未挂
  `require_rate_limit`（项目鉴权是路由级依赖，不是全局中间件），
  `AUTH_ENABLED=true` 时该端点绕过 Bearer 鉴权与限流。
- **错误泄露面**：同步端点把 `str(exc)` 原样放进 `subtask_results[].error`
  与顶层 `error`，可能包含内部路径/堆栈细节，需要安全投影。

## 目标

1. P1 后端：多 Agent 事件观察者 + `POST /api/v1/multi-agent/runs/stream` SSE，
   修复两个端点的鉴权/限流缺口。
2. P2 后端：多 Agent Run 落库（PostgreSQL）+ `GET /api/v1/multi-agent/runs`
   历史列表 / `GET /api/v1/multi-agent/runs/{run_id}` 详情（租户隔离）。
3. P3 前端：`多 Agent 编排` 页面 —— 任务输入、运行配置、子任务实时状态、
   汇总输出、历史查看。

## 非目标（明确不做）

- 子任务内部 `answer_delta` 流式（子任务经 ChatService 非流式执行，保持不变）
- 前端 React Flow 编排画布、跨进程队列、分布式执行
- Quota 预占模型变更（子任务各自经 ChatService 边界记账，与 M2 一致）
- 多 Agent Run 接入长期记忆 / Workflow Builder

## SSE 事件契约（`POST /api/v1/multi-agent/runs/stream`）

事件按 `run_id` / `sequence` 单调递增发布，`occurred_at` 为真实 UTC 时间；
空流不补造事件，Provider 错误/超时/取消不改写为成功。

| 事件 | 触发点 | 关键字段（安全投影） |
| --- | --- | --- |
| `run_started` | service.run 进入 | `run_id` |
| `subtasks_planned` | Supervisor 拆分完成 | `subtasks[]`：`id`、`agent_role`、`description`（脱敏+截断 256）、`depends_on`（id 列表）、`subtask_count`、`reasoning`（脱敏+截断 256） |
| `subtask_started` | 子任务开始执行 | `task_id`、`agent_role` |
| `subtask_completed` | 子任务成功 | `task_id`、`agent_role`、`duration_ms`、`token_usage`、`output_summary`（脱敏+截断 256） |
| `subtask_failed` | 子任务失败 | `task_id`、`agent_role`、`error_code`（白名单映射）、`duration_ms` |
| `subtask_skipped` | 依赖失败跳过 | `task_id` |
| `run_completed` | 全部完成 | `final_output`（脱敏全文）、`total_token_usage`、`duration_ms`、`subtask_results[]` 摘要 |
| `run_failed` | 拆分失败/死锁/fail_fast | `error_code`、`total_token_usage`、`duration_ms` |
| `run_timed_out` | total_timeout 超时 | 同上 |
| `run_cancelled` | 外部取消（断连） | 同上 |
| `run_budget_exceeded` | total_token_budget 超限 | 同上 |
| `stream_error` | 流启动边界失败 | `error_code`；可缺 `run_id`/`sequence`，不代表终态 |

边界规则（对齐 Agent SSE 契约）：

- 不公开原始 Prompt、Provider 响应、原始 `str(exc)`、堆栈、内部路径、密钥
- 错误一律映射到有限 `error_code` 白名单（`supervisor_failed`、
  `subtask_failed`、`dependency_deadlock`、`timeout`、`cancelled`、
  `budget_exceeded`、`internal_error`、`stream_setup_failed`）
- 客户端断连 → `cancel_event` → 在跑子任务取消 → 终态 `run_cancelled`
  真实产生并持久化（客户端可能收不到，但记录不撒谎）
- 文本脱敏复用 `app/api/agent.py` 的公开正则边界（提取为共享模块，行为不变）

## 后端改造点（文件级）

- `app/api/redaction.py`（新）：从 `app/api/agent.py` 提取
  `_sanitize_public_text` 及其正则常量（纯移动，agent.py 改为 re-import，
  现有测试零变化）
- `app/multi_agent/events.py`（新）：`MultiAgentEventKind` StrEnum、
  `MultiAgentEvent` dataclass（含 `to_public_dict` 安全投影，与上表一致）
- `app/multi_agent/models.py`：`SubtaskResult` / `OrchestrationResult` 不动协议；
  `OrchestrationState` 增加 `stop_reason` 概念（由 status 映射）
- `app/multi_agent/orchestrator.py`：`execute()` 增加
  `observer: MultiAgentEventObserver | None`（async Protocol）与
  `cancel_event: asyncio.Event | None`；在子任务 start/complete/fail/skip 与
  终态处调用 observer；DAG 循环检查 cancel_event；取消在跑任务
- `app/multi_agent/service.py`：`run()` 接受 observer + cancel_event，
  统一分配 `sequence`、emit `run_started` / `subtasks_planned` / 终态事件
- `app/schemas/multi_agent.py`（新）：`MultiAgentStreamEvent` Pydantic 模型；
  同步响应的 `error` 字段改为安全映射（保持既有字段名兼容）
- `app/api/multi_agent.py`：两个 POST 端点挂 `require_rate_limit` +
  X-RateLimit 头；新增 `/runs/stream`（复刻 `app/api/agent.py` 的
  `_stream_events` produce/queue/断连轮询模式）
- `app/db/models.py`：`MultiAgentRunRecordTable`（`multi_agent_run_records`）
- `app/db/init.py`：create_all 注册 + 幂等 migrate helper（模式同
  `migrate_run_records_schema`）
- `app/services/multi_agent_run_record_service.py`（新）：
  save / list_runs / get_run（owner_scope = workspace_id ?? api_key_hash，
  跨租户 404）+ 公开投影函数（allowlist）
- `app/core/container.py`：`provide_multi_agent_run_record_service()`
  （无 engine → None，端点 503，与 Agent Run 记录一致）
- 测试：`tests/test_multi_agent_events.py`（observer 事件序列）、
  `tests/test_multi_agent_stream_api.py`（SSE 顺序/终态/取消/断连/敏感清洗）、
  `tests/test_multi_agent_records.py`（持久化 + 历史 API + 租户隔离）、
  既有 `tests/test_multi_agent.py` 保持通过

## 前端改造点

- `frontend/src/multiagent/`（新）：`types.ts`、`stream.ts`（SSE 解析，
  未知事件返回 null 不抛错）、`reducer.ts`（事件 → 状态机）、
  `client.ts`（运行 + 历史 API）、`MultiAgentConsole.tsx`（UI）
- `frontend/src/App.tsx`：导航新增 `多 Agent 编排` 页
- UI：任务输入 + 可折叠高级配置（max_subtasks / max_concurrency /
  failure_policy / total_timeout）+ 运行/停止按钮 + 子任务卡片
  （状态徽标 pending/running/completed/failed/skipped、耗时、token、
  依赖关系）+ 汇总输出面板 + 历史列表/详情查看
- 仅展示后端公开字段；缺失显示"后端未提供"；停止只停止等待，只有真实
  `run_cancelled` 才显示后端取消（对齐 Agent Console 语义）
- 测试：`stream.test.ts`、`reducer.test.ts`、`client.test.ts`、
  `MultiAgentConsole.test.tsx`

## 验收

- `ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest` 全绿
- 前端 `npm run format:check` / `lint` / `typecheck` / `test` / `build` 全绿
- SSE 端点在 `AUTH_ENABLED=true` 下无 Bearer 返回 401（缺口修复回归）
- 历史详情跨租户 404；无 DB 时历史端点 503
- 事件序列测试覆盖：正常完成、fail_fast、timeout、budget、取消、断连

## 收口与验收记录（2026-09-06）

> P1/P2 后端主体完成后，先按本规划口径收口高/中优先级缺口，再进入 P3；
> 以下项目均已实现、Code Review 修复并提交。

### 高优先级（进 P3 前必须收口）

- [x] `GET /api/v1/multi-agent/runs` 与 `GET /runs/{run_id}` 补 `require_rate_limit` 鉴权（对照 `app/api/runs.py` 历史接口，匿名不可进，`owner_scope` 不可为 None 越权）
- [x] 同步响应 `error` 改安全投影：`app/api/multi_agent.py` 的 `subtask_results[].error` 与顶层 `error` 不直接放 `r.error` / `result.error`；`service.py` 的 `Supervisor decomposition failed: {exc}` 不拼原始 `str(exc)`，只留白名单 `error_code`
- [x] 补 `tests/test_multi_agent_stream_api.py`：SSE 顺序/终态/取消/断连/脱敏/无 Bearer 401
- [x] 补 `tests/test_multi_agent_records.py`：落库 + 历史列表/详情 + 跨租户 404 + 无 DB 503
- [x] `clear_container_cache()` 补 `provide_multi_agent_run_record_service.cache_clear()`，避免 lifespan 重启复用已 dispose 的 factory

### 中优先级（与规划文字对齐）

- [x] `final_output` 截断口径定版：规划写脱敏全文，实现用 `_bounded_summary` 截 256，`events.py` 的 `_bounded_result`（8192）未使用，二选一并落实
- [x] `project_run_response` 白名单落地：`GET 详情` 改调该 allowlist，或删除死代码
- [x] `app/db/init.py` 补新表幂等 migrate helper（仿 `migrate_run_records_schema`），并清理 `_CORE_TABLES` 中 `AgentRunRecordTable` 重复项
- [x] 单任务 `CANCELLED` 归类：`orchestrator.py` 的 `_execute_dag` 结果分支补 `CANCELLED/SKIPPED` 处理，避免误判为 `COMPLETED`

### 门禁抽查现状

- [x] 新文件 `ruff format --check` / `ruff check` 通过
- [x] `mypy app tests` 通过（335 文件）
- [x] `pytest tests/test_multi_agent*.py` 通过（含新增 stream/records）
- [x] 全量 `pytest` 通过：本机默认 1042 passed / 39 skipped；GitHub Actions
  `ci`、`compatibility-312`、`rag-golden`、`e2e` 四个 job 全绿
- [x] 前端五项门禁（P3 后补齐）：prettier / oxlint / typecheck / vitest / build 全绿
- [x] `tests/test_quota*.py` 日期边界修复已随收口落地，月末/月初断言不再依赖
  “离下月一定超过一天”的假设

### 实现期额外发现（已修复）

- `app/multi_agent/supervisor.py` 的 `ChatRequest` 只在 `TYPE_CHECKING` 下导入，运行时 `decompose()` 必 `NameError`，已改为运行时导入；此前单测只覆盖 `_parse_decision` 故未暴露。

### E2E 修复（2026-09-06）

- 历史列表按 workspace 记录原始 `workspace_id`，查询时不再用 sha256 摘要比对；
  新增真实注册用户场景与跨 workspace 404 回归。
- 详情响应不要求 `subtask_count`；前端类型从继承改为 `Omit`，详情适配用
  `subtaskResults.length` 回填，避免后端摘要与详情字段形态不一致导致回放失败。
- 本地 Playwright 与 GitHub Actions `e2e` job 均已通过。
