# 阶段 6 发现

## 当前基线

- 当前分支：`codex/sprint-7-multi-llm-gateway`；本轮开始前工作区已有阶段 6 文档、前端实现和 Vite 配置变更，本轮只继续修改指定文档，不回退既有变更。
- 阶段 6 已新增 Agent SSE；真实浏览器已通过开发期 Vite proxy 验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待后的后端终态未知、offline 后 `connection_lost`、恢复网络后的重试，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行。
- 真实浏览器已验证 320/375/768/1024/1440 五档无横向溢出，并核对 Agent 模式展示；Trace 步骤按钮已验证 `aria-expanded`/`aria-controls`，点击后保持按钮焦点并正确展开，普通键盘输入与提交路径也已验证；完整屏幕阅读器验证尚未完成。
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
- RAG 真实浏览器未验证：当前默认 `RAG_ENABLED=false`。Docker Desktop 虽可启动，但 Compose 拉取 Ollama 镜像超过 1.5GB 后停止，未形成可用的 PostgreSQL/pgvector+embedding 服务。RAG 安全投影和状态由后端/组件测试覆盖，但这些测试不能提供真实来源，不能伪造引用或把测试结果记为浏览器端来源。
- 前端 Abort/停止等待不等于后端取消；只有真实 `run_cancelled` 才能确认后端取消，网络断连或启动阶段 `stream_error` 均不代表 Run 终态。
- `stream_error` 启动失败帧仅包含 `event` 和 `error_code` 也可以成立，因为 Runtime 可能尚未启动；前端解析器允许缺失 `run_id`/`sequence`，将字段归一化后由客户端归类为 `AgentNetworkError`。该帧不代表 Run 终态。
- 同步 Agent API 已有安全 RAG 投影 `_to_rag_summary()` 和安全错误映射；SSE 必须复用同一投影边界，不应把 `ToolResult.content` 或模型消息直接发给客户端。
- 当前 `AgentEvent` 的 `message` 可能包含用户输入、模型回答或内部错误，不能直接序列化到 SSE；必须按事件类型做最小字段投影和敏感清洗。

## 本轮阶段完成审计

- 阶段 6 已具备真实浏览器 Agent SSE 的有效证据：增量回答、实时 Trace、两步 calculator Tool Call、停止等待语义、断网/恢复重试、普通键盘输入/提交、Trace disclosure 的 ARIA 语义与焦点保持，以及五档布局回归。
- 未完成项必须保持原样标注：RAG 真实浏览器路径因默认开关和基础设施不可用未验证；Docker Compose 未形成可用 PostgreSQL/pgvector+embedding 服务；完整屏幕阅读器验证尚未完成；回答内精确引用、持久化 Trace、事件回放和精确 usage 不在本阶段完成。
- 本轮完成开发代理与错误重试修复，验收记录已补录；已有 commits `a810254` 和 `73c4d3d` 已提交并推送，本轮文档变更待提交/推送，等待用户 Code Review。
