import type { FormEvent, JSX } from 'react'
import { useMemo, useRef, useState } from 'react'
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
import { ChatBackendError, createChatClient, type ChatClient } from './chat/client.ts'
import { getRuntimeConfig } from './chat/config.ts'
import type { ChatApiMessage, ChatMessage } from './chat/types.ts'

type ConsoleMode = 'chat' | 'agent'
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
  unknown: '未知步骤',
}

const eventLabels: Record<string, string> = {
  run_started: '运行开始',
  model_decision: '模型决策',
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
  if (run.status === 'stopped') return 'Agent 运行已停止，未返回最终回答。'
  return 'Agent 运行失败。'
}

const formatMetric = (value: number | null): string =>
  value === null ? '后端未提供' : String(value)

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
  no_relevant_sources: '后端未返回与当前查询匹配的来源。',
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
  return 'RAG 未找到相关来源。'
}

const getSafeChatErrorMessage = (error: ChatBackendError): string => {
  if (error.status === 401 || error.status === 403) return 'Chat 请求未通过鉴权，请检查运行时凭据。'
  if (error.status === 408 || error.status === 504) return 'Chat 请求超时，请稍后重试。'
  if (error.status === 429) return 'Chat 请求过于频繁，请稍后重试。'
  if (error.status >= 500) return 'Chat 服务暂时不可用，请稍后重试。'
  return `Chat 请求失败（HTTP ${error.status}），请稍后重试。`
}

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
            关联工具：knowledge_search · 步骤序号：{tool.stepIndex} · 调用标识：
            {tool.callId ?? '后端未提供'}
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
        aria-label={`工具调用 ${tool.name}，${toolStatusLabels[tool.status]}，${expanded ? '收起' : '展开'}`}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="traceToggleText">
          <strong>{tool.name}</strong>
          {!tool.known ? <span className="unknownTool">未知工具</span> : null}
        </span>
        <span className={`traceBadge badge-${tool.status}`}>{toolStatusLabels[tool.status]}</span>
      </button>
      <div id={contentId} className="traceDetails" hidden={!expanded}>
        <p>步骤序号：{tool.stepIndex}</p>
        <p>调用标识：{tool.callId ?? '后端未提供'}</p>
        <p>耗时：{tool.durationMs === null ? '后端未提供' : `${tool.durationMs} ms`}</p>
        <p>输入摘要：{tool.inputSummary ?? '后端未提供'}</p>
        <p>输出摘要：{tool.outputSummary ?? '后端未提供'}</p>
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
              <strong>{tool.name}</strong>
              {!tool.known ? <em>未知工具</em> : null}
              <span>{toolStatusLabels[tool.status]}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div id={contentId} className="traceDetails stepDetails" hidden={!expanded}>
        <p>{step.summary}</p>
        <dl className="traceFacts">
          <div>
            <dt>开始时间</dt>
            <dd>{step.startedAt ?? '后端未提供'}</dd>
          </div>
          <div>
            <dt>完成时间</dt>
            <dd>{step.completedAt ?? '后端未提供'}</dd>
          </div>
          <div>
            <dt>耗时</dt>
            <dd>{step.durationMs === null ? '后端未提供' : `${step.durationMs} ms`}</dd>
          </div>
        </dl>
        <dl className="srFacts" aria-label="步骤时间信息">
          <div>
            <dt>开始时间</dt>
            <dd>{step.startedAt ?? '后端未提供'}</dd>
          </div>
          <div>
            <dt>完成时间</dt>
            <dd>{step.completedAt ?? '后端未提供'}</dd>
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
              {step.events.map((event) => (
                <li key={event.id}>{eventLabels[event.kind] ?? event.kind}</li>
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
  const defaultChatClient = useMemo(
    () =>
      createChatClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: runtimeConfig.apiKey,
      }),
    [runtimeConfig],
  )
  const defaultAgentClient = useMemo(
    () =>
      createAgentClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: runtimeConfig.apiKey,
      }),
    [runtimeConfig],
  )
  const resolvedChatClient = chatClient ?? defaultChatClient
  const resolvedAgentClient = agentClient ?? defaultAgentClient
  const [mode, setMode] = useState<ConsoleMode>('chat')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sessionCount, setSessionCount] = useState(0)
  const [clearedCount, setClearedCount] = useState(0)
  const [requestStatus, setRequestStatus] = useState<RequestStatus>('idle')
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

  const sessionLabel = sessionCount === 0 ? '未命名会话' : `本地会话 ${sessionCount}`
  const isActive =
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

  const resetConversation = (action: 'new' | 'clear'): void => {
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

    if (action === 'new') {
      setSessionCount((count) => count + 1)
    } else {
      setClearedCount((count) => count + 1)
    }
  }

  const handleStop = (): void => {
    const request = activeRequest.current
    if (!request) return

    request.stopped = true
    request.controller.abort()
    activeRequest.current = null
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
      await resolvedChatClient.streamChat(
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
        },
        request.controller.signal,
      )
      if (!request.stopped) {
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
    setRequestId(null)
    setAgentRun(null)
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
                ? 'Agent Run 已停止。'
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
      if (activeRequest.current === request) activeRequest.current = null
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const content = draft.trim()
    if (!content || isActive) return
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
    ? runStatusLabels[agentRun.status]
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

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Agent Console · Phase 6</p>
          <h1>对话与 Agent Trace</h1>
          <p className="heroCopy">
            {
              '普通模式继续使用真实 Chat SSE；Agent 模式使用真实 Agent SSE，Trace 实时更新；回答支持后端真实 answer_delta 增量。'
            }
          </p>
        </div>
        <div className="statusPill">{sessionLabel}</div>
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
              onClick={() => setMode('chat')}
            >
              普通 Chat SSE 模式
            </button>
            <button
              type="button"
              className={mode === 'agent' ? 'modeActive' : 'secondaryButton'}
              aria-pressed={mode === 'agent'}
              disabled={isActive}
              onClick={() => setMode('agent')}
            >
              Agent Run 模式
            </button>
          </div>

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
              {agentSseAvailable ? '实时 Agent SSE' : 'Agent Run 兼容路径'}
            </span>
          </div>

          {isActive && mode === 'agent' && !agentRun && resolvedAgentClient.streamAgent ? (
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
                    <button
                      type="button"
                      className="copyButton"
                      aria-label={`复制 Run ID ${agentRun.runId}`}
                      onClick={() => void handleCopy('Run ID', agentRun.runId)}
                    >
                      复制 Run ID
                    </button>
                  ) : null}
                  {copyFeedback ? (
                    <span className="copyFeedback" aria-live="polite">
                      {copyFeedback}
                    </span>
                  ) : null}
                </span>
                <span>停止原因：{agentRun.stopReason ?? '后端未提供'}</span>
                <span>总 Token：{formatMetric(agentRun.usage.totalTokens)}</span>
                <span>时间与耗时：后端未提供</span>
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
          <button type="button" onClick={() => resetConversation('new')}>
            新建会话
          </button>
          <button
            type="button"
            className="secondaryButton"
            onClick={() => resetConversation('clear')}
          >
            清空当前会话
          </button>
        </div>
      </footer>
    </main>
  )
}

export default App
