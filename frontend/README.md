# AI Platform Mini Frontend

基于官方 Vite + React + TypeScript 脚手架的 Agent Console。当前完成阶段 2：普通 Chat SSE 对话闭环。

## 阶段 2已完成

- 消息列表、用户输入框、发送按钮和普通回答空状态。
- 真实调用 `POST /v1/chat/completions?stream=true`。
- 请求体发送 OpenAI-compatible `messages` 与 `stream: true`。
- 解析 `data: {JSON}\n\n`，合并 `choices[0].delta.content`，以 `data: [DONE]` 标记完成。
- 发送期间禁用重复提交，并支持 AbortController 停止当前请求。
- 清晰区分已完成、已停止、后端错误、网络失败和 SSE 在完成标记前中断。
- 展示响应头 `X-Request-ID`，支持复制；后端未提供 Run ID，因此前端不生成或伪造 Run ID。
- 支持新建会话和清空当前会话。
- 右侧明确显示 `Agent Trace/SSE 尚未接入`，不展示模拟 Trace、Tool Call 或 RAG 来源。
- 组件测试覆盖空状态、发送、增量合并、完成、重复提交、停止、错误/断连、新建和清空。

## 鉴权与跨源运行时边界

后端 Chat API 使用 Bearer API Key，但当前后端没有配置 `CORSMiddleware`，Vite 也没有默认 proxy。因此，浏览器从前端开发服务器（例如 `http://localhost:5173`）直接访问后端（例如 `http://localhost:8000`）时，除了需要有效的 `Authorization: Bearer ...` 外，还需要后端允许该前端 Origin；仅设置 API Key 不能绕过浏览器 CORS。

前端不把 API Key 写入源码、Git、默认配置或编译产物。生产入口可在加载前端 bundle 之前注入运行时配置：

```html
<script>
  window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
    apiBaseUrl: 'http://localhost:8000',
    apiKey: '<runtime-injected-key>',
  }
</script>
```

该运行时注入方式只解决配置传递，不改变浏览器暴露 Bearer Key 的安全边界：浏览器端 Key 可被页面环境、扩展或用户开发者工具看到，不适合高权限长期密钥。若后端暂时不提供 CORS，推荐使用受控的同源反向代理/BFF，在代理侧处理鉴权和跨源边界；本阶段不修改后端，也不默认加入 Vite proxy 或注入任何 Key。

组件也支持通过 `App` 的 `chatClient` 属性注入测试客户端；真实应用使用 `window.__AI_PLATFORM_RUNTIME_CONFIG__`。未配置 `apiBaseUrl` 时，前端使用当前页面同源路径 `/v1/chat/completions`。

## Scripts

- `npm run dev` starts the Vite development server.
- `npm run build` type-checks and builds the production bundle.
- `npm run lint` runs Oxlint.
- `npm run format:check` checks formatting with Prettier.
- `npm run typecheck` runs TypeScript project references.
- `npm run test` runs Vitest.
- `npm run preview` serves the production build locally.
