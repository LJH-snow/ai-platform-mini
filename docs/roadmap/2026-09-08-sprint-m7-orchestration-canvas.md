# Sprint M7 设计文档：多 Agent 编排画布与可视化编排

> 状态：设计中（2026-09-08）。
> 关联：M3 事件模型与 SSE、M4 真实 AgentRuntime 执行、M5 answer_delta 透传、
> M6 事件级持久化与全量回放。

## 背景与动机

M3-M6 已把多 Agent 编排从"可演示"推进到"可执行、可观察、可回放"：

- M3：SSE 生命周期事件流、`multi_agent_run_records` 摘要持久化。
- M4：子任务真正走 `AgentRuntime`，支持 Tool/RAG。
- M5：子任务 `answer_delta` 透传到多 Agent SSE。
- M6：事件级持久化、Step/Tool 全量回放。

但目前存在一个明确的断层：**用户只能"写 prompt 跑"，无法"画流程跑"**。

- `Supervisor` 用 LLM 自动分解任务，用户无法直接干预 DAG 结构。
- `OrchestrationConfig` 的 `agent_configs`、`failure_policy`、`max_concurrency`
  等参数只能通过代码或 API 直接调用，没有可视化配置入口。
- M3/M4/M5/M6 连续四次把"编排画布拖拽编辑"列为非目标，
  现在执行层、事件层、持久层全部打牢，画布只是这些能力的前端具象化。

本 Sprint 的目标是把 M3-M6 积累的编排能力"交还给用户"——
用可视化 DAG 编辑器设计多 Agent 工作流，保存为可复用模板，
从画布直接发起 run 并实时观察执行。

## 目标 / 非目标

### 目标

1. 可视化 DAG 编辑器：拖拽创建子任务节点，连线定义依赖关系。
2. 节点级配置：每个节点可设 agent_role、model、system_prompt、
   max_steps、token_budget、可用工具。
3. 编排级配置：failure_policy、concurrency、total_timeout、total_token_budget。
4. 配置持久化：保存为命名模板，支持列表/加载/删除/导出/导入。
5. 画布直连运行：从画布发起 run，实时 SSE 可视化执行进度。
6. 全程复用现有事件模型、安全投影与租户隔离，不引入新协议。

### 非目标

- 跨进程任务队列 / worker 恢复（留给后续 Sprint）。
- 运行时拖拽修改正在执行的编排（画布只用于设计阶段）。
- 协作编辑 / 多人实时同步。
- 自动布局算法优化（先用手动拖拽，后续可引入 dagre 等）。
- 改变现有 SSE 语义、终态判定或取消语义。

## 现状核对（P0）

进入画布开发前，先做一次编排参数的"可配置性审计"：

| 参数 | 当前来源 | 画布暴露方式 |
|------|----------|--------------|
| subtasks (id/description/role/depends_on) | Supervisor LLM 分解 | 画布节点拖拽编辑 |
| agent_configs[role].model | 默认 / 代码指定 | 节点配置面板 |
| agent_configs[role].system_prompt | 默认 / 代码指定 | 节点配置面板 |
| agent_configs[role].max_steps | 默认 / 代码指定 | 节点配置面板 |
| agent_configs[role].token_budget | 默认 / 代码指定 | 节点配置面板 |
| failure_policy | OrchestrationConfig 默认 | 编排级配置面板 |
| max_concurrency | OrchestrationConfig 默认 | 编排级配置面板 |
| total_timeout | OrchestrationConfig 默认 | 编排级配置面板 |
| total_token_budget | OrchestrationConfig 默认 | 编排级配置面板 |

校验动作：

- 确认 `OrchestrationConfig` 能完整承载画布产出的所有参数。
- 确认 `MultiAgentService.run()` 能接受"跳过 Supervisor 分解、直接使用画布 DAG"的模式。
- 确认现有 SSE 事件模型能区分"Supervisor 分解"和"画布直接运行"两种来源。
- 将核对结论写入 `findings.md`，修复差异后再进入 P1。

## 数据模型

### 新增 `multi_agent_configs` 表

一行一个命名编排配置（画布产出的 DAG + 参数）：

```text
id              uuid PK
workspace_id    varchar 租户隔离键（复用 run_records 的 owner_scope 语义）
name            varchar 配置名称（同 workspace 内唯一）
description     varchar nullable 配置描述
version         int 默认 1，每次保存 +1
dag_json        jsonb NOT NULL 画布 DAG 定义（节点 + 连线 + 配置）
orchestration_config jsonb NOT NULL OrchestrationConfig 序列化
created_at      timestamptz
updated_at      timestamptz
created_by      varchar nullable
```

`dag_json` 结构：

```json
{
  "nodes": [
    {
      "id": "task_1",
      "role": "research",
      "description": "Search for latest AI trends",
      "config": {
        "model": "gpt-4o",
        "system_prompt": "...",
        "max_steps": 5,
        "token_budget": 4000
      },
      "position": { "x": 120, "y": 80 }
    }
  ],
  "edges": [
    { "id": "e1", "source": "task_1", "target": "task_2" }
  ]
}
```

设计要点：

- `workspace_id` 与 `multi_agent_run_records` 同源，保证租户隔离一致。
- `dag_json` 只存画布结构，运行时由服务层转为 `SupervisorDecision`。
- `orchestration_config` 存编排级参数，运行时合并到 `OrchestrationConfig`。
- 不放强外键到 run_records，保持"无 engine → 跳过"行为。

### 幂等迁移

仿 `migrate_multi_agent_run_records_schema` 新增
`migrate_multi_agent_configs_schema`，注册到 `app/db/init.py` 的 `init_db`。

## 后端 API

### 配置 CRUD

```
POST   /api/v1/multi-agent/configs        创建配置
GET    /api/v1/multi-agent/configs        列表（按 workspace 隔离）
GET    /api/v1/multi-agent/configs/{id}   详情
PUT    /api/v1/multi-agent/configs/{id}   更新（版本 +1）
DELETE /api/v1/multi-agent/configs/{id}   删除
POST   /api/v1/multi-agent/configs/{id}/export  导出 JSON
POST   /api/v1/multi-agent/configs/import       导入 JSON
```

- 所有端点按 workspace 隔离，跨租户或不存在返回 404。
- 无 DB 时返回 503，与历史接口一致。
- `name` 同 workspace 内唯一，冲突返回 409。

### 从配置发起运行

扩展现有 `POST /api/v1/multi-agent/runs/stream`：

```json
{
  "config_id": "uuid",
  "user_input": "用户任务描述"
}
```

服务层行为：

- 有 `config_id` 时，从 `multi_agent_configs` 加载 DAG，跳过 Supervisor 分解，
  直接构造 `SupervisorDecision` 送入 Orchestrator。
- 无 `config_id` 时，保持现有 Supervisor 分解行为（向后兼容）。
- 事件模型新增 `run_source: "supervisor" | "config"` 字段，便于回放区分来源。

### 配置历史运行

```
GET /api/v1/multi-agent/configs/{id}/runs
```

返回使用该配置发起的所有 run 列表（按 workspace 隔离）。

## 服务层改造

### `MultiAgentConfigService`

新增 `app/services/multi_agent_config_service.py`：

- `create_config / list_configs / get_config / update_config / delete_config`
- `export_config / import_config`
- `list_config_runs`
- 租户隔离复用 `get_run` 的 owner 判定逻辑。

### `MultiAgentService.run()` 扩展

```python
async def run(
    self,
    user_input: str,
    *,
    config_id: str | None = None,  # 新增：从画布配置加载
    config: MultiAgentConfig | None = None,  # 新增：预加载的配置对象
    ...
) -> OrchestrationResult:
```

- 有 `config_id` 时，从 DB 加载配置，转为 `SupervisorDecision`。
- 有 `config` 时直接使用（服务层内部调用场景）。
- 两者都无时，走现有 Supervisor 分解路径。

### `SupervisorDecision` 工厂

新增 `app/multi_agent/decision_factory.py`：

- `from_config(config: MultiAgentConfig) -> SupervisorDecision`
- 把 `dag_json` 的 nodes/edges 转为 `Subtask` 列表。
- 把 `orchestration_config` 合并到运行时的 `OrchestrationConfig`。

## 前端画布

### 技术选型

- **React Flow**（`@xyflow/react`）— 成熟的 React DAG 编辑库，
  支持拖拽、缩放、自定义节点、连线验证。
- 不引入 dagre 等自动布局（首轮手动拖拽，后续 Sprint 可加）。

### 组件结构

```
CanvasPage.tsx              — 画布页面入口
├── Canvas.tsx              — React Flow 画布容器
├── nodes/
│   ├── AgentNode.tsx       — 自定义节点（角色图标 + 名称 + 状态）
│   └── AgentNodeHandle.tsx — 连线 handle
├── panels/
│   ├── NodeConfigPanel.tsx — 节点配置侧边栏
│   ├── OrchestrationPanel.tsx — 编排级配置
│   └── ConfigListPanel.tsx — 已保存配置列表
├── hooks/
│   ├── useCanvasState.ts   — 画布状态管理
│   └── useRunFromCanvas.ts — 从画布发起运行
└── utils/
    ├── dagToConfig.ts      — 画布 → API 请求
    └── configToDag.ts      — API 响应 → 画布
```

### 交互流程

1. **新建画布** — 空白画布，从节点面板拖入 Agent 节点。
2. **配置节点** — 点击节点打开配置面板，设 role/model/prompt/steps/budget。
3. **连线** — 从输出 handle 拖到输入 handle 定义依赖（自动校验无环）。
4. **编排配置** — 右侧面板设 failure_policy/concurrency/timeout/budget。
5. **保存** — 命名后保存到 `multi_agent_configs`。
6. **运行** — 点击"运行"按钮，输入 user_input，发起 SSE 实时执行。
7. **观察** — 画布节点实时显示执行状态（pending/running/completed/failed），
   连线高亮显示数据流。

### 节点状态可视化

| 状态 | 节点样式 |
|------|----------|
| pending | 灰色边框 |
| running | 蓝色脉冲动画 |
| completed | 绿色边框 + 对勾 |
| failed | 红色边框 + 叉号 |
| cancelled | 橙色边框 |

### 与现有 MultiAgentPanel 的关系

- `MultiAgentPanel` 保持为"快速运行"入口（写 prompt → Supervisor 分解）。
- `CanvasPage` 为"高级编排"入口（画 DAG → 直接运行）。
- 两者共享 SSE 连接逻辑和事件 reducer，只是运行发起方式不同。

## 边界与安全

- 画布产出的 DAG 在运行前做无环校验（拓扑排序检测）。
- 节点数上限（如 20 个），防止画布无限膨胀。
- 所有文本在走 `sanitize_public_text` 与长度上限。
- 配置导入做 JSON Schema 校验，拒绝非法结构。
- 租户隔离：配置只能被同 workspace 用户查看/运行/修改。
- 配置删除采用软删除或级联检查（若存在关联 run 则提示）。

## 验收标准

后端：

- `ruff format --check .`、`ruff check .`、`mypy app tests`、全量 `pytest` 全绿。
- 配置 CRUD 端点按 workspace 隔离，跨租户 404，无 DB 503。
- 从配置发起 run 时跳过 Supervisor，直接使用画布 DAG。
- 事件模型 `run_source` 字段正确区分来源。
- `INTEGRATION_TEST=1` 下配置持久化与运行可重复执行。

前端：

- `npm run format:check` / `lint` / `typecheck` / `test` / `build` 全绿。
- 画布支持拖拽创建节点、连线定义依赖、配置面板编辑。
- 从画布发起 run，节点实时显示执行状态。
- 配置保存/加载/导出/导入功能完整。

## 测试计划

- `tests/test_multi_agent_config_service.py`（新）：配置 CRUD、租户隔离、唯一性。
- `tests/test_multi_agent_config_api.py`（新）：API 端点、404/503/409 语义。
- `tests/test_multi_agent_decision_factory.py`（新）：dag_json → SupervisorDecision 转换。
- `tests/test_multi_agent_canvas_run.py`（新）：从配置发起 run、跳过 Supervisor、事件来源。
- `tests/test_multi_agent_events.py`：新增 `run_source` 字段投影。
- 前端：`Canvas.test.tsx` 覆盖拖拽/连线/配置；`dagConverter.test.ts` 覆盖双向转换。

## 文件级改造点

后端：

- `app/db/models.py`：`MultiAgentConfigTable`
- `app/db/init.py`：注册新表 + `migrate_multi_agent_configs_schema`
- `app/services/multi_agent_config_service.py`（新）
- `app/multi_agent/decision_factory.py`（新）
- `app/multi_agent/service.py`：`run()` 扩展 `config_id` / `config` 参数
- `app/multi_agent/events.py`：`MultiAgentEvent` 新增 `run_source` 字段
- `app/api/multi_agent.py`：配置 CRUD 端点 + runs 扩展
- `app/schemas/multi_agent.py`：配置 schema + 回放响应扩展
- `app/core/container.py`：配置服务 Provider

前端：

- `frontend/src/canvas/`（新目录）
  - `CanvasPage.tsx`
  - `Canvas.tsx`
  - `nodes/AgentNode.tsx`
  - `panels/NodeConfigPanel.tsx`
  - `panels/OrchestrationPanel.tsx`
  - `panels/ConfigListPanel.tsx`
  - `hooks/useCanvasState.ts`
  - `hooks/useRunFromCanvas.ts`
  - `utils/dagConverter.ts`
- `frontend/src/multiagent/`：复用 SSE/reducer 逻辑
- `frontend/src/App.tsx`：新增 `/canvas` 路由

## 决策点（待确认）

1. 画布运行是否支持"部分执行"（只运行选中节点子图）？
   建议首轮不做，先支持全量运行，后续可按需扩展。
2. 配置版本是否可回滚？
   建议首轮只保留最新版 + 导出备份，后续可做版本历史。
3. 画布节点是否支持"子编排"（嵌套另一个配置）？
   建议首轮不做，保持扁平 DAG，后续可做嵌套。
4. React Flow 是否引入自动布局？
   建议首轮手动拖拽，后续可引入 dagre 提升体验。
