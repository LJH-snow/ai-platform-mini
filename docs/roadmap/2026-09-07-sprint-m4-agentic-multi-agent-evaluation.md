# Sprint M4：真正 Agentic 的多 Agent + 相对单 Agent 量化评测

日期：2026-09-07
状态：已实现（已通过门禁，待推送）

## 背景

M3 已交付多 Agent 编排闭环：Supervisor 任务拆分、Orchestrator 依赖/并发/失败策略/
总预算执行、SSE 事件流、Run 持久化、前端控制台与 E2E。但当前子任务执行仍然走
`ChatService.chat()`（`app/multi_agent/orchestrator.py` 的 `_execute_task` 路径），
并未真正进入 `AgentRuntime`/Tool Registry/RAG 工具链路。从能力上讲，当前是多 Agent
编排壳，而不是真正 Agentic 的多 Agent：Research Agent 不能真正检索知识库，
Writer/Reviewer 也不能基于工具产出或来源步骤工作。

同时，现有评测基础设施已具备进入条件：

- Tool System / Agent Runtime 稳定，单 Agent 已有同步、同步+SSE 与 quota/取消边界；
- `Agent Benchmark` 已能通过真实 `AgentService` 运行 golden task set；
- RAG 工具和运行记录持久化可用。

M4 的目标是把多 Agent 从“Shell 编排”推进到“有真实 Agent Runtime 子任务的编排”，
并建立可离线、可重复、可解释的量化证据，回答“多 Agent 相对单 Agent 是否值得”。

## 目标

P1 后端：子任务执行 Agent 化。Orchestrator 的可执行单元从普通 Chat 调用升级为有界
`AgentService`/Runtime 调用，支持工具调用、RAG、记忆、max_steps、Token 预算、quota
和取消语义，并保持 M3 SSE/持久化/租户契约不变。

P2 评测：建立“调研并生成带来源报告”的确定性 golden set，跑通单 Agent vs 多 Agent
对比，落库指标、支持 CI 离线复现，先定判据再解释结果。

P3 前端：在 Multi-Agent 页增加单 Agent vs 多 Agent 对比结果历史与指标卡片，复用
Agent Studio Benchmark 的展示模式，不做编排画布。

## 非目标（明确不做）

- 跨进程队列、分布式执行、断点续跑；
- 子任务内部 `answer_delta` SSE（子任务保持 Agent Run 内部事件，不承诺逐 token
  穿透到多 Agent 流）；
- 前端 React Flow 编排画布；
- 新 Provider 协议、新的 RAG 引擎或新的存储系统；
- 在未定义成功判据前，基于一次真实 LLM 跑分下结论。

## P1：子任务 Agent 化

### 数据契约

- `Subtask` 增加可选 `agent_id: str | None`；角色仍保留为 `agent_role`。
  M3 请求不带 `agent_id` 时，保持按角色从内置/默认配置解析，兼容旧请求。
- 子任务执行由 Orchestrator 调用 `AgentService.run()`，复用现有 Agent Runtime：
  max_steps、Tool Registry、RAG 注入、长期记忆检索、token budget、quota/reservation
  与取消传播。
- 子任务输入：
  - 主输入仍是 `task.description`（可含依赖模板，保持现有 `input_template` 能力）；
  - 依赖结果不得全量拼接给下游。提供安全摘要注入边界：只注入脱敏截断的
    `final_output`/来源元数据，总字符数受配置上限约束。
- 预算聚合：
  - 子任务内部由 Agent Runtime 正常记录 run 级 token；
  - MultiAgentService 负责把子任务 token 和总 budget 聚合到多 Agent run 级；
  - 避免重复计费：子任务经 AgentService 触发的 quota/usage 记录保持与单 Agent
    一致；多 Agent run 级数字只做聚合展示/上限判定，不二次扣费。

### 关键边界

- 不在 Orchestrator 内重新实现 Agent loop、Tool 执行或 RAG 注入；只做编排和
  聚合。
- 失败语义保持 M3：子任务失败映射到 `subtask_failed`；Supervisor 失败、
  超时、预算、取消均保持现有终态事件。
- SSE 字段不暴露子任务完整 Tool 参数/Provider 响应；可新增安全的
  `tool_calls` 计数/摘要或来源计数，但必须先定义 allowlist。
- `AgentService.run()` 的鉴权/上下文保持由 API 层注入；Orchestrator 不感知
  HTTP。

## P2：单 Agent vs 多 Agent 对比评测

### 先定义收益判据（写死，不后改）

对同一 golden set：

- 多 Agent 的任务完成率必须不低于单 Agent 基线；
- 多 Agent 的 Token 成本涨幅不得超过配置阈值（建议默认 +40%，可在配置/测试调整）；
- Tool Call Accuracy（或来源覆盖）必须高于单 Agent，或至少证明有领域收益；
- 必须包含“故意失败/无来源”用例，且多 Agent 必须如实报告失败，不得补造来源或
  成功结论；
- 延迟阈值允许放宽：多 Agent 因多轮依赖天然比单 Agent 慢，但异常退化为串行调用
  的长任务需要被识别。

### Golden set 设计

覆盖三类：

1. 只需工具即可完成：例如计算/汇总类，验证多 Agent 不无故退化为纯文本；
2. 需要多步调研：例如“调研 A 并生成带来源报告”，Research 必须调用
   `knowledge_search`/RAG Tool，Writer 必须基于 Research 安全输出生成报告；
3. 故意失败/空来源：例如知识库无相关内容、上游任务失败，验证终态真实、不补造
  来源。

至少提供一个 Mock/RAG fixture 场景，保证 CI 离线、可确定性重放；真实 LLM 跑分
作为本地/可选扩展，不作为 CI 硬门禁。

### 指标

- Task Completion Rate
- Tool Call Accuracy / 来源覆盖
- Average Steps
- Average Latency
- Total Token 成本
- 失败用例的真实终态正确率

复用 `app/evals/agent_benchmark.py` 的 repository/record 结构，扩展 `task_set`
与 `metric_payload`，不另建一套不相干评测代码。

## P3：最小对比界面

- Multi-Agent 页新增“对比评测”区块：选择 Agent 与 golden set，运行单 Agent vs
  多 Agent；
- 展示指标卡片和对比表格；
- 结果历史复用 Benchmark 列表模式，按 workspace 隔离；
- 不做画布、不做复杂评价器，不做 LLM-as-judge。

## 验收

- 后端：单 Agent / 多 Agent / Supervisor 失败 / 超时 / 预算 / 取消 / 断连测试
  全绿；
- 前端：对比评测 UI 的 client/reducer/panel 测试、prettier / oxlint / typecheck /
  vitest / build 全绿；
- CI：一个离线 golden gate job 必须验证“有工具/无来源/故意失败”三类场景；
- 演示：通过一次真实 RAG/Tool 子任务执行，页面能看到工具步骤、来源和真实报告；
- 不把未实现的能力写进 README/简历；M3 的 SSE、持久化、历史回放行为不回归。

## 后续切片（不进入 M4）

- 多 Agent 事件级持久化与 Step/Tool 回放；
- 跨进程队列、worker 恢复、分布式执行；
- 子任务内部 `answer_delta` 透传到多 Agent SSE；
- 编排画布 / Workflow Builder 集成；
- 基于 LLM judge 的开放评测。
