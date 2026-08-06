# AI Platform Mini：管理员、API Key 与 HR RAG 演示说明

> 本文用于 HR 产品演示和内部讲解。
>
> 文中的所有密钥均为占位符，例如 `<ADMIN_API_KEY>`、`<USER_API_KEY>`。请勿把真实密钥写入文档、截图、演示录屏、代码仓库或聊天记录。

## 一、演示目标

本次演示展示一条完整、可审计的使用链路：

```text
管理员登录
  → 创建普通用户 API Key
  → 原始 Key 只显示一次
  → 复制给演示用户
  → 用户在前端输入 User Key
  → 上传并向量化 HR 文档
  → 用户发起 RAG 问答
  → 管理员查看 Token、Agent Run 和 RAG 审计记录
```

演示重点不是让 HR 记住技术细节，而是说明：

1. 管理员可以控制谁能够使用模型。
2. 普通用户只能使用模型和知识库能力，不能管理其他 Key。
3. RAG 回答可以展示参考来源，而不是只展示一个无法追溯的答案。
4. Token 消耗、Agent Run、工具调用和 RAG 来源都可以通过管理员视角复核。

## 二、角色和 Key 边界

### 1. 管理员 Key

管理员 Key 用于进入管理员后台和调用管理员接口，权限包括：

- 创建普通用户 API Key；
- 查看已创建 Key 的名称、状态、创建时间和最近使用时间；
- 撤销普通用户 Key；
- 查看按 Key 聚合的 Token 消耗；
- 查看 Agent Run、工具调用和 RAG 来源摘要；
- 在故障排查时确认请求是否成功、超时或因配额停止。

管理员 Key 是高权限凭据，**不应复制给普通用户，也不应填入普通用户前端**。管理员 Key 泄露后，持有人可能继续创建、查看或撤销其他 Key。

### 2. 普通用户 Key

普通用户 Key 用于：

- 在前端输入后使用 Chat、Agent 或 RAG 问答；
- 让系统按用户 Key 统计 Token 消耗；
- 让管理员能够把一次运行记录归属到具体用户或业务场景。

普通用户 Key **不能**：

- 登录管理员后台；
- 创建或撤销其他 Key；
- 查看全局 Token 消耗；
- 查看其他用户的 Agent Run 或 RAG 记录。

## 三、管理员登录

### 页面操作

1. 打开 AI Platform Mini 前端。
2. 进入“管理员后台”或“管理员登录”页面。
3. 在管理员 Key 输入框中填入：

   ```text
   <ADMIN_API_KEY>
   ```

4. 点击“登录”。
5. 登录成功后进入管理员工作台。

管理员 Key 建议通过密码管理器或安全的临时渠道传递。演示结束后，不要把管理员 Key 留在浏览器自动填充、截图或共享文档中。

### API 验证方式（页面异常时使用）

管理员接口都使用 Bearer Token：

```http
Authorization: Bearer <ADMIN_API_KEY>
```

可以用以下请求验证管理员 Key 是否有效：

```bash
curl -i \
  -H "Authorization: Bearer <ADMIN_API_KEY>" \
  http://127.0.0.1:8000/admin/api-keys
```

返回 HTTP `200` 表示管理员 Key 有效；返回 `401` 或 `403` 时，请参阅本文的“常见问题”。

## 四、创建普通用户 Key

### 页面操作

1. 在管理员工作台打开“API Key 管理”。
2. 点击“创建普通 API Key”。
3. 输入 Key 名称，例如：

   ```text
   HR 演示用户
   ```

4. 点击“创建”。
5. 页面显示新建的原始 Key 后，立即点击“复制”。
6. 将 Key 通过安全渠道发送给演示用户。
7. 演示用户确认收到后，关闭或离开该提示区域。

### 原始 Key 只显示一次

创建成功后，系统会返回原始 Key，但列表页以后只显示脱敏后的 Key 标识或哈希前缀。请注意：

- 原始 Key 不应写进文档；
- 不应把原始 Key 放进 Git、`.env` 提交记录或截图；
- 如果关闭页面前没有复制，通常无法再次查看原始值；
- 如果原始 Key 丢失，应撤销旧 Key，再创建一个新的普通用户 Key；
- 只有 Key 的哈希前缀、名称和状态适合用于管理员列表展示。

### API 验证方式

创建接口：

```http
POST /admin/api-keys
Authorization: Bearer <ADMIN_API_KEY>
Content-Type: application/json
```

请求示例：

```bash
curl -sS -X POST \
  -H "Authorization: Bearer <ADMIN_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"name":"HR 演示用户"}' \
  http://127.0.0.1:8000/admin/api-keys
```

响应中的 `raw_key` 就是需要复制给用户的原始 Key。`raw_key` 只应在创建成功的瞬间使用，不要把响应保存到公共日志或共享终端。

## 五、用户在前端输入 Key

1. 打开普通用户前端页面。
2. 找到“用户 API Key”或“连接模型”区域。
3. 粘贴管理员创建并发送的：

   ```text
   <USER_API_KEY>
   ```

4. 点击“保存”“连接”或“开始使用”。
5. 发送一条普通模型问题，确认返回成功。
6. 再发送 HR RAG 演示提示词，确认回答中出现知识库来源或 RAG 来源状态。

用户页面只需要普通用户 Key，不需要管理员 Key。前端可以保存当前浏览器会话中的 Key，但演示结束后建议主动清除浏览器中的 Key，并在管理员后台撤销演示 Key。

请求模型时，前端应以如下方式传递普通用户 Key：

```http
Authorization: Bearer <USER_API_KEY>
```

不要把 Key 拼接在 URL、问题文本、RAG 文档内容或浏览器可公开分享的截图中。

## 六、HR RAG 文档向量化和检索

### 1. 准备 HR 演示文档

仓库提供了一个不含真实敏感信息的演示文档：

```text
demo/hr-rag-demo.txt
```

文档包含以下示例政策：

- 正式员工年假天数；
- 差旅报销提交时限；
- 正式员工健康福利；
- 午餐补贴；
- 试用期员工的福利限制。

演示时应明确告诉 HR：该文件是“演示版政策”，不代表正式人事制度。

### 2. 启用 RAG 所需条件

RAG 演示需要以下依赖处于可用状态：

- `RAG_ENABLED=true`；
- PostgreSQL 与 pgvector 可连接；
- Ollama 可访问；
- Ollama 已准备 Embedding 模型，例如 `<RAG_EMBEDDING_MODEL>`；
- 后端配置的向量维度与 Embedding 模型输出一致。

示例配置只使用占位符表达，不包含任何真实密钥：

```dotenv
RAG_ENABLED=true
RAG_EMBEDDING_MODEL=<RAG_EMBEDDING_MODEL>
DATABASE_URL=<POSTGRESQL_ASYNCPG_URL>
```

### 3. 文档向量化

在项目根目录执行：

```bash
./.venv/bin/python scripts/ingest.py demo/hr-rag-demo.txt
```

该过程会：

1. 读取 UTF-8 文本；
2. 计算文档 SHA-256，用于识别重复文档；
3. 按配置切分文本块；
4. 调用 Embedding 模型，把文本块转换为向量；
5. 将文档、文本块和向量写入 PostgreSQL/pgvector。

同一个文件重复执行时，如果内容没有变化，系统会识别为已入库文档，不应在数据库中重复创建相同内容。

### 4. 发起 RAG 检索

RAG 问答不是简单地把整篇文件直接塞给模型。系统会先根据问题检索相似文本块，再把相关内容作为受限上下文交给模型。

可用的 RAG Chat 接口为：

```http
POST /api/v1/chat/rag
Authorization: Bearer <USER_API_KEY>
Content-Type: application/json
```

接口示例：

```bash
curl -N -X POST \
  -H "Authorization: Bearer <USER_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"<CHAT_MODEL>",
    "messages":[
      {
        "role":"user",
        "content":"正式员工每年有多少天年假？请根据知识库回答，并说明参考来源。"
      }
    ]
  }' \
  http://127.0.0.1:8000/api/v1/chat/rag
```

Agent RAG 演示则使用 Agent 接口，让 Agent 自主调用知识库搜索工具：

```http
POST /api/v1/agent/runs/stream
Authorization: Bearer <USER_API_KEY>
Content-Type: application/json
```

前端看到的重点状态包括：

- RAG 开始检索；
- 知识库检索成功；
- 检索到的来源数量；
- Agent 的工具调用；
- 最终回答；
- Run 完成、超时或因预算停止。

## 七、HR 演示提示词

建议直接复制下面这段提示词到前端 Agent 输入框：

```text
请先搜索知识库中的员工福利与报销政策，再回答：

1. 正式员工每年有多少天年假？
2. 差旅报销需要在多少个工作日内提交？
3. 试用期员工有哪些福利限制？

请根据检索到的知识库内容回答，并在回答末尾说明参考来源。不要直接凭常识回答。
```

### 预期回答要点

根据演示文档，回答应至少覆盖：

- 正式员工每个自然年度有 `15` 个工作日带薪年假；
- 差旅报销应在出差完成后的 `10` 个工作日内提交；
- 试用期员工暂不享有年度奖金和年度健康体检福利，但可以访问内部知识库并参加必要培训；
- 回答末尾应能看到来源摘要或 RAG 来源状态。

演示时不要要求模型回答演示文档没有提供的具体制度细节。如果知识库没有相关内容，应展示“知识库未提供足够信息”，而不是鼓励模型凭常识补全。

## 八、查看 Token 消耗

管理员进入“Token 用量”区域后，可以按普通用户 Key 查看：

- 使用的模型；
- 请求次数；
- Prompt Token；
- Completion Token；
- Total Token。

支持按日期或月份查询。接口示例：

```http
GET /admin/usage/daily?key_hash_prefix=<KEY_HASH_PREFIX>&date=YYYY-MM-DD
Authorization: Bearer <ADMIN_API_KEY>
```

```http
GET /admin/usage/monthly?key_hash_prefix=<KEY_HASH_PREFIX>&month=YYYY-MM
Authorization: Bearer <ADMIN_API_KEY>
```

示例命令：

```bash
curl -sS \
  -H "Authorization: Bearer <ADMIN_API_KEY>" \
  "http://127.0.0.1:8000/admin/usage/daily?key_hash_prefix=<KEY_HASH_PREFIX>&date=YYYY-MM-DD"
```

说明：

- Token 用量应以服务端记录为准；
- 不要在前端根据回答字数自行伪造 Token 数；
- 部分 Provider 可能只能提供估算值，展示时应标明估算性质；
- RAG 会把检索到的上下文纳入最终模型请求，因此 RAG 请求的 Prompt Token 可能高于普通 Chat 请求。

## 九、查看 Agent Run 和 RAG 审计记录

管理员进入“Agent Run / RAG 记录”区域后，可以查看：

- Run ID 与 Request ID；
- 使用的普通用户 Key 名称和脱敏标识；
- 模型名称；
- Run 状态和停止原因；
- 开始时间、完成时间和耗时；
- Token 总量；
- 工具调用数量；
- RAG 参考来源数量。

打开单条记录后，可以进一步查看安全投影后的 Agent 响应、步骤、工具摘要和 RAG 来源摘要。

对应接口：

```http
GET /admin/agent-runs?limit=50
Authorization: Bearer <ADMIN_API_KEY>
```

```http
GET /admin/agent-runs/<RUN_ID>
Authorization: Bearer <ADMIN_API_KEY>
```

### 审计字段的安全边界

审计记录用于复核运行结果，不等于保存所有原始请求。默认应避免保存或展示：

- 原始 API Key；
- 完整用户隐私输入；
- 原始 Prompt；
- 原始工具参数；
- 完整工具输出；
- Provider 原始响应；
- 内部堆栈和服务器路径。

前端出现很多“回答增量”属于 SSE 流式输出的内部事件：模型逐段生成文本时，系统会连续发送 `answer_delta`。管理员查看审计时，重点应关注 Run 状态、工具调用、RAG 来源、耗时和 Token，而不是把每一段回答增量当成一次独立业务请求。

## 十、Key 状态和撤销

管理员在“API Key 管理”列表中可以查看：

- Key 脱敏标识；
- Key 名称；
- 当前状态；
- 创建时间；
- 最近使用时间。

撤销步骤：

1. 找到需要停用的普通用户 Key；
2. 确认名称和脱敏标识；
3. 点击“撤销”；
4. 再次确认操作；
5. 让用户重新输入一个新创建的普通用户 Key。

撤销后，该 Key 的后续模型、Agent 和 RAG 请求应返回未授权错误。撤销操作是不可逆的演示动作，误撤销时应重新创建新 Key，而不是尝试恢复原始 Key。

接口示例：

```http
DELETE /admin/api-keys/<KEY_HASH_PREFIX>
Authorization: Bearer <ADMIN_API_KEY>
```

## 十一、建议的 HR 现场演示顺序

1. 先用一句话说明管理员 Key 与普通用户 Key 的权限边界。
2. 管理员登录后台，但不要在投影上展示完整管理员 Key。
3. 创建名为“HR 演示用户”的普通 Key。
4. 强调原始 Key 只显示一次，并复制给演示用户。
5. 切换到普通用户页面，输入普通 Key。
6. 先发送一个普通问题，证明 Key 已生效。
7. 执行文档向量化，展示“文档已入库/已生成向量”的结果。
8. 粘贴本文的 HR RAG 提示词。
9. 展示回答、参考来源和 Agent 工具调用。
10. 回到管理员后台，查看 Token 消耗、Agent Run 和 RAG 记录。
11. 最后撤销“HR 演示用户”Key，说明权限可以被管理员及时回收。

## 十二、常见问题

### 1. 返回 401：为什么 Key 无效？

常见原因：

- 没有携带 `Authorization` 请求头；
- 使用了 `<ADMIN_API_KEY>` 或 `<USER_API_KEY>` 这样的占位符，而不是实际值；
- 复制 Key 时多了空格、换行或引号；
- Key 已被管理员撤销；
- 后端重启后，生成的 Key 没有使用持久化认证存储，导致 Key 不再存在；
- 前端代理仍使用旧 Key，用户输入的新 Key 没有真正随请求发送。

排查顺序：

1. 重新复制原始普通 Key；
2. 确认前端当前使用的是普通 Key，不是管理员 Key；
3. 用管理员后台查看该 Key 是否仍为有效状态；
4. 通过后端日志中的 Request ID 定位请求；
5. 必要时撤销旧 Key，重新创建一个普通 Key。

### 2. 为什么 Agent 请求超过 30 秒？

模型回答、Agent 多步骤工具调用和 RAG 检索可能共同超过 30 秒。常见原因包括：

- Agent 需要多轮模型决策；
- Ollama 首次加载模型较慢；
- Embedding 或向量库检索耗时；
- RAG 上下文变长，模型生成时间增加；
- 前端默认等待时间小于后端允许的运行时间；
- 代理或网关还有更短的连接超时。

演示前建议：

- 确认前端等待时间与后端 Agent 超时配置一致；
- 将 Agent 演示问题控制在 1—3 个清晰问题；
- 提前启动模型并检查 Ollama 可用；
- 先运行一次短问题预热；
- 如果页面显示“后端终态未知”，不要把它直接解释为模型失败，应先查看 Agent Run 记录。

### 3. 为什么时间不是北京时间？

所有面向 HR 的演示时间应统一解释为中国标准时间，即 `Asia/Shanghai`（UTC+08:00）。如果页面时间与北京时间不一致，请检查：

- 浏览器所在设备的时区；
- 前端日期格式化是否显式使用 `Asia/Shanghai`；
- 后端写入时间是否带有时区信息；
- 数据库和日志容器的时区配置；
- 是否把无时区时间戳错误地当成本地时间解析。

演示时不要只说“今天”“刚才”，建议同时展示绝对日期，例如：

```text
2026-08-06 14:30（Asia/Shanghai，UTC+08:00）
```

### 4. 为什么 RAG 显示“未提供”或没有来源？

常见原因：

- `RAG_ENABLED` 未开启；
- 文档还没有执行向量化；
- PostgreSQL/pgvector 不可用；
- Embedding 模型不可用或维度不匹配；
- 问题与知识库内容距离过远；
- 检索结果超过距离阈值，被系统安全过滤；
- Agent 没有调用知识库工具；
- 前端只显示最终答案，没有展开 RAG 来源区域。

建议先用本文提供的 HR 问题测试，再检查 Agent Run 中是否出现 RAG 工具调用和来源数量。没有可靠来源时，正确行为是明确说明知识库没有提供足够信息，而不是生成一个看似确定的制度答案。

### 5. 为什么管理员看不到 Token 或 Agent Run？

- 查询日期或月份没有覆盖请求发生的时间；
- 选择了错误的 Key 哈希前缀；
- 请求实际使用的是另一个普通用户 Key；
- Provider 没有返回精确 Token，记录可能为空或为估算值；
- 数据库未启用或记录持久化不可用；
- Agent Run 尚未结束，记录还没有进入最终状态；
- 管理员后台使用了普通用户 Key，无法读取管理接口。

### 6. 能否把管理员 Key 填给普通用户？

不建议，也不应作为正常流程。管理员 Key 的权限远高于普通用户 Key。正确流程是：管理员创建普通用户 Key，用户只使用普通用户 Key；演示结束后，管理员撤销该普通用户 Key。

## 十三、演示结束后的清理

1. 清除演示浏览器中的普通用户 Key。
2. 撤销“HR 演示用户”普通 Key。
3. 清理投影、截图和聊天记录中的敏感值。
4. 确认文档中仍然只有占位符，没有真实 Key。
5. 如使用了临时管理员 Key，按内部安全流程轮换或撤销。
6. 保留必要的审计记录，但不要导出包含原始 Prompt、隐私内容或原始凭据的日志。
