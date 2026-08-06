import type { ChatApiMessage, ChatStreamHandlers, ChatStreamResult } from './types.ts'

export type ChatClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

export class ChatBackendError extends Error {
  readonly status: number
  readonly code: string | null
  readonly requestId: string | null
  readonly threadId: string | null

  constructor(
    message: string,
    status: number,
    code: string | null,
    requestId: string | null,
    threadId: string | null = null,
  ) {
    super(message)
    this.name = 'ChatBackendError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.threadId = threadId
  }
}

export class ChatNetworkError extends Error {
  constructor(message = '网络请求失败。') {
    super(message)
    this.name = 'ChatNetworkError'
  }
}

export class ChatStreamInterruptedError extends Error {
  constructor(message = 'SSE 连接在收到完成标记前中断。') {
    super(message)
    this.name = 'ChatStreamInterruptedError'
  }
}

type ErrorPayload = {
  code?: unknown
  message?: unknown
  request_id?: unknown
  thread_id?: unknown
}

export type ChatClient = {
  streamChat: (
    messages: ChatApiMessage[],
    handlers: ChatStreamHandlers,
    signal: AbortSignal,
    threadId?: string | null,
  ) => Promise<ChatStreamResult>
}

const getRequestId = (response: Response): string | null => {
  return response.headers.get('X-Request-ID')
}

const getErrorPayload = (value: unknown): ErrorPayload => {
  if (typeof value !== 'object' || value === null) {
    return {}
  }

  return value as ErrorPayload
}

const parseErrorResponse = async (
  response: Response,
  requestId: string | null,
): Promise<ChatBackendError> => {
  let payload: ErrorPayload = {}

  try {
    payload = getErrorPayload(await response.json())
  } catch {
    // The status text is the only backend detail available for a non-JSON error.
  }

  const errorRequestId = typeof payload.request_id === 'string' ? payload.request_id : requestId
  const code = typeof payload.code === 'string' ? payload.code : null
  const threadId = typeof payload.thread_id === 'string' ? payload.thread_id : null
  const message =
    response.status === 401 || response.status === 403
      ? 'Chat 请求未通过鉴权，请检查运行时凭据。'
      : response.status === 408 || response.status === 504
        ? 'Chat 请求超时，请稍后重试。'
        : response.status === 429
          ? 'Chat 请求过于频繁，请稍后重试。'
          : response.status >= 500
            ? 'Chat 服务暂时不可用，请稍后重试。'
            : `Chat 请求失败（HTTP ${response.status}），请稍后重试。`

  return new ChatBackendError(message, response.status, code, errorRequestId, threadId)
}

const parseSseEvent = (event: string): string | null => {
  const dataLines = event
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())

  if (dataLines.length === 0) {
    return null
  }

  return dataLines.join('\n')
}

const getDeltaContent = (payload: unknown): string => {
  if (typeof payload !== 'object' || payload === null) {
    return ''
  }

  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) {
    return ''
  }

  const firstChoice = choices[0]
  if (typeof firstChoice !== 'object' || firstChoice === null) {
    return ''
  }

  const delta = (firstChoice as { delta?: unknown }).delta
  if (typeof delta !== 'object' || delta === null) {
    return ''
  }

  const content = (delta as { content?: unknown }).content
  return typeof content === 'string' ? content : ''
}

const getThreadId = (payload: unknown): string | null => {
  if (typeof payload !== 'object' || payload === null) {
    return null
  }
  const threadId = (payload as { thread_id?: unknown }).thread_id
  return typeof threadId === 'string' && threadId.length > 0 ? threadId : null
}

const readSseStream = async (
  response: Response,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<string | null> => {
  if (!response.body) {
    throw new ChatStreamInterruptedError('SSE 响应没有可读取的响应体。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let completed = false
  let threadId: string | null = null

  const processEvent = (event: string): void => {
    const data = parseSseEvent(event)
    if (data === null) {
      return
    }

    if (data === '[DONE]') {
      completed = true
      return
    }

    let payload: unknown
    try {
      payload = JSON.parse(data) as unknown
    } catch {
      throw new ChatStreamInterruptedError('SSE 返回了无法解析的 JSON。')
    }

    const content = getDeltaContent(payload)
    if (content) {
      handlers.onDelta(content)
    }
    const eventThreadId = getThreadId(payload)
    if (eventThreadId !== null) {
      threadId = eventThreadId
      handlers.onThreadId?.(eventThreadId)
    }
  }

  try {
    while (!completed) {
      const { done, value } = await reader.read()
      if (done) {
        buffer += decoder.decode()
        break
      }

      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
      let separatorIndex = buffer.indexOf('\n\n')
      while (separatorIndex >= 0) {
        const event = buffer.slice(0, separatorIndex)
        buffer = buffer.slice(separatorIndex + 2)
        processEvent(event)
        if (completed) {
          break
        }
        separatorIndex = buffer.indexOf('\n\n')
      }
    }

    buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    if (!completed && buffer.trim()) {
      processEvent(buffer)
    }
  } catch (error) {
    if (signal.aborted) {
      throw error
    }
    if (error instanceof ChatStreamInterruptedError) {
      throw error
    }
    throw new ChatStreamInterruptedError('SSE 读取过程中连接中断。')
  } finally {
    reader.releaseLock()
  }

  if (!completed && !signal.aborted) {
    throw new ChatStreamInterruptedError()
  }
  return threadId
}

export function createChatClient(options: ChatClientOptions = {}): ChatClient {
  const fetchImpl = options.fetchImpl ?? fetch
  const apiBaseUrl = options.apiBaseUrl ?? ''
  const endpoint = `${apiBaseUrl.replace(/\/$/, '')}/v1/chat/completions?stream=true`

  return {
    async streamChat(messages, handlers, signal, threadId): Promise<ChatStreamResult> {
      let response: Response
      try {
        response = await fetchImpl(endpoint, {
          method: 'POST',
          headers: {
            Accept: 'text/event-stream',
            'Content-Type': 'application/json',
            ...(options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {}),
          },
          body: JSON.stringify({
            messages,
            stream: true,
            ...(threadId ? { thread_id: threadId } : {}),
          }),
          signal,
        })
      } catch (error) {
        if (signal.aborted) {
          throw error
        }
        throw new ChatNetworkError()
      }

      const requestId = getRequestId(response)
      if (requestId) {
        handlers.onRequestId(requestId)
      }

      if (!response.ok) {
        throw await parseErrorResponse(response, requestId)
      }

      const resolvedThreadId = await readSseStream(response, handlers, signal)
      return { requestId, threadId: resolvedThreadId }
    },
  }
}
