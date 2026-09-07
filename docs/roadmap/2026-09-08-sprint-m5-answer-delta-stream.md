# Sprint M5：子任务 answer_delta 透传到多 Agent SSE

日期：2026-09-08
状态：已实现（已推送）

## 背景

M4 已让子任务真正走 `AgentService`/AgentRuntime，但多 Agent 的 SSE 目前只发布
`subtask_started` / `subtask_completed` 等编排级事件，子任务内部的增量回答
（`answer_delta`）没有透传到多 Agent 流。用户无法在同一个多 Agent 流里看到
Research/Writer 子任务“正在输出什么”，只能等子任务结束后拿到截断摘要。

M5 的目标是把子任务内部的 `answer_delta` 安全地透传到多 Agent SSE，保持 M3/M4
的脱敏、长度上限、错误码白名单和终止语义不变。

## 目标

- Orchestrator 在走 `AgentService` 的执行路径时，把子任务内部 `ANSWER_DELTA`
  事件转发为多 Agent 事件 `subtask_answer_delta`；
- 透传只发生在 SSE（有 observer）路径；同步 `/runs` 仍保持 M4 行为；
- 每个 delta 都是脱敏、长度有界的文本片段，附带 `task_id`、`agent_role`、
  `agent_event_kind`、`step_index`；
- delta 事件必须在同子任务的 `subtask_completed` 之前按序号单调发出；
- 前端把 `subtask_answer_delta` 纳入 SSE 解析，并在子任务时间线渲染增量文本；
- 不暴露工具参数、Provider 响应原文、原始 token 计数等内部字段。

## 设计

- 新增 `MultiAgentEventKind.SUBTASK_ANSWER_DELTA`；
- `MultiAgentEvent` 新增 `agent_event_kind`、`step_index` 字段，`to_public_dict`
  同步输出；
- `MultiAgentStreamEvent` 新增 `agent_event_kind`、`step_index` 字段；
- Orchestrator 内的 `_SubtaskAnswerDeltaObserver` 实现 `AgentEventObserver`：
  只转发 `ANSWER_DELTA`，异步调 `observer.on_event`，并在子任务完成前
  `drain()` 保证顺序；
- Orchestrator 只在 `agent_service` + observer 同时存在时开启
  `streaming=True`，让 AgentRuntime 产出真实 delta，且不改同步 fallback。

## 测试

- 事件投影包含新字段；
- Orchestrator 会把 `ANSWER_DELTA` 转发为 `subtask_answer_delta`，且在
  `subtask_completed` 之前；
- SSE 投影保留 `agent_event_kind` / `step_index`；
- 前端 stream parser 识别并校验 `subtask_answer_delta` 的字段；
- 前端 reducer 将 delta 追加到对应子任务的 `answerDeltas`；
- 前端 MultiAgentPanel 时间线渲染增量段落；
- 现有 M3/M4 事件、SSE、比较评测测试不回归。

## 非目标

- 子任务内部 Step/Tool 全量回放到多 Agent 流；
- 多 Agent 事件级持久化与回放；
- 跨进程队列、分布式执行；
- 编排画布。
