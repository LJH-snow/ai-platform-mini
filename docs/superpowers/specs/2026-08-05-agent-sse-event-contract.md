# Agent SSE 事件契约

> 阶段 6，2026-08-05

## 范围

本契约新增 `POST /api/v1/agent/runs/stream`。它复用现有
`AgentRuntime`、`AgentService`、鉴权、限流、配额和 Request ID 边界；不复制一套
Agent 执行循环，也不改变同步 `POST /api/v1/agent/runs` 或普通 Chat SSE。

SSE 使用标准帧：

```text
event: <event-name>
data: <one JSON object>

```

除启动阶段的 `stream_error` 外，公开事件均来自 Runtime Observer。Runtime 内部的
`model_decision` 不会发送到客户端。

## 公共字段

正常 Run 事件包含：

| 字段 | 含义 |
| --- | --- |
| `event` | 事件名称，由 SSE `event:` 行和 JSON 中的 `event` 同时表达 |
| `run_id` | 当前 Run 的真实稳定标识 |
| `request_id` | Middleware 生成的 Request ID；可能为 `null` |
| `sequence` | Runtime 事件序号；事件在 Observer 和 SSE 中按 FIFO 顺序发送 |
| `step_index` | 1 起始的 Step 序号；仅 Step/Tool/Answer 相关事件提供 |
| `call_id` | 模型真实 Tool Call ID；仅 Tool 事件提供 |
| `tool_name` | 真实工具名；不包含工具参数 |
| `status` | Run 或 Step 的公开状态 |
| `stop_reason` | 终止原因；只在终止事件出现 |
| `answer` | `assistant_message` 的完整真实回答 |
| `succeeded` | Tool 是否成功 |
| `error_code` | 允许列表中的工具或 RAG 错误码 |
| `rag` | `knowledge_search` 的安全 RAG 投影 |

字段缺失或为 `null` 时，客户端必须按事件语义安全降级。服务端不会把 Prompt、
原始 Tool 输入/输出、Provider 原始响应、堆栈、内部路径、API Key 或模型内部决策
写入 SSE。

## 事件生命周期

在正常执行中，顺序是 `run_started`，随后每个 Step 的
`step_started`、零个或多个 Tool 事件、`step_completed`，必要时在 Step 之间继续
下一个 Step；有回答时发送 `assistant_message`，最后发送且只发送一个终止事件。
`sequence` 是稳定的 Runtime 顺序，不代表时间、耗时或 Token 增量。

| 事件 | 真实来源与负载 |
| --- | --- |
| `run_started` | Runtime 开始；有 `run_id`、`request_id`、`sequence` |
| `step_started` | Runtime 开始一个真实 Step；有 `step_index` |
| `tool_started` | Runtime 开始真实工具调用；有 `step_index`、`call_id`、`tool_name` |
| `rag_started` | `knowledge_search` 的 `tool_started` 安全投影；`rag.status` 为 `loading`，来源为空 |
| `tool_completed` | 真实工具成功完成；有 Tool 关联字段和 `succeeded: true` |
| `tool_failed` | 真实工具失败；有安全 `error_code`，不含原始错误详情 |
| `step_completed` | Runtime 完成真实 Step；有 `step_index` |
| `assistant_message` | 模型提供的一次完整回答；不是 token delta，不拆分、不补造 Token 数 |
| `run_completed` | `status=completed` 的真实终止事件 |
| `run_failed` | `status=failed` 的真实终止事件 |
| `run_timed_out` | `status=timed_out` 的真实终止事件 |
| `run_cancelled` | Runtime 确认外部取消后的真实终止事件 |
| `run_stopped` | 真实停止但不属于上述四类的终止事件，例如达到最大步数或 Token budget |
| `stream_error` | SSE 启动阶段错误帧；不代表 Runtime 已经产生 Run 终态 |

终止事件由 Runtime 的单次 `run_stopped` 映射而来，每个 Run 最多一个。Runtime 在
异常、超时或取消收尾时会补齐已开始但未完成的 `step_completed`，再发送终止事件。

当前实现的 `stream_error` 最小负载为：

```json
{"event":"stream_error","error_code":"stream_setup_failed"}
```

它可能没有 `run_id` 或 `sequence`，因为 Runtime 可能尚未启动。前端解析器允许这种
启动阶段帧，缺失字段会归一化为内部安全值，客户端随后将其归类为
`AgentNetworkError`。普通 Run 事件仍要求真实的 `run_id` 和非负 `sequence`。
`stream_error` 只表示流无法启动或连接边界出错，不代表 Agent Run 已产生任何终态，
也不能替代 `run_failed`、`run_timed_out` 或 `run_cancelled`。

## RAG 投影

`knowledge_search` 只复用同步 Agent API 已确认的安全投影。`rag_started` 表示真实
工具已经开始，状态为 `loading`；完成或失败事件携带 `rag` 时，状态可以是：

- `success_with_sources`
- `no_relevant_sources`
- `knowledge_base_empty`
- `rag_unavailable`
- `embedding_failed`
- `output_unavailable`
- `failed`

来源只包含安全边界内的 `document_id`、`chunk_id`、`chunk_index`、截断后的
`content`、真实 `distance` 和 `truncated`。服务不可用、空知识库、无相关结果、
Embedding 失败、输出截断或输出格式错误分别保留真实可区分状态；没有来源时不生成
文档名、URL、rank、引用编号或其他推断数据。检索内容仍是不可信参考材料。

## 取消、断连和错误

- 前端 `AbortController` 或“停止等待”只停止前端消费；除非收到真实
  `run_cancelled`，不得显示后端已取消。
- 服务端检测客户端断开后设置 Runtime 的 `cancel_event`。如果 Runtime 在断开前
  已发出终止事件，客户端可以按已收到的终止事件显示；断开本身不伪造终止事件。
- 网络中断显示 `connection_lost`，响应格式错误显示 `response_format_error`，
  二者都不等于 `failed` 或 `cancelled`。
- `stream_error` 只说明流无法启动，不说明 Agent 已运行、失败或取消。

## 不在阶段 6 范围

阶段 6 不实现回答内精确引用、MCP UI、持久化 Trace 查询、复杂多 Agent 编排、
真实 token 级回答流、事件历史回放或断线后的 Run 状态查询。后端没有提供时间、
耗时、Token 增量、工具参数、工具原始输出和 Provider 原始响应，前后端均不伪造这些
数据。
