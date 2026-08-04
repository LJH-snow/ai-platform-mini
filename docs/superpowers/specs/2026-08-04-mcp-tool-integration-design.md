# Sprint 11 MCP Tool 接入设计说明

- 对应路线图：Sprint 11：MCP 工具接入
- 当前切片：MCP transport、工具发现、内部 Tool Adapter、权限授予和受控生命周期
- 状态：开发中，等待 Code Review

## 目标

在不修改 Agent Runtime 核心协议的前提下，将外部 MCP Server 暴露的只读工具映射为现有 `app.tools.protocols.Tool`。Agent 只看到统一的工具名称、描述和参数 Schema，不需要知道工具来自本地实现还是 MCP 进程。

## 本轮实现

### 1. stdio JSON-RPC Client

`MCPProcessClient` 管理一个 MCP Server 子进程，负责：

- `initialize` 握手和 `notifications/initialized` 通知；
- `tools/list` 工具发现；
- `tools/call` 工具调用；
- 单请求串行化、请求超时和无效响应处理；
- stderr 消费、进程终止和异常路径清理。

### 2. MCPToolManager

Manager 负责多个 MCP Server 的生命周期和工具发现：

- 按 `MCPServerConfig` 启动 Server；
- 只暴露 `allowed_tools` 中的工具；
- 按配置的最大风险等级过滤工具；
- 只接受带有明确只读/破坏性 annotations 的真实 stdio 工具，未知风险元数据 fail-closed；
- 单个 Server 不可用时跳过该 Server，不影响其他工具；
- 单个 Server 返回重复工具名时隔离该 Server，避免错误延迟到首次 Agent 请求；
- 应用关闭时关闭已启动的 Client。

### 3. MCPToolAdapter

Adapter 将远端工具映射为内部 Tool：

- 工具名使用 `mcp__<server>__<tool>`，避免与本地工具冲突；
- 输入 Schema 直接复用 MCP 的 `inputSchema`；
- 调用结果归一化为 `{ok, content, error}`；
- 每个 Server 默认需要 `mcp:server:<server>` 权限；该权限由应用容器仅授予已成功发现且显式配置的 Server，不接受模型或用户输入授予。

### 4. Settings 与生命周期

- `MCP_ENABLED` 默认关闭；关闭时不会解析 Server 配置；
- `MCP_SERVERS_JSON` 只接受显式 JSON 数组，并校验名称、命令、allowlist、风险等级、超时和环境变量类型；
- FastAPI lifespan 启动阶段发现工具，关闭阶段释放 MCP 子进程；
- 不可用 Server 被记录并隔离，不阻塞应用或其他 Server。

## 安全边界

- 默认不信任未知 Server；
- 默认不开放未 allowlist 的 Tool；
- MCP Tool 不因为实现了内部 Tool Protocol 就自动获得执行权限；
- `ToolExecutor` 继续负责权限、超时、异常和输出长度控制。
- 风险元数据不完整或无法确认时不注册工具，避免把未知能力按低风险放行。

## 本轮非目标

- 不引入第三方 MCP SDK；
- 不支持 HTTP/SSE MCP Transport；
- 不自动从环境变量加载任意 Server 命令；
- 不注册默认 MCP Server 到生产配置；
- 不实现写入、文件、Shell 或高风险 MCP Tool。

## 后续切片

1. 增加 Server health/readiness 信息与更细粒度的 Tool allowlist；
2. 评估 HTTP Transport、重连和能力缓存；
3. 补充生产部署策略与可观测性。
