# 阶段 6 进度

## 2026-08-06 后续收口

- 已完成 RAG readiness 契约：`/api/v1/ready` 返回真实 RAG、数据库和 Embedding 状态，前端知识库页面按状态机展示，不再用缺失 runtime config 误判 RAG 关闭。
- 已完成 Agent budget 修复：前后端默认 `token_budget=8192`、`max_steps=4`、`timeout_seconds=60`，SSE 展示真实累计 Token；预算超限仍保留真实终态，不伪造回答。
- 已完成 Chat/Agent/RAG 模式隔离：知识库入口进入 Agent Run 并使用受限 `rag` preset，普通 Chat SSE 不执行工具调用；长会话桌面端使用独立滚动区域，移动端保持单列布局。
- 真实浏览器验证覆盖 Chat 模式、calculator Agent、RAG preset 来源路径、页面滚动和 375px 移动端无横向溢出；`no_relevant_sources` 由测试覆盖，完整屏幕阅读器仍未验证。

## 2026-08-05

- 已读取项目协作规则、规划 skill 和子代理治理规则。
- 已确认当前分支与工作区状态，确认阶段 5 的同步 Agent 基线。
- 已创建阶段计划、发现记录和进度记录。
- 已完成后端 Runtime/API 与前端 Trace/SSE 定向阅读，确认可安全复用真实 Observer 事件；Agent final answer 通过显式 `ChatService.chat_stream()` provider chunks 支持真实文本 `answer_delta`。
- 架构核验完成：Runtime 负责稳定排序、累计完整答案和取消/超时边界；`answer_delta` 不是精确 Token 计数，不能伪造 usage、时间或耗时。
- 已委托后端子代理 Pascal，写入范围限定为后端 Runtime/service/API/schema 与后端测试；等待其完成后由主代理 Review。
- 后端子代理已完成 Agent SSE、Runtime Step/RAG Observer 事件、安全投影、取消边界和测试；后端门禁通过。
- 前端子代理已完成 Agent SSE 解析、实时 reducer、Trace 增量更新、状态区分和测试；阶段 6 初始前端门禁通过（当时为 7 个测试文件、79 个测试；后续收口已扩展为 13 个文件、141 个测试）。
- 文档子任务已完成：新增 Agent SSE 事件契约和阶段 6 开发记录，更新根 README、前端 README 与计划记录。
- 开发期已加入 Vite dev-only proxy：`AI_PLATFORM_DEV_API_BASE_URL` 配置后端地址，`AI_PLATFORM_DEV_API_KEY` 只由 Node proxy 注入 `Authorization`，两个变量不进入浏览器 bundle 或生产构建。
- 真实浏览器已通过代理验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待并显示“后端终态未知”、offline 后 `connection_lost`、恢复网络后重试成功，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行；Trace 步骤按钮已验证 `aria-expanded`/`aria-controls`，点击后焦点保持并正确展开，普通键盘输入与提交也已验证。
- 已在真实浏览器验证 320/375/768/1024/1440 五档无横向溢出，并核对 Agent 模式展示；完整屏幕阅读器验证仍未完成。
- 刚完成 `npm run a11y:smoke`：使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过；初始空态与真实 Agent/RAG 状态 axe `violations=0`，初始空态另有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断），不是 violation，不能写成 axe 完全无 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、键盘 Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出均通过；完整 VoiceOver/NVDA/Orca 仍未验证。
- RAG 真实浏览器证据已补齐：Ollama 已安装 `nomic-embed-text`，真实 `/api/embed` 返回 1 个 768 维向量；PostgreSQL/pgvector 空库查询的 Agent SSE 路径为 `RAG loading` → `knowledge_base_empty` → `run_completed`。使用仓库已有 `docs/superpowers/specs/2026-08-04-agent-runtime-design.md` 真实 ingest 53 个 chunks 后，浏览器来源路径显示 `success_with_sources` 和 5 条真实来源，展示 `document_id`、`chunk_id`、`chunk_index`、`distance`、`content` 安全投影；该次 UI Run 后续因 `token_budget_exceeded` 停止。直接真实 SSE 请求使用 `token_budget=8192`、`max_steps=3`，收到 `rag_started`、`tool_completed`（`success_with_sources`，5 条 refs）、多个 `answer_delta`，并以唯一 `run_timed_out`（`deadline_exceeded`）终止，不能写成 `run_completed`。RAG 安全投影仍只来自后端真实事件，不能伪造引用。启动阶段 `stream_error` 可缺少 `run_id`/`sequence`，前端解析器已兼容并由客户端归类为 `AgentNetworkError`；该帧不代表 Run 终态。
- `assistant_message` 仅作为 legacy/非 streaming 兼容事件；空流不生成补充回答，Provider 错误、超时和取消保留真实终态。精确 Token 统计/usage、事件历史回放、持久化 Trace 查询、回答内精确引用、MCP UI 和复杂多 Agent 编排不在阶段 6 范围。
- 阶段完成审计：阶段 6 的真实 Agent SSE、Trace、Tool Call、停止等待、断网/恢复、键盘输入/提交、Trace disclosure 语义、五档布局、RAG 空库和 RAG 成功来源证据已齐；成功来源那次 UI Run 的真实终态是 `token_budget_exceeded` 停止，独立 SSE 验证的真实终态是唯一 `run_timed_out(deadline_exceeded)`，没有把它们改写成 `run_completed`。完整屏幕阅读器验证仍未完成，因为当前环境没有 VoiceOver/NVDA/Orca；浏览器 DOM、键盘、ARIA、live region 和五档响应式已验证。本轮只更新指定文档，不提交 Git，保留阶段 7 未进入和等待用户 Code Review。


## 2026-08-31 恢复会话收口

- 已恢复当前工作区：`main` 相对 `origin/main` ahead 2，发现多 Agent 后端提交已完成但 README/学习总结/架构图仍需收口，且存在未跟踪 `docs/architecture/`。
- 已修复恢复时发现的质量门禁问题：`app/multi_agent/supervisor.py` fallback 缩进经 `ruff format` 归一化；`tests/test_quota.py` 月配额 retry-after 断言改为月末安全范围，避免 2026-08-31 仅剩不足一天时失败。
- 已补齐 README、开发日志和 Agent Runtime roadmap：记录长期记忆、多 Agent 编排、公开 API、未完成边界和 Sprint M2 学习总结。
- 已完成最后一轮 CI/E2E 收口：Playwright 配置改为显式绑定 backend/frontend 端口并用 URL readiness 检查，避免 webServer 在 GitHub Actions 中误判为未就绪；最新推送的 CI 四个 job 已全部通过。
- 已用 Archify 更新总体架构图并重新 `deliver`：HTML artifact sha256 `00f9b50f8c8d8b049a3bec02d6dcdba43d8d0f037f40a23c92d29e7c5a529af0`，spec sha256 `63cd401c9d7e8315af6bdd10750cd6abc2ded4b2fe972f2da50a85d88d27fe9d`。
- 验证通过：`ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest`（1009 passed, 39 skipped）；Archify showcase validation 9/9 通过，visual-check 1440×900/1600×1000/1920×1080/2048×1320 明暗主题无溢出，视觉审阅仍标记为 pending。
