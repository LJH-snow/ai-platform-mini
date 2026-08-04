# Sprint 8：Agent Runtime 核心设计说明

> 设计日期：2026-08-04  
> 项目：`ai-platform-mini`  
> Sprint：8  
> 状态：已实现、待 Code Review  
> 设计范围：仅定义最小 Agent Runtime，不引入 LangChain、LangGraph 或新的模型客户端。

## 1. 背景与设计结论

当前仓库已经具备一个可复用的 LLM 应用基础：

- `app/api/` 提供 FastAPI 路由；
- `app/services/chat_service.py` 负责构造聊天消息、调用 Provider 并解析结果；
- `app/providers/` 提供 `LLMProvider`、`ProviderRouter` 以及 Ollama/OpenAI 实现；
- `app/schemas/chat.py` 定义普通 Chat 请求和响应；
- 现有 `/api/v1/chat` 支持普通同步请求，其他模块已经围绕鉴权、限流、配额、Usage 和 Request ID 建立了边界；
- `app/rag/` 已有独立的 RAG MVP，不应在本 Sprint 直接嵌入 Agent Runtime；
- `app/db/` 已有 PostgreSQL 及 pgvector 初始化、会话和模型基础。

Sprint 8 的目标是在这些边界之上增加一个**可独立测试的最小执行内核**：模型返回最终答案时结束；模型返回工具决策时执行测试 Tool，将结果回填到状态，再请求模型继续决策。

核心结论如下：

1. 普通 Chat 与 Agent Run 是两条并行链路，`/api/v1/chat` 的行为不变。
2. `AgentRuntime` 不依赖 FastAPI、SQLAlchemy、HTTP 客户端或具体 Provider。
3. Runtime 只依赖两个端口：模型决策端口和最小 Tool 执行端口。
4. Sprint 8 使用注入的 Tool 映射，不提前实现 Sprint 9 的 Registry、动态发现、权限系统或高风险工具。
5. Runtime 的事件首先是内存中的类型化事件，用于测试、日志和 API 响应摘要；本 Sprint 不做持久化 Run Trace，也不承诺 Agent SSE。
6. 现有 `ChatService` 仍是模型调用的应用服务。Agent Runtime 不直接复制 `OllamaService`、OpenAI Provider 或 ProviderRouter 的逻辑。

## 2. 目标

### 2.1 本 Sprint 必须实现的目标

1. 定义类型化的运行状态、步骤、事件、状态和停止原因。
2. 实现不依赖 FastAPI 的 Agent 执行循环。
3. 支持模型在每一步选择：
   - 直接给出最终答案；或
   - 请求执行一个已注入的测试 Tool。
4. 支持单次和多次 Tool 调用，并将工具结果回填给下一次模型决策。
5. 支持并验证以下停止条件：
   - 模型给出最终答案；
   - 达到 `max_steps`；
   - 超过 deadline；
   - 超过本次 Run 的 token budget；
   - 请求被取消。
6. 统一记录模型决策、工具开始/结束、最终答案、失败和停止等事件。
7. 通过 `AgentService` 将 Runtime 接入 FastAPI，同时复用现有鉴权、限流、配额、Usage 和错误响应边界。
8. 保持现有普通 Chat API 和全部现有测试不变。

### 2.2 成功标准

- Runtime 可以在没有 FastAPI 应用上下文的情况下被单元测试直接调用。
- “直接回答”“一次工具调用”“多次工具调用”均能得到确定的状态转移。
- Tool 失败会形成结构化 Tool 错误结果并回填给模型，不会把原始异常直接泄漏给客户端。
- `max_steps`、deadline、token budget 和取消都不会导致无限循环。
- 每个终止结果都具有明确的 `RunStatus` 和 `StopReason`。
- 新 Agent API 不会改变 `/api/v1/chat` 的请求、响应和错误行为。

## 3. 非目标与明确不承诺的能力

以下内容不属于 Sprint 8，不应为了通过设计而在本 Sprint 提前实现：

- 不引入 LangChain、LangGraph、AutoGen 或其他 Agent 编排框架。
- 不重写 `ChatService`、`LLMProvider`、`ProviderRouter` 或现有 Provider。
- 不修改普通 `/api/v1/chat` 的协议，不将普通 Chat 强制改为 Agent 模式。
- 不实现 Sprint 9 的通用 `ToolRegistry`、Tool 去重、动态 Schema 导出和统一 `ToolExecutor`。
- 不实现 calculator、filesystem、terminal、浏览器自动化、任意网络访问等生产工具。
- 不实现 MCP、RAG Tool Adapter、Memory、多 Agent、人工审批和持久化检查点。
- 不实现 Run/Step/ToolCall 数据库表、历史查询和完整可观测平台。
- 不承诺模型原生 Function Calling、严格 JSON Schema 输出或所有模型都能可靠地产生工具决策。
- 不在本 Sprint 引入新的向量数据库、消息队列或微服务拆分。
- 不承诺 Agent SSE、WebSocket 或前端完整的 Step/Tool 可视化；本 Sprint 的 API 以同步 JSON 为主。

如果模型无法遵守决策格式，Runtime 应明确失败并返回 `INVALID_MODEL_DECISION`，而不是静默猜测或把普通文本当作工具参数。

## 4. 现有 ChatService 与 Agent Runtime 的边界

### 4.1 现有 ChatService 的职责

`ChatService` 当前位于 `app/services/chat_service.py`，职责包括：

- 将 `ChatRequest` 的 `system_prompt`、history 和当前用户消息转换成 Provider 消息；
- 将模型、温度和最大输出 Token 转换为 Provider 参数；
- 调用注入的 `LLMProvider`；
- 将 Provider 响应解析为 `ChatResponse` 或 `ProviderChatResult`；
- 保留现有 Ollama/OpenAI 路由、Usage 字段和流式解析行为。

它不应负责：

- 判断是否需要工具；
- 解析 Agent 状态；
- 维护 Agent 步数和停止策略；
- 执行工具；
- 记录 Agent 事件；
- 处理 Agent 的多步循环。

### 4.2 Agent Runtime 的职责

`AgentRuntime` 位于领域层，负责：

- 接收一次 Agent Run 的初始输入和运行策略；
- 保存类型化状态；
- 请求模型做一次决策；
- 根据决策执行一个工具或结束；
- 将工具结果转换成模型下一步可读的消息；
- 在每次循环前检查 `max_steps`、deadline、token budget 和取消；
- 发布类型化事件；
- 返回一个确定的最终 `AgentRunResult`。

它不应知道：

- FastAPI 的 `Request`、`Response`、Dependency 或异常处理器；
- API Key、HTTP Header 和 Rate Limit Header；
- SQLAlchemy Session、Repository 和数据库表；
- Ollama、OpenAI、HTTP URL 或 Provider 的认证细节；
- RAG 的 Embedding、pgvector 查询和文档切片。

### 4.3 AgentService 的职责

`AgentService` 是 API 与 Runtime 之间的应用层，负责将现有应用基础设施接到 Runtime：

1. 接收 API 层已校验的 Agent 请求。
2. 创建 `AgentRunRequest`、运行策略和测试 Tool 映射。
3. 通过一个 `AgentModel` 适配器复用现有 `ChatService`。
4. 复用现有配额预留、结算、Usage 记录和 Request Context。
5. 将 Runtime 结果转换为 Agent API 响应。
6. 将领域异常交给现有的异常映射边界处理，而不是在 Runtime 中生成 HTTP 响应。

推荐依赖方向：

```text
app/api/agent.py
        |
        v
app/services/agent_service.py
        |
        v
app/agents/runtime.py ----> AgentModel
        |                         |
        v                         v
   Tool mapping          ChatService
                                  |
                                  v
                           ProviderRouter
```

Runtime 只能依赖自身的领域类型和 Protocol。`AgentService` 可以依赖 `ChatService`、Quota、Usage 和应用配置，但不得反向让 `ChatService` 依赖 `AgentRuntime`。

### 4.4 建议的文件边界

本 Sprint 实现时可以新增以下文件，但本设计说明本身不创建它们：

```text
app/
├── agents/
│   ├── __init__.py
│   ├── models.py       # State, decision, event, result and policy types
│   ├── protocols.py    # AgentModel and AgentTool protocols
│   └── runtime.py      # The framework-independent execution loop
├── api/
│   └── agent.py        # HTTP contract and dependency wiring
├── schemas/
│   └── agent.py        # Request and response models
└── services/
    └── agent_service.py
```

`app/agents/` 是新增领域层；不要新增与现有 `app/providers/`、`app/services/`、`app/db/` 重复含义的 `backend/`、`llm/` 或 `database/` 顶层目录。

## 5. 类型化领域模型

### 5.1 JSON 类型边界

工具参数和工具结果需要允许 JSON 数据，但不能让 `Any` 在整个 Runtime 中扩散。建议在领域层定义受限 JSON 类型：

```python
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
```

如果第三方边界只能返回 `object`，应在适配器入口完成校验并转换为 `JsonObject`；Runtime 内部不接收未经校验的任意对象。

### 5.2 状态与状态枚举

```python
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(StrEnum):
    FINAL_ANSWER = "final_answer"
    MAX_STEPS = "max_steps"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    CANCELLED = "cancelled"
    MODEL_ERROR = "model_error"
    INVALID_MODEL_DECISION = "invalid_model_decision"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class AgentState:
    run_id: UUID
    status: RunStatus
    messages: tuple["AgentMessage", ...]
    steps: tuple["AgentStep", ...]
    answer: str | None
    started_at: datetime
    deadline_monotonic: float | None
    max_steps: int
    token_budget: int
    tokens_used: int
```

设计约束：

- `AgentState` 表示 Runtime 的当前快照，不承担数据库持久化职责。
- `messages` 和 `steps` 使用不可变序列，状态更新通过新快照完成，避免事件观察者修改内部状态。
- `status=running` 时 `answer` 必须为空；`status=completed` 时必须有答案。
- `steps` 的 `index` 从 1 开始并连续递增。
- 每次模型决策最多对应一个 Tool 调用；如果模型直接回答，该 Step 不产生 Tool 调用。
- `tokens_used` 只累计当前 Run 已知的模型 Token 用量；未知用量不能伪造为精确值。

### 5.3 Agent 消息

现有 `app/schemas/chat.py` 的 `ChatMessage` 只有 `system`、`user`、`assistant` 三种角色，且没有 Tool Call 元数据。Agent Runtime 不应修改普通 Chat schema 来承载内部状态，而应使用独立的内部消息类型：

```python
from dataclasses import dataclass
from typing import Literal

AgentMessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class AgentMessage:
    role: AgentMessageRole
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
```

`ChatServiceAgentModel` 负责将 `AgentMessage` 转换成现有 `ChatRequest` 可接受的输入。这个转换可以采用明确的决策协议文本，不应让 Runtime 直接拼接 Provider payload。

### 5.4 Agent Step

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AgentStep:
    index: int
    started_at: datetime
    finished_at: datetime | None
    decision_kind: str
    tool_name: str | None
    tool_call_id: str | None
    tool_succeeded: bool | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error_code: str | None
```

Step 记录的是本次决策和执行的摘要，不保存完整的敏感参数或超长工具输出。完整事件在 Sprint 8 只存在于内存事件列表中，后续 Sprint 13 再决定持久化和脱敏策略。

## 6. 模型决策接口

### 6.1 决策类型

模型决策必须是显式的判别联合，而不是让 Runtime 通过字符串前缀猜测：

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinalAnswerDecision:
    answer: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: JsonObject


@dataclass(frozen=True, slots=True)
class ToolCallDecision:
    call: ToolCall


ModelDecision = FinalAnswerDecision | ToolCallDecision
```

约束：

- `answer` 不能为空字符串。
- `call_id`、Tool 名称和参数必须经过格式校验。
- 一次决策只允许一个 `ToolCall`。并行工具调用留到后续 Sprint，在没有评测收益前不增加复杂度。
- Runtime 不根据普通文本自行推断工具名或参数。

### 6.2 AgentModel Protocol

```python
from collections.abc import Sequence
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    run_id: UUID
    messages: tuple[AgentMessage, ...]
    available_tools: tuple["ToolDescriptor", ...]
    remaining_steps: int
    remaining_token_budget: int


class AgentModel(Protocol):
    async def decide(
        self,
        request: ModelRequest,
        *,
        deadline_monotonic: float | None,
    ) -> tuple[ModelDecision, "ModelUsage"]: ...
```

`AgentModel` 是 Runtime 的模型端口，不等同于现有 `LLMProvider`：

- `LLMProvider` 处理 Provider 协议和原始响应；
- `ChatService` 处理普通 Chat 请求和 Provider 结果；
- `AgentModel` 处理 Agent 决策协议，将模型输出解析成 `ModelDecision`。

Sprint 8 的生产适配器可以在 `AgentService` 外部构造一个 `ChatServiceAgentModel`：它调用现有 `ChatService`，要求模型返回受限的 JSON 决策，再进行 Pydantic 或显式类型校验。该适配器必须对空内容、非法 JSON、未知决策类型、缺失字段和超长参数返回明确的模型决策解析错误；不得自由文本猜测或静默兜底。

这不表示所有当前模型都已经支持可靠 Tool Calling。没有可靠决策输出时，结果应为可诊断的失败；不要在 Runtime 内添加隐式重试、自由文本解析或“猜测用户意图”的兜底逻辑。

### 6.3 Tool 描述

Sprint 8 不建立 Registry，但模型仍需看到当前可用测试 Tool 的最小描述：

```python
@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: JsonObject
```

`available_tools` 来自 AgentService 注入的 Tool 映射快照。Runtime 只把它传给 `AgentModel`，不负责注册、查询、去重或动态导出。

## 7. Sprint 8 测试 Tool 接口

### 7.1 最小 Protocol

```python
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ToolContext:
    run_id: UUID
    step_index: int
    deadline_monotonic: float | None


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    output: JsonObject | str
    error_code: str | None = None
    error_message: str | None = None


class AgentTool(Protocol):
    @property
    def descriptor(self) -> ToolDescriptor: ...

    async def execute(
        self,
        arguments: JsonObject,
        context: ToolContext,
    ) -> ToolResult: ...
```

### 7.2 注入方式

Sprint 8 的 Runtime 构造函数接收：

```python
tools: Mapping[str, AgentTool]
```

构造时应复制为只读映射或不可变快照。Runtime 每次决策按 `call.name` 查找 Tool：

- 找到 Tool：校验调用名称与返回结果的基本一致性，再执行；
- 找不到 Tool：生成 `UNKNOWN_TOOL` 结构化结果，回填给模型；
- Tool 返回 `ok=False`：生成 Tool 失败事件并回填给模型；
- Tool 抛出已知异常：转换为安全的 Tool 失败结果；
- Tool 抛出未知异常：记录日志和错误事件，客户端只看到归一化错误信息。

Tool 失败默认不是立即终止整个 Run。它作为模型可见的 `tool` 消息进入下一轮，模型可以选择修正参数、换用其他可用 Tool 或直接回答。如果后续达到 `max_steps` 或其他限制，最终停止原因仍以实际触发的停止条件为准。

### 7.3 测试实现要求

测试中至少提供以下假的 Tool，不放入生产模块：

- `EchoTool`：返回输入参数，验证参数传递和结果回填；
- `CountingTool`：记录调用次数，验证多步循环和步骤边界；
- `FailingTool`：返回结构化失败或抛出已知异常，验证错误回填；
- `BlockingTool`：等待事件或超时，验证 deadline 和取消传播。

测试 Model 也应是假的、确定性的序列模型，不依赖 Ollama、OpenAI、网络或真实 Token 采样。

## 8. 执行循环

### 8.1 单步语义

一个 Step 的边界是“一次模型决策，以及该决策最多对应的一次 Tool 执行”：

```text
Step N
  1. 检查取消、deadline、max_steps、token budget
  2. 发布 ModelDecisionRequested
  3. 调用 AgentModel.decide()
  4. 记录模型用量和 ModelDecisionMade
  5. 如果是 FinalAnswer：更新答案并完成
  6. 如果是 ToolCall：查找并执行 Tool
  7. 将 ToolResult 转为 tool message
  8. 发布 ToolCallFinished
  9. 创建下一份 AgentState
  10. 进入 Step N+1
```

### 8.2 伪代码

下面的伪代码只表达控制流，不作为生产实现的最终代码：

```python
async def run(initial: AgentRunRequest) -> AgentRunResult:
    state = create_initial_state(initial)
    emit(RunStarted(state))

    try:
        while state.status is RunStatus.RUNNING:
            check_cancelled()
            check_deadline(state)
            check_token_budget(state)
            check_step_budget(state)

            decision, usage = await model.decide(
                build_model_request(state),
                deadline_monotonic=state.deadline_monotonic,
            )
            state = record_model_decision(state, decision, usage)
            emit(ModelDecisionMade(state, decision))

            if isinstance(decision, FinalAnswerDecision):
                state = complete_with_answer(state, decision.answer)
                emit(RunCompleted(state))
                return build_result(state)

            tool_result = await execute_tool(decision.call, state)
            state = append_tool_result(state, tool_result)
            emit(ToolCallFinished(state, tool_result))

        return build_result(state)
    except asyncio.CancelledError:
        state = mark_cancelled(state)
        emit(RunCancelled(state))
        raise
    except AgentStop as exc:
        state = mark_stopped(state, exc.reason)
        emit(RunStopped(state, exc.reason))
        return build_result(state)
    except AgentRuntimeError as exc:
        state = mark_failed(state, exc.reason)
        emit(RunFailed(state, exc))
        return build_result(state)
```

实现时不得使用不可观测的裸 `while True`。循环必须有显式的步数上限，并在模型调用前、Tool 调用前和每次状态转移前检查运行限制。

### 8.3 Tool 结果回填

Tool 结果应转为带有 `tool_name`、`call_id` 和安全输出的 `AgentMessage(role="tool")`。回填内容必须：

- 保留成功/失败状态；
- 保留必要的错误码和可供模型修正的简短信息；
- 截断超长输出，避免一次 Tool 调用耗尽上下文；
- 不直接回填 Python 异常的 repr、堆栈、密钥或内部路径；
- 不在本 Sprint 引入通用 Prompt Injection 防护，但应在代码中保留明确的 Tool 输出边界，后续由安全 Sprint 加强。

## 9. 运行限制与取消

### 9.1 `max_steps`

- `max_steps` 是本次 Run 允许的最大决策次数。
- 必须是正整数，并有服务端上限；客户端不能通过请求绕过上限。
- 在开始下一次模型决策前检查。若已经完成了最后允许的 Tool 执行，但没有最终答案，则 Run 以 `STOPPED / MAX_STEPS` 结束。
- `max_steps` 不是模型输出 Token 上限，两者必须分别记录。

建议 Sprint 8 初始默认值为 5，服务端硬上限为 20。具体数值应通过构造参数或应用配置注入，不要写死在 Runtime 的循环逻辑中。

### 9.2 deadline

- 使用单调时钟计算 deadline，不使用 wall-clock 差值判断剩余时间。
- deadline 应覆盖整个 Run，而不是为每一个 Step 重新计时。
- 模型调用和 Tool 调用都必须继承剩余时间；如果底层客户端支持 timeout，应传入剩余秒数。
- 在调用前和返回后都检查 deadline，避免一个已经超时的结果继续驱动下一步。
- 超时结束时返回 `STOPPED / DEADLINE_EXCEEDED`，并保留已完成 Step 摘要。

建议 Sprint 8 初始默认值为 30 秒，服务端硬上限为 120 秒。这里是设计基线，不代表当前应用已经存在对应环境变量。

### 9.3 token budget

`token_budget` 是单个 Agent Run 的模型 Token 预算，和现有账号级 Quota 是两个层次：

- Agent Runtime 用它阻止单个 Run 无限增加模型调用成本；
- 现有 `QuotaService` 继续负责 API Key 的日/月额度和预留结算；
- AgentService 负责在一次 Run 开始前创建保守的 Quota reservation，并在完成、失败或取消时通过现有生命周期结算；
- Runtime 不直接操作数据库配额 Repository。

Runtime 应在以下时机检查预算：

1. 发起模型调用前，预算不足则停止；
2. 模型返回后，累计已知的 prompt/completion Token；
3. Tool 回填后，下一次模型调用前再次检查；
4. Provider 未提供 Token 统计时，只能使用明确标注为估算的值，并不能把估算当成精确 Usage。

预算用尽时返回 `STOPPED / TOKEN_BUDGET_EXCEEDED`。如果模型在预算边界仍返回了完整最终答案，应以“是否允许消费该调用”的策略为准；Sprint 8 采用保守策略，在调用前预算不足即停止。

### 9.4 取消

- `asyncio.CancelledError` 必须继续向上抛出，Runtime 不得吞掉取消。
- Runtime 可以在重新抛出前发布 `RunCancelled` 事件并更新内存状态。
- AgentService/API 层负责让现有 Quota reservation 进入释放或结算流程。
- Tool 实现必须接收任务取消；不能通过 `shield`、后台无引用任务或同步阻塞调用逃避取消。
- 取消不应被包装为普通 `500`。若 HTTP 客户端断开，具体响应可能无法发送，但日志和内部事件必须保留取消原因。

## 10. 错误与停止原因

### 10.1 领域错误与终止结果分离

可恢复的 Tool 失败和不可恢复的 Runtime 错误不能混为一谈：

| 情况 | 是否立即终止 | Runtime 行为 |
|---|---:|---|
| Tool 返回业务失败 | 否 | 生成 `ToolResult(ok=False)`，回填给模型 |
| Tool 未找到 | 否 | 生成 `UNKNOWN_TOOL` 结果，回填给模型 |
| Tool 参数不符合测试 Tool 要求 | 否 | 生成 `INVALID_TOOL_ARGUMENTS` 结果，回填给模型 |
| 模型返回非法决策 | 是 | `FAILED / INVALID_MODEL_DECISION` |
| 模型调用不可用 | 是 | `FAILED / MODEL_ERROR` |
| 达到最大步数 | 是 | `STOPPED / MAX_STEPS` |
| 超过 deadline | 是 | `STOPPED / DEADLINE_EXCEEDED` |
| Token 预算耗尽 | 是 | `STOPPED / TOKEN_BUDGET_EXCEEDED` |
| 请求取消 | 是 | `CANCELLED / CANCELLED`，并重新抛出取消 |
| 未知 Runtime 异常 | 是 | `FAILED / INTERNAL_ERROR` |

### 10.2 结果模型

建议 Runtime 返回领域结果，而不是直接抛出 HTTP 异常：

```python
@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: UUID
    status: RunStatus
    answer: str | None
    stop_reason: StopReason | None
    steps: tuple[AgentStep, ...]
    events: tuple["AgentEvent", ...]
    usage: "RunUsage"
```

- 正常完成时：`status=completed`、`answer` 非空、`stop_reason=final_answer`。
- 限制停止时：`status=stopped`、`answer` 可以为空、`stop_reason` 为具体限制原因。
- 模型或 Runtime 失败时：`status=failed`、客户端得到归一化错误；`events` 仅供受控的内部记录和测试使用。
- 取消时：调用方通常收到取消异常，不将取消伪装成成功结果。

### 10.3 HTTP 错误映射原则

新增 Agent API 应沿用现有 `app/core/exceptions.py` 和 `app/schemas/error.py` 的统一错误响应结构，包含 `code`、`message` 和 `request_id`。实现时只增加 Agent 必需的错误码，不改变现有错误码含义。

建议映射：

- 请求校验失败：沿用 FastAPI/Pydantic 的 422；
- 未认证、限流、配额不足：沿用现有鉴权、限流和 Quota 错误；
- 模型不可用：映射为与现有 Provider 错误一致的 502 类错误；
- 非法模型决策：映射为可诊断的 502 或内部 Agent 错误，不暴露模型原始响应；
- 运行限制：优先通过 200 响应中的 `status=stopped` 和 `stop_reason` 表达，因为这是一个已被系统控制性结束的 Run；
- 未知 Runtime 异常：沿用统一 500 处理器。

不要把 `MAX_STEPS`、`DEADLINE_EXCEEDED` 或 `TOKEN_BUDGET_EXCEEDED` 伪装成 Provider 失败。普通 `/api/v1/chat` 的错误映射完全不变。

## 11. Agent 事件模型

### 11.1 事件类型

事件是 Runtime 观察点，不是数据库模型。建议使用带 `event_type` 判别字段的不可变事件联合：

```python
@dataclass(frozen=True, slots=True)
class AgentEventBase:
    run_id: UUID
    sequence: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RunStarted(AgentEventBase):
    event_type: Literal["run_started"] = "run_started"


@dataclass(frozen=True, slots=True)
class ModelDecisionMade(AgentEventBase):
    decision_kind: Literal["final_answer", "tool_call"] = "tool_call"
    step_index: int = 0


@dataclass(frozen=True, slots=True)
class ToolCallStarted(AgentEventBase):
    tool_name: str = ""
    call_id: str = ""
    step_index: int = 0


@dataclass(frozen=True, slots=True)
class ToolCallFinished(AgentEventBase):
    tool_name: str = ""
    call_id: str = ""
    ok: bool = False
    step_index: int = 0


@dataclass(frozen=True, slots=True)
class RunStopped(AgentEventBase):
    reason: StopReason = StopReason.INTERNAL_ERROR


AgentEvent = (
    RunStarted | ModelDecisionMade | ToolCallStarted | ToolCallFinished | RunStopped
)
```

实际实现可以调整字段组织，但必须满足：

- 每个事件有 `run_id`、单调递增的 `sequence` 和时间戳；
- 事件顺序与状态转移顺序一致；
- Tool 参数和输出默认脱敏或摘要化；
- 事件发布失败不能破坏核心 Run，除非事件 Sink 被明确配置为 fail-fast；
- 事件类型不直接暴露 Provider 原始 payload。

还应包含模型错误、运行失败、运行完成和取消事件。为了控制第一版复杂度，不要求为每个内部函数创建事件。

### 11.2 事件 Sink

Runtime 构造时注入一个可选事件接收器：

```python
class AgentEventSink(Protocol):
    async def publish(self, event: AgentEvent) -> None: ...
```

默认 Sink 可以收集到内存列表。测试通过内存 Sink 断言事件顺序；AgentService 可以将事件转换为响应摘要或日志；Sprint 13 再决定是否接入持久化和 SSE。

## 12. 未来 Sprint 9 Tool Registry 的迁移边界

Sprint 8 的临时结构必须为 Sprint 9 留出替换点，但不能提前实现 Registry。

### 12.1 Sprint 8 临时结构

```text
AgentService
    └── Mapping[str, AgentTool]
             └── AgentRuntime
```

Runtime 只依赖：

- `AgentTool` Protocol；
- `ToolDescriptor`；
- `ToolContext`；
- `ToolResult`。

它不依赖具体的字典类型，也不直接访问工具类的私有字段。

### 12.2 Sprint 9 替换方式

Sprint 9 可以增加：

```text
AgentService
    └── ToolRegistry
            ├── list_descriptors()
            └── resolve(name)
                    └── ToolExecutor
                            └── AgentRuntime
```

迁移时只需让 AgentService 根据 Registry 构造本次 Run 的工具视图，或让 Runtime 的依赖从 `Mapping` 提升为最小的 `ToolResolver` Protocol。以下内容不得在 Sprint 8 偷渡到 Runtime：

- Tool 注册生命周期；
- 动态插件加载；
- 风险等级和权限决策；
- 参数 Schema 自动生成；
- 超时、重试、输出截断的通用 Executor；
- MCP、RAG、文件系统或网络工具的协议适配。

### 12.3 不可破坏的兼容点

Sprint 9 必须继续兼容 Sprint 8 的测试 Tool 和 `AgentTool` 语义。Tool 的调用结果、失败回填、事件字段和 `ToolContext` 不应因为引入 Registry 而改变；如果需要扩展，应增加可选字段而不是改变已有字段含义。

## 13. API 集成契约

### 13.1 路由与兼容性

建议新增：

```text
POST /api/v1/agent/runs
```

该路由和现有 `/api/v1/chat` 并行：

```text
POST /api/v1/chat          -> ChatService -> ProviderRouter
POST /api/v1/agent/runs   -> AgentService -> AgentRuntime
```

本 Sprint 不修改、废弃或重定向 `/api/v1/chat`。Agent 路由使用现有 API Key 鉴权、限流、Quota reservation、Usage collector、Request ID 和异常处理机制。

### 13.2 请求契约

建议请求模型为独立的 `AgentRunRequest`，不要复用 `ChatRequest` 作为内部状态容器：

```json
{
  "message": "请先使用 echo 工具确认输入，再回答。",
  "model": "qwen3",
  "system_prompt": "你是一个简洁的助手。",
  "history": [],
  "max_steps": 5,
  "deadline_ms": 30000,
  "token_budget": 2048
}
```

字段规则：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `message` | 是 | 最新用户消息，沿用普通 Chat 的非空约束 |
| `model` | 否 | 沿用现有 Provider 路由语义 |
| `system_prompt` | 否 | 初始系统指令 |
| `history` | 否 | 仅允许现有 `system/user/assistant` Chat 消息 |
| `max_steps` | 否 | 本 Run 决策次数；默认 5，服务端硬上限 20 |
| `deadline_ms` | 否 | 整个 Run 的 deadline；默认 30000，服务端硬上限 120000 |
| `token_budget` | 否 | 本 Run 模型 Token 预算；默认 2048，服务端硬上限由配置决定 |

Sprint 8 不加入 `stream` 字段。这样不会把尚未实现的 Agent SSE 暴露成看似可用的能力。后续如果要支持流式事件，应通过向后兼容的独立契约设计，而不是复用普通 Chat SSE 的 chunk 格式。

### 13.3 响应契约

成功请求返回 200：

```json
{
  "run_id": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
  "status": "completed",
  "answer": "已确认输入。",
  "stop_reason": "final_answer",
  "steps": [
    {
      "index": 1,
      "decision_kind": "tool_call",
      "tool_name": "echo",
      "tool_succeeded": true
    },
    {
      "index": 2,
      "decision_kind": "final_answer",
      "tool_name": null,
      "tool_succeeded": null
    }
  ],
  "usage": {
    "prompt_tokens": 120,
    "completion_tokens": 48,
    "estimated": false
  }
}
```

响应设计要求：

- `run_id` 供日志和后续 Run Trace 使用，但 Sprint 8 不提供查询接口；
- `status`、`stop_reason` 是客户端判断结果的主要字段；
- `steps` 只返回摘要，不返回完整 Prompt、Tool 参数、Provider 原始响应或敏感内容；
- `events` 不作为稳定公开字段，本 Sprint 由内部 Event Sink 和测试使用；
- `answer` 在受限停止时可以为空；客户端必须先检查 `status`；
- `usage.estimated=true` 时表示 Token 是估算值，不能假装是 Provider 精确用量。

### 13.4 配额与响应头

Agent API 的配额边界由 `AgentService` 负责连接：

1. 先完成请求校验和身份/限流依赖；
2. 根据初始输入和 `token_budget` 创建一次保守 reservation；
3. 调用 Runtime；
4. 成功、停止、失败或取消都经过现有 `ReservationLifecycle` 释放/结算；
5. 使用实际可得的模型 Usage 调用现有 `UsageCollector`；
6. 沿用现有 `X-RateLimit-*` 响应头。

Runtime 不直接返回 HTTP Header，也不直接访问 API Key 或数据库。

## 14. 测试策略

### 14.1 Runtime 单元测试

建议新增 `tests/test_agent_runtime.py`，不启动 FastAPI、不访问网络、不依赖真实 Ollama 或 PostgreSQL。至少覆盖：

1. **直接回答**：第一次决策为 `FinalAnswerDecision`，状态为 completed，只有模型决策事件。
2. **单次工具调用**：模型返回 Tool Call，EchoTool 成功，第二次返回最终答案。
3. **多次工具调用**：CountingTool 被按预期调用多次，Step index 连续，最终答案正确。
4. **Tool 失败回填**：FailingTool 失败后，下一次 ModelRequest 包含 tool 错误消息。
5. **未知 Tool**：不崩溃，形成 `UNKNOWN_TOOL` 结果并回填。
6. **非法模型决策**：Runtime 返回 `FAILED / INVALID_MODEL_DECISION`，不执行任何 Tool。
7. **max_steps 边界**：模型持续请求工具时，在准确的 Step 数停止，不多调用一次模型。
8. **deadline 边界**：模型或 Tool 阻塞时返回 `STOPPED / DEADLINE_EXCEEDED`。
9. **token budget 边界**：用量达到预算后不发起下一次模型调用。
10. **取消传播**：取消 Runtime Task 或设置外部取消信号后，返回 `CANCELLED / EXTERNAL_CANCELLED`，并完成内部任务清理。
11. **事件顺序**：事件 sequence 单调递增，事件顺序与状态转移一致。
12. **工具输出边界**：超长输出被截断，异常内部信息不进入模型消息和 API 摘要。

### 14.2 AgentModel 适配器测试

适配器测试使用假的 `ChatService`：

- 合法 JSON 决策解析为 `FinalAnswerDecision` 或 `ToolCallDecision`；
- 缺少字段、未知 kind、错误参数类型和空答案都映射为明确的模型决策解析错误；
- `ChatService` 的 Provider 错误不会被误判为模型给出的最终答案；
- Tool 描述和历史消息按约定进入模型请求；
- 不测试真实模型的概率性行为，也不把某个模型的自然语言输出当作稳定协议。

### 14.3 AgentService/API 测试

建议新增 `tests/test_agent_service.py` 和 `tests/test_agent_api.py`：

- API Key 鉴权和限流依赖顺序正确；
- Quota reservation 在成功、失败、限制停止和取消路径都能释放/结算；
- UsageCollector 收到 Agent Run 的已知 Usage 和延迟；
- `POST /api/v1/agent/runs` 返回固定响应结构；
- `status=stopped` 的 Run 仍返回明确 `stop_reason`；
- Agent 错误采用统一 `ErrorResponse`，包含 `request_id`；
- 现有 `tests/test_chat_api.py` 和 `tests/test_chat_service.py` 不需要因为 Agent 增加而修改行为断言。

### 14.4 质量门禁

Sprint 8 实现后仍必须执行仓库规定的检查：

```text
ruff format --check .
ruff check .
mypy app tests
pytest
```

如果 Agent API 只使用内存测试 Tool，不需要为了 Sprint 8 新增 PostgreSQL 集成测试；需要数据库的持久化、RAG Tool 和 Run Trace 测试分别放到后续 Sprint。所有新增生产函数、方法和类必须有显式类型标注，代码注释使用英文，文档正文使用中文。

## 15. 实施顺序

1. 先新增领域类型和 Protocol，确保不依赖 FastAPI。
2. 实现内存 Event Sink 和假的 Test Tool/Model 测试夹具。
3. 实现 Runtime 单步和循环，先通过直接回答、工具调用和限制条件测试。
4. 实现 `ChatServiceAgentModel` 适配边界，不修改 `ChatService` 的公共行为。
5. 实现 `AgentService`，接入现有 Quota、Usage、Request Context 和异常边界。
6. 最后新增 Agent API 路由和独立的 Pydantic schema。
7. 运行全部质量门禁，展示变更并等待 Code Review；Review 未通过前不得进入 Sprint 9。

## 16. Sprint 8 的明确交付边界

Sprint 8 完成时，可以声称：

> 在现有 `ChatService` 之上实现了一个不依赖 Web 框架的最小 Agent Runtime，支持类型化模型决策、测试 Tool 调用、多步状态转移、事件记录、最大步数、deadline、Token budget 和取消传播，并通过独立 Agent API 接入现有鉴权、配额和 Usage 基础设施。

Sprint 8 完成时，不能声称：

- 已经有通用 Tool Registry；
- 已经接入 RAG、MCP、Memory 或 Multi-Agent；
- 已经支持任意模型的原生 Function Calling；
- 已经完成持久化追踪、Agent SSE 或完整前端可视化；
- 已经具备任意文件、终端、浏览器或网络操作能力。

这些能力分别属于后续 Sprint，并必须在实现和测试完成后再写入 README、简历或 Demo 说明。
