# Sprint E2：Workflow Builder（可视化编排，对标 Dify）

状态：设计文档（待 review）
前置：Sprint E1 已闭环（Billing/Audit）；固定 PDF 工作流（app/workflows/）已有 checkpointer/运行状态机/协议化模型可复用；Agent/RAG/Tool 能力全部现成（作为节点复用）

---

## 第 1 节：E2 设计文档

### 1. 产品语义

用户用"节点 + 连线"编排固定流程（区别于单 Agent 自由决策）：

```
用户提问
  ↓
[LLM 节点] 意图判断
  ↓
[条件节点] 是否售后？ ──是──→ [知识库节点] 检索 → [LLM 节点] 回答
      │
      └─否──→ [工具节点] 查订单 → [LLM 节点] 总结 → [输出节点]
```

企业价值：稳定/可控/可审计（每节点可观测）——与"单 Agent 自由决策"互补而非替代（Agent 节点可嵌入现有 AgentService 作为子流程）。

### 2. 数据模型（2 表）

```sql
workflows(
  id            UUID PK,
  workspace_id  UUID NOT NULL FK → workspaces.id (CASCADE),
  name          text NOT NULL,
  description   text DEFAULT '',
  status        text NOT NULL DEFAULT 'draft',   -- draft | published
  definition    JSONB NOT NULL,                  -- 节点图全量定义（见 2.1）
  version       INT NOT NULL DEFAULT 1,          -- 发布版本快照（发布时 version+1 且 definition 冻结为快照）
  created_by    text NULL,
  created_at    timestamptz,
  updated_at    timestamptz
)

workflow_runs(
  id            UUID PK,
  workflow_id   UUID NOT NULL FK → workflows.id (CASCADE),
  workspace_id  UUID NOT NULL,
  status        text NOT NULL,                   -- running | completed | failed | cancelled
  inputs        JSONB NOT NULL,                  -- 用户输入变量
  definition    JSONB NOT NULL,                  -- 执行时的定义快照（发布版本不可变——审计可复现）
  node_results  JSONB NOT NULL DEFAULT '[]',     -- 每节点执行记录（见 4）
  error         text NULL,
  total_duration_ms INT NULL,
  created_at    timestamptz,
  completed_at  timestamptz NULL
)
```

索引：`workflow_runs(workflow_id, created_at)`、`workflows(workspace_id)`。

#### 2.1 definition JSONB 结构（冻结）

```jsonc
{
  "nodes": [
    {"id": "n1", "type": "input",  "config": {}},
    {"id": "n2", "type": "llm",    "config": {"model": null, "system_prompt": "...",
                                               "prompt_template": "{{input.text}}"}},
    {"id": "n3", "type": "knowledge", "config": {"query_template": "{{n2.output}}", "top_k": 5}},
    {"id": "n4", "type": "tool",    "config": {"tool": "calculator",
                                               "arguments_template": {"expression": "{{n3.output}}"}}},
    {"id": "n5", "type": "condition", "config": {"branches": [
        {"id": "b1", "condition": "{{n2.output}} contains '退款'", "target": "n6"},
        {"id": "b2", "target": "n7"}  // else 分支（无 condition = 默认）
    ]}},
    {"id": "n6", "type": "llm",    "config": {"prompt_template": "基于 {{n3.output}} 回答"}},
    {"id": "n7", "type": "agent",  "config": {"agent_id": null, "prompt": "..."}},
    {"id": "n8", "type": "output", "config": {"output_template": "{{n6.output}}"}}
  ],
  "edges": [
    {"from": "n1", "to": "n2"},
    {"from": "n2", "to": "n3"},
    {"from": "n2", "to": "n4"},
    {"from": "n5", "to": "n6"},  // 分支边由 condition 的 target 表达，edges 仅表达无条件边？
  ]
}
```

**设计决策（冻结）**：
- **nodes + edges 双列表**：nodes 含全部节点与配置；edges 表达**无条件顺序边**；条件节点的分支用 config.branches（含 condition + target）表达——分支不重复出现在 edges（避免两处不一致）。**校验器保证**：每个节点的入边数 ≤1（串行流，第一波不支持并发扇出；条件节点是唯一出边 >1 的节点，其出边由 branches 表达）。
- **条件表达式**：第一波只支持 `{{var}} contains '字符串'` / `{{var}} is empty` / `{{var}} == '字符串'` 三种字面形式（确定性求值，无任意表达式引擎——安全与可复现优先）；未知形式 → 校验失败。
- **变量模板**：`{{node_id.output}}` / `{{input.字段}}`——字符串插值；模板引用不存在的节点 → 校验失败（拓扑可达性 + 引用存在性双校验）。

### 3. 节点类型（第一波，6 种）

| 类型 | 输入 | 输出 | 实现 |
|---|---|---|---|
| `input` | 无（用户输入） | `text`（字符串，用户输入的 JSON 序列化） | 透传 |
| `llm` | `prompt_template` 渲染文本 | `output`（文本） | ChatService（复用） |
| `knowledge` | `query_template` | `output`（检索结果拼接文本）+ `references`（受限摘要） | RAGService（复用，含 safety 语义） |
| `tool` | `tool` 名 + `arguments_template` | `output`（执行结果文本） | ToolExecutor（复用，白名单校验） |
| `condition` | 变量表达式 | 分支选择（控制流，无数据输出） | 求值器（3 种字面形式） |
| `agent` | `agent_id`（复用 Agent 定义）+ 可选 `prompt` | `output`（最终回答） | AgentService（复用，作为子流程） |
| `output` | `output_template` | 最终响应 | 透传 |

**第一波不做**：循环节点、并行扇出/汇聚、代码节点、HTTP 节点、延迟/人工审批节点（审批节点作为第二波——现有固定 PDF 工作流的审批模式可迁移）。

### 4. WorkflowEngine（P1 核心）

```
执行流程：
1. 加载 definition（workflow 当前版本或 run 快照）+ 用户输入
2. 校验（首次执行或发布时）：DAG 无环（拓扑排序）、入边 ≤1、模板引用存在、
   条件表达式合法、工具名在白名单/注册表内、agent_id 存在且属于该 workspace
3. 拓扑序逐节点执行：input → ... → output
4. 每节点执行记录：{node_id, type, started_at, duration_ms, status,
   input_summary(≤256 字符截断), output_summary(≤256 字符截断), error?}
   —— 摘要截断与 AgentStepSummary 同哲学（不落原始大 payload）
5. 条件节点求值后只走选中分支（另一分支不执行）
6. 节点失败 → run failed + error 记录（第一波不做重试/降级）
7. 结果：output 节点值 + node_results 全量落库
```

- **校验与执行分离**：`validate_definition(definition, workspace_id)`（纯函数 + workspace 依赖检查）在发布与执行前都调用；执行前再校验一次（防定义被并发修改）。
- **变量求值**：`render_template(template, variables)` 纯函数——`{{...}}` 占位符替换，未定义变量 → 校验错误（fail-fast）。
- **可审计**：run 的 node_results 落库（审计查询用）；workflow 发布记录进 audit（E1b 钩子扩展）。
- **并发**：单 run 内节点串行执行（第一波无并行）；不同 run 天然并发（无共享状态）。

### 5. API（P2）

| 端点 | 说明 |
|---|---|
| `POST /api/v1/workflows` | 创建（draft，含 definition 校验） |
| `GET /api/v1/workflows` | 列表（workspace 隔离） |
| `GET /api/v1/workflows/{id}` | 详情（definition 全量） |
| `PUT /api/v1/workflows/{id}` | 更新（draft 可改；published 需先回 draft 或版本化——决策：**published 禁止直接改**，改前必须先置 draft） |
| `POST /api/v1/workflows/{id}/publish` | 发布（version+1，definition 快照冻结） |
| `POST /api/v1/workflows/{id}/runs` | 执行（body: inputs；用当前 published 版本，draft 也可试运行——决策：**允许对 draft 试运行**，run 快照 definition） |
| `GET /api/v1/workflows/{id}/runs` | 运行历史（分页 limit） |
| `GET /api/v1/workflows/runs/{run_id}` | 运行详情（inputs/node_results/status） |

鉴权：与 agents 同款（require_rate_limit + workspace scope；跨 workspace 404）。
配额：workflow 执行计入 usage（llm 节点走 ChatService 自动计费；整体 run 可计一次——**第一波不做 run 级配额**，节点级计费随现有链路自动生效）。

### 6. 前端（P3）

React Flow（`@xyflow/react`）：
- 左侧节点面板（6 种类型拖拽）、画布连线（无条件边）、条件节点分支配置表单（condition + target 下拉）
- 节点配置表单（每个类型一个表单：llm 的 prompt 模板、knowledge 的 query 模板、tool 的 arguments 模板等）
- 保存（draft）/ 发布（确认 → 版本快照）/ 试运行（输入变量 → 显示 node_results 时间线）
- 校验错误展示（图上标注）

### 7. 实施拆分

| 批 | 内容 | 验收 |
|---|---|---|
| **P1**（后端引擎） | workflows/workflow_runs 表 + definition 校验器（纯函数）+ WorkflowEngine（拓扑执行、6 节点类型、变量模板、条件求值、node_results） | 单测：拓扑排序/无环校验/模板渲染/条件三形式/6 节点执行（fake 依赖注入）/失败路径/摘要截断；与 workspace 无关（引擎层注入依赖） |
| **P2**（API + 持久化） | CRUD/发布/执行/历史/详情端点 + 双存储 repository + 校验接入（工具白名单/agent 归属） | API 测试：workspace 隔离、published 冻结、draft 试运行、run 快照、校验错误 422；audit 钩子（publish/run） |
| **P3**（前端） | React Flow 画布 + 节点表单 + 试运行视图 | 前端测试：画布渲染/保存/发布/校验错误展示/试运行 node_results 时间线 |

P1 与 P2 有依赖（P2 调引擎），但 P2 可并行开发校验器接口（先定 protocol）；P3 依赖 P2 API。

### 8. 明确不做（backlog）

- 循环节点、并行扇出/汇聚、人工审批节点（第二波：迁移现有 PDF 审批流模式）
- 任意表达式引擎（条件求值保持 3 种字面形式）
- run 级配额/计费（节点级自动随现有链路）
- 版本回滚 UI（保留历史版本列表只读）
- 前端 i18n

---

## 第 2 节：P1 派发提示词（WorkflowEngine + 校验器）

```
你在 /Users/Admin/Desktop/ai-platform-mini 仓库中实现 Sprint E2 的 P1（Workflow 定义模型 +
校验器 + 执行引擎，纯后端、不接 DB 与 API）。
先读 docs/roadmap/2026-08-08-sprint-e2-workflow-builder.md 第 1 节（设计文档，不可改）、
仓库根 AGENTS.md，以及以下现有代码（复用其模式）：app/services/chat_service.py（ChatService
接口）、app/rag/service.py（RAGService.prepare/answer 接口）、app/tools/executor.py
（ToolExecutor.execute 接口）、app/services/agent_service.py（AgentService.run 接口）、
app/agents/models.py（AgentStepSummary 的摘要截断哲学）。

实现（新包 app/workflows/engine/，与现有固定 PDF 工作流 app/workflows/ 平级不冲突）：
1. app/workflows/engine/models.py：
   - NodeType StrEnum：input/llm/knowledge/tool/condition/agent/output
   - WorkflowDefinition dataclass（nodes: list[WorkflowNode]，edges: list[WorkflowEdge]，
     version: int）——按第 1 节 2.1 JSON 结构解析/序列化（from_dict/to_dict 纯函数）
   - WorkflowNode（id/type/config: dict）、WorkflowEdge（from/to）
   - NodeResult dataclass（node_id/type/status/started_at/duration_ms/input_summary/
     output_summary/error?）——摘要 ≤256 字符截断（复用 _sanitize 风格，纯函数）
   - WorkflowRunResult dataclass（status/inputs/output/node_results/error?）
2. app/workflows/engine/validation.py（纯函数，不依赖 workspace 外部服务）：
   - validate_definition(definition) -> None（抛 WorkflowValidationError）：
     - 节点 id 唯一、type 合法、edges 引用存在
     - DAG 无环（拓扑排序，Kahn 算法）
     - 每节点入边 ≤1（条件节点例外：出边由 branches 表达且 ≤1 个入边）
     - condition 节点 branches：target 存在、至多一个无 condition 的默认分支
     - 模板引用校验：所有 {{node_id.output}} 引用存在且目标节点拓扑序早于引用者
       （fail-fast 防运行时未定义变量）
     - 条件表达式只允许三种字面形式：{{var}} contains 'x' / {{var}} is empty /
       {{var}} == 'x'（正则校验，未知形式拒绝）
     - 至少一个 input 节点和一个 output 节点；input 出边 =1
   - render_template(template, variables) -> str（纯替换；未定义变量抛错）
   - evaluate_condition(expr, variables) -> bool（三种字面形式求值）
3. app/workflows/engine/executor.py：
   - NodeExecutor Protocol（execute(node, variables, context) -> NodeOutput）——测试注入 fake
   - WorkflowEngine：
     - __init__(node_executors: Mapping[NodeType, NodeExecutor])
     - async run(definition, inputs) -> WorkflowRunResult：
       校验 → 拓扑序执行（入边 ≤1 保证单路径；condition 选择分支后仅执行选中目标）→
       每节点包 NodeResult（时间/状态/摘要/错误）→ 失败即停（run failed）→
       output 节点值作为最终输出
     - 摘要截断：input/output summary 用纯函数截断（≤256 字符，中文安全——按字符数）
4. 测试（tests/test_workflow_engine.py）：
   - 校验：合法 DAG 通过；环拒绝；入边 >1 拒绝；模板引用不存在/拓扑序错误拒绝；
     条件表达式非法拒绝；分支 target 不存在拒绝；默认分支至多一个
   - 模板：简单替换/嵌套变量/未定义抛错
   - 条件：contains/is empty/== 三形式 + 未知形式抛错
   - 引擎：6 节点类型全链路（fake executors 脚本化）——input→llm→condition 选中分支→
     tool→output；condition 未选中分支不执行（fake 计数断言）；节点失败 → run failed +
     error 记录 + 后续节点不执行；摘要截断断言（长文本 → 256 字符）
   - 变量传递：前一节点 output 在后一节点 prompt_template 中可用
5. 范围边界：不建表、不写 API、不碰前端、不碰现有 app/workflows/ 固定流程文件
   （只读其模式）。所有依赖（ChatService/RAGService/ToolExecutor/AgentService）只通过
   NodeExecutor Protocol 注入——引擎不直接 import 服务实现。

验收：
- 现有全部测试保持通过（引擎是新包，零改动现有代码）；
- 新增测试全绿；ruff format --check .、ruff check .、mypy app tests、pytest 全绿；
- 引擎不依赖 DB/settings/workspace（纯注入）——可独立单测。

完成后不要继续；列出变更文件清单和关键设计说明，交给用户 Code Review。
```

---

## 派发说明

1. **P1 完全隔离**：新包 `app/workflows/engine/` + 新测试文件，不触碰任何现有文件——与其他并行窗口零冲突；
2. P1 验收后出 P2（API + 持久化）提示词；P3（React Flow）在 P2 后；
3. 硬验收：现有测试零变化（引擎纯注入、无 DB 依赖）。
