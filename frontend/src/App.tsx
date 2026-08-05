import type { FormEvent, JSX } from 'react'
import { useMemo, useRef, useState } from 'react'
import './App.css'
import { createChatClient, type ChatClient } from './chat/client.ts'
import { getRuntimeConfig } from './chat/config.ts'
import type { ChatMessage } from './chat/types.ts'

type RequestStatus =
  | 'idle'
  | 'sending'
  | 'completed'
  | 'stopped'
  | 'failed'
  | 'network'
  | 'interrupted'

type AppProps = {
  chatClient?: ChatClient
}

type ActiveRequest = {
  controller: AbortController
  stopped: boolean
}

const createMessage = (role: ChatMessage['role'], content: string): ChatMessage => ({
  id: `${role}-${crypto.randomUUID()}`,
  role,
  content,
})

const statusLabels: Record<RequestStatus, string> = {
  idle: '待发送',
  sending: '回答生成中',
  completed: '已完成',
  stopped: '已停止',
  failed: '后端错误',
  network: '网络失败',
  interrupted: 'SSE 已断连',
}

function App({ chatClient }: AppProps): JSX.Element {
  const runtimeConfig = useMemo(() => getRuntimeConfig(), [])
  const defaultChatClient = useMemo(
    () =>
      createChatClient({
        apiBaseUrl: runtimeConfig.apiBaseUrl,
        apiKey: runtimeConfig.apiKey,
      }),
    [runtimeConfig],
  )
  const client = chatClient ?? defaultChatClient
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [sessionCount, setSessionCount] = useState(0)
  const [clearedCount, setClearedCount] = useState(0)
  const [requestStatus, setRequestStatus] = useState<RequestStatus>('idle')
  const [requestId, setRequestId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const activeRequest = useRef<ActiveRequest | null>(null)

  const sessionLabel = sessionCount === 0 ? '未命名会话' : `本地会话 ${sessionCount}`
  const isSending = requestStatus === 'sending'

  const resetConversation = (action: 'new' | 'clear'): void => {
    if (activeRequest.current) {
      activeRequest.current.stopped = true
      activeRequest.current.controller.abort()
      activeRequest.current = null
    }

    setMessages([])
    setDraft('')
    setRequestId(null)
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
    if (!request) {
      return
    }

    request.stopped = true
    request.controller.abort()
    activeRequest.current = null
    setRequestStatus('stopped')
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const content = draft.trim()
    if (!content || isSending) {
      return
    }

    if (sessionCount === 0) {
      setSessionCount(1)
    }

    const userMessage = createMessage('user', content)
    const assistantMessage = createMessage('assistant', '')
    const nextMessages = [...messages, userMessage]
    const request = {
      controller: new AbortController(),
      stopped: false,
    }

    activeRequest.current = request
    setMessages([...nextMessages, assistantMessage])
    setDraft('')
    setRequestId(null)
    setErrorMessage(null)
    setRequestStatus('sending')

    try {
      await client.streamChat(
        nextMessages.map(({ role, content: messageContent }) => ({
          role,
          content: messageContent,
        })),
        {
          onDelta: (delta) => {
            if (activeRequest.current !== request || request.stopped) {
              return
            }

            setMessages((currentMessages) =>
              currentMessages.map((message) =>
                message.id === assistantMessage.id
                  ? { ...message, content: `${message.content}${delta}` }
                  : message,
              ),
            )
          },
          onRequestId: (id) => {
            if (activeRequest.current !== request || request.stopped) {
              return
            }

            setRequestId(id)
          },
        },
        request.controller.signal,
      )

      if (!request.stopped) {
        setRequestStatus('completed')
      }
    } catch (error) {
      if (request.stopped) {
        return
      }

      if (error instanceof DOMException && error.name === 'AbortError') {
        setRequestStatus('stopped')
      } else if (error instanceof Error && error.name === 'ChatStreamInterruptedError') {
        setErrorMessage(error.message)
        setRequestStatus('interrupted')
      } else if (error instanceof Error && error.name === 'ChatBackendError') {
        setErrorMessage(error.message)
        setRequestStatus('failed')
        if ('requestId' in error && typeof error.requestId === 'string') {
          setRequestId(error.requestId)
        }
      } else if (error instanceof Error && error.name === 'ChatNetworkError') {
        setErrorMessage(error.message)
        setRequestStatus('network')
      } else {
        setErrorMessage(error instanceof Error ? error.message : '网络请求失败。')
        setRequestStatus('network')
      }
    } finally {
      if (activeRequest.current === request) {
        activeRequest.current = null
      }
    }
  }

  const handleCopyRequestId = async (): Promise<void> => {
    if (!requestId || !navigator.clipboard) {
      return
    }

    await navigator.clipboard.writeText(requestId)
  }

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Agent Console · Phase 2</p>
          <h1>普通流式对话</h1>
          <p className="heroCopy">
            当前页面已接入真实 Chat SSE，回答会按增量内容渲染。Agent Trace、Tool Call、RAG 来源和
            Run ID 仍未接入，不会在前端伪造。
          </p>
        </div>
        <div className="statusPill">{sessionLabel}</div>
      </section>

      <section className="consoleGrid" aria-label="Agent Console">
        <article className="panel conversationPanel">
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

          {messages.length === 0 ? (
            <div className="emptyState">
              <div className="emptyIcon">A</div>
              <h3>开始一段普通对话</h3>
              <p>输入问题后，前端会真实调用 Chat SSE，并将回答增量显示在这里。</p>
            </div>
          ) : (
            <div className="messageList" aria-live="polite" aria-label="消息列表">
              {messages.map((message) => (
                <div key={message.id} className={`message message-${message.role}`}>
                  <span className="messageRole">{message.role === 'user' ? '你' : '助手'}</span>
                  <p>{message.content || (isSending ? '…' : '（无文本内容）')}</p>
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
              placeholder="例如：解释一下当前项目的 Chat SSE 契约"
              rows={3}
              disabled={isSending}
            />
            <div className="composerActions">
              <span className="composerHint">Bearer Key 仅支持运行时注入，不会写入前端源码。</span>
              {isSending ? (
                <button type="button" className="stopButton" onClick={handleStop}>
                  停止请求
                </button>
              ) : (
                <button type="submit" disabled={!draft.trim()}>
                  发送消息
                </button>
              )}
            </div>
          </form>
        </article>

        <aside className="panel tracePanel">
          <div className="panelHeader">
            <h2>Agent Trace</h2>
            <span>未接入</span>
          </div>
          <div className="traceNotice">
            <h3>Agent Trace/SSE 尚未接入</h3>
            <p>
              阶段 2 只接入普通 Chat SSE。Run Trace、Tool Call、RAG 来源和 Agent Run ID
              会在后续后端公共推送契约完成后再接入。
            </p>
          </div>
          <ul className="traceList" aria-label="Trace status">
            <li>普通回答：已接入真实 Chat SSE</li>
            <li>Agent Trace/SSE：尚未接入</li>
            <li>Run ID：后端未提供，不伪造</li>
          </ul>
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
