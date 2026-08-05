# 阶段 6：Agent SSE 与实时 Trace 开发记录

## 目标与决策

阶段 6 将同步 Agent Run Console 扩展为实时观察界面。后端复用现有 Runtime 的
Observer 事件和 `AgentService`，通过独立的 `POST /api/v1/agent/runs/stream` 暴露
类型化 SSE；前端复用现有 `frontend/` 工程，通过 SSE 解析器和 reducer 增量更新
回答、Step、Tool Call、RAG 安全投影与终态。

Runtime 已能产生真实的有序事件，因此本阶段没有使用轮询、前端定时器或把一次性
JSON 拆成假事件。事件由单调 `sequence` 和真实 `run_id`/`request_id` 关联，终止
状态只由 Runtime 的一次 `run_stopped` 映射产生。

## 交付结果

- 后端增加 Agent SSE 路由、类型化公开投影、RAG loading/结果投影、终止语义和断连
  cancel_event 边界。
- 前端 Agent 模式改用 SSE，普通 Chat SSE 和同步 Agent API 保持兼容；reducer 拒绝
  重复/乱序/其他 Run 事件，并对未知事件和坏字段安全降级。
- 事件测试覆盖正常 Run、多 Step、calculator、knowledge_search、RAG 空/失败状态、
  Tool 失败、超时、取消、FIFO、单终止和敏感字段清洗；前端测试覆盖解析、去重、
  状态转换和错误恢复。
- 高频回答内容不进入逐字 live region；重要状态使用低频播报，Trace disclosure
  保持键盘和 `aria-expanded`/`aria-controls` 语义。
- 开发期增加 Vite dev-only proxy：`/api` 和 `/v1` 由 Node 进程转发到
  `AI_PLATFORM_DEV_API_BASE_URL`，并仅由 Node proxy 使用 `AI_PLATFORM_DEV_API_KEY`
  注入后端 `Authorization` header；两个变量不进入浏览器 bundle 或生产构建。

## 验证记录

后端已通过 `ruff format --check .`、`ruff check .`、`mypy app tests` 和
`pytest -q`；集成测试按既有环境条件跳过。前端已通过 `npm run format:check`、
`npm run lint`、`npm run typecheck`、`npm test -- --run`（7 个测试文件、79 个测试全部通过）
和 `npm run build`。

已在真实浏览器验证 320px、375px、768px、1024px、1440px 五档无横向溢出，
并核对 Agent 模式展示。通过开发期 Vite proxy 已完成真实后端浏览器端到端验证，
具体包括 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、
停止等待后显示“后端终态未知”、offline 后 `connection_lost`、恢复网络后重试成功，
以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行。Trace 步骤按钮已在真实浏览器核对
`aria-expanded`/`aria-controls`；点击后按钮焦点保持不变，Trace 内容正确展开。普通
键盘输入与提交路径也已验证。

RAG 失败状态已验证，成功来源/空知识库路径未验证：PostgreSQL/pgvector 已真实启动，
但 Ollama embedding 服务不可用。真实浏览器中的 Agent SSE 先显示“RAG 来源加载中”，
随后显示“来源暂不可用”，RAG 错误码为 `embedding_failed`，最终显示“RAG 未找到相关
来源”；后端日志确认真实调用了 `/api/embed` 并返回 404。这只证明真实 RAG
loading/unavailable/embedding_failed 路径，不证明有来源或空知识库成功检索。RAG 安全投影
和状态由后端/组件测试覆盖；测试覆盖不等同于真实浏览器来源，不能据此伪造来源或声称
回答包含精确引用。Trace 按钮的焦点保持和展开语义已完成浏览器验证，但完整屏幕阅读器
验证仍未完成，不能把语义化控件测试或静态回归等同于辅助技术验收。

开发期 proxy 的两个变量都不使用 `VITE_` 前缀，只由 Vite 的 Node 进程读取；普通 Chat
SSE 和 Agent SSE 都通过标准 Vite proxy 转发，以保留流式响应语义。生产构建不启用该
proxy，生产入口仍保持原有运行时配置和 Bearer Key 安全边界。

## 阶段完成审计口径

- 阶段 6 的“已完成”依据：后端/前端门禁通过，真实浏览器通过开发期 proxy 验证真实
  Agent SSE 的增量、Trace、Tool Call、停止等待、断网/恢复、普通键盘输入/提交和
  Trace disclosure 交互路径，包含 `aria-expanded`/`aria-controls`、点击后焦点保持及
  正确展开；五档视口均无横向溢出。
- 阶段 6 的 RAG 验证口径：RAG 失败状态已验证，成功来源/空知识库路径未验证。
  PostgreSQL/pgvector 已真实启动，但 Ollama embedding 服务不可用；真实浏览器显示“RAG
  来源加载中”后显示“来源暂不可用”，RAG 错误码为 `embedding_failed`，最终显示“RAG 未
  找到相关来源”，后端日志确认真实调用 `/api/embed` 并返回 404。这只证明真实 RAG
  loading/unavailable/embedding_failed 路径，不证明有来源或空知识库成功检索。完整屏幕
  阅读器验证仍未完成。
- 阶段 6 不宣称回答内精确引用、持久化 Trace 查询、事件回放、精确 usage、MCP UI 或
 复杂多 Agent 编排仍不在阶段 6 范围内。本轮已完成开发代理、重试修复、浏览器和测试验收，
  已有 commits `a810254` 和 `73c4d3d` 已提交并推送；本轮验收记录已补录，当前文档变更待提交/推送，等待用户 Code Review，不进入阶段 7。

## 已知限制与 Review 项

Agent final answer 已支持真实文本 `answer_delta`：增量来自显式
`ChatService.chat_stream()` 的 provider chunks，Runtime 按稳定 `sequence` 转发并累计
完整答案。`assistant_message` 仅保留为 legacy/非 streaming 兼容事件，同步 Agent API
仍保持非流式。空流不生成补充文本；Provider 错误、超时和取消保留真实终态，已知增量
可以保留但不会被改写为成功。后端不提供精确 Token 统计/usage、时间、耗时、工具载荷
或 Provider 原始响应，前端不补造这些数据。增量与完整回答都经过敏感字段清洗。前端
主动停止只停止等待，后端取消必须以真实 `run_cancelled` 为依据。

启动阶段 `stream_error` 可能缺少 `run_id`/`sequence`；前端解析器允许该启动阶段帧，
会将缺失字段归一化，并由客户端归类为 `AgentNetworkError`。它只表示流无法启动或
连接边界出错，不代表 Run 终态。另一个需要 Review 的架构决策是
Observer 与 Recorder 同时存在时的兼容方式，以及断连后是否需要独立的后台 Run 状态
查询；本阶段不引入持久化查询。

## 学习总结

复用 Runtime Observer 能让实时 Trace 与同步结果共享真实执行顺序，避免前端推断 Agent 状态。`answer_delta` 必须来自真实 provider chunk，不能从模型 JSON 或完整回答拆分。开发期 Vite proxy 说明真实浏览器验证还需要清晰的服务端鉴权边界：key 只在 Node proxy 注入，不能进入浏览器 bundle。事件公开层必须严格投影字段，尤其是 RAG、错误和回答内容；RAG 测试证据不能替代真实来源。Abort、网络断连和后端取消是不同状态，必须由不同证据驱动。精确 usage、事件回放、持久化查询、回答内精确引用、MCP UI、完整辅助技术验收和复杂编排留在后续阶段。
