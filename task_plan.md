# 阶段 6：Agent SSE 与实时 Trace

## 目标

在复用现有 Agent Runtime 的前提下，交付真实后端 Agent SSE、前端实时 Trace、类型化事件契约、测试和文档；主代理只负责 Review、架构判断、验收和进度记录，生产代码由子代理修改。本轮已补齐真实 RAG 空库/成功来源和 SSE 超时证据，当前只更新指定文档、不提交 Git，暂停等待用户 Code Review；阶段 7 未进入。

## 阶段

- [completed] 1. 阅读现有 Runtime、API、Chat SSE、Agent Trace、RAG 契约和测试，判断是否能安全支持真实实时事件
- [completed] 2. 子代理实现后端 Agent SSE 契约、Runtime 事件桥接和后端测试（限定后端文件）
- [completed] 3. Review 后端变更并运行后端质量门禁，确认事件顺序、终止语义、取消/断连边界和敏感字段清洗
- [completed] 4. 子代理实现前端 SSE 消费、实时 reducer/状态机、Trace 增量 UI 和前端测试（限定 frontend 文件）
- [completed] 5. Review 前端变更并运行前端质量门禁（7 个测试文件、79 个测试全部通过）；完成五档浏览器回归、Trace 步骤按钮 `aria-expanded`/`aria-controls`、点击后焦点保持与正确展开、普通键盘输入/提交验证，以及真实 `npm run a11y:smoke`（含 4 个 disclosure、Space 焦点、live region 和 320px 溢出），并保留完整屏幕阅读器验证未完成的限制
- [completed] 6. 更新事件契约相关阶段文档、前端 README、根 README、进度、发现和学习总结；补录真实 embedding、空知识库、53 个 chunks ingest、5 条安全来源、回答增量和超时终止证据（限定文档文件）
- [completed] 7. 阶段 7 功能未进入：RAG 成功来源和空知识库浏览器路径已验证；成功来源 UI Run 的真实终态为 `token_budget_exceeded` 停止，独立 SSE 的真实终态为唯一 `run_timed_out(deadline_exceeded)`；完整屏幕阅读器验证仍受环境限制，浏览器 DOM/键盘/ARIA/live-region/五档响应式已验证；本轮文档不提交 Git，当前暂停等待用户 Code Review

## 约束与决策门

- 不创建临时工程，不复制 Agent 执行逻辑，不伪造事件或数据。
- 每个子代理必须先阅读现有代码、README、Runtime、Chat SSE、Agent Trace 和测试。
- 多子代理必须使用互斥文件写入范围；主代理不修改生产代码。
- 若 Runtime 无法安全产生真实事件，则报告 blocked，不用定时器或拆 JSON 冒充实时。
- 同步 `POST /api/v1/agent/runs`、普通 Chat SSE、阶段 3-5 行为必须保持兼容。
- 阶段 7 的精确引用、MCP UI、持久化 Trace 查询、复杂多 Agent 编排不在范围内。
- `answer_delta` 是真实文本增量，不等同于精确 Token 统计/usage；事件历史回放也不在阶段 6 范围内。
- 真实浏览器通过开发期 Vite proxy 验证 Agent `answer_delta`、实时 Trace、calculator 两步真实 Tool Call、停止等待后的后端终态未知、offline 后 `connection_lost`、恢复网络后的重试、`Shift+Enter` 多行和 `Ctrl+Enter` 运行；Trace 步骤按钮验证了 `aria-expanded`/`aria-controls`、点击后焦点保持和正确展开，普通键盘输入/提交也已验证；同时验证 320/375/768/1024/1440 无横向溢出。
- `npm run a11y:smoke` 使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过：初始空态与真实 Agent/RAG 状态 axe `violations=0`；初始空态另有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断），不是 violation，不能写成 axe 完全无 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、键盘 Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出均通过；完整 VoiceOver/NVDA/Orca 仍未验证。
- RAG 真实验证证据：`nomic-embed-text` 的 `/api/embed` 返回 1 个 768 维向量；空库浏览器 Agent SSE 为 `RAG loading` → `knowledge_base_empty` → `run_completed`；真实 ingest `docs/superpowers/specs/2026-08-04-agent-runtime-design.md` 得到 53 个 chunks，浏览器来源路径显示 `success_with_sources` 和 5 条安全来源。成功来源 UI Run 因 `token_budget_exceeded` 停止；直接 SSE（`token_budget=8192`、`max_steps=3`）收到 `rag_started`、`tool_completed`（5 条 refs）、多个 `answer_delta`，唯一终态为 `run_timed_out(deadline_exceeded)`。
- 阶段完成口径：阶段 6 的 Agent SSE/Trace/Tool/恢复路径、普通键盘输入/提交、Trace disclosure 浏览器语义、RAG 空库/成功来源和真实终止语义已完成验证；完整屏幕阅读器验证保持未完成，因环境没有可用 VoiceOver/NVDA/Orca。浏览器 DOM、键盘、ARIA、live-region 和五档响应式已验证；本轮只更新文档、不提交 Git，当前等待用户 Code Review，不进入阶段 7。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| codebase-memory MCP 工具未在当前会话暴露 | 1 | 回退到 `rg` 和定向文件阅读，未影响代码审查 |

## 2026-08-06 文档收口

- 对照当前代码补齐 HR 展示向 README、前端 README、`progress.md` 和 `findings.md`：明确 Agent Runtime、RAG preset、Chat/Agent 模式隔离、RAG readiness、预算默认值、实时累计 Token 和独立滚动布局。
- 修正过时描述：`calculator` 允许展示脱敏摘要，`knowledge_search` 只展示 RAG 状态与安全来源，未知工具只展示计数；原始 payload、Prompt、Provider 响应和详细内部错误仍不公开。
- 当前提交范围只包含本轮已 Review 的实现、测试和文档；`.env`、`output/`、`demo/`、`.headroom/`、`.tokensave/`、`CLAUDE.md` 等临时或辅助文件不纳入提交。
