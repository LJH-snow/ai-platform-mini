# Sprint 9 Tool System 设计说明

- **日期**：2026-08-04
- **状态**：已实现，待 Code Review
- **对应路线图**：Sprint 9：Tool Registry 与 Executor
- **前置条件**：Sprint 8 Agent Runtime 已实现并通过质量门禁
- **实现约束**：本说明同时记录实现边界和验证要求；实现已落地，等待 Code Review

## 1. 背景与目标

Sprint 8 已经建立了一个不依赖 Web 框架的最小 Agent Runtime。当前 Runtime 可以接收模型决策、按工具名称查找 `AgentTool`、执行工具、把结果回填到 `AgentState`，并处理多步循环、全局超时、外部取消、异常安全边界和输出截断。

当前工具接入方式仍然是一次性注入字典：

```text
AgentService
    └── Mapping[str, AgentTool]
            └── AgentRuntime
```

这种方式适合验证 Agent loop，但工具的元数据、参数约束、风险和权限没有独立边界。Sprint 9 的目标是把工具调用从 Agent loop 中抽离为可治理的 Tool System，同时保持 Sprint 8 的 Runtime 接口和行为兼容。

Sprint 9 要解决的问题：

1. 用稳定的 Tool Protocol 表达工具实现，用元数据表达模型可见的工具描述和治理信息。
2. 用 `ToolRegistry` 负责注册、去重、查询和导出模型工具 Schema。
3. 用 `ToolExecutor` 负责参数校验、权限检查、超时与取消、异常归一化和执行结果标准化。
4. 让 Agent Runtime 继续负责 Run 生命周期、Step 边界、全局 deadline、状态更新和事件顺序，而不是承担工具治理细节。
5. 通过低风险的 `calculator` 和测试用 `echo` 工具验证完整链路。

## 2. 非目标与明确边界

以下能力不属于 Sprint 9，不能为了实现 Tool System 提前混入：

- 不接入 MCP Server、MCP Client 或 MCP Tool Adapter；MCP 放到 Sprint 11。
- 不把现有 RAG Pipeline 接入 Tool Registry；RAG Tool 放到 Sprint 10。
- 不提供任意文件读取、写入、删除、目录遍历或 Shell 执行能力。
- 不提供任意网络访问、URL 抓取、HTTP 请求、浏览器自动化或 DNS 查询能力。
- 不做动态插件加载、Python 模块热加载或远程工具发现。
- 不做 Multi-Agent、任务编排、工作流 DSL 或并行 Agent。
- 不做自动重试、补偿事务或副作用回滚。
- 不做工具调用持久化、Run Trace 数据库模型或完整的 SSE 事件协议。
- 不改变既有 `/api/v1/chat` 和 `/api/v1/chat/rag` 的对外语义。
- 不把权限校验交给模型，也不以模型输出的风险等级作为授权依据。

Sprint 9 只验证本地、确定性、低风险工具的统一执行边界。后续高风险能力必须先经过独立的权限和安全设计。

## 3. 与当前 Agent Runtime 的兼容契约

### 3.1 当前实际接口

Sprint 8 当前的核心接口如下，Sprint 9 的设计必须以它为基线：

```python
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    step_index: int
    request_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class AgentTool(Protocol):
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> str: ...
```

`AgentRuntime` 当前构造参数和运行参数为：

```python
AgentRuntime(
    model: AgentModel,
    tools: Mapping[str, AgentTool] | None = None,
    *,
    tool_executor: ToolExecutor | None = None,
    tool_output_max_chars: int = 8192,
)

await runtime.run(
    user_input: str,
    *,
    max_steps: int = 8,
    timeout: float | None = None,
    deadline: float | None = None,
    cancel_event: asyncio.Event | None = None,
    token_budget: int | None = None,
    run_id: str | None = None,
)
```

当前 Runtime 的职责不能被破坏：

- 在兼容路径中从 `Mapping[str, AgentTool]` 通过工具名查找实现，或通过 `ToolExecutor` 进入 Registry/Executor 链路；
- 为每次调用创建带 `request_id`/`metadata` 默认值的 `ToolContext`；
- 使用全局 `timeout`/`deadline` 和 `cancel_event` 控制模型与工具调用；
- 未知工具返回 `ToolResult(succeeded=False, error="tool_not_found")`；
- 工具普通异常不会把异常文本回传给模型，而是使用固定的安全错误信息；
- 工具输出默认最多 8192 个字符，超过时设置 `ToolResult.truncated=True`；
- 工具调用前后继续产生 `TOOL_STARTED`、`TOOL_COMPLETED` 或 `TOOL_FAILED` 事件；
- 工具结果继续以 `ToolResult` 写入 `AgentStep` 并回填为 `tool` 消息。

### 3.2 推荐迁移方式

Sprint 9 第一版不删除 `AgentRuntime.tools` 的 Mapping 兼容路径。当前实现由 `AgentService` 组装 Registry 和 Executor，并通过可选的 `tool_executor` 直接注入 Runtime；旧的 Mapping 路径仍保留，供 Sprint 8 测试工具和渐进迁移使用。后续若需要进一步隔离 Runtime 与 Tool System，再增加独立 Adapter：

```text
AgentService
    ├── ToolRegistry
    ├── ToolExecutor
    └── AgentRuntime
            ├── optional ToolExecutor
            └── legacy Mapping[str, AgentTool]
```

当前实现没有额外创建 `ExecutorBackedAgentTool`，而是让 Runtime 在提供 `tool_executor` 时直接走 Executor 链路；同时保留 Mapping 注入路径，保证以下代码仍然有效：

```python
runtime = AgentRuntime(model, tools={"echo": echo_tool})
```

不允许为了引入 Registry 而删除 `Mapping[str, AgentTool]` 的兼容能力。

### 3.3 单一职责

| 组件 | Sprint 9 职责 | 不负责的事情 |
|---|---|---|
| `AgentRuntime` | Run/Step 循环、全局 deadline、取消传播、状态和事件 | 注册生命周期、权限策略、参数 Schema 解析 |
| `ToolRegistry` | 静态注册、名称唯一性、查询、导出描述 | 执行工具、重试、权限决策 |
| `ToolExecutor` | 解析、授权、参数校验、单次执行、异常归一化 | 决定下一步模型策略、持久化 Run |
| `AgentService` | 组装依赖、提供本次 Run 的工具视图 | 绕过 Registry 直接调用工具实现 |
| 具体 Tool | 实现自己的确定性业务逻辑 | 读取 HTTP 请求、访问 FastAPI、决定权限 |

## 4. Tool Protocol 与领域模型

### 4.1 Tool Protocol

现有 `AgentTool` 是 Runtime 的最小执行协议，Sprint 9 继续保留它，不要求每个工具实现类继承某个基类。这样可以继续支持 Sprint 8 的测试 Tool，也方便以后通过 Adapter 接入 RAG 或 MCP，而不把外部协议带入 Agent Core。

Tool 实现必须满足：

- `execute` 是异步方法；
- 参数从 `Mapping[str, object]` 接收，工具内部不得假设输入一定可信；
- 只通过 `ToolContext` 获取当前 Run 的最小上下文；
- 成功时返回任意可安全序列化为文本的对象；
- 不能自行捕获并吞掉取消信号；
- 不能把异常堆栈、密钥、内部路径或请求对象写入返回值。

Tool System 使用独立的 `Tool` Protocol 表达模型可见元数据和执行方法，当前实际接口为：

```python
class Tool(Protocol):
    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> object: ...
```

`ToolDescriptor` 在 Registry 中规范化 `name`、`description`、输入/输出 Schema、`risk_level` 和 `required_permissions`。风险和权限元数据只能由服务端工具实现声明，不能由模型修改。

### 4.2 注册约束

Registry 保存工具实现实例，并在注册和导出时构造不可变的 `ToolDescriptor` 视图：

- `descriptor.name` 与 Registry 主键一致；
- 同名注册失败，不静默覆盖；
- Schema 用于模型选择和参数预校验，最终校验必须在 Executor 再做一次；
- Registry 对外导出的 Schema 是深拷贝，调用方不能反向修改工具实现的 Schema。

### 4.3 工具结果

对 Runtime 来说，最终结果仍然是当前的 `ToolResult`：

```python
ToolResult(
    call_id=call_id,
    name=tool_name,
    content=content,
    succeeded=succeeded,
    error=error_code_or_none,
    truncated=truncated,
)
```

Executor 可以在内部使用更丰富的执行结果，但适配到 Runtime 时不能改变已有字段的含义。错误必须使用稳定的机器可读错误码，`content` 只放模型可以安全看到的固定消息或脱敏后的结果。

## 5. 元数据、风险等级与权限

### 5.1 元数据最小集合

每个可注册工具至少需要：

| 字段 | 用途 | 要求 |
|---|---|---|
| `name` | Tool Call 名称和 Registry 主键 | 唯一、稳定、非空 |
| `description` | 提供给模型的用途说明 | 说明输入、输出和限制，不写成开放式权限声明 |
| `input_schema` | 模型调用 Schema 和参数校验依据 | 根节点为 object，明确 properties 和 required |
| `risk_level` | 策略选择和审计分类 | 由开发者声明，不能由模型修改 |
| `required_permissions` | 执行前的权限要求 | 默认空集合；权限不足默认拒绝 |
| Executor timeout | 单个工具的执行上限 | 当前由 `ToolExecutor` 统一配置，未在工具元数据中单独覆盖 |

本 Sprint 不要求把所有 JSON Schema 关键字都实现。最小支持集合为：`type`、`properties`、`required`、`additionalProperties`、`enum`、`minimum`、`maximum`、`minLength`、`maxLength`、`items`。不支持的关键字必须在注册或启动校验阶段明确拒绝，而不是静默忽略。

### 5.2 风险等级

风险等级用于治理和默认策略，不是工具运行时的自描述文本：

| 等级 | 含义 | Sprint 9 默认策略 |
|---|---|---|
| `LOW` | 纯计算或受限本地逻辑，无外部写副作用 | 默认允许已注册工具，可选额外权限 |
| `MEDIUM` | 可能访问受控资源或产生较明显成本 | Sprint 9 不提供具体工具，默认拒绝 |
| `HIGH` | 文件、网络、命令、账号、外部写入等高影响能力 | Sprint 9 不注册，默认拒绝 |

`calculator` 和测试 `echo` 声明为 `LOW`。未来的 RAG、MCP、文件或网络 Tool 必须在各自 Sprint 中重新完成威胁建模，不能仅因为实现了 `AgentTool` Protocol 就自动获得执行资格。

### 5.3 权限模型

Sprint 9 采用服务端显式授权、默认拒绝未知能力的模型：

```text
模型请求 Tool Call
        |
        v
Registry resolve(name)
        |
        v
Tool 存在且已在本次 Run allowlist 中？
        |
        v
required_permissions ⊆ granted_permissions？
        |
        +-- 否：安全失败，不执行实现
        |
        +-- 是：进入参数校验和执行
```

权限规则：

1. 模型只能提出工具名和参数，不能提出或修改 `granted_permissions`。
2. 当前实现由 `ToolExecutor` 构造参数提供 `max_risk_level` 和 `granted_permissions`；`AgentService` 默认只启用 `LOW` 风险且无额外权限的工具。
3. Registry 中存在不等于任意风险工具都可执行；每次调用仍需通过 Executor 的风险和权限检查。
4. 空权限集合只代表“不需要额外权限的已注册工具”，不代表可以调用未注册工具。
5. 权限不足的错误不应泄漏所需权限的完整内部结构；对模型返回固定的 `tool_permission_denied`。
6. 权限判断在执行前完成，并且每次 Tool Call 都重新判断，不使用跨请求的可变授权状态。

第一版可以使用字符串权限标识，例如 `tool:calculator`、`tool:echo`，但权限常量必须集中定义，禁止在模型 Prompt 或 API 请求中任意传入。

## 6. 执行上下文

当前 `ToolContext` 是 Runtime 与工具之间的最小执行上下文：

```python
@dataclass(frozen=True)
class ToolContext:
    run_id: str
    step_index: int
    request_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
```

Sprint 9 继续以它作为 Tool Protocol 的参数，保持以下不变量：

- `run_id` 标识一次隔离的 Agent Run；
- `step_index` 从 1 开始，与 `AgentStep.index` 对齐；
- Context 不携带 FastAPI `Request`、数据库 Session、Provider 客户端或任意可变全局对象；
- 工具不能通过 Context 修改 AgentState、追加消息或改变权限；
- 工具不能从 Context 推导出用户身份并自行做授权决定。

超时和取消是 Executor 的控制信号，不应要求所有现有 Tool 修改签名。Executor 接收 Runtime 传入的有效 deadline 和 `cancel_event`，在调用边界控制 await；如果未来需要向工具传递只读租户或审计信息，应以带默认值的可选字段扩展 `ToolContext`，不得删除或重命名 `run_id`、`step_index`。

推荐的内部控制上下文可以包含：

```text
ToolExecutionControl
├── run_id
├── step_index
├── effective_deadline
├── cancel_event
├── granted_permissions
└── output_limit
```

其中 `effective_deadline`、`cancel_event` 和 `granted_permissions` 由 Executor 使用，不自动暴露给不需要它们的具体工具。这样可以避免把权限和取消机制伪装成业务参数。

## 7. ToolRegistry 设计

### 7.1 职责

`ToolRegistry` 是进程内、显式组装的工具目录，负责：

- 注册 `RegisteredTool`；
- 校验工具名称和元数据；
- 拒绝同名覆盖；
- 按名称解析工具；
- 返回稳定排序的工具描述；
- 导出给模型适配器所需的 JSON Schema 视图；
- 提供工具描述和查询能力；当前的风险/权限判断由 `ToolExecutor` 执行。

Registry 不负责：

- 调用工具实现；
- 重新解释工具参数；
- 记录完整 Prompt 或敏感参数；
- 从网络或文件系统动态加载工具；
- 根据模型的话术动态注册工具。

### 7.2 建议接口

设计目标可以表达为：

```python
class ToolRegistry(Protocol):
    def register(self, tool: RegisteredTool) -> None: ...

    def resolve(self, name: str) -> RegisteredTool | None: ...

    def list_metadata(self) -> tuple[ToolMetadata, ...]: ...

    def model_schemas(
        self,
        allowed_names: frozenset[str],
    ) -> tuple[Mapping[str, object], ...]: ...
```

实际实现可使用内存字典，但必须封装在 Registry 内部。`model_schemas()` 只导出通过 allowlist 的工具，排序必须稳定，避免同一组工具在不同请求中产生不确定 Prompt。

### 7.3 注册校验

注册阶段应拒绝：

- 空名称、非法名称或重复名称；
- 空描述；
- 根节点不是 object 的输入 Schema；
- `required` 引用不存在的属性；
- `additionalProperties` 等关键字类型错误；
- 非法或负数的工具超时；
- `risk_level` 不在受支持枚举内；
- 权限名称为空或包含未定义格式。

注册失败是配置错误，应在应用组装阶段尽早暴露，不应等到模型第一次调用时才发现。

## 8. ToolExecutor 设计

### 8.1 调用顺序

一次 Tool Call 的执行顺序固定为：

```text
1. 解析工具名
2. Registry resolve
3. 本次 Run allowlist 检查
4. 风险等级策略检查
5. 权限检查
6. 参数结构校验
7. 参数值校验和规范化
8. 计算有效工具 deadline
9. 执行 AgentTool.execute(arguments, ToolContext)
10. 规范化成功结果
11. 转换为 Runtime 可消费的 ToolResult
```

任何前置检查失败都不得调用具体工具实现。工具不能依赖“模型已经看过 Schema”来跳过第 6、7 步。

### 8.2 参数校验边界

参数输入的类型是 `Mapping[str, object]`，因此 Executor 必须把它视为不可信的外部输入。校验至少包括：

- 顶层必须是 object-like mapping；
- 必填字段必须存在；
- 不允许的未知字段必须拒绝，除非 Schema 明确允许额外属性；
- 基本类型、枚举、数值范围和字符串长度必须符合 Schema；
- 嵌套 object/array 按受支持的 Schema 递归校验；
- 校验失败不能调用工具，也不能把原始参数完整回传给模型或 API。

错误内容建议统一为：

```text
Tool arguments are invalid. [error_code=tool_invalid_arguments]
```

对外错误码可保持稳定；详细字段、收到的值和校验路径只写入受控日志，并且需要脱敏和长度限制。

### 8.3 超时与取消

Executor 必须同时服从 Run 级和 Tool 级限制：

```text
effective_deadline = min(
    run_remaining_deadline,
    tool_timeout_deadline,
)
```

- 没有 Run deadline 时，可以使用工具元数据中的 `timeout_seconds`；
- 没有工具级超时时，使用 Run 剩余时间；
- 两者都没有时，不新增隐式的无限等待，应用层应配置合理默认值；
- 超时后取消正在等待的工具任务，并等待其清理完成；
- `cancel_event` 触发时立即停止等待，并沿用 Runtime 的 `CANCELLED / EXTERNAL_CANCELLED` 语义；
- `asyncio.CancelledError` 不得被普通 `Exception` 分支转成 `tool_execution_failed`；
- Sprint 9 不做自动重试，避免未知工具副作用被重复执行。

工具实现应尽量在收到取消后快速退出，但 Executor 不能假设第三方实现一定合作。对于无法取消的同步阻塞调用，Sprint 9 不提供绕过方案，不把它包装成安全的异步工具。

### 8.4 异常安全边界

Executor 需要把实现异常和模型可见结果隔离：

| 情况 | Runtime 结果 | 模型可见内容 |
|---|---|---|
| 工具不存在 | `succeeded=False`, `error=tool_not_found` | `Requested tool is unavailable.` |
| 权限不足 | `succeeded=False`, `error=tool_permission_denied` | 固定权限拒绝消息 |
| 参数非法 | `succeeded=False`, `error=tool_invalid_arguments` | 固定参数错误消息 |
| 工具普通异常 | `succeeded=False`, `error=tool_execution_failed` | `Tool execution failed. [error_code=tool_execution_failed]` |
| 工具超时 | Run 按 deadline 结束 | 不伪造成功结果，不回传异常文本 |
| 外部取消或任务取消 | Run 按取消语义结束 | 不把取消当作普通工具失败 |

要求：

- 不向模型回传 `str(exc)`、traceback、模块名、文件路径或环境变量；
- 不把完整参数、完整结果或异常对象直接写入普通日志；
- `BaseException` 中的取消类信号必须保留控制流语义；
- 普通异常可以记录内部错误摘要，但日志内容需要脱敏和长度限制；
- 工具失败后仍然生成可供 Runtime 回填的失败 `ToolResult`，除非本次 Run 已被 deadline 或取消终止。

### 8.5 输出截断

Sprint 8 已由 `AgentRuntime` 提供默认 8192 字符的最终安全上限，并通过 `ToolResult.truncated` 记录是否截断。Sprint 9 不应再实现一套互相冲突的截断规则。

推荐的职责划分：

1. Executor 负责确保成功结果为 `str`，拒绝不可规范化的返回值；
2. Runtime 继续作为最终边界执行 `tool_output_max_chars` 截断；
3. 如果未来增加工具级输出上限，Executor 只能把更小的上限作为策略传给 Runtime，不能提前把标记丢失；
4. 截断标记继续使用当前 `ToolResult.truncated=True`，内容末尾继续使用 `...[tool output truncated]`；
5. 错误消息本身使用固定短文本，不需要按工具原始异常截断；
6. 截断后的内容才允许进入下一次模型决策，不能让模型收到完整大结果而 API 只返回摘要。

因此，Sprint 9 的验收重点是验证现有全局截断在 Registry/Executor 链路中不回归，而不是扩展成复杂的多级输出策略。

## 9. 内置工具范围

### 9.1 Calculator

`calculator` 是 Sprint 9 的第一个真实内置 Tool，用来验证参数 Schema、确定性执行和低风险权限策略。

建议能力：

- 输入一个受限的数学表达式或明确的数值参数结构；
- 支持基本算术：加、减、乘、除、括号；
- 返回规范化的数值或短文本结果；
- 声明为 `risk_level=SAFE`；
- 只需要 `tool:calculator` 这一项显式工具权限；
- 对表达式长度、数字位数、运算深度和结果大小设置上限；
- 对除零、非法字符、非法语法、溢出或超限返回稳定错误码。

实现严禁直接调用 `eval`、`exec` 或执行任意 Python AST。可以采用白名单解析器，或使用只允许数值和算术运算的受限实现；具体实现方案在编码阶段单独评审。

`calculator` 不做：变量绑定、函数调用、导入模块、随机数、日期时间、文件读写、网络访问和 shell 命令。

### 9.2 Echo Tool

`echo` 是测试和演示工具，不是生产业务能力：

- 输入一个受限长度的 `text` 字符串；
- 原样返回经过长度限制的文本；
- 声明为 `risk_level=SAFE`；
- 只用于验证 Registry、参数校验、执行上下文、Tool Call 事件、失败回填和输出截断；
- 不访问外部状态，不写数据库，不访问文件或网络。

Echo Tool 的测试实现可以放在测试夹具中；如果需要用于本地 Demo，也应作为显式注册的内存工具，不要把它变成默认开放的通用反射入口。

## 10. Agent Runtime 集成与事件语义

### 10.1 集成方式

AgentService 在创建 Agent Run 时：

1. 从应用容器获取静态 Registry；
2. 根据服务端 allowlist 和调用方授权构造本次 Run 的工具视图；
3. 为模型适配器导出允许工具的描述和输入 Schema；
4. 为 Runtime 注入 `ExecutorBackedAgentTool` Mapping；
5. 让 Runtime 使用现有 `timeout`、`deadline`、`cancel_event` 和 `tool_output_max_chars`。

模型看到的工具描述只能来自 Registry 的已注册元数据，不能来自工具对象的 `repr()`、模块名或异常信息。

### 10.2 事件不变量

引入 Registry/Executor 后，已有事件语义继续成立：

- `TOOL_STARTED` 只表示 Runtime 开始处理一个合法的 Tool Call，不表示工具已经成功；
- `TOOL_COMPLETED` 只表示得到成功结果；
- `TOOL_FAILED` 表示未知工具、权限、参数或普通执行失败；
- 事件 `sequence` 从 1 连续递增；
- `occurred_at` 使用 UTC 且非递减；
- `ToolResult.call_id`、`name` 与模型的 Tool Call 对齐；
- 失败结果仍然按既有语义回填 Agent State，供后续模型决定是否结束或修正；
- deadline/外部取消导致的终止不伪造 `TOOL_COMPLETED`。

如果后续需要增加 `permission_denied`、`validation_failed` 等更细的观测字段，应增加可选错误码或内部事件属性，不改变现有事件类型的含义。

## 11. 实际模块边界

Sprint 9 保持现有 `app/agents/` 领域边界，在同级新增 `app/tools/`。当前实现的文件和依赖方向如下：

```text
app/
├── agents/
│   ├── models.py              # AgentState/ToolCall/ToolResult/ToolContext
│   ├── protocols.py           # AgentModel/AgentTool 兼容协议
│   └── runtime.py             # Agent loop，支持 Mapping 或 ToolExecutor
└── tools/
    ├── models.py              # ToolContext、Descriptor、执行结果、风险等级
    ├── protocols.py           # Tool Protocol
    ├── registry.py             # ToolRegistry：注册、去重、查询、Schema 导出
    ├── executor.py             # Schema、超时、异常和输出边界
    └── calculator.py            # AST 白名单算术工具
```

实际依赖方向：

```text
API / AgentService
        |
        +--> ToolRegistry ---> Tool Protocol / Tool metadata
        |
        +--> ToolExecutor ---> Registered Tool implementation
        |
        +--> AgentRuntime ---> AgentModel
                    ^
                    |
          optional tool_executor
```

`calculator.py` 不反向依赖 FastAPI、ChatService、RAG、Provider 或数据库层。Echo 只存在于测试工具中，用于验证 Registry/Executor 链路，不作为生产默认工具暴露。

## 12. 测试与验收标准

本节记录 Sprint 9 的实现验收范围；对应测试已经补充，以下清单用于 Code Review 和后续回归：

### Registry

- 正常注册和按名称解析；
- 同名注册被拒绝，不覆盖原工具；
- 非法名称、空描述和非法 Schema 被拒绝；
- allowlist 过滤和模型 Schema 稳定排序；
- 未注册工具不会进入模型工具列表。

### 参数与权限

- 缺少必填字段失败且不调用工具；
- 未知字段、错误类型、枚举和值范围失败；
- 未授权工具失败且不调用工具；
- 风险等级策略拒绝 `MEDIUM/HIGH` 工具；
- 合法请求只获得当前 Run 的允许工具。

### Executor 控制流

- Calculator 合法表达式成功；
- Calculator 非法字符、除零和超限输入安全失败；
- Echo 正常回显；
- 工具普通异常不会泄漏异常文本；
- 工具超时被取消并按 Runtime deadline 语义结束；
- 外部 `cancel_event` 触发后不被转成普通工具错误；
- 工具任务清理完成，不留下后台任务；
- 超长成功输出最终只进入 Runtime 的截断边界，并正确设置 `truncated`。

### Runtime 兼容性

- 仍可直接传入 `Mapping[str, AgentTool]`；
- Registry/Executor 适配后的 Tool Call 与 Sprint 8 测试 Tool 的结果字段一致；
- `TOOL_STARTED`、`TOOL_COMPLETED`、`TOOL_FAILED` 顺序和 sequence 不回归；
- 工具失败结果仍能回填下一次模型决策；
- 现有 Agent API、RAG API 和全量质量门禁不回归。

## 13. 交付清单与完成定义

Sprint 9 只有同时满足以下条件才算完成：

- `AgentTool` 与 `ToolContext` 的兼容契约保留；
- Registry 可以注册、去重、解析并导出允许工具 Schema；
- Executor 在调用实现前完成授权和参数校验；
- Executor 正确处理工具级和 Run 级超时、取消及异常安全边界；
- Runtime 继续负责最终输出截断和 ToolResult 回填；
- `calculator` 在明确 allowlist 下可运行，Echo 仅作为测试工具；
- 没有引入 MCP、RAG Tool、文件访问或网络访问；
- README 记录新增 Tool System 和学习总结；
- 通过 `ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest`；
- 应用可通过 `uvicorn app.main:app --reload` 启动；
- 代码完成用户 Code Review 并获得批准后，使用 conventional commit 提交。

## 14. 设计取舍总结

Sprint 9 不追求一次性实现“万能工具平台”，而是先建立一个可审计、可测试、低风险的执行边界。保持现有 `AgentTool.execute(arguments, ToolContext) -> str` 可以让 Tool Registry/Executor 演进不破坏 Sprint 8 Runtime，也为后续 RAG 和 MCP Adapter 留出清晰接入点。当前实现让 `AgentService` 负责组装 Registry/Executor，Runtime 通过可选 `tool_executor` 接入新链路，同时保留 Mapping 兼容路径。Registry 负责“有哪些工具”，Executor 负责“能否安全执行”，Runtime 负责“如何推进 Agent Run”；三者边界清晰后，后续能力扩展才不会再次把所有逻辑堆回 Chat API 或 Agent loop。


## 15. 实现记录

Sprint 9 已落地 `app/tools/` 下的 Tool Protocol、Registry、Executor 和 AST 白名单 Calculator，并完成 `AgentRuntime`/`AgentService` 集成与工具专项测试。当前状态为实现完成、等待用户 Code Review；通过批准后再提交 Sprint 9 的 conventional commit。
