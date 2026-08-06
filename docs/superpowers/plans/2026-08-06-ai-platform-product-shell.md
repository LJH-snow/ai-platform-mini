# AI Platform Mini Product Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已有真实 Chat/Agent/RAG/Admin 能力包装成可供 HR 演示的 AI Platform Mini 产品化前端。

**Architecture:** 在现有 React App 外增加轻量平台壳层和三个纯前端页面组件。Console 继续由 App 持有请求状态与 SSE 生命周期；Dashboard 和模型目录通过最小的 models client 读取真实 `/api/v1/models`；Prompt Studio 只负责 localStorage 模板并通过回调向 Console 注入草稿。

**Tech Stack:** React 19, TypeScript, Vite, CSS, Vitest, Testing Library, Playwright screenshot smoke.

---

### Task 1: 建立平台数据边界

**Files:**
- Create: `frontend/src/platform/types.ts`
- Create: `frontend/src/platform/client.ts`
- Test: `frontend/src/platform/client.test.ts`

- [ ] 为模型目录定义 `PlatformModel`、`ModelsResponse` 和安全错误类型，客户端只暴露模型 id 与 provider。
- [ ] 实现 `createPlatformClient({ apiBaseUrl, apiKey, fetchImpl })`，请求 `/api/v1/models`，对网络错误、401/403、5xx 返回用户可读错误。
- [ ] 为成功响应、认证失败和网络失败补充单元测试。
- [ ] 运行 `npm run test -- src/platform/client.test.ts`。

### Task 2: 创建概览、模型目录和 Prompt Studio 页面

**Files:**
- Create: `frontend/src/platform/Dashboard.tsx`
- Create: `frontend/src/platform/ModelCatalog.tsx`
- Create: `frontend/src/platform/PromptStudio.tsx`
- Create: `frontend/src/platform/platform.css`
- Test: `frontend/src/platform/platform.test.tsx`

- [ ] Dashboard 接收 `modelCount`、`modelName`、`apiKeyConfigured`、`ragEnabled`、`onNavigate`，展示真实状态和四个快速入口。
- [ ] ModelCatalog 调用平台客户端，展示加载、成功、无 Key、网络失败和空模型列表状态。
- [ ] PromptStudio 提供四个模板，支持编辑、保存、恢复默认和带入 Console；模板内容保存在 localStorage。
- [ ] 补充页面交互测试，验证 Dashboard 导航回调、Prompt 保存/带入和模型加载错误边界。
- [ ] 运行 `npm run test -- src/platform/platform.test.tsx`。

### Task 3: 将平台壳层接入 App

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/AgentTrace.test.tsx`

- [ ] 将页面状态扩展为 `dashboard | console | prompts | models | admin`，默认进入 Dashboard。
- [ ] 增加响应式侧边导航和移动端顶部导航；导航按钮带有当前页面 `aria-current`。
- [ ] 保留 Console 现有测试需要的可访问名称和请求状态行为，并增加快速提示词填充回调。
- [ ] AdminDashboard 通过平台壳层进入，返回时回到 Dashboard。
- [ ] 更新现有测试，使需要 Console 的用例先点击“智能对话”入口；新增默认 Dashboard 和页面导航断言。
- [ ] 运行完整前端测试、类型检查、lint 和格式检查。

### Task 4: 浏览器视觉与响应式验证

**Files:**
- Modify: `frontend/README.md`
- Modify: `README.md`

- [ ] 启动 Vite，使用 Playwright 检查 Dashboard、Console、Prompt Studio、Model Catalog 四个页面。
- [ ] 验证桌面首屏、375px 移动宽度、导航切换、Prompt 带入和无 Key 状态。
- [ ] 记录截图和验证结果，不将临时截图写入仓库。
- [ ] README 增加平台页面和 HR 演示顺序。
- [ ] 运行项目要求的 Python 验证命令以及全部前端验证命令。
