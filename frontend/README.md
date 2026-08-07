# AI Platform Mini Frontend

基于 Vite + React + TypeScript 的 AI 应用平台前端。当前包含平台概览、Chat SSE、Agent Run、RAG 知识库、Prompt Studio、模型目录和管理员审计入口；在保留阶段 2—5 能力的基础上，接入真实 Agent SSE、实时 Trace、Tool/RAG 状态和错误恢复边界。

## HR 演示重点

- **Chat SSE**：普通对话，不执行工具调用，不展示 Agent Tool Trace 或 RAG 来源。
- **Agent Run**：真实模型决策、工具调用、RAG 状态、回答增量和终态均来自后端事件。
- **知识库问答**：从知识库页面进入时自动切换到 Agent Run，并启用受限 `RAG Agent preset`，先检索再回答。
- **真实失败语义**：无相关来源、预算超限、超时、取消、鉴权失败和健康检查失败分别展示，不把失败状态改写成成功。
- **长内容布局**：桌面端保留左侧平台导航与会话管理区，会话与 Trace 随内容自然增长，页面整体滚动；移动端保持单列自然滚动。
- **会话管理入口**：用户 API Key 与新建会话位于左侧导航下方，右侧仅保留对话工作台、会话与 Agent Trace。
- **服务端会话记忆**：Chat/Agent 客户端发送 `thread_id`，从响应或 SSE 事件回填当前线程；线程 id 存入 `sessionStorage`，刷新后调用 `GET /api/v1/conversations/{thread_id}/messages` 恢复历史并显示。恢复失败分两种：临时失败（网络、5xx、429 等）保留线程继续提问；`404 CONVERSATION_NOT_FOUND` 表示线程已失效，前端清空线程并在下一次提问时新建。`memory` 存储在后端重启后会丢失历史，跨重启恢复需设置 `CONVERSATION_STORAGE=postgres`；新建或清空会话时清空。
- **历史会话列表**：左侧栏展示当前租户的过往会话（按最近更新排序），点击即可恢复消息并继续提问；新建会话不会删除历史记录。

## 阶段 3 已完成

### 两种真实请求模式

- **普通 Chat SSE 模式**：继续调用 `POST /v1/chat/completions?stream=true`，解析 `choices[0].delta.content` 并按增量显示回答。
- **Agent Run 模式**：调用 `POST /api/v1/agent/runs/stream`，按真实 SSE 事件增量更新左侧回答与右侧 Trace；同步 `POST /api/v1/agent/runs` 客户端仍保留兼容。
- Agent final answer 支持真实文本 `answer_delta`：每个 `delta` 来自显式 Agent final-answer `ChatService.chat_stream()` 的 provider chunk，前端按真实事件增量显示；普通 Chat SSE 行为保持不变。`assistant_message` 作为 legacy/非 streaming 兼容事件保留，也用于 Runtime 直接以工具结果完成（例如 calculator 捷径）时传递完整真实回答，不由前端拆分或伪造。

### Agent Trace 与状态

- 展示后端真实 `run_id`、`status`、`stop_reason`、`steps`、`events` 和 `usage`；后端没有返回 Run ID 时不生成替代值。
- Agent SSE 可展示后端真实累计 Token；只有同步 JSON 提供 `prompt_tokens`/`completion_tokens` 分项时才展示分项，不补造缺失数据。
- 支持 `completed`、`stopped`、`failed`、`cancelled` 和 `timed_out` 等后端终态，并展示前端同步请求的运行中状态。
- 浏览器主动中止请求时只显示“前端已停止等待，后端终态未知”，不会声称后端 Runtime 已取消。
- 步骤按后端索引稳定排序，相同步骤索引和相同事件摘要会去重。
- 步骤卡片展示序号、决策类型、状态、工具名称和安全摘要，可通过键盘操作的按钮展开或收起。
- 公开响应不包含事件时间戳或耗时，所以开始时间、完成时间与耗时显示“后端未提供”，不会用 `0` 或本地假时间代替。
- Token 只在 `usage` 对应字段非空时显示实际值；缺失值显示“后端未提供”。

### Tool Call 卡片

- 支持 `calculator`、`knowledge_search`、MCP 风格工具（`mcp__server__tool`）和未知工具名称。
- MCP 工具名称在 `frontend/src/agent/tool-name.ts` 的 `MCP_TOOL_NAME_MAP` 中维护精确展示名；未映射且格式明确的名称显示为 `MCP 工具：tool（server）`；只有前缀的 `mcp__` 显示通用标签 `MCP 工具`；`mcp__server__`、`mcp__a`、`mcp__a__b__c`、`mcp__my__server__search` 等无法明确解析出 server/tool 的名称保留原始名，避免误判。
- 可表达工具成功、失败、超时、取消和未知状态；同步请求等待期间不会伪造具体工具正在执行。
- 工具卡片只展示后端安全投影：`calculator` 可展示脱敏后的 expression/result，`knowledge_search` 隐藏原始 query、展示 RAG 状态与来源，未知工具只展示参数数量和结果字符数；原始 payload、调用耗时和详细错误仍显示“后端未提供”。
- 工具摘要可展开或收起；长文本会安全截断，并清理 API Key、Bearer Token、内部路径和常见堆栈行。
- HTTP、网络和异常响应使用用户可理解的固定错误信息，不展示 Provider 原始响应、Python 堆栈、内部路径、API Key 或模型思维链。

### 交互与回归保护

- 支持停止当前请求、失败后重新运行、新建会话和清空当前会话。
- 请求身份校验避免停止、清空或新建会话后的旧请求更新当前界面。
- 窄屏下会话区与 Trace 区改为单列，Trace 内容允许安全换行，不横向溢出。
- 保留阶段 2 的 Chat SSE 请求、增量合并、断连分类、Request ID 和取消行为测试。
- 新增测试覆盖空 Trace、calculator 成功/失败/超时/取消、未知工具、重复步骤/事件去重、长摘要截断、异常响应、展开/收起、回答与 Trace 状态一致、本地取消和重新运行。

## 阶段 4 已完成

- **同步 Agent Run RAG 来源**：在对应的 `knowledge_search` Tool Call 卡片内读取公开契约 `steps[].tool_calls[].rag.references`，保持 `stepIndex` 与 `callId` 关联，不展示回答级全局来源。
- **真实来源字段**：来源卡片只展示后端实际提供的稳定文档/分块标识、分块序号、片段摘要和 distance；字段缺失时显示“后端未提供”，不会生成文档名称、URL、rank、引用编号或其他推断字段。
- **空与错误状态**：区分来源缺失、无相关来源、知识库为空、RAG 服务不可用、Embedding 失败、输出不可用和其他失败；服务故障不会伪装成无相关来源。
- **安全展示**：来源片段遵循后端截断边界，过长或 `truncated=true` 时显示安全提示；warning 作为不可信参考提示展示，所有来源内容使用普通文本渲染，不执行 HTML 或来源中的指令。
- **能力边界**：RAG 来源是不可信参考材料，不等同于回答内精确引用，也不表示模型对某个来源做出了可验证的精确引用。Agent Run 的实时路径使用 Agent SSE，但不承诺回答内精确引用或持久化 Trace 查询。

## 阶段 5 已完成

### 可访问性与状态播报

- 输入框、发送/运行、停止、新建会话、清空会话和失败重试均使用语义化控件，可通过键盘操作；文本输入保留多行 Enter，使用 `Ctrl/⌘ + Enter` 发送或运行。
- Step、Tool Call 和 RAG 来源均提供独立 disclosure；按钮使用 `aria-expanded`、`aria-controls` 和包含对象名称及展开/收起状态的动态 accessible name，折叠目标持续存在并通过 `hidden` 控制可见性。
- 使用单独、低频的 live region 播报 Chat 开始/完成/停止/SSE 断连、Agent 开始/完成/失败/超时/前端停止等待、RAG 有来源/无来源/不可用和重试开始；Chat SSE 的每个增量只更新视觉内容，不触发单独播报。
- 状态同时通过文字、结构和状态标签表达，不依赖颜色；焦点轮廓保持可见，辅助文字、状态 badge 和错误提示在窄屏下仍可读。

### 复制、错误与恢复

- Request ID 和真实 Run ID 提供明确的复制按钮名称；复制成功、Clipboard API 不可用或复制被拒绝时均有可理解的反馈。
- Chat 后端错误、网络失败和 SSE 断连提供安全、可操作的重试文案；Agent 失败、超时、取消、网络错误和响应错误明确区分，并保留已有回答与 Trace。
- 重试会清理上一 Agent Run 的回答、Trace、来源和错误；旧请求的晚到回调不会污染新 Run 或新会话；页面不展示 Provider 原始响应、堆栈、内部路径或 API Key。
- RAG 来源支持独立展开/收起，来源内容沿用阶段 4 的安全投影和截断边界，不复制未展示的敏感字段。

### 响应式目标与验证

- 响应式目标宽度为 **320px、375px、768px、1024px 和 1440px**；长回答、Run ID、call ID、chunk ID、Tool 摘要和 RAG 内容允许换行或安全截断，操作区和触摸目标保持可用。
- 已运行前端五项门禁：`npm run format:check`、`npm run lint`、`npm run typecheck`、`npm test -- --run`、`npm run build`；当前结果为 **13 个测试文件、141 个测试全部通过**。
- 已在真实浏览器验证 320/375/768/1024/1440 五档无横向溢出，并核对静态页面文案和 Agent 模式展示。该回归不等同于键盘焦点或屏幕阅读器验证。
- 开发期 Vite proxy 已提供同源真实后端边界；本轮真实浏览器已通过代理验证 Agent `answer_delta` 增量、实时 Trace、calculator 两步真实 Tool Call、停止等待后显示“后端终态未知”、offline 后显示 `connection_lost`、恢复网络后重试成功，以及 `Shift+Enter` 多行和 `Ctrl+Enter` 运行。

## 阶段 6 实时 Agent Run

- 事件顺序为 `run_started`、每个 Step 的 `step_started`/Tool/RAG/`step_completed`、按 `sequence` 排序的 `answer_delta`（或完整 `assistant_message`）、最后一个终止事件；公开事件带真实 `run_id`、`request_id` 和 `sequence`，Tool 事件带 `step_index`、`call_id`、`tool_name`。`answer_delta.delta` 只来自真实 provider chunk，Runtime 同时累计完整答案。
- 支持 `run_completed`、`run_failed`、`run_timed_out`、`run_cancelled` 和 `run_stopped`；同一 Run 只接受一个终止状态。未知、重复、乱序或缺失字段事件安全降级。
- 前端状态覆盖 connecting、running、waiting、tool running/completed/failed、RAG loading/completed、completed、failed、timeout、cancelled、connection lost 和 response format error；状态不只依赖颜色。
- 前端 Abort/停止等待不等于后端取消，只有收到真实 `run_cancelled` 才显示后端取消；网络断连和 `stream_error` 分别表示连接/启动边界，不伪造 Run 终态。
- RAG 仅显示 `knowledge_search` 的 loading、真实安全来源和真实失败状态；不显示 Prompt、工具输入输出、Provider 原始响应、堆栈、密钥、时间、耗时或假 Token。空回答流不会生成补充文本；Provider 错误、超时或取消只展示相应真实错误/终止状态，已知增量不会被伪装成完整成功回答。

### 阶段 6 真实浏览器验证边界

- 本轮通过开发期 Vite proxy 完成真实 Agent SSE 浏览器验证；proxy 使用 Node 进程环境变量 `AI_PLATFORM_DEV_API_BASE_URL` 和 `AI_PLATFORM_DEV_API_KEY`，key 只由 Node proxy 注入后端请求，不进入浏览器 bundle 或 `import.meta.env`。
- 已验证真实增量回答、实时 Trace、两步 calculator Tool Call、停止等待与后端终态未知、网络断开后的 `connection_lost`、恢复网络后的重试，以及键盘多行和运行快捷键。
- `npm run a11y:smoke` 已使用真实 Chromium、Vite proxy 和真实后端 Agent/RAG 路径通过：初始空态与真实 Agent/RAG 状态均为 axe `violations=0`；初始空态另有 1 个 `incomplete` color-contrast（`.emptyIcon` 内容过短无法判断），不是 violation，不能写成 axe 完全无 incomplete。4 个 disclosure 的 `aria-expanded`/`aria-controls`/`hidden` 关系、键盘 Space 后焦点保持、live region 非逐字播报和 320px 无横向溢出均通过。完整 VoiceOver/NVDA/Orca 仍未验证。
- Ollama 已安装 `nomic-embed-text`，真实 `/api/embed` 返回 1 个 768 维向量。真实 PostgreSQL/pgvector 空库的 Agent SSE 浏览器路径为 `RAG loading` → `knowledge_base_empty` → `run_completed`；使用仓库已有 `docs/superpowers/specs/2026-08-04-agent-runtime-design.md` 真实 ingest 53 个 chunks 后，浏览器真实来源路径显示 `success_with_sources` 和 5 条来源，展示的安全投影字段包括 `document_id`、`chunk_id`、`chunk_index`、`distance` 和 `content`。该次 UI Run 后续因 `token_budget_exceeded` 停止。直接真实 SSE 请求使用 `token_budget=8192`、`max_steps=3`，收到 `rag_started`、`tool_completed`（`success_with_sources`，5 条 refs）、多个 `answer_delta`，最后收到唯一 `run_timed_out`（`deadline_exceeded`）；这条请求不能记录为 `run_completed`。当前默认 `RAG_ENABLED=false`，上述验证使用显式启用的真实本地依赖；来源仅来自后端安全事件，前端没有拼接或伪造来源。
- 320/375/768/1024/1440 五档均无横向溢出。浏览器 DOM 语义、键盘焦点、ARIA 和 live region 已验证；完整屏幕阅读器仍未完成，因为当前环境没有可用的 VoiceOver、NVDA 或 Orca，不能把浏览器语义回归等同于辅助技术验收。阶段 7 未进入，当前等待人工 Code Review。

## 尚未实现或不在阶段 6 范围

- **事件时间与步骤耗时**：当前公开 API 未提供，前端不推算或伪造。
- **详细工具载荷和错误**：前端不读取 Provider 或内部 Runtime 原始对象；公开 UI 只展示后端安全投影，原始 payload、Prompt、Provider 响应和堆栈不公开。
- 精确 Token 统计/usage、事件历史回放、持久化 Trace 查询、回答内精确引用、完整 MCP 工具管理/配置 UI 和复杂多 Agent 编排不在阶段 6 范围；`answer_delta` 是真实文本增量，不等同于逐 Token 计数。前端只负责 `mcp__` 风格工具名称的友好展示，不改变后端原始工具名。
- 启动失败的 `stream_error` 可以缺少 `run_id`/`sequence`；前端解析器会将缺失字段归一化，并由客户端归类为 `AgentNetworkError`。该帧只表示流启动或连接边界错误，不代表 Agent Run 已进入任何终态。

详细事件字段和边界见 [Agent SSE 事件契约](../docs/superpowers/specs/2026-08-05-agent-sse-event-contract.md)。

## 鉴权与跨源运行时边界

后端 Chat 和 Agent API 使用 Bearer API Key。开发期 `npm run dev` 会启用 Vite dev-only proxy：同源 `/api` 和 `/v1` 请求会转发到 Node 进程环境变量 `AI_PLATFORM_DEV_API_BASE_URL`，未设置时默认 `http://127.0.0.1:8000`。如果 Node 进程环境变量 `AI_PLATFORM_DEV_API_KEY` 非空，proxy 会在转发到后端时注入 `Authorization: Bearer ...`。

开发启动示例：

```bash
AI_PLATFORM_DEV_API_BASE_URL=http://127.0.0.1:8000 \
AI_PLATFORM_DEV_API_KEY=sk-your-dev-key \
npm run dev
```

这些变量刻意不使用 `VITE_` 前缀，只由 Vite 的 Node 进程读取，不通过 `import.meta.env`、源码默认值、runtime config 或生产构建暴露给浏览器。普通 Chat SSE 和 Agent SSE 都走标准 Vite proxy 路径，保持流式响应，不在前端代码中改写 SSE 传输。

前端不把 API Key 写入源码、Git、默认配置或编译产物。生产入口可以在 bundle 加载前注入运行时配置：

```html
<script>
  window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
    apiBaseUrl: 'http://localhost:8000',
    apiKey: '<runtime-injected-key>',
  }
</script>
```

运行时注入不改变浏览器暴露 Bearer Key 的安全边界。生产构建不启用 Vite dev proxy，生产入口仍保持运行时配置不变；高权限长期密钥应由同源 BFF、服务端代理或其他受控鉴权边界持有。浏览器跨源直连时除了有效的 `Authorization: Bearer ...`，仍需要后端允许前端 Origin。

`App` 支持注入 `chatClient` 和 `agentClient` 作为测试边界；真实应用使用 `window.__AI_PLATFORM_RUNTIME_CONFIG__`。未配置 `apiBaseUrl` 时，前端使用同源 `/v1/chat/completions` 与 `/api/v1/agent/runs`。

## Scripts

- `npm run dev`：启动 Vite 开发服务器。
- `npm run build`：执行 TypeScript 构建并生成生产包。
- `npm run lint`：运行 Oxlint。
- `npm run format:check`：使用 Prettier 检查格式。
- `npm run typecheck`：运行 TypeScript project references 检查。
- `npm run test`：运行 Vitest。
- `npm run preview`：预览生产构建。

## 产品化平台页面

当前前端默认进入 AI Platform Mini 平台概览，页面通过左侧导航连接以下真实或明确标注边界的能力：

- **平台概览**：展示 API Gateway、模型 Provider、Agent Runtime、RAG 状态和四条快速演示路径。
- **对话工作台**：继续使用真实 Chat SSE / Agent SSE，展示 Agent Trace、Tool Call、RAG 来源、Request ID 和错误恢复。
- **Prompt Studio**：编辑四个内置模板，保存到浏览器 `localStorage`，并将演示问题带入对话工作台。
- **模型目录**：调用 `/api/v1/models` 读取真实模型，不展示后端尚未实现的启动、停止、删除按钮。
- **管理员后台**：复用现有管理员 Key、Token 用量和 Agent Run 审计流程。

如果没有 API Key，平台会显示需要配置的状态，不会把未验证的模型、请求次数或延迟写成真实指标。生产环境仍建议通过同源 BFF 或服务端代理持有高权限密钥。

#### 本轮学习总结

这次改版的关键不是堆叠虚假功能，而是把已有真实的 SSE、Agent Trace、RAG 和模型接口组织成清晰的产品演示路径。平台壳层与 Console 解耦后，页面导航不会干扰请求生命周期，Prompt Studio 也可以在不扩展后端的前提下体现提示词工程能力。模型目录只展示 `/api/v1/models` 的真实结果，避免演示时把不存在的模型启停能力说成已完成。

### 知识库页面

平台导航中的“知识库”页面现在支持真实 PDF 入库：用户可以选择或拖拽 PDF，前端调用 `POST /api/v1/rag/documents`，展示提取文本、生成 Embedding、写入 pgvector 的明确流程状态，并通过 `GET /api/v1/rag/documents` 列出已索引文档。页面只展示安全元数据，不展示文档原文、向量或未经处理的后端异常；入库完成后可以一键带入知识库问答场景。

知识库页面会在 API Key 缺失或 `RAG_ENABLED=false` 时禁用上传；上传后轮询真实 queued/processing/completed/failed 任务状态，完成后刷新列表，并支持所属 Key 的文本预览和删除。页面不保存原始 PDF，只展示有界提取文本和安全元数据；“去知识库问答”会进入 Agent Run 并启用受限的 `RAG Agent preset`，确保先调用 `knowledge_search` 再回答。

## RAG readiness、模式隔离与页面布局

- 前端启动时读取 `/api/v1/ready` 的真实 `rag` 状态，区分 RAG 未启用、数据库不可用、Embedding 不可用、健康检查失败和知识库为空。
- Chat SSE 只负责普通对话；Agent Run 才展示 Tool Call、RAG 状态和 Trace。知识库入口不会误用普通 Chat SSE。
- `RAG Agent preset` 由服务端约束先检索再回答；`no_relevant_sources` 显示真实 0 来源，不把模型一般知识包装成知识库答案。
- 桌面端使用内容驱动高度，会话区与 Trace 区随内容自然增长，由页面整体滚动；移动端恢复单列自然滚动。
