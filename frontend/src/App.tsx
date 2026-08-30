import type { FormEvent, JSX } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  AgentBackendError,
  AgentNetworkError,
  AgentResponseError,
  createAgentClient,
  type AgentClient,
} from './agent/client.ts'
import type {
  AgentRag,
  AgentRagReference,
  AgentRun,
  AgentRunInput,
  AgentRunStatus,
  AgentToolCall,
  AgentTraceStep,
} from './agent/types.ts'
import {
  initialAgentStreamState,
  reduceAgentStream,
  type AgentStreamState,
} from './agent/reducer.ts'
import { AgentStreamFormatError } from './agent/stream.ts'
import { compactAgentTraceEvents } from './agent/trace.ts'
import { isKnownTool, localizeToolName } from './agent/tool-name.ts'
import { AdminDashboard, USER_KEY_STORAGE } from './admin/AdminDashboard.tsx'
import { createAuthClient } from './auth/client.ts'
import { LoginPage } from './auth/LoginPage.tsx'
import { RegisterPage } from './auth/RegisterPage.tsx'
import { WorkspaceSwitcher } from './auth/WorkspaceSwitcher.tsx'
import { MemberManagement } from './auth/MemberManagement.tsx'
import { formatAgentTimestamp } from './agent/time.ts'
import { Dashboard } from './platform/Dashboard.tsx'
import { createKnowledgeClient } from './platform/knowledge.ts'
import { createPlatformClient } from './platform/client.ts'
import { KnowledgeBase } from './platform/KnowledgeBase.tsx'
import { ModelCatalog } from './platform/ModelCatalog.tsx'
import { AgentStudio } from './platform/AgentStudio.tsx'
import { Billing } from './platform/Billing.tsx'
import { createConfigClient } from './platform/config-client.ts'
import { PromptStudio } from './platform/PromptStudio.tsx'
import { RunDetail } from './platform/RunDetail.tsx'
import { RunList } from './platform/RunList.tsx'
import { ToolCenter } from './platform/ToolCenter.tsx'
import { UsageDashboardPage } from './platform/UsageDashboard.tsx'
import { useRagRuntimeStatus } from './platform/rag-status.ts'
import { ChatBackendError, createChatClient, type ChatClient } from './chat/client.ts'
import { createWorkflowClient } from './workflow/client.ts'
import { WorkflowPanel } from './workflow/WorkflowPanel.tsx'
import { WorkflowBuilder } from './workflow-builder/WorkflowBuilder.tsx'
import { createWorkflowBuilderClient } from './workflow-builder/client.ts'
import { getRuntimeConfig } from './chat/config.ts'
import type { ChatApiMessage, ChatMessage, ConversationSummary } from './chat/types.ts'

type ConsoleMode = 'chat' | 'agent'
type AppPage =
  | 'dashboard'
  | 'console'
  | 'knowledge'
  | 'prompts'
  | 'models'
  | 'workflow'
  | 'workflow-builder'
  | 'admin'
  | 'members'
  | 'agents'
  | 'tools'
  | 'run'
  | 'usage'
  | 'runs'
  | 'billing'
type RequestStatus =
  | 'idle'
  | 'sending'
  | 'agent_running'
  | 'running'
  | 'completed'
  | 'stopped'
  | 'client_cancelled'
  | 'cancelled'
  | 'timed_out'
  | 'failed'
  | 'chat_failed'
  | 'network'
  | 'interrupted'
  | 'connecting'
  | 'waiting'
  | 'tool_running'
  | 'tool_completed'
  | 'tool_failed'
  | 'rag_loading'
  | 'rag_completed'
  | 'response_format_error'
  | 'connection_lost'

type AppProps = {
  chatClient?: ChatClient
  agentClient?: AgentClient
}

type ActiveRequest = {
  controller: AbortController
  stopped: boolean
  kind: ConsoleMode
  assistantMessageId: string
}

const createMessage = (role: ChatMessage['role'], content: string): ChatMessage => ({
  id: `${role}-${crypto.randomUUID()}`,
  role,
  content,
})

const THREAD_ID_STORAGE = 'ai-platform-thread-id'

const RAG_PRESET_QUESTION =
  '请基于知识库内容回答：什么是智能体？必须先调用 knowledge_search；如果没有相关来源，请明确说明知识库没有相关内容，不要使用未检索到的知识进行回答。'

const statusLabels: Record<RequestStatus, string> = {
  idle: '待发送',
  sending: '回答生成中',
  agent_running: 'Agent 运行中',
  running: 'Agent 执行中',
  completed: '已完成',
  stopped: '已停止',
  client_cancelled: '请求已取消',
  cancelled: '运行已取消',
  timed_out: '运行超时',
  failed: '运行失败',
  chat_failed: '后端错误',
  network: '网络失败',
  interrupted: 'SSE 已断连',
  connecting: '连接 Agent 中',
  waiting: '等待 Agent 事件',
  tool_running: '工具调用中',
  tool_completed: '工具调用完成',
  tool_failed: '工具调用失败',
  rag_loading: 'RAG 加载中',
  rag_completed: 'RAG 已完成',
  response_format_error: '响应格式错误',
  connection_lost: '连接已断开',
}

const formatConversationTime = (value: string | null): string => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  return new Intl.DateTimeFormat(
    'zh-CN',
    sameDay ? { hour: '2-digit', minute: '2-digit' } : { month: 'numeric', day: 'numeric' },
  ).format(date)
}

const runStatusLabels: Record<AgentRunStatus, string> = {
  idle: '待运行',
  running: '运行中',
  completed: '已完成',
  stopped: '已停止',
  failed: '运行失败',
  cancelled: '运行已取消',
  timed_out: '运行超时',
  unknown: '未知状态',
}

const agentRunStatusLabel = (run: AgentRun): string =>
  run.status === 'stopped' && run.stopReason === 'token_budget_exceeded'
    ? '预算超限'
    : runStatusLabels[run.status]

const toolStatusLabels: Record<AgentToolCall['status'], string> = {
  running: '工具调用中',
  succeeded: '工具调用成功',
  failed: '工具调用失败',
  timed_out: '工具调用超时',
  cancelled: '工具调用已取消',
  unknown: '工具状态未知',
}

const decisionLabels: Record<AgentTraceStep['decisionKind'], string> = {
  final_answer: '最终回答',
  tool_call: '工具调用',
  invalid: '无效决策',
  unknown: '步骤信息待确认',
}

const eventLabels: Record<string, string> = {
  run_started: '运行开始',
  model_decision: '模型决策',
  step_planned: '步骤计划',
  tool_started: '工具开始',
  tool_completed: '工具完成',
  tool_failed: '工具失败',
  answer_delta: '回答增量',
  answer: '最终回答',
  run_stopped: '运行结束',
}

const requestStatusForRun = (status: AgentRunStatus): RequestStatus => {
  if (status === 'completed') return 'completed'
  if (status === 'cancelled') return 'cancelled'
  if (status === 'timed_out') return 'timed_out'
  if (status === 'stopped') return 'stopped'
  return 'failed'
}

const fallbackAnswerForRun = (run: AgentRun): string => {
  if (run.answer) return run.answer
  if (run.status === 'completed') return 'Agent 已完成，但后端未返回文本回答。'
  if (run.status === 'cancelled') return 'Agent 运行已取消，未返回最终回答。'
  if (run.status === 'timed_out') return 'Agent 运行超时，未返回最终回答。'
  if (run.status === 'stopped') {
    if (run.stopReason === 'token_budget_exceeded') {
      return 'Agent 达到 token 预算，已完成检索但未生成最终回答。'
    }
    const reason = run.stopReason ? `停止原因：${run.stopReason}。` : ''
    return `Agent 运行已停止。${reason}已完成 ${run.steps.length} 个步骤，但未返回最终回答。`
  }
  return 'Agent 运行失败。'
}

const formatMetric = (value: number | null): string =>
  value === null ? '后端未提供' : String(value)

const getToolCallMetrics = (run: AgentRun): { actual: number; cached: number } => {
  const calls = run.steps.flatMap((step) => step.toolCalls)
  return {
    actual: calls.filter((tool) => !tool.cached).length,
    cached: calls.filter((tool) => tool.cached).length,
  }
}

const ragStatusTitles: Record<AgentRag['status'], string> = {
  loading: '参考来源：加载中',
  success_with_sources: '参考来源：暂无可用来源',
  no_relevant_sources: '参考来源：暂无相关来源',
  knowledge_base_empty: '参考来源：知识库为空',
  rag_unavailable: '参考来源：来源暂不可用',
  embedding_failed: '参考来源：来源暂不可用',
  output_unavailable: '参考来源：来源暂不可用',
  failed: '参考来源：来源暂不可用',
}

const ragStatusDescriptions: Record<AgentRag['status'], string> = {
  loading: 'RAG 正在加载来源，完成后将显示检索结果。',
  success_with_sources: '后端未提供可展示的来源内容。',
  no_relevant_sources:
    '当前知识库没有关于该问题的相关内容（来源数量：0）。如果模型仍然给出回答，那是模型的一般回答，不是基于知识库内容。',
  knowledge_base_empty: '当前知识库没有可检索的内容。',
  rag_unavailable: 'RAG 服务当前不可用，未生成来源。',
  embedding_failed: '查询向量生成失败，未生成来源。',
  output_unavailable: 'RAG 工具输出当前不可用，未生成来源。',
  failed: 'RAG 检索失败，未生成来源。',
}

const getRagAnnouncement = (run: AgentRun): string | null => {
  const ragTools = run.steps.flatMap((step) =>
    step.toolCalls.filter((tool) => tool.name === 'knowledge_search'),
  )
  if (ragTools.length === 0) return null

  const rag = ragTools.at(-1)?.rag
  if (rag === null || rag === undefined) return 'RAG 来源不可用，当前响应没有可展示的来源。'
  if (rag.status === 'loading') return 'RAG 来源加载中。'
  if (rag.references.length > 0) return `RAG 来源已加载，共 ${rag.references.length} 条。`
  if (rag.status === 'rag_unavailable' || rag.status === 'failed') {
    return 'RAG 来源加载失败，当前响应没有可展示的来源。'
  }
  if (rag.status === 'no_relevant_sources')
    return '知识库未找到相关来源，当前响应没有可展示的来源。'
  return 'RAG 未找到相关来源。'
}

const getSafeChatErrorMessage = (error: ChatBackendError): string => {
  if (error.status === 401 || error.status === 403) return 'Chat 请求未通过鉴权，请检查运行时凭据。'
  if (error.status === 408 || error.status === 504) return 'Chat 请求超时，请稍后重试。'
  if (error.status === 429) return 'Chat 请求过于频繁，请稍后重试。'
  if (error.status >= 500) return 'Chat 服务暂时不可用，请稍后重试。'
  return `Chat 请求失败（HTTP ${error.status}），请稍后重试。`
}

const isConversationNotFoundError = (error: unknown): boolean =>
  error instanceof ChatBackendError &&
  (error.status === 404 || error.code === 'CONVERSATION_NOT_FOUND')

const getSafeAgentErrorMessage = (error: Error): string => {
  if (error instanceof AgentNetworkError) return '无法连接 Agent 服务，请稍后重试。'
  if (error instanceof AgentResponseError) return 'Agent 服务返回了无法识别的响应，请稍后重试。'
  if (error instanceof AgentBackendError) {
    if (error.status === 401 || error.status === 403)
      return 'Agent 请求未通过鉴权，请检查运行时凭据。'
    if (error.status === 408 || error.status === 504) return 'Agent 请求超时，请稍后重试。'
    if (error.status === 429) return 'Agent 请求过于频繁，请稍后重试。'
    if (error.status >= 500) return 'Agent 服务暂时不可用，请稍后重试。'
    return `Agent 请求失败（HTTP ${error.status}），请稍后重试。`
  }
  return 'Agent 请求失败，未展示未经处理的内部错误。'
}

const MAX_RAG_CONTENT_LENGTH = 1200

const formatRagValue = (value: string | number | null): string =>
  value === null ? '后端未提供' : String(value)

const getRagContent = (
  reference: AgentRagReference,
): { content: string | null; truncated: boolean } => {
  if (reference.content === null) {
    return { content: null, truncated: reference.truncated }
  }
  const contentTruncated = reference.content.length > MAX_RAG_CONTENT_LENGTH
  return {
    content: contentTruncated
      ? `${reference.content.slice(0, MAX_RAG_CONTENT_LENGTH - 1)}…`
      : reference.content,
    truncated: reference.truncated || contentTruncated,
  }
}

function RagReferenceCard({ reference }: { reference: AgentRagReference }): JSX.Element {
  const { content, truncated } = getRagContent(reference)

  return (
    <li className="ragReferenceCard">
      <dl className="ragReferenceFacts">
        <div>
          <dt>文档标识</dt>
          <dd>{formatRagValue(reference.documentId)}</dd>
        </div>
        <div>
          <dt>分块标识</dt>
          <dd>{formatRagValue(reference.chunkId)}</dd>
        </div>
        <div>
          <dt>分块序号</dt>
          <dd>{formatRagValue(reference.chunkIndex)}</dd>
        </div>
        <div>
          <dt>距离</dt>
          <dd>{formatRagValue(reference.distance)}</dd>
        </div>
      </dl>
      <div className="ragReferenceContent">
        <span className="metricLabel">后端片段摘要</span>
        <p>{content ?? '后端未提供'}</p>
      </div>
      {truncated ? (
        <p className="ragTruncatedNotice">内容已按安全边界截断，未展示完整片段。</p>
      ) : null}
    </li>
  )
}

function RagSection({ tool }: { tool: AgentToolCall }): JSX.Element | null {
  const [expanded, setExpanded] = useState(true)
  if (tool.name !== 'knowledge_search') return null
  if (tool.rag === null) {
    return (
      <div className="ragUnavailableNotice" role="status">
        <strong>参考来源：暂无可用来源</strong>
        <span>状态：后端未提供 · 来源数量：后端未提供</span>
        <span>当前 Agent Run 响应未提供可展示的来源字段；前端不会生成来源卡片或引用。</span>
      </div>
    )
  }

  const { rag } = tool
  const hasReferences = rag.references.length > 0
  const contentId = `${tool.id}-rag-content`
  return (
    <section
      className={hasReferences ? 'ragSources' : 'ragUnavailableNotice'}
      aria-label="RAG 参考来源"
    >
      <button
        type="button"
        className="ragToggle"
        aria-expanded={expanded}
        aria-controls={contentId}
        aria-label={`${hasReferences ? `参考来源：${rag.references.length} 条` : ragStatusTitles[rag.status]}，${expanded ? '收起参考来源' : '展开参考来源'}`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span>
          {hasReferences ? `参考来源：${rag.references.length} 条` : ragStatusTitles[rag.status]}
        </span>
        <span aria-hidden="true">{expanded ? '收起' : '展开'}</span>
      </button>
      <div id={contentId} className="ragContent" hidden={!expanded}>
        <div className="ragHeader">
          <span>
            关联工具：{localizeToolName(tool.name)} · 步骤序号：{tool.stepIndex} · 调用标识：
            {tool.callId ?? '后端未提供'}
          </span>
          <span>
            状态：{rag.status} · 来源数量：{rag.references.length}
          </span>
        </div>
        {rag.warning ? <p className="ragWarning">不可信参考提示：{rag.warning}</p> : null}
        {hasReferences ? (
          <ol className="ragReferenceList">
            {rag.references.map((reference, index) => (
              <RagReferenceCard
                key={`${tool.callId ?? tool.id}-rag-${index}`}
                reference={reference}
              />
            ))}
          </ol>
        ) : rag.status === 'no_relevant_sources' ? (
          <div className="ragNoSourcesNotice">
            <strong>当前知识库没有关于该问题的相关内容</strong>
            <span>
              {localizeToolName(tool.name)} 未找到相关来源（来源数量：0）；这不是数据库或 Embedding
              故障。
            </span>
            <span>
              如果模型仍然给出回答，那是模型的一般回答，不是基于知识库内容，请不要把它当作知识库答案。
            </span>
          </div>
        ) : (
          <span>{ragStatusDescriptions[rag.status]}</span>
        )}
        {rag.errorCode ? <span>RAG 错误码：{rag.errorCode}</span> : null}
      </div>
    </section>
  )
}

function ToolCallCard({ tool }: { tool: AgentToolCall }): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const contentId = `${tool.id}-content`

  return (
    <div className={`toolCard tool-${tool.status}`}>
      <button
        type="button"
        className="traceToggle toolToggle"
        aria-expanded={expanded}
        aria-controls={contentId}
        aria-label={`工具调用 ${localizeToolName(tool.name)}，${toolStatusLabels[tool.status]}，${expanded ? '收起' : '展开'}`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="traceToggleText">
          <strong>{localizeToolName(tool.name)}</strong>
          {!isKnownTool(tool.name) ? <span className="unknownTool">未知工具</span> : null}
        </span>
        <span className={`traceBadge badge-${tool.status}`}>{toolStatusLabels[tool.status]}</span>
      </button>
      <div id={contentId} className="traceDetails" hidden={!expanded}>
        <p>步骤序号：{tool.stepIndex}</p>
        <p>调用标识：{tool.callId ?? '后端未提供'}</p>
        <p>执行方式：{tool.cached ? '复用已有结果，未重复执行' : '实际执行'}</p>
        <p>耗时：{tool.durationMs === null ? '后端未提供' : `${tool.durationMs} ms`}</p>
        <p>参数数量：{formatMetric(tool.argumentCount)}</p>
        <p>输入摘要：{tool.inputSummary ?? '后端未提供'}</p>
        <p>输出摘要：{tool.outputSummary ?? '后端未提供'}</p>
        <p>结果字符数：{formatMetric(tool.resultChars)}</p>
        {tool.errorCode ? <p>错误码：{tool.errorCode}</p> : null}
        {tool.errorMessage ? <p className="safeError">{tool.errorMessage}</p> : null}
        <RagSection tool={tool} />
      </div>
    </div>
  )
}

function TraceStepCard({ step }: { step: AgentTraceStep }): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const contentId = `${step.id}-content`

  return (
    <li className={`traceStep step-${step.status}`}>
      <button
        type="button"
        className="traceToggle"
        aria-expanded={expanded}
        aria-controls={contentId}
        aria-label={`步骤 ${step.index}，${decisionLabels[step.decisionKind]}，${expanded ? '收起' : '展开'}`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="traceToggleText">
          <strong>步骤 {step.index}</strong>
          <span>{decisionLabels[step.decisionKind]}</span>
        </span>
        <span className={`traceBadge badge-${step.status}`}>{runStatusLabels[step.status]}</span>
      </button>
      {step.toolCalls.length > 0 ? (
        <ul className="toolPreviewList" aria-label={`步骤 ${step.index} 工具摘要`}>
          {step.toolCalls.map((tool) => (
            <li key={tool.id}>
              <strong>{localizeToolName(tool.name)}</strong>
              {!isKnownTool(tool.name) ? <em>未知工具</em> : null}
              {tool.cached ? <em>复用结果</em> : null}
              <span>{toolStatusLabels[tool.status]}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div id={contentId} className="traceDetails stepDetails" hidden={!expanded}>
        <p>{step.summary}</p>
        <p>工具数量：{formatMetric(step.toolCount)}</p>
        <dl className="traceFacts">
          <div>
            <dt>开始时间</dt>
            <dd>{formatAgentTimestamp(step.startedAt)}</dd>
          </div>
          <div>
            <dt>完成时间</dt>
            <dd>{formatAgentTimestamp(step.completedAt)}</dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd>{step.durationMs === null ? '后端未提供' : `${step.durationMs} ms`}</dd>
          </div>
        </dl>
        <dl className="srFacts" aria-label="步骤时间信息">
          <div>
            <dt>开始时间</dt>
            <dd>{formatAgentTimestamp(step.startedAt)}</dd>
          </div>
          <div>
            <dt>完成时间</dt>
            <dd>{formatAgentTimestamp(step.completedAt)}</dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd>{step.durationMs === null ? '后端未提供' : `${step.durationMs} ms`}</dd>
          </div>
        </dl>
        {step.events.length > 0 ? (
          <div className="eventSummary">
            <span className="metricLabel">可审计事件</span>
            <ol>
              {compactAgentTraceEvents(step.events).map(({ event, count }) => (
                <li key={event.id}>
                  {eventLabels[event.kind] ?? event.kind}
                  {count > 1 ? `（${count} 次）` : null}
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <p>关联事件：后端未提供</p>
        )}
        {step.toolCalls.map((tool) => (
          <ToolCallCard key={tool.id} tool={tool} />
        ))}
      </div>
    </li>
  )
}

function App({ chatClient, agentClient }: AppProps): JSX.Element {
  const runtimeConfig = useMemo(() => getRuntimeConfig(), [])
  const ragStatus = useRagRuntimeStatus(runtimeConfig.apiBaseUrl, runtimeConfig.ragEnabled)
  const [page, setPage] = useState<AppPage>('dashboard')
  const [userApiKey, setUserApiKey] = useState(
    () => sessionStorage.getItem(USER_KEY_STORAGE) ?? runtimeConfig.apiKey ?? '',
  )
  const [userApiKeyInput, setUserApiKeyInput] = useState(
    () => sessionStorage.getItem(USER_KEY_STORAGE) ?? '',
  )
  // Auth page switching
  const [authPage, setAuthPage] = useState<'login' | 'register' | null>(null)
  // Run replay: run_id opened from the console Trace panel
  const [replayRunId, setReplayRunId] = useState<string | null>(null)
  // Workspace selection
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null)
  // activeWorkspaceRole will be set by WorkspaceSwitcher in Sprint A5 X-Workspace-Id integration
  const [activeWorkspaceRole, setActiveWorkspaceRole] = useState<string | null>(null)
  void setActiveWorkspaceRole // referenced for future use
  const effectiveApiKey = userApiKey.trim() || runtimeConfig.apiKey
  // Auth client (recreated when apiKey changes)
  const authClient = useMemo(
    () => createAuthClient({ apiBaseUrl: runtimeConfig.apiBaseUrl }),
    [runtimeConfig.apiBaseUrl],
  )
  const platformClient = useMemo(
    () =>
      createPlatformClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const configClient = useMemo(
    () =>
      createConfigClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const knowledgeClient = useMemo(
    () =>
      createKnowledgeClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const workflowClient = useMemo(
    () =>
      createWorkflowClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const workflowBuilderClient = useMemo(
    () =>
      createWorkflowBuilderClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const [modelCount, setModelCount] = useState<number | null>(null)
  const [modelName, setModelName] = useState<string | null>(null)
  useEffect(() => {
    if (!effectiveApiKey) {
      setModelCount(null)
      setModelName(null)
      return
    }

    let cancelled = false
    void platformClient
      .listModels()
      .then((models) => {
        if (cancelled) return
        setModelCount(models.length)
        setModelName(models[0]?.id ?? null)
      })
      .catch(() => {
        if (cancelled) return
        setModelCount(null)
        setModelName(null)
      })
    return () => {
      cancelled = true
    }
  }, [effectiveApiKey, platformClient])
  const defaultChatClient = useMemo(
    () =>
      createChatClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const defaultAgentClient = useMemo(
    () =>
      createAgentClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: effectiveApiKey,
      }),
    [effectiveApiKey, runtimeConfig.apiBaseUrl],
  )
  const resolvedChatClient = chatClient ?? defaultChatClient
  const resolvedAgentClient = agentClient ?? defaultAgentClient
  const [mode, setMode] = useState<ConsoleMode>('chat')
  const [ragPreset, setRagPreset] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sessionCount, setSessionCount] = useState(0)
  const [clearedCount, setClearedCount] = useState(0)
  const [threadId, setThreadId] = useState<string | null>(() =>
    sessionStorage.getItem(THREAD_ID_STORAGE),
  )
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [conversationsStatus, setConversationsStatus] = useState<
    'idle' | 'loading' | 'ready' | 'failed'
  >('idle')
  const [requestStatus, setRequestStatus] = useState<RequestStatus>('idle')
  const [agentSseActive, setAgentSseActive] = useState(false)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null)
  const [traceUnavailableMessage, setTraceUnavailableMessage] = useState<string | null>(null)
  const [lastAgentInput, setLastAgentInput] = useState<AgentRunInput | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('准备就绪。')
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null)
  const [lastChatInput, setLastChatInput] = useState<ChatApiMessage[] | null>(null)
  const [lastChatAssistantMessageId, setLastChatAssistantMessageId] = useState<string | null>(null)
  const [lastAgentAssistantMessageId, setLastAgentAssistantMessageId] = useState<string | null>(
    null,
  )
  const agentStreamState = useRef<AgentStreamState>(initialAgentStreamState)
  const activeRequest = useRef<ActiveRequest | null>(null)
  const historyRestoreController = useRef<AbortController | null>(null)
  const historyRestoreStartedFor = useRef<string | null>(null)

  useEffect(() => {
    if (!effectiveApiKey) {
      setConversations([])
      setConversationsStatus('idle')
      return
    }

    let cancelled = false
    setConversationsStatus('loading')
    void resolvedChatClient
      .listConversations()
      .then((list) => {
        if (cancelled) return
        setConversations(list)
        setConversationsStatus('ready')
      })
      .catch(() => {
        if (cancelled) return
        setConversationsStatus('failed')
      })

    return () => {
      cancelled = true
    }
  }, [effectiveApiKey, resolvedChatClient])

  useEffect(() => {
    if (!threadId || messages.length > 0 || historyRestoreStartedFor.current === threadId) {
      return
    }

    historyRestoreStartedFor.current = threadId
    const controller = new AbortController()
    historyRestoreController.current = controller
    let cancelled = false
    void resolvedChatClient
      .listThreadMessages(threadId, controller.signal)
      .then((history) => {
        if (cancelled || historyRestoreStartedFor.current !== threadId) return
        setMessages(history)
        setErrorMessage(null)
        setAnnouncement('已恢复会话历史。')
      })
      .catch((error: unknown) => {
        if (cancelled || historyRestoreStartedFor.current !== threadId) return
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (isConversationNotFoundError(error)) {
          setThreadId(null)
          sessionStorage.removeItem(THREAD_ID_STORAGE)
          setErrorMessage(null)
          setAnnouncement('原会话已失效，已准备好新会话。')
          return
        }
        setErrorMessage('会话历史恢复失败，可继续提问。')
        setAnnouncement('会话历史恢复失败，可继续提问。')
      })
      .finally(() => {
        if (historyRestoreController.current === controller) {
          historyRestoreController.current = null
        }
      })

    return () => {
      cancelled = true
      controller.abort()
      historyRestoreStartedFor.current = null
      if (historyRestoreController.current === controller) {
        historyRestoreController.current = null
      }
    }
  }, [messages.length, resolvedChatClient, threadId])

  const activeConversation = conversations.find((item) => item.thread_id === threadId)
  const sessionLabel =
    activeConversation?.title ?? (sessionCount === 0 ? '未命名会话' : `本地会话 ${sessionCount}`)
  const isActive =
    agentSseActive ||
    requestStatus === 'sending' ||
    requestStatus === 'agent_running' ||
    ['connecting', 'waiting', 'running', 'tool_running', 'rag_loading'].includes(requestStatus)

  const replaceAssistantContent = (messageId: string, content: string): void => {
    setMessages((currentMessages) =>
      currentMessages.map((message) =>
        message.id === messageId ? { ...message, content } : message,
      ),
    )
  }

  const refreshConversations = (): void => {
    if (!effectiveApiKey) return
    void resolvedChatClient
      .listConversations()
      .then((list) => {
        setConversations(list)
        setConversationsStatus('ready')
      })
      .catch(() => {
        setConversationsStatus('failed')
      })
  }

  const storeThreadId = (nextThreadId: string): void => {
    setThreadId(nextThreadId)
    sessionStorage.setItem(THREAD_ID_STORAGE, nextThreadId)
    refreshConversations()
  }

  const cancelHistoryRestore = (): void => {
    historyRestoreController.current?.abort()
    historyRestoreController.current = null
    historyRestoreStartedFor.current = null
  }

  const resetConversation = (action: 'new' | 'clear'): void => {
    cancelHistoryRestore()
    if (activeRequest.current) {
      activeRequest.current.stopped = true
      activeRequest.current.controller.abort()
      activeRequest.current = null
    }

    setMessages([])
    setDraft('')
    setRequestId(null)
    setAgentRun(null)
    agentStreamState.current = initialAgentStreamState
    setTraceUnavailableMessage(null)
    setLastAgentInput(null)
    setLastChatInput(null)
    setLastChatAssistantMessageId(null)
    setLastAgentAssistantMessageId(null)
    setErrorMessage(null)
    setCopyFeedback(null)
    setAnnouncement(action === 'new' ? '已新建会话。' : '已清空当前会话。')
    setRequestStatus('idle')
    setAgentSseActive(false)

    setThreadId(null)
    sessionStorage.removeItem(THREAD_ID_STORAGE)

    if (action === 'new') {
      setSessionCount((count) => count + 1)
    } else {
      setClearedCount((count) => count + 1)
    }
  }

  const openConversation = (nextThreadId: string): void => {
    if (nextThreadId === threadId) return
    cancelHistoryRestore()
    if (activeRequest.current) {
      activeRequest.current.stopped = true
      activeRequest.current.controller.abort()
      activeRequest.current = null
    }

    setMessages([])
    setDraft('')
    setRequestId(null)
    setAgentRun(null)
    agentStreamState.current = initialAgentStreamState
    setTraceUnavailableMessage(null)
    setLastAgentInput(null)
    setLastChatInput(null)
    setLastChatAssistantMessageId(null)
    setLastAgentAssistantMessageId(null)
    setErrorMessage(null)
    setCopyFeedback(null)
    setRequestStatus('idle')
    setAgentSseActive(false)
    setThreadId(nextThreadId)
    sessionStorage.setItem(THREAD_ID_STORAGE, nextThreadId)
    setPage('console')
    setAnnouncement('已打开历史会话。')
  }

  const handleStop = (): void => {
    const request = activeRequest.current
    if (!request) return

    request.stopped = true
    request.controller.abort()
    activeRequest.current = null
    setAgentSseActive(false)
    if (request.kind === 'agent') {
      replaceAssistantContent(request.assistantMessageId, '请求已取消；后端终态未知。')
      setRequestStatus('client_cancelled')
      setAnnouncement('已停止等待 Agent 响应；后端终态未知。')
    } else {
      setRequestStatus('stopped')
      setAnnouncement('Chat 请求已停止。')
    }
  }

  const executeChat = async (
    nextMessages: ChatMessage[],
    assistantMessage: ChatMessage,
  ): Promise<void> => {
    const request: ActiveRequest = {
      controller: new AbortController(),
      stopped: false,
      kind: 'chat',
      assistantMessageId: assistantMessage.id,
    }
    activeRequest.current = request
    setAgentSseActive(false)
    setRequestId(null)
    setAgentRun(null)
    agentStreamState.current = initialAgentStreamState
    setTraceUnavailableMessage(null)
    setErrorMessage(null)
    setCopyFeedback(null)
    setLastChatAssistantMessageId(assistantMessage.id)
    setRequestStatus('sending')
    setAnnouncement('Chat 请求已开始。')

    try {
      const result = await resolvedChatClient.streamChat(
        nextMessages.map(({ role, content }) => ({ role, content })),
        {
          onDelta: (delta) => {
            if (activeRequest.current !== request || request.stopped) return
            setMessages((currentMessages) =>
              currentMessages.map((message) =>
                message.id === assistantMessage.id
                  ? { ...message, content: `${message.content}${delta}` }
                  : message,
              ),
            )
          },
          onRequestId: (id) => {
            if (activeRequest.current === request && !request.stopped) setRequestId(id)
          },
          onThreadId: (id) => {
            if (activeRequest.current === request && !request.stopped) storeThreadId(id)
          },
        },
        request.controller.signal,
        threadId,
      )
      if (!request.stopped) {
        if (result.threadId) storeThreadId(result.threadId)
        setRequestStatus('completed')
        setAnnouncement('Chat 请求已完成。')
      }
    } catch (error) {
      if (request.stopped) return
      if (error instanceof DOMException && error.name === 'AbortError') {
        setRequestStatus('stopped')
        setAnnouncement('Chat 请求已停止。')
      } else if (error instanceof Error && error.name === 'ChatStreamInterruptedError') {
        setErrorMessage('Chat SSE 连接已断开，可重试。')
        setRequestStatus('interrupted')
        setAnnouncement('Chat SSE 已断连，可重试。')
      } else if (error instanceof ChatBackendError) {
        setErrorMessage(getSafeChatErrorMessage(error))
        setRequestStatus('chat_failed')
        setAnnouncement('Chat 后端请求失败，可重试。')
        if (error.requestId) setRequestId(error.requestId)
        if (error.threadId) storeThreadId(error.threadId)
      } else if (error instanceof Error && error.name === 'ChatNetworkError') {
        setErrorMessage('无法连接 Chat 服务，请稍后重试。')
        setRequestStatus('network')
        setAnnouncement('Chat 网络请求失败，可重试。')
      } else {
        setErrorMessage('Chat 请求失败，可重试。')
        setRequestStatus('network')
        setAnnouncement('Chat 请求失败，可重试。')
      }
    } finally {
      if (activeRequest.current === request) activeRequest.current = null
    }
  }

  const executeAgent = async (
    input: AgentRunInput,
    assistantMessage: ChatMessage,
  ): Promise<void> => {
    const request: ActiveRequest = {
      controller: new AbortController(),
      stopped: false,
      kind: 'agent',
      assistantMessageId: assistantMessage.id,
    }
    activeRequest.current = request
    setAgentSseActive(resolvedAgentClient.streamAgent !== undefined)
    setRequestId(null)
    setAgentRun(null)
    agentStreamState.current = initialAgentStreamState
    setTraceUnavailableMessage(null)
    setErrorMessage(null)
    setCopyFeedback(null)
    setLastAgentAssistantMessageId(assistantMessage.id)
    setRequestStatus(resolvedAgentClient.streamAgent ? 'connecting' : 'agent_running')
    setLastAgentInput(input)
    setAnnouncement('Agent Run 已开始。')

    try {
      let run: AgentRun
      if (resolvedAgentClient.streamAgent) {
        await resolvedAgentClient.streamAgent(
          input,
          {
            onEvent: (event) => {
              if (activeRequest.current !== request || request.stopped) return
              if (event.thread_id) storeThreadId(event.thread_id)
              const next = reduceAgentStream(agentStreamState.current, event)
              if (next === agentStreamState.current) return
              agentStreamState.current = next
              if (next.run) {
                setAgentRun(next.run)
                if (next.run.answer !== null)
                  replaceAssistantContent(assistantMessage.id, next.run.answer)
              }
              if (event.event === 'run_started') setRequestStatus('waiting')
              if (event.event === 'step_started') setRequestStatus('running')
              if (event.event === 'tool_started') setRequestStatus('tool_running')
              if (event.event === 'rag_started') setRequestStatus('rag_loading')
              if (event.event === 'tool_completed') setRequestStatus('tool_completed')
              if (event.event === 'tool_failed') setRequestStatus('tool_failed')
              if (event.event === 'answer_delta') setRequestStatus('running')
              if (event.event === 'assistant_message') setRequestStatus('running')
              if (event.event === 'rag_started') setAnnouncement('RAG 来源加载中。')
            },
          },
          request.controller.signal,
        )
        run = agentStreamState.current.run ?? {
          runId: null,
          status: 'unknown',
          answer: null,
          stopReason: null,
          steps: [],
          events: [],
          usage: {
            promptTokens: null,
            completionTokens: null,
            totalTokens: null,
            estimated: false,
          },
        }
        if (!agentStreamState.current.terminal)
          throw new AgentStreamFormatError('Agent SSE 未提供终止事件。')
      } else {
        run = await resolvedAgentClient.runAgent(input, request.controller.signal)
      }
      if (activeRequest.current !== request || request.stopped) return
      setAgentRun(run)
      if (run.threadId) storeThreadId(run.threadId)
      replaceAssistantContent(assistantMessage.id, fallbackAnswerForRun(run))
      setRequestStatus(requestStatusForRun(run.status))
      const runAnnouncement =
        run.status === 'completed'
          ? 'Agent Run 已完成。'
          : run.status === 'timed_out'
            ? 'Agent Run 已超时，可重试。'
            : run.status === 'cancelled'
              ? 'Agent Run 已被后端取消。'
              : run.status === 'stopped'
                ? run.stopReason === 'token_budget_exceeded'
                  ? 'Agent 达到 token 预算，已完成检索但未生成最终回答。'
                  : 'Agent Run 已停止。'
                : 'Agent Run 失败，可重试。'
      const ragAnnouncement = getRagAnnouncement(run)
      setAnnouncement(ragAnnouncement ? `${ragAnnouncement} ${runAnnouncement}` : runAnnouncement)
    } catch (error) {
      if (request.stopped) return
      if (error instanceof DOMException && error.name === 'AbortError') {
        replaceAssistantContent(assistantMessage.id, '请求已取消；后端终态未知。')
        setRequestStatus('client_cancelled')
        setAnnouncement('已停止等待 Agent 响应；后端终态未知。')
      } else if (error instanceof AgentNetworkError) {
        const message = getSafeAgentErrorMessage(error)
        if (error.threadId) storeThreadId(error.threadId)
        replaceAssistantContent(assistantMessage.id, message)
        setTraceUnavailableMessage('本次 Agent 请求未收到可展示的 Trace。')
        setErrorMessage(message)
        setRequestStatus('connection_lost')
        setAnnouncement('Agent 网络请求失败，可重试。')
      } else if (error instanceof AgentStreamFormatError) {
        const message = 'Agent SSE 响应格式错误，请重试。'
        replaceAssistantContent(assistantMessage.id, message)
        setTraceUnavailableMessage('本次 Agent 请求的实时事件无法安全解析。')
        setErrorMessage(message)
        setRequestStatus('response_format_error')
        setAnnouncement('Agent 响应格式错误，可重试。')
      } else if (error instanceof AgentBackendError || error instanceof AgentResponseError) {
        const message = getSafeAgentErrorMessage(error)
        if (error instanceof AgentBackendError && error.threadId) storeThreadId(error.threadId)
        replaceAssistantContent(assistantMessage.id, message)
        setTraceUnavailableMessage('本次 Agent 请求未收到可展示的 Trace。')
        setErrorMessage(message)
        setRequestStatus('failed')
        setAnnouncement('Agent 请求失败，可重试。')
      } else {
        const message = getSafeAgentErrorMessage(error instanceof Error ? error : new Error())
        replaceAssistantContent(assistantMessage.id, message)
        setTraceUnavailableMessage('本次 Agent 请求未收到可展示的 Trace。')
        setErrorMessage(message)
        setRequestStatus('failed')
        setAnnouncement('Agent 请求失败，可重试。')
      }
    } finally {
      if (activeRequest.current === request) {
        activeRequest.current = null
        setAgentSseActive(false)
      }
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const content = draft.trim()
    if (!content || isActive) return
    cancelHistoryRestore()
    if (sessionCount === 0) setSessionCount(1)

    const userMessage = createMessage('user', content)
    const assistantMessage = createMessage('assistant', '')
    const previousMessages = messages
    const nextMessages = [...previousMessages, userMessage]
    setMessages([...nextMessages, assistantMessage])
    setDraft('')

    if (mode === 'agent') {
      await executeAgent(
        {
          message: content,
          history: previousMessages.map(({ role, content: messageContent }) => ({
            role,
            content: messageContent,
          })),
          threadId,
          ...(ragPreset ? { preset: 'rag' as const } : {}),
        },
        assistantMessage,
      )
    } else {
      const chatInput = nextMessages.map(({ role, content: messageContent }) => ({
        role,
        content: messageContent,
      }))
      setLastChatInput(chatInput)
      await executeChat(nextMessages, assistantMessage)
    }
  }

  const handleRetry = async (): Promise<void> => {
    if (isActive) return
    if (mode === 'agent' && lastAgentInput) {
      const previousAssistantId = lastAgentAssistantMessageId
      const assistantMessage = createMessage('assistant', '')
      setMessages((current) => [
        ...current.filter((message) => message.id !== previousAssistantId),
        assistantMessage,
      ])
      setAnnouncement('重新运行 Agent 已开始。')
      await executeAgent(lastAgentInput, assistantMessage)
    } else if (mode === 'chat' && lastChatInput) {
      const previousAssistantId = lastChatAssistantMessageId
      const assistantMessage = createMessage('assistant', '')
      setMessages((current) => [
        ...current.filter((message) => message.id !== previousAssistantId),
        assistantMessage,
      ])
      setAnnouncement('重新发送 Chat 已开始。')
      await executeChat(
        lastChatInput.map(({ role, content }) => ({
          id: `${role}-${crypto.randomUUID()}`,
          role,
          content,
        })),
        assistantMessage,
      )
    }
  }

  const handleCopy = async (label: string, value: string | null): Promise<void> => {
    if (!value) return
    if (!navigator.clipboard) {
      setCopyFeedback(`${label}复制失败：当前环境不支持剪贴板。`)
      return
    }
    try {
      await navigator.clipboard.writeText(value)
      setCopyFeedback(`${label}已复制。`)
    } catch {
      setCopyFeedback(`${label}复制失败，请手动选择文本。`)
    }
  }

  const traceStatus = agentRun
    ? agentRunStatusLabel(agentRun)
    : traceUnavailableMessage
      ? 'Trace 不可用'
      : '无运行结果'
  const agentSseAvailable = resolvedAgentClient.streamAgent !== undefined
  const canRetry =
    !isActive &&
    ((mode === 'agent' && lastAgentInput !== null) ||
      (mode === 'chat' && lastChatInput !== null)) &&
    [
      'failed',
      'network',
      'timed_out',
      'chat_failed',
      'interrupted',
      'connection_lost',
      'response_format_error',
    ].includes(requestStatus)

  if (page === 'admin') {
    return (
      <AdminDashboard apiBaseUrl={runtimeConfig.apiBaseUrl} onBack={() => setPage('dashboard')} />
    )
  }

  const applyUserApiKey = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const normalized = userApiKeyInput.trim()
    setUserApiKey(normalized)
    if (normalized) {
      sessionStorage.setItem(USER_KEY_STORAGE, normalized)
      setAnnouncement('用户 Key 已保存，本次请求将使用该 Key。')
    } else {
      sessionStorage.removeItem(USER_KEY_STORAGE)
      setAnnouncement('已清除用户 Key，将使用开发环境默认凭据（如有）。')
    }
  }

  const handleLogin = (apiKey: string) => {
    sessionStorage.setItem(USER_KEY_STORAGE, apiKey)
    setUserApiKey(apiKey)
    setUserApiKeyInput(apiKey)
    setAuthPage(null)
    setAnnouncement('登录成功。')
  }

  const handleRegister = (apiKey: string) => {
    sessionStorage.setItem(USER_KEY_STORAGE, apiKey)
    setUserApiKey(apiKey)
    setUserApiKeyInput(apiKey)
    setAuthPage(null)
    setAnnouncement('注册成功，已自动登录。')
  }

  // ── Auth page rendering ──────────────────────────────────────────
  // When user explicitly chose to log in/register, show auth pages.
  // Legacy key entry via sidebar still works as before.
  if (authPage === 'login') {
    return (
      <LoginPage
        client={authClient}
        onLogin={handleLogin}
        onSwitchToRegister={() => setAuthPage('register')}
      />
    )
  }
  if (authPage === 'register') {
    return (
      <RegisterPage
        client={authClient}
        onRegister={handleRegister}
        onSwitchToLogin={() => setAuthPage('login')}
      />
    )
  }

  const navigateToConsole = (nextDraft?: string): void => {
    if (nextDraft) setDraft(nextDraft)
    setPage('console')
    setAnnouncement(nextDraft ? '演示问题已带入对话工作台。' : '已打开对话工作台。')
  }

  const openRagChat = (): void => {
    setMode('agent')
    setRagPreset(true)
    setDraft(RAG_PRESET_QUESTION)
    setPage('console')
    setAnnouncement('已进入知识库问答（RAG Agent preset），问题已准备好。')
  }

  const handleNewSession = (): void => {
    resetConversation('new')
    setPage('console')
  }

  const renderPlatformShell = (content: JSX.Element): JSX.Element => {
    const navigation: Array<{ id: AppPage; label: string; shortLabel: string }> = [
      { id: 'dashboard', label: '平台概览', shortLabel: '概览' },
      { id: 'console', label: '对话工作台', shortLabel: '对话' },
      { id: 'workflow', label: 'PDF 工作流', shortLabel: '工作流' },
      { id: 'workflow-builder', label: 'Workflow Builder', shortLabel: '编排' },
      { id: 'knowledge', label: '知识库', shortLabel: 'RAG' },
      { id: 'prompts', label: 'Prompt Studio', shortLabel: 'Prompt' },
      { id: 'agents', label: 'Agent Studio', shortLabel: 'Agent' },
      { id: 'tools', label: 'Tool Center', shortLabel: '工具' },
      { id: 'usage', label: '用量仪表盘', shortLabel: '用量' },
      { id: 'billing', label: 'Billing / 计划', shortLabel: '计划' },
      { id: 'runs', label: 'Run 历史', shortLabel: 'Run' },
      { id: 'models', label: '模型目录', shortLabel: '模型' },
    ]

    return (
      <main className="platformShell">
        <aside className="platformSidebar" aria-label="平台导航">
          <div className="brandLockup">
            <span className="brandMark" aria-hidden="true">
              A
            </span>
            <div>
              <strong>AI Platform</strong>
              <span>MINI / LOCAL LAB</span>
            </div>
          </div>
          <nav className="platformNav" aria-label="主导航">
            <WorkspaceSwitcher
              client={authClient}
              apiKey={effectiveApiKey ?? ''}
              currentWorkspaceId={activeWorkspaceId}
              onWorkspaceChange={(id) => {
                setActiveWorkspaceId(id)
                // TODO(Sprint A5): set X-Workspace-Id header for API calls
              }}
            />
            <span className="navSectionLabel">PAGES</span>
            {navigation.map((item) => (
              <button
                type="button"
                key={item.id}
                className={
                  page === item.id ? 'platformNavItem platformNavItemActive' : 'platformNavItem'
                }
                onClick={() => setPage(item.id)}
                aria-current={page === item.id ? 'page' : undefined}
              >
                <span className="navItemGlyph" aria-hidden="true">
                  {item.shortLabel.slice(0, 1)}
                </span>
                <span>{item.label}</span>
              </button>
            ))}
            <span className="navSectionLabel navSectionLabelSpaced">CONTROL</span>
            <button type="button" className="platformNavItem" onClick={() => setPage('admin')}>
              <span className="navItemGlyph" aria-hidden="true">
                S
              </span>
              <span>管理员后台</span>
            </button>
          </nav>
          <div className="sidebarSession">
            <span className="navSectionLabel">SESSION</span>
            <form className="sidebarKeyForm" onSubmit={applyUserApiKey}>
              <label htmlFor="user-api-key">用户 API Key</label>
              <p className="sidebarKeyHint">
                管理员创建普通 Key 后粘贴到这里；管理员 Key 不要填入。
              </p>
              <input
                id="user-api-key"
                type="password"
                value={userApiKeyInput}
                onChange={(event) => setUserApiKeyInput(event.target.value)}
                placeholder="sk-…"
                autoComplete="off"
              />
              <div className="sidebarKeyActions">
                <button type="submit">保存并使用</button>
                <button
                  type="button"
                  className="secondaryButton"
                  onClick={() => {
                    setUserApiKeyInput('')
                    setUserApiKey('')
                    sessionStorage.removeItem(USER_KEY_STORAGE)
                    setAnnouncement('已清除用户 Key。')
                  }}
                >
                  清除
                </button>
              </div>
              <span className="keyEntryStatus">
                {userApiKey ? '已配置用户 Key' : '未配置用户 Key'}
              </span>
            </form>
            <button type="button" className="newSessionButton" onClick={handleNewSession}>
              新建会话
            </button>
            <button
              type="button"
              className="secondaryButton"
              style={{ marginTop: 8, width: '100%' }}
              onClick={() => setAuthPage('login')}
            >
              登录 / 注册
            </button>
            <span className="navSectionLabel conversationHistoryLabel">会话记录</span>
            <nav className="conversationHistoryList" aria-label="会话记录">
              {conversationsStatus === 'loading' && conversations.length === 0 ? (
                <span className="conversationHistoryEmpty">加载中…</span>
              ) : null}
              {conversationsStatus === 'failed' && conversations.length === 0 ? (
                <span className="conversationHistoryEmpty">会话记录加载失败</span>
              ) : null}
              {conversationsStatus === 'ready' && conversations.length === 0 ? (
                <span className="conversationHistoryEmpty">暂无历史会话</span>
              ) : null}
              {conversations.length > 0 ? (
                <ul className="conversationHistoryItems">
                  {conversations.map((conversation) => (
                    <li key={conversation.thread_id}>
                      <button
                        type="button"
                        className={
                          conversation.thread_id === threadId
                            ? 'conversationHistoryItem conversationHistoryItemActive'
                            : 'conversationHistoryItem'
                        }
                        aria-current={conversation.thread_id === threadId ? 'true' : undefined}
                        onClick={() => openConversation(conversation.thread_id)}
                      >
                        <span className="conversationHistoryTitle">{conversation.title}</span>
                        <span className="conversationHistoryTime">
                          {formatConversationTime(conversation.updated_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </nav>
          </div>
          <div className="sidebarFooter">
            <span
              className={effectiveApiKey ? 'sidebarStatus sidebarStatusOnline' : 'sidebarStatus'}
            >
              <span aria-hidden="true" />
              {effectiveApiKey ? '已登录' : '需要 API Key'}
            </span>
            <button
              type="button"
              className="secondaryButton"
              onClick={() => {
                sessionStorage.removeItem(USER_KEY_STORAGE)
                setUserApiKey('')
                setUserApiKeyInput('')
                setAnnouncement('已退出登录。')
              }}
              style={{ marginTop: 8, width: '100%' }}
            >
              退出登录
            </button>
            <span className="sidebarVersion">FastAPI · Ollama · React</span>
          </div>
        </aside>
        <div className="platformContent">{content}</div>
      </main>
    )
  }

  if (page === 'dashboard') {
    return renderPlatformShell(
      <Dashboard
        apiKeyConfigured={Boolean(effectiveApiKey)}
        modelCount={modelCount}
        modelName={modelName}
        ragStatus={ragStatus}
        onNavigate={(nextPage, preset) => {
          if (preset === 'agent') {
            setMode('agent')
            setRagPreset(false)
            setDraft('请分析这段代码，并说明 Agent 如何选择工具。')
            setAnnouncement('Agent 演示问题已准备好。')
          }
          setPage(nextPage)
        }}
      />,
    )
  }
  if (page === 'knowledge') {
    return renderPlatformShell(
      <KnowledgeBase
        apiKeyConfigured={Boolean(effectiveApiKey)}
        ragStatus={ragStatus}
        client={knowledgeClient}
        maxUploadBytes={runtimeConfig.ragMaxUploadBytes ?? 10_000_000}
        onOpenRagChat={openRagChat}
      />,
    )
  }
  if (page === 'prompts') {
    return renderPlatformShell(
      <PromptStudio client={configClient} onUsePrompt={navigateToConsole} />,
    )
  }
  if (page === 'agents') {
    return renderPlatformShell(<AgentStudio client={configClient} />)
  }
  if (page === 'tools') {
    return renderPlatformShell(<ToolCenter client={configClient} />)
  }
  if (page === 'usage') {
    return renderPlatformShell(<UsageDashboardPage client={configClient} />)
  }
  if (page === 'billing') {
    return renderPlatformShell(<Billing client={configClient} />)
  }
  if (page === 'runs') {
    return renderPlatformShell(
      <RunList
        client={configClient}
        onOpenRun={(runId) => {
          setReplayRunId(runId)
          setPage('run')
        }}
      />,
    )
  }
  if (page === 'run' && replayRunId !== null) {
    return renderPlatformShell(
      <RunDetail client={configClient} runId={replayRunId} onBack={() => setPage('console')} />,
    )
  }
  if (page === 'models') {
    return renderPlatformShell(
      <ModelCatalog apiKeyConfigured={Boolean(effectiveApiKey)} client={platformClient} />,
    )
  }
  if (page === 'workflow') {
    return renderPlatformShell(
      <WorkflowPanel apiKeyConfigured={Boolean(effectiveApiKey)} client={workflowClient} />,
    )
  }
  if (page === 'workflow-builder') {
    return renderPlatformShell(
      <WorkflowBuilder
        apiKeyConfigured={Boolean(effectiveApiKey)}
        client={workflowBuilderClient}
        configClient={configClient}
      />,
    )
  }
  if (page === 'members') {
    return renderPlatformShell(
      <MemberManagement
        client={authClient}
        apiKey={effectiveApiKey ?? ''}
        workspaceId={activeWorkspaceId ?? ''}
        currentUserRole={activeWorkspaceRole ?? 'member'}
      />,
    )
  }

  return renderPlatformShell(
    <div className="consolePage">
      <div className="shell">
        <section className="hero">
          <div>
            <p className="eyebrow">WORKSPACE · REAL-TIME AI</p>
            <h1>对话与 Agent Trace</h1>
            <p className="heroCopy">
              {
                '普通模式继续使用真实 Chat SSE；Agent 模式使用真实 Agent SSE，Trace 实时更新；回答支持后端真实 answer_delta 增量。'
              }
            </p>
          </div>
          <div className="heroActions">
            <div className="statusPill">{sessionLabel}</div>
            <button type="button" className="secondaryButton" onClick={() => setPage('dashboard')}>
              返回平台概览
            </button>
            <button type="button" className="secondaryButton" onClick={() => setPage('admin')}>
              管理员后台
            </button>
          </div>
        </section>

        <section className="consoleGrid" aria-label="Agent Console">
          <article className="panel conversationPanel" aria-label="会话">
            <div className="panelHeader">
              <div>
                <h2>会话</h2>
                <span className={`requestStatus status-${requestStatus}`} aria-hidden="true">
                  {statusLabels[requestStatus]}
                </span>
              </div>
              {requestId ? (
                <div className="requestIdGroup">
                  <span className="metricLabel">Request ID</span>
                  <button
                    type="button"
                    className="copyButton"
                    aria-label={`复制 Request ID ${requestId}`}
                    onClick={() => void handleCopy('Request ID', requestId)}
                  >
                    {requestId}
                  </button>
                  {copyFeedback ? (
                    <span className="copyFeedback" aria-live="polite">
                      {copyFeedback}
                    </span>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="modeSwitch" role="group" aria-label="请求模式">
              <button
                type="button"
                className={mode === 'chat' ? 'modeActive' : 'secondaryButton'}
                aria-pressed={mode === 'chat'}
                disabled={isActive}
                onClick={() => {
                  setMode('chat')
                  setRagPreset(false)
                }}
              >
                普通 Chat SSE 模式
              </button>
              <button
                type="button"
                className={mode === 'agent' ? 'modeActive' : 'secondaryButton'}
                aria-pressed={mode === 'agent'}
                disabled={isActive}
                onClick={() => {
                  setMode('agent')
                  setRagPreset(false)
                }}
              >
                Agent Run 模式
              </button>
            </div>

            <div className="modeStatus" role="group" aria-label="当前请求模式状态">
              <span
                className={mode === 'chat' ? 'modeBadge modeBadgeChat' : 'modeBadge modeBadgeAgent'}
              >
                {mode === 'chat' ? 'Chat SSE 模式' : 'Agent Run 模式'}
              </span>
              {ragPreset && mode === 'agent' ? (
                <span className="modeBadge modeBadgeRag">RAG Agent preset</span>
              ) : null}
            </div>
            <p className="modeHint">
              {mode === 'chat'
                ? '普通对话，不执行工具调用，不会产生 Agent Tool Trace 或 RAG 来源。'
                : ragPreset
                  ? '知识库问答：先调用 knowledge_search 检索知识库，再基于检索结果回答；实时展示 Trace / Tool Call / RAG。'
                  : '实时 Agent SSE：展示步骤、Tool Call、RAG 状态和来源；终态、超时、取消、预算超限均使用真实后端状态。'}
            </p>

            <div className="srOnlyStatus" role="status" aria-live="polite" aria-atomic="true">
              {statusLabels[requestStatus]}。{announcement}
            </div>

            {messages.length === 0 ? (
              <div className="emptyState">
                <div className="emptyIcon">A</div>
                <h3>{mode === 'chat' ? '开始一段普通对话' : '运行一次真实 Agent'}</h3>
                <p>
                  {mode === 'chat'
                    ? '输入问题后，前端会真实调用 Chat SSE，并将回答增量显示在这里。'
                    : agentSseAvailable
                      ? 'Agent 模式通过真实 Agent SSE 实时更新回答与 Trace；后端 answer_delta 会按增量显示。'
                      : 'Agent Run 将在返回结果后显示回答与 Trace；当前客户端未提供实时 Agent SSE。'}
                </p>
              </div>
            ) : (
              <ol className="messageList" aria-label="消息列表">
                {messages.map((message) => (
                  <li key={message.id} className={`message message-${message.role}`}>
                    <span className="messageRole">{message.role === 'user' ? '你' : '助手'}</span>
                    <p>{message.content || (isActive ? '…' : '（无文本内容）')}</p>
                  </li>
                ))}
              </ol>
            )}

            {errorMessage ? (
              <div className="errorNotice" role="alert">
                <span>{errorMessage}</span>
                {canRetry ? (
                  <button type="button" className="inlineRetryButton" onClick={handleRetry}>
                    {mode === 'chat' ? '重试 Chat' : '重新运行 Agent'}
                  </button>
                ) : null}
              </div>
            ) : null}

            <form className="composer" onSubmit={handleSubmit}>
              <label htmlFor="message-input">输入消息</label>
              <textarea
                id="message-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={
                  mode === 'chat'
                    ? '例如：解释一下当前项目的 Chat SSE 契约'
                    : '例如：请使用 calculator 计算 (12 + 8) × 3'
                }
                rows={3}
                disabled={isActive}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                    event.preventDefault()
                    event.currentTarget.form?.requestSubmit()
                  }
                }}
              />
              <div className="composerActions">
                <span className="composerHint">
                  {mode === 'chat'
                    ? '普通回答为实时 Chat SSE；Enter 换行，Ctrl/⌘ + Enter 发送。'
                    : agentSseAvailable
                      ? 'Agent 使用真实 Agent SSE 实时更新回答与 Trace，后端 answer_delta 会增量显示；Enter 换行，Ctrl/⌘ + Enter 运行。'
                      : 'Agent Run 等待后端结果；当前客户端未提供实时 Agent SSE。Enter 换行，Ctrl/⌘ + Enter 运行。'}
                </span>
                {isActive ? (
                  <button type="button" className="stopButton" onClick={handleStop}>
                    停止请求
                  </button>
                ) : (
                  <button type="submit" disabled={!draft.trim()}>
                    {mode === 'chat' ? '发送消息' : '运行 Agent'}
                  </button>
                )}
              </div>
            </form>
          </article>

          <aside className="panel tracePanel" aria-label="Agent Trace">
            <div className="panelHeader">
              <div>
                <h2>Agent Trace</h2>
                <span>{requestStatus === 'agent_running' ? '运行中' : traceStatus}</span>
              </div>
              <span className="traceDelivery">
                {mode === 'chat'
                  ? 'Chat SSE'
                  : agentSseAvailable
                    ? '实时 Agent SSE'
                    : 'Agent Run 兼容路径'}
              </span>
            </div>

            {mode === 'chat' ? (
              <div className="traceNotice traceChatMode">
                <h3>Chat SSE 模式</h3>
                <p>普通对话，不执行工具调用；不会产生 Agent Tool Trace 或 RAG 来源。</p>
                <p>切换到 Agent Run 模式后可运行真实 Agent 并查看步骤、Tool Call 与 RAG 状态。</p>
              </div>
            ) : isActive && mode === 'agent' && !agentRun && resolvedAgentClient.streamAgent ? (
              <div className="traceNotice">
                <h3>{statusLabels[requestStatus]}</h3>
                <p>正在接收后端真实 Agent SSE，Trace 将随事件实时更新。</p>
              </div>
            ) : requestStatus === 'agent_running' && !agentRun ? (
              <div className="traceNotice">
                <h3>等待 Agent Run</h3>
                <p>正在等待 Agent Run 结果；当前客户端未提供实时 Agent SSE。</p>
              </div>
            ) : requestStatus === 'client_cancelled' ? (
              <div className="traceNotice traceCancelled">
                <h3>前端已停止等待，后端终态未知</h3>
                <p>浏览器已中止等待响应；这不代表后端 Agent Runtime 已被取消。</p>
              </div>
            ) : traceUnavailableMessage ? (
              <div className="traceNotice traceUnavailable">
                <h3>未收到 Trace</h3>
                <p>{traceUnavailableMessage}</p>
                <p>没有可安全展示的运行或步骤数据；页面不会补造 Run、事件或工具结果。</p>
                {canRetry ? (
                  <button type="button" className="retryButton" onClick={handleRetry}>
                    重新运行
                  </button>
                ) : null}
              </div>
            ) : agentRun ? (
              <div className="traceContent">
                <div className="traceMeta">
                  <span className="runIdLine">
                    <span>Run ID：{agentRun.runId ?? '后端未提供'}</span>
                    {agentRun.runId ? (
                      <>
                        <button
                          type="button"
                          className="copyButton"
                          aria-label={`复制 Run ID ${agentRun.runId}`}
                          onClick={() => void handleCopy('Run ID', agentRun.runId)}
                        >
                          复制 Run ID
                        </button>
                        <button
                          type="button"
                          className="copyButton"
                          aria-label={`回放 Run ${agentRun.runId}`}
                          onClick={() => {
                            setReplayRunId(agentRun.runId)
                            setPage('run')
                          }}
                        >
                          回放
                        </button>
                      </>
                    ) : null}
                    {copyFeedback ? (
                      <span className="copyFeedback" aria-live="polite">
                        {copyFeedback}
                      </span>
                    ) : null}
                  </span>
                  <span>停止原因：{agentRun.stopReason ?? '后端未提供'}</span>
                  <span>
                    工具调用：实际 {getToolCallMetrics(agentRun).actual} 次，复用{' '}
                    {getToolCallMetrics(agentRun).cached} 次
                  </span>
                  <span>总 Token：{formatMetric(agentRun.usage.totalTokens)}</span>
                  <span>开始：{formatAgentTimestamp(agentRun.startedAt)}</span>
                  <span>完成：{formatAgentTimestamp(agentRun.completedAt)}</span>
                  <span>
                    耗时：
                    {agentRun.durationMs === null || agentRun.durationMs === undefined
                      ? '后端未提供'
                      : `${agentRun.durationMs} ms`}
                  </span>
                </div>
                {agentRun.steps.length === 0 ? (
                  <div className="traceNotice compactNotice">
                    <h3>后端返回空 Trace</h3>
                    <p>本次 Agent Run 没有步骤事件；前端不会补造模型决策或工具调用。</p>
                  </div>
                ) : (
                  <ol className="stepTimeline" aria-label="Agent 步骤时间线">
                    {agentRun.steps.map((step) => (
                      <TraceStepCard key={step.id} step={step} />
                    ))}
                  </ol>
                )}
                {canRetry ? (
                  <button type="button" className="retryButton" onClick={handleRetry}>
                    重新运行
                  </button>
                ) : null}
              </div>
            ) : (
              <div className="traceNotice">
                <h3>{agentSseAvailable ? '等待 Agent SSE' : '等待 Agent Run'}</h3>
                <p>
                  {agentSseAvailable
                    ? '切换到 Agent Run 模式后发起真实 Agent SSE，Trace 将随事件实时更新。'
                    : '切换到 Agent Run 模式后等待 Agent Run 结果；当前客户端未提供实时 Agent SSE。'}
                </p>
                {ragPreset ? (
                  <p>已启用 RAG Agent preset：运行前将调用 knowledge_search 检索知识库。</p>
                ) : null}
                {canRetry ? (
                  <button type="button" className="retryButton" onClick={handleRetry}>
                    重新运行
                  </button>
                ) : null}
              </div>
            )}
          </aside>
        </section>

        <footer className="metricsBar">
          <div>
            <span className="metricLabel">会话数</span>
            <strong>{sessionCount}</strong>
          </div>
          <div>
            <span className="metricLabel">清空次数</span>
            <strong>{clearedCount}</strong>
          </div>
          <div className="metricWide">
            <span className="metricLabel">请求状态</span>
            <strong>{statusLabels[requestStatus]}</strong>
          </div>
          <div className="actions">
            <button
              type="button"
              className="secondaryButton"
              onClick={() => resetConversation('clear')}
            >
              清空当前会话
            </button>
          </div>
        </footer>
      </div>
    </div>,
  )
}

export default App
