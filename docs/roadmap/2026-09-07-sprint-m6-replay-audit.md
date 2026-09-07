# Sprint M6 设计文档：多 Agent 事件持久化与全量回放

> 状态：已实现（2026-09-07）。
> 关联：M3 `multi_agent_run_records` 摘要持久化、M4 真子任务执行、M5 子任务
> `answer_delta` 增量流。

## 背景与动机

M3/M4/M5 已把多 Agent 编排从“可演示”推进到“可实时观察”：

- M3：SSE 生命周期事件流、`multi_agent_run_records` 摘要持久化、租户隔离历史。
- M4：子任务改为真实 `AgentRuntime` 执行，支持 Tool/RAG。
- M5：子任务内部的 `answer_delta` 通过 `SUBTASK_ANSWER_DELTA` 转发到多 Agent SSE。

但目前存在一个明确的断层：**实时看得到，历史回放不到**。

- `app/multi_agent/events.py`、`app/schemas/multi_agent.py`、前端 reducer 三处
  已对齐 `subtask_answer_delta`、`agent_event_kind`、`step_index`。
- 但 `app/services/multi_agent_run_record_service.py` 落库的 payload 只有
  `subtask_results` 摘要，不含增量事件，也不含子任务内部 Step / Tool 过程。
  `GET /runs/{run_id}` 详情回放时 `answerDeltas` 恒为空，Step / Tool 回放更不存在。
- 子任务内部目前只转发了 `ANSWER_DELTA`（
  `app/multi_agent/orchestrator.py::_SubtaskAnswerDeltaObserver`），
  `AgentEventKind` 里的 `STEP_STARTED/COMPLETED`、`TOOL_STARTED/COMPLETED/FAILED`
  尚未进入多 Agent 事件模型。

本 Sprint 的目标是把“执行史”变成可回放的数据资产，先做一次跨层架构校验，
再补事件级持久化与子任务 Step / Tool 全量回放。

## 目标 / 非目标

### 目标

1. 事件模型 ↔ 持久化白名单 ↔ 前端状态机三处对齐，先产出核对清单与差异清单。
2. 多 Agent 生命周期事件（含 `answer_delta`）按 run 落库，提供租户隔离的
   时间线回放。
3. 子任务内部 Step / Tool 事件进入多 Agent 事件模型、持久化与前端回放。
4. 全程复用现有安全投影与 `error_code` 白名单，不引入新协议，不伪造终态。

### 非目标

- 跨进程持久化任务队列 / worker 恢复（留给后续 Sprint）。
- 编排画布拖拽编辑（未列入）。
- 改变现有 SSE 语义、终态判定或取消语义。
- 重新设计 AgentRuntime 事件模型（只做多 Agent 侧的映射与转发）。

## 现状核对（P0）

先做跨 Sprint 的一致性校验，输出「三方对齐核对表」。核对点：

| 现象 | 事件模型 | SSE Schema | 前端 reducer | 持久化回放 |
| --- | --- | --- | --- | --- |
| run 生命周期 | ✅ | ✅ | ✅ | ✅ 摘要 |
| subtasks_planned | ✅ | ✅ | ✅ | ❌ | 
| 子任务 lifecycle | ✅ | ✅ | ✅ | ✅ 仅摘要 |
| answer_delta | ✅ | ✅ | ✅ | ❌ |
| step / tool 事件 | ❌（未建模） | ❌ | ❌ | ❌ |

校验动作：

- 逐字段比对 `MultiAgentEvent.to_public_dict()` ↔ `MultiAgentStreamEvent` 字段 ↔
  前端 `MultiAgentStreamEvent` 消费逻辑，出现不一致的地方记为差异项。
- 核对 `project_run_response` 白名单是否与历史回放需要的最小集合一致。
- 确认 list / detail / events 三端口的租户隔离逻辑从同一 `owner_scope` 派生。
- 将核对结论写入 `findings.md`，修复差异后再进入 P1。

## 事件持久化与回放（主）

### 数据模型

新增 `multi_agent_run_events` 表，一行一个已脱敏的公开事件：

```text
id            serial / uuid  PK
run_id        FK 逻辑关联 multi_agent_run_records（不建强约束，便于无 DB 跳过）
sequence      int   事件序号，来自 SequencedObserver
kind          varchar  MultiAgentEventKind.value
occurred_at   timestamptz
task_id       varchar nullable
agent_role    varchar nullable
agent_event_kind varchar nullable  转发的内部 Agent 事件（如 answer_delta）
step_index    int nullable
output_summary varchar nullable
error_code    varchar nullable
reasoning     varchar nullable
final_output  text nullable
total_token_usage bigint nullable
subtasks      jsonb default null   计划任务安全投影
subtask_results jsonb default null 终态结果安全投影
payload       jsonb NOT NULL       该事件的 to_public_dict() 全量（已脱敏）
```

设计要点：

- 持久化的就是 `event.to_public_dict()`，与 SSE 共用同一条安全边界，
  避免“回放通道再脱敏一次”造成口径漂移。
- 事件写入采用 best-effort observer：记录失败只记日志，绝不阻断编排
  （与 `BestEffortObserver` 语义一致）。
- `run_id` 不放强外键，保持与摘要表一致的“无 engine → 跳过”行为。

### 幂等迁移

仿 `migrate_multi_agent_run_records_schema` 新增
`migrate_multi_agent_run_events_schema`，注册到 `app/db/init.py` 的 `init_db`。

### 捕获与写入

- 在 `MultiAgentService.run` / 编排入口构造一个
  `MultiAgentEventRecorder` observer；有 engine 时启用，无 engine 时置空。
- 记录器与 SSE bridge 平级（各自独立 `on_event`），共享 `SequencedObserver`
  分配的同一递增序号，保证流式与持久化顺序一致。
- 每条事件写一个事务；`answer_delta` 可能很多，逐条写入仍应有容量保护
  （见“边界”）。

### 回放接口

新增 `GET /api/v1/multi-agent/runs/{run_id}/events`：

- 按 `sequence` 升序返回事件列表（分页可选，默认限制条数）。
- 租户隔离复用 `get_run` 的 owner 判定：跨租户或不存在返回 404。
- 无 DB 时返回 503，与历史接口一致。
- 响应 schema 复用事件安全投影，字段与 SSE 对齐。

## Step / Tool 全量回放（P2）

当前只透传 `ANSWER_DELTA`。为做到 Step / Tool 回放，需把内部 Agent 事件
映射到多 Agent 事件模型：

| 内部 AgentEventKind | 多 Agent 事件建议 | 落库字段 |
| --- | --- | --- |
| STEP_STARTED | subtask_step_started | task_id, step_index |
| STEP_COMPLETED | subtask_step_completed | task_id, step_index, output_summary |
| TOOL_STARTED | subtask_tool_started | task_id, step_index, tool_name, call_id |
| TOOL_COMPLETED | subtask_tool_completed | task_id, step_index, tool_name, output_summary |
| TOOL_FAILED | subtask_tool_failed | task_id, step_index, tool_name, error_code |
| ANSWER_DELTA | subtask_answer_delta（已有） | task_id, step_index, output_summary |

实现注意：

- `_SubtaskAnswerDeltaObserver` 升级为通用「子任务事件转发器」，按白名单
  映射事件并保持 `drain()` 的顺序保证。
- Tool 事件只暴露 `tool_name` 与安全 `output_summary`，不暴露原始 arguments /
  raw payload（沿用 `app/api/redaction.py` 边界）。
- `MultiAgentEvent` 补齐 `tool_name` / `call_id` 字段，schema 与前端同步加，
  确保三处仍然对齐。

## 前端回放（收尾）

复用现有 `reduceMultiAgentStream` 状态机做历史回放：

- 详情页改为优先拉取 `events` 序列并按 `sequence` 回放；摘要仅做兜底。
- 时间线支持子任务 Step / Tool 条目，`answerDeltas` 拼接展示。
- 缺失字段显示“后端未提供”，不补造时间、来源或回答。
- 仅展示后端公开字段；历史回放与实时 SSE 共用同一套 MDA 解析器。

## 边界与安全

- 所有文本在写库前已走 `sanitize_public_text` 与长度上限。
- `error_code` 只能来自 `PUBLIC_ERROR_CODES` 白名单。
- 每条事件 payload 是 `to_public_dict()`，天然不含原始 Prompt、API Key、
  Provider 响应或堆栈。
- 事件条数要有上限保护（如单 run 事件数 / payload 尺寸上限），防止
  `answer_delta` 无限增长拖垮存储。
- 事件写入失败不影响编排终态与 SSE 语义（best-effort）。

## 验收标准

后端：

- `ruff format --check .`、`ruff check .`、`mypy app tests`、全量 `pytest` 全绿。
- `GET /runs/{run_id}/events` 能回放出 `answer_delta` 与 Step / Tool 事件；
  跨租户 404；无 DB 503。
- `INTEGRATION_TEST=1` 下事件落库幂等迁移与写入可重复执行。

前端：

- `npm run format:check` / `lint` / `typecheck` / `test` / `build` 全绿。
- 历史详情能按事件回放，含子任务 Step / Tool 展示，不伪造数据。

## 测试计划

- `tests/test_multi_agent_events.py`：新增 Step / Tool 事件映射与白名单。
- `tests/test_multi_agent_stream_api.py`：新增 Step / Tool 事件出现在 SSE。
- `tests/test_multi_agent_records.py`：事件落库、回放接口、跨租户 404、容量上限。
- `tests/test_multi_agent_events_replay.py`（新）：事件持久化 + 回放 + 幂等迁移。
- 前端：`reducer.test.ts` 覆盖 Step / Tool / 回放；`adapter.test.ts` 覆盖事件
  到详情的换算。

## 文件级改造点

- `app/db/models.py`：`MultiAgentRunEventTable`
- `app/db/init.py`：注册新表 + `migrate_multi_agent_run_events_schema`
- `app/multi_agent/events.py`：`MultiAgentEventKind` 增 Step / Tool 事件；
  `MultiAgentEvent` 增 `tool_name` / `call_id`
- `app/multi_agent/orchestrator.py`：`_SubtaskAnswerDeltaObserver` →
  通用子任务事件转发器
- `app/services/multi_agent_run_record_service.py`：事件写入 `save_event` /
  `list_events`
- `app/api/multi_agent.py`：`GET /runs/{run_id}/events` 端点
- `app/schemas/multi_agent.py`：事件 schema 增字段 / 增回放响应
- `app/core/container.py`：事件记录库 Provider（无 engine → None 保持一致）
- `frontend/src/multiagent/`：types / reducer / adapter / Panel 支持 Step-Tool 回放

## 决策点（待确认）

1. Step / Tool 事件是否本轮一并落库，还是只先落 `answer_delta`？我建议一并做，
   因为数据模型与转发器一起设计成本最低，拆两轮反而要多改一次表结构。
2. 事件条数上限取多少？建议先给一个保守值（如单 run 1000 条或 2MB payload），
   后续可按需调整。
3. 回放接口是否分页？我建议先内置 `limit` 且默认返回全部，前端分页后补。
