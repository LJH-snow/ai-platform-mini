# M7 学习总结：多 Agent 编排画布

## 学到了什么

- ReactFlow (`@xyflow/react`) 的 `NodeTypes` 自定义节点、`applyNodeChanges`/`applyEdgeChanges` 状态管理模式
- DAG → SupervisorDecision 的转换逻辑：把画布节点映射为子任务、边映射为依赖关系
- SQLAlchemy 异步会话下的租户隔离查询模式（`or_` 组合 workspace_id 过滤）

## 为什么这样设计

- 把 DAG 配置持久化到 `MultiAgentConfigTable` 而非每次让用户重画，支持保存/复用/版本管理
- `DecisionFactory.from_dag()` 把画布产物直接转成 `SupervisorDecision`，复用已有 Orchestrator 执行链路，避免为画布单独写一套执行器
- `run_source` 字段区分 "canvas" 和 "supervisor" 两种来源，便于后续分析不同入口的使用模式

## 遇到了什么问题

- 前端 TypeScript 严格模式下，未使用的导入和 setter 会导致 `tsc -b` 失败（TS6133/TS6196）
- 后端 ruff 的 ANN401 规则禁止 `Any` 类型，测试 helper 函数返回 `SimpleNamespace` 需要改用 `object`
- `typing.object` 不存在，`object` 是内置类型不需要从 typing 导入

## 如何解决的

- 未使用的 state setter 前缀 `_` 标记（如 `_setConfigDescription`），未使用的参数改为 `_userInput`
- 测试 helper 返回类型从 `Any` 改为 `object`，移除 `from typing import object`（因为 object 是内置类型）
- 用 `ColumnElement[bool]` 标注 SQLAlchemy 过滤条件返回类型，避免 `Any`

## 关键文件

- `app/multi_agent/decision_factory.py` — DAG → SupervisorDecision 转换
- `app/services/multi_agent_config_service.py` — 配置 CRUD + 租户隔离
- `frontend/src/canvas/CanvasPage.tsx` — ReactFlow 画布主页面
- `frontend/src/canvas/AgentNode.tsx` — 自定义 Agent 节点组件
