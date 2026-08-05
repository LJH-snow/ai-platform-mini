# 阶段 6：Agent SSE 与实时 Trace

## 目标

在复用现有 Agent Runtime 的前提下，交付真实后端 Agent SSE、前端实时 Trace、类型化事件契约、测试和文档；主代理只负责 Review、架构判断、验收和进度记录，生产代码由子代理修改。本轮收口包含提交和推送，完成后暂停等待用户 Code Review。

## 阶段

- [completed] 1. 阅读现有 Runtime、API、Chat SSE、Agent Trace、RAG 契约和测试，判断是否能安全支持真实实时事件
- [completed] 2. 子代理实现后端 Agent SSE 契约、Runtime 事件桥接和后端测试（限定后端文件）
- [completed] 3. Review 后端变更并运行后端质量门禁，确认事件顺序、终止语义、取消/断连边界和敏感字段清洗
- [completed] 4. 子代理实现前端 SSE 消费、实时 reducer/状态机、Trace 增量 UI 和前端测试（限定 frontend 文件）
- [completed] 5. Review 前端变更并运行前端质量门禁（7 个测试文件、79 个测试全部通过）；完成五档浏览器回归，并保留键盘焦点/屏幕阅读器未完成完整浏览器级验证的限制
- [completed] 6. 更新事件契约相关阶段文档、前端 README、根 README、进度、发现和学习总结；补录 Vite proxy 下的真实浏览器 Agent SSE 验证与 RAG 未验证边界（限定文档文件）
- [completed] 7. 阶段 7 功能未进入：已完成全量验收、Git 提交、工作区检查和推送；当前暂停等待用户 Code Review

## 约束与决策门

- 不创建临时工程，不复制 Agent 执行逻辑，不伪造事件或数据。
- 每个子代理必须先阅读现有代码、README、Runtime、Chat SSE、Agent Trace 和测试。
- 多子代理必须使用互斥文件写入范围；主代理不修改生产代码。
- 若 Runtime 无法安全产生真实事件，则报告 blocked，不用定时器或拆 JSON 冒充实时。
- 同步 `POST /api/v1/agent/runs`、普通 Chat SSE、阶段 3-5 行为必须保持兼容。
- 阶段 7 的精确引用、MCP UI、持久化 Trace 查询、复杂多 Agent 编排不在范围内。
- `answer_delta` 是真实文本增量，不等同于精确 Token 统计/usage；事件历史回放也不在阶段 6 范围内。
- 真实浏览器通过开发期 Vite proxy 验证 Agent `answer_delta`、实时 Trace、calculator 两步真实 Tool Call、停止等待后的后端终态未知、offline 后 `connection_lost`、恢复网络后的重试、`Shift+Enter` 多行和 `Ctrl+Enter` 运行；同时验证 320/375/768/1024/1440 无横向溢出。
- 默认 `RAG_ENABLED=false` 且没有可用 PostgreSQL/RAG 服务，RAG 真实浏览器不纳入已完成证据；RAG 安全投影和状态仅由后端/组件测试覆盖，不能伪造来源。键盘焦点与屏幕阅读器未完成完整浏览器级验证。
- 阶段完成口径：阶段 6 的 Agent SSE/Trace/Tool/恢复路径已完成验证，RAG 真实浏览器和完整辅助技术验证保持未完成；已提交并推送，当前等待用户 Code Review，不进入阶段 7。

## 错误记录

| 错误 | 尝试 | 处理 |
|---|---:|---|
| codebase-memory MCP 工具未在当前会话暴露 | 1 | 回退到 `rg` 和定向文件阅读，未影响代码审查 |
