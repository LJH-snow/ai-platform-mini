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
  AgentRun,
  AgentRunInput,
  AgentRunStatus,
  AgentToolCall,
  AgentTraceStep,
} from './agent/types.ts'
import { createChatClient, type ChatClient } from './chat/client.ts'
import { getRuntimeConfig } from './chat/config.ts'
import type { ChatMessage } from './chat/types.ts'

type ConsoleMode = 'chat' | 'agent'
type RequestStatus =
  | 'idle'
  | 'sending'
  | 'agent_running'
  | 'completed'
  | 'stopped'
  | 'client_cancelled'
  | 'cancelled'
  | 'timed_out'
  | 'failed'
  | 'chat_failed'
  | 'network'
  | 'interrupted'

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
  completed: '已完成',
  stopped: '已停止',
  client_cancelled: '请求已取消',
  cancelled: '运行已取消',
  timed_out: '运行超时',
  failed: '运行失败',
  chat_failed: '后端错误',
  network: '网络失败',
  interrupted: 'SSE 已断连',
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
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="traceToggleText">
          <strong>{tool.name}</strong>
          {!tool.known ? <span className="unknownTool">未知工具</span> : null}
        </span>
        <span className={`traceBadge badge-${tool.status}`}>{toolStatusLabels[tool.status]}</span>
      </button>
      {expanded ? (
        <div id={contentId} className="traceDetails">
          <p>步骤序号：{tool.stepIndex}</p>
          <p>耗时：{tool.durationMs === null ? '后端未提供' : `${tool.durationMs} ms`}</p>
          <p>输入摘要：{tool.inputSummary ?? '后端未提供'}</p>
          <p>输出摘要：{tool.outputSummary ?? '后端未提供'}</p>
          {tool.errorCode ? <p>错误码：{tool.errorCode}</p> : null}
          {tool.errorMessage ? <p className="safeError">{tool.errorMessage}</p> : null}
        </div>
      ) : null}
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
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="traceToggleText">
          <strong>步骤 {step.index}</strong>
          <span>{decisionLabels[step.decisionKind]}</span>
        </span>
        <span className={`traceBadge badge-${step.status}`}>{runStatusLabels[step.status]}</span>
      </button>
      {step.toolCalls.length > 0 ? (
        <div className="toolPreviewList" aria-label={`步骤 ${step.index} 工具摘要`}>
          {step.toolCalls.map((tool) => (
            <span key={tool.id}>
              <strong>{tool.name}</strong>
              {!tool.known ? <em>未知工具</em> : null}
              <span>{toolStatusLabels[tool.status]}</span>
            </span>
          ))}
        </div>
      ) : null}
      {expanded ? (
        <div id={contentId} className="traceDetails stepDetails">
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
          <div className="srFacts" aria-label="步骤时间信息">
            <span>开始时间：{step.startedAt ?? '后端未提供'}</span>
            <span>完成时间：{step.completedAt ?? '后端未提供'}</span>
            <span>耗时：{step.durationMs === null ? '后端未提供' : `${step.durationMs} ms`}</span>
          </div>
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
      ) : null}
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
  const activeRequest = useRef<ActiveRequest | null>(null)

  const sessionLabel = sessionCount === 0 ? '未命名会话' : `本地会话 ${sessionCount}`
  const isActive = requestStatus === 'sending' || requestStatus === 'agent_running'

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
    setTraceUnavailableMessage(null)
    setLastAgentInput(null)
    setErrorMessage(null)
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
    } else {
      setRequestStatus('stopped')
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
    setTraceUnavailableMessage(null)
    setErrorMessage(null)
    setRequestStatus('sending')

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
      if (!request.stopped) setRequestStatus('completed')
    } catch (error) {
      if (request.stopped) return
      if (error instanceof DOMException && error.name === 'AbortError') {
        setRequestStatus('stopped')
      } else if (error instanceof Error && error.name === 'ChatStreamInterruptedError') {
        setErrorMessage(error.message)
        setRequestStatus('interrupted')
      } else if (error instanceof Error && error.name === 'ChatBackendError') {
        setErrorMessage(error.message)
        setRequestStatus('chat_failed')
        if ('requestId' in error && typeof error.requestId === 'string')
          setRequestId(error.requestId)
      } else if (error instanceof Error && error.name === 'ChatNetworkError') {
        setErrorMessage(error.message)
        setRequestStatus('network')
      } else {
        setErrorMessage('网络请求失败。')
        setRequestStatus('network')
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
    setRequestStatus('agent_running')
    setLastAgentInput(input)

    try {
      const run = await resolvedAgentClient.runAgent(input, request.controller.signal)
      if (activeRequest.current !== request || request.stopped) return
      setAgentRun(run)
      replaceAssistantContent(assistantMessage.id, fallbackAnswerForRun(run))
      setRequestStatus(requestStatusForRun(run.status))
    } catch (error) {
      if (request.stopped) return
      if (error instanceof DOMException && error.name === 'AbortError') {
        replaceAssistantContent(assistantMessage.id, '请求已取消；后端终态未知。')
        setRequestStatus('client_cancelled')
      } else if (
        error instanceof AgentBackendError ||
        error instanceof AgentNetworkError ||
        error instanceof AgentResponseError
      ) {
        replaceAssistantContent(assistantMessage.id, error.message)
        setTraceUnavailableMessage('本次 Agent 请求未收到可展示的 Trace。')
        setErrorMessage(error.message)
        setRequestStatus(error instanceof AgentNetworkError ? 'network' : 'failed')
      } else {
        const message = 'Agent 请求失败，未展示未经处理的内部错误。'
        replaceAssistantContent(assistantMessage.id, message)
        setTraceUnavailableMessage('本次 Agent 请求未收到可展示的 Trace。')
        setErrorMessage(message)
        setRequestStatus('failed')
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
      await executeChat(nextMessages, assistantMessage)
    }
  }

  const handleRetry = async (): Promise<void> => {
    if (!lastAgentInput || isActive) return
    const assistantMessage = createMessage('assistant', '')
    setMessages((current) => [...current, assistantMessage])
    await executeAgent(lastAgentInput, assistantMessage)
  }

  const handleCopyRequestId = async (): Promise<void> => {
    if (requestId && navigator.clipboard) await navigator.clipboard.writeText(requestId)
  }

  const traceStatus = agentRun
    ? runStatusLabels[agentRun.status]
    : traceUnavailableMessage
      ? 'Trace 不可用'
      : '无运行结果'
  const canRetry =
    mode === 'agent' &&
    lastAgentInput !== null &&
    ['failed', 'network', 'timed_out'].includes(requestStatus) &&
    !isActive

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Agent Console · Phase 3</p>
          <h1>对话与 Agent Trace</h1>
          <p className="heroCopy">
            普通模式继续使用真实 Chat SSE；Agent Run 使用同步 JSON，并在请求完成后加载可审计
            Trace。当前不是实时 Agent SSE，也不伪造时间、耗时、工具载荷或 Token。
          </p>
        </div>
        <div className="statusPill">{sessionLabel}</div>
      </section>

      <section className="consoleGrid" aria-label="Agent Console">
        <article className="panel conversationPanel" aria-label="会话">
          <div className="panelHeader">
            <div>
              <h2>会话</h2>
              <span className={`requestStatus status-${requestStatus}`} role="status">
                {statusLabels[requestStatus]}
              </span>
            </div>
            {requestId ? (
              <div className="requestIdGroup">
                <span className="metricLabel">Request ID</span>
                <button type="button" className="copyButton" onClick={handleCopyRequestId}>
                  {requestId}
                </button>
              </div>
            ) : null}
          </div>

          <div className="modeSwitch" aria-label="请求模式">
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

          {messages.length === 0 ? (
            <div className="emptyState">
              <div className="emptyIcon">A</div>
              <h3>{mode === 'chat' ? '开始一段普通对话' : '运行一次真实 Agent'}</h3>
              <p>
                {mode === 'chat'
                  ? '输入问题后，前端会真实调用 Chat SSE，并将回答增量显示在这里。'
                  : '请求结束后同时显示最终回答与同步 Trace；等待期间不会伪造实时步骤。'}
              </p>
            </div>
          ) : (
            <div className="messageList" aria-live="polite" aria-label="消息列表">
              {messages.map((message) => (
                <div key={message.id} className={`message message-${message.role}`}>
                  <span className="messageRole">{message.role === 'user' ? '你' : '助手'}</span>
                  <p>{message.content || (isActive ? '…' : '（无文本内容）')}</p>
                </div>
              ))}
            </div>
          )}

          {errorMessage ? (
            <div className="errorNotice" role="alert">
              {errorMessage}
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
            />
            <div className="composerActions">
              <span className="composerHint">
                {mode === 'chat'
                  ? '普通回答为实时 Chat SSE。'
                  : 'Agent Trace 在同步请求完成后加载，非实时。'}
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
            <span className="traceDelivery">完成后加载 · 非实时</span>
          </div>

          {requestStatus === 'agent_running' ? (
            <div className="traceNotice">
              <h3>等待同步结果</h3>
              <p>完成后加载 Trace，非实时</p>
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
                <span>Run ID：{agentRun.runId ?? '后端未提供'}</span>
                <span>停止原因：{agentRun.stopReason ?? '后端未提供'}</span>
                <span>总 Token：{formatMetric(agentRun.usage.totalTokens)}</span>
                <span>时间与耗时：后端未提供</span>
              </div>
              {agentRun.steps.length === 0 ? (
                <div className="traceNotice compactNotice">
                  <h3>后端返回空 Trace</h3>
                  <p>本次同步响应没有步骤；前端不会补造模型决策或工具调用。</p>
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
              <h3>等待 Agent Run</h3>
              <p>切换到 Agent Run 模式后发起真实同步请求，完成后在此加载 Trace，非实时。</p>
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
