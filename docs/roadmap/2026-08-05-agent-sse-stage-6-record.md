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

## 验证记录

后端已通过 `ruff format --check .`、`ruff check .`、`mypy app tests` 和
`pytest -q`；集成测试按既有环境条件跳过。前端已通过 `npm run format:check`、
`npm run lint`、`npm run typecheck`、`npm test -- --run`（7 个测试文件、70 个测试）
和 `npm run build`。

浏览器五档回归未完成：当前环境没有可用浏览器二进制，因此没有声称已验证
320px、375px、768px、1024px、1440px 的真实布局、连接中断、键盘焦点或屏幕阅读器
行为。

## 已知限制与 Review 项

当前模型协议没有 token delta，`assistant_message` 是一次完整的真实回答，不是
逐 token 流。后端没有提供时间、耗时、Token 增量、工具载荷或 Provider 原始响应。
前端主动停止只停止等待，后端取消必须以真实 `run_cancelled` 为依据。

启动阶段 `stream_error` 可能缺少 `run_id`/`sequence`；前端解析器允许该启动阶段帧，
会将缺失字段归一化，并由客户端归类为 `AgentNetworkError`。它只表示流无法启动或
连接边界出错，不代表 Run 终态。另一个需要 Review 的架构决策是
Observer 与 Recorder 同时存在时的兼容方式，以及断连后是否需要独立的后台 Run 状态
查询；本阶段不引入持久化查询。

## 学习总结

复用 Runtime Observer 能让实时 Trace 与同步结果共享真实执行顺序，避免前端推断 Agent 状态。事件公开层必须严格投影字段，尤其是 RAG、错误和回答内容。模型没有 token delta 时，完整回答事件比伪造 token 增量更诚实。Abort、网络断连和后端取消是不同状态，必须由不同证据驱动。把持久化查询和复杂编排留在阶段 7 以后，可以控制本阶段的协议边界。
