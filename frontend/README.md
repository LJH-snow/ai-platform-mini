# AI Platform Mini Frontend

基于 Vite + React + TypeScript 的 Agent Console。当前完成阶段 3：保留普通 Chat SSE，并接入同步 Agent Run Trace 与工具调用卡片。

## 阶段 3 已完成

### 两种真实请求模式

- **普通 Chat SSE 模式**：继续调用 `POST /v1/chat/completions?stream=true`，解析 `choices[0].delta.content` 并按增量显示回答。
- **Agent Run 模式**：调用同步 `POST /api/v1/agent/runs`，请求结束后同时更新左侧最终回答与右侧 Trace。
- Agent Trace 明确标记为“完成后加载，非实时”；普通 Chat SSE 不会被伪装成 Agent Trace。

### Agent Trace 与状态

- 展示后端真实 `run_id`、`status`、`stop_reason`、`steps`、`events` 和 `usage`；后端没有返回 Run ID 时不生成替代值。
- 支持 `completed`、`stopped`、`failed`、`cancelled` 和 `timed_out` 等后端终态，并展示前端同步请求的运行中状态。
- 浏览器主动中止请求时只显示“前端已停止等待，后端终态未知”，不会声称后端 Runtime 已取消。
- 步骤按后端索引稳定排序，相同步骤索引和相同事件摘要会去重。
- 步骤卡片展示序号、决策类型、状态、工具名称和安全摘要，可通过键盘操作的按钮展开或收起。
- 公开响应不包含事件时间戳或耗时，所以开始时间、完成时间与耗时显示“后端未提供”，不会用 `0` 或本地假时间代替。
- Token 只在 `usage` 对应字段非空时显示实际值；缺失值显示“后端未提供”。

### Tool Call 卡片

- 支持 `calculator` 和未知工具名称。
- 可表达工具成功、失败、超时、取消和未知状态；同步请求等待期间不会伪造具体工具正在执行。
- 工具输入、输出、调用耗时和详细错误不在当前后端公开契约中，因此卡片明确显示“后端未提供”。
- 工具摘要可展开或收起；长文本安全工具会截断内容，并清理 API Key、Bearer Token、内部路径和常见堆栈行。
- HTTP、网络和异常响应使用用户可理解的固定错误信息，不展示 Provider 原始响应、Python 堆栈、内部路径、API Key 或模型思维链。

### 交互与回归保护

- 支持停止当前请求、失败后重新运行、新建会话和清空当前会话。
- 请求身份校验避免停止、清空或新建会话后的旧请求更新当前界面。
- 窄屏下会话区与 Trace 区改为单列，Trace 内容允许安全换行，不横向溢出。
- 保留阶段 2 的 Chat SSE 请求、增量合并、断连分类、Request ID 和取消行为测试。
- 新增测试覆盖空 Trace、calculator 成功/失败/超时/取消、未知工具、重复步骤/事件去重、长摘要截断、异常响应、展开/收起、回答与 Trace 状态一致、本地取消和重新运行。

## 尚未实现

- **Agent SSE / 实时 Trace 推送**：当前 Agent Run 是同步 JSON，只能在请求完成后加载完整 Trace。
- **事件时间与步骤耗时**：当前公开 API 未提供，前端不推算或伪造。
- **工具输入、输出和详细错误**：当前公开 API 未提供，前端不读取 Provider 或内部 Runtime 原始对象。
- **RAG 来源 UI**：阶段 4 尚未开始。
- 持久化 Trace 查询、实时 Token 更新和其他后续 Agent Console 能力尚未实现。

## 鉴权与跨源运行时边界

后端 Chat 和 Agent API 使用 Bearer API Key，但当前后端没有配置 `CORSMiddleware`，Vite 也没有默认 proxy。浏览器跨源直连时除了有效的 `Authorization: Bearer ...`，还需要后端允许前端 Origin。

前端不把 API Key 写入源码、Git、默认配置或编译产物。生产入口可以在 bundle 加载前注入运行时配置：

```html
<script>
  window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
    apiBaseUrl: 'http://localhost:8000',
    apiKey: '<runtime-injected-key>',
  }
</script>
```

运行时注入不改变浏览器暴露 Bearer Key 的安全边界。高权限长期密钥应由同源 BFF、服务端代理或其他受控鉴权边界持有；本阶段不修改后端，也不默认加入 Vite proxy。

`App` 支持注入 `chatClient` 和 `agentClient` 作为测试边界；真实应用使用 `window.__AI_PLATFORM_RUNTIME_CONFIG__`。未配置 `apiBaseUrl` 时，前端使用同源 `/v1/chat/completions` 与 `/api/v1/agent/runs`。

## Scripts

- `npm run dev`：启动 Vite 开发服务器。
- `npm run build`：执行 TypeScript 构建并生成生产包。
- `npm run lint`：运行 Oxlint。
- `npm run format:check`：使用 Prettier 检查格式。
- `npm run typecheck`：运行 TypeScript project references 检查。
- `npm run test`：运行 Vitest。
- `npm run preview`：预览生产构建。
