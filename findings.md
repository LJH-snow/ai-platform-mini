# 阶段 6 发现

## 当前基线

- 当前分支：`codex/sprint-7-multi-llm-gateway`；本轮开始前工作区已有阶段 6 文档、前端实现和 Vite 配置变更，本轮只继续修改指定文档，不回退既有变更。
- 阶段 6 已新增 Agent SSE；真实浏览器已通过开发期 Vite proxy 验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待后的后端终态未知、offline 后 `connection_lost`、恢复网络后的重试，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行。
- 真实浏览器已验证 320/375/768/1024/1440 五档无横向溢出，并核对 Agent 模式展示；Trace 步骤按钮已验证 `aria-expanded`/`aria-controls`，点击后保持按钮焦点并正确展开，普通键盘输入与提交路径也已验证；完整屏幕阅读器验证尚未完成。
- 刚完成的 `npm run a11y:smoke` 使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过：初始空态与真实 Agent/RAG 状态 axe `violations=0`；初始空态另有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断），不是 violation，也不能写成 axe 完全无 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、键盘 Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出均通过；完整 VoiceOver/NVDA/Orca 仍未验证。
- 根 README 明确：RAG 公开投影只允许安全来源摘要，不能展示原始输入/输出、Prompt、Provider 响应、堆栈、密钥、内部路径或假引用。
- 项目要求 Python 3.12-3.14，默认 3.14；提交前需通过 Ruff、mypy、pytest 及前端五项门禁。

## 待读重点

- `app/agent/`、`app/api/`、`app/services/agent_service.py`、`app/runs/` 的事件/执行/取消能力。
- `frontend/src/agent/`、`frontend/src/AgentTrace*`、`frontend/src/App.tsx` 的适配层、状态和 UI。
- `tests/test_agent_runtime.py`、`tests/test_agent_api.py`、`tests/test_run_trace.py` 以及前端 Agent 测试。

## 架构核验结论

- `AgentRuntime.run()` 已有真实、单运行的 `AgentEvent` 序列：`run_started`、`model_decision`、`tool_started`、`tool_completed/tool_failed`、`answer`、`run_stopped`；事件带单调 sequence、UTC 时间、run_id 和 step_index。
- Runtime 已通过 `deadline`、`cancel_event` 和任务取消区分超时、外部取消和任务取消，工具执行也经过受控等待；适合在观察者边界接入 SSE，但必须保证 stream producer 的生命周期不泄漏。
- Agent final answer 已通过显式 `ChatService.chat_stream()` provider chunks 提供真实 `answer_delta`；Runtime 负责按稳定顺序转发并累计完整答案。`assistant_message` 仅为 legacy/非 streaming 兼容，不能由前端拆分模型 JSON 或完整回答伪造增量。空流、Provider 错误、超时和取消必须保留真实语义，精确 Token 统计/usage、回放、持久化查询、回答内精确引用、MCP UI 和复杂多 Agent 编排仍不在阶段 6 范围。
- 开发期 Vite proxy 使用 `AI_PLATFORM_DEV_API_BASE_URL` 和 `AI_PLATFORM_DEV_API_KEY`；key 只由 Node proxy 注入后端请求，不进入浏览器 bundle、`import.meta.env` 或生产构建。生产构建不启用该 proxy。
- RAG 真实浏览器验证已完成：Ollama `nomic-embed-text` 已安装，`/api/embed` 真实返回 1 个 768 维向量；PostgreSQL/pgvector 空库 Agent SSE 路径真实显示 `RAG loading` → `knowledge_base_empty` → `run_completed`。使用仓库已有 `docs/superpowers/specs/2026-08-04-agent-runtime-design.md` 真实 ingest 53 个 chunks 后，浏览器真实来源路径显示 `success_with_sources`、5 条真实来源及 `document_id`、`chunk_id`、`chunk_index`、`distance`、`content` 安全投影；该次 UI Run 后续真实因 `token_budget_exceeded` 停止。独立直接 SSE 请求以 `token_budget=8192`、`max_steps=3` 收到 `rag_started`、`tool_completed`（`success_with_sources`，5 条 refs）、多个 `answer_delta` 和唯一 `run_timed_out(deadline_exceeded)`，因此不能记为 `run_completed`。测试与浏览器证据均没有伪造来源或终态。
- 前端 Abort/停止等待不等于后端取消；只有真实 `run_cancelled` 才能确认后端取消，网络断连或启动阶段 `stream_error` 均不代表 Run 终态。
- `stream_error` 启动失败帧仅包含 `event` 和 `error_code` 也可以成立，因为 Runtime 可能尚未启动；前端解析器允许缺失 `run_id`/`sequence`，将字段归一化后由客户端归类为 `AgentNetworkError`。该帧不代表 Run 终态。
- 同步 Agent API 已有安全 RAG 投影 `_to_rag_summary()` 和安全错误映射；SSE 必须复用同一投影边界，不应把 `ToolResult.content` 或模型消息直接发给客户端。
- 当前 `AgentEvent` 的 `message` 可能包含用户输入、模型回答或内部错误，不能直接序列化到 SSE；必须按事件类型做最小字段投影和敏感清洗。

## 本轮阶段完成审计

- 阶段 6 已具备真实浏览器 Agent SSE 的有效证据：增量回答、实时 Trace、两步 calculator Tool Call、停止等待语义、断网/恢复重试、普通键盘输入/提交、Trace disclosure 的 ARIA 语义与焦点保持，以及五档布局回归。
- 未完成项必须保持准确标注：`npm run a11y:smoke` 的初始空态与真实 Agent/RAG 状态 axe `violations=0`，但初始空态有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断）；4 个 disclosure 的 ARIA/`hidden` 关系、Space 焦点保持、live region 非逐字播报和 320px 无横向溢出已通过。完整屏幕阅读器验证尚未完成，因为环境没有 VoiceOver/NVDA/Orca；浏览器 DOM、键盘、ARIA、live region 和五档响应式已验证。回答内精确引用、持久化 Trace、事件回放和精确 usage 不在本阶段完成；RAG 成功来源/空库浏览器路径已验证，但成功来源那次 UI Run 的终态是 `token_budget_exceeded`，独立 SSE 的终态是 `run_timed_out(deadline_exceeded)`，不能把后者写成 `run_completed`。
- 本轮只更新指定文档，不修改生产代码、测试或配置，不提交 Git；已有阶段 6 commits `a810254`、`73c4d3d` 和 `e5f3e00` 已提交并推送，继续等待用户 Code Review，不进入阶段 7。
