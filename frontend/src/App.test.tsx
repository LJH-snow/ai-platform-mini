import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.tsx'
import { ChatBackendError, ChatNetworkError, type ChatClient } from './chat/client.ts'
import type { ChatApiMessage, ChatStreamHandlers, ChatStreamResult } from './chat/types.ts'

type ControlledRequest = {
  handlers: ChatStreamHandlers
  messages: ChatApiMessage[]
  signal: AbortSignal
  resolve: (result: ChatStreamResult) => void
  reject: (error: Error) => void
}

const createControlledClient = (): {
  client: ChatClient
  emitDelta: (content: string, index?: number) => void
  emitRequestId: (requestId: string, index?: number) => void
  finish: (index?: number) => void
  fail: (error: Error, index?: number) => void
  getRequest: (index?: number) => ControlledRequest
  getRequestCount: () => number
} => {
  const requests: ControlledRequest[] = []
  const client: ChatClient = {
    streamChat: vi.fn(
      (
        messages: ChatApiMessage[],
        handlers: ChatStreamHandlers,
        signal: AbortSignal,
      ): Promise<ChatStreamResult> => {
        return new Promise((resolve, reject) => {
          requests.push({ handlers, messages, signal, resolve, reject })
        })
      },
    ),
  }

  const getRequest = (index = requests.length - 1): ControlledRequest => {
    const request = requests[index]
    if (!request) {
      throw new Error(`Missing controlled request at index ${index}`)
    }
    return request
  }

  return {
    client,
    emitDelta: (content, index) => getRequest(index).handlers.onDelta(content),
    emitRequestId: (requestId, index) => getRequest(index).handlers.onRequestId(requestId),
    finish: (index) => {
      const request = getRequest(index)
      request.handlers.onRequestId('req-test-123')
      request.resolve({ requestId: 'req-test-123' })
    },
    fail: (error, index) => getRequest(index).reject(error),
    getRequest,
    getRequestCount: () => requests.length,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the initial chat state and describes the synchronous Agent Trace boundary', () => {
    render(<App />)

    expect(screen.getByText('开始一段普通对话')).toBeInTheDocument()
    expect(screen.getByText('等待 Agent Run')).toBeInTheDocument()
    expect(screen.getByText(/完成后在此加载 Trace，非实时/)).toBeInTheDocument()
    expect(screen.queryByText(/Run ID：/)).not.toBeInTheDocument()
  })

  it('sends messages, merges deltas, and exposes the completed request id', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '你好')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    expect(controlled.getRequest().messages).toEqual([{ role: 'user', content: '你好' }])
    expect(screen.getByRole('status')).toHaveTextContent('回答生成中')
    expect(screen.queryByRole('button', { name: '发送消息' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止请求' })).toBeInTheDocument()

    controlled.emitDelta('你好，')
    controlled.emitDelta('世界！')
    await waitFor(() => expect(screen.getByText('你好，世界！')).toBeInTheDocument())

    controlled.finish()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))
    expect(screen.getByRole('button', { name: 'req-test-123' })).toBeInTheDocument()
  })

  it('prevents duplicate submission while a request is active', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '重复测试')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    expect(screen.getByRole('button', { name: '停止请求' })).toBeInTheDocument()
    expect(controlled.getRequestCount()).toBe(1)
  })

  it('stops the active request and ignores late delta and request id callbacks', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '停止测试')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    await user.click(screen.getByRole('button', { name: '停止请求' }))

    expect(controlled.getRequest().signal.aborted).toBe(true)
    expect(screen.getByRole('status')).toHaveTextContent('已停止')

    controlled.emitDelta('不应追加')
    controlled.emitRequestId('old-request-id')

    expect(screen.queryByText('不应追加')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'old-request-id' })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('已停止')
  })

  it('distinguishes backend errors, network failures, and interrupted SSE streams', async () => {
    const user = userEvent.setup()
    const backendFailure = createControlledClient()
    const { rerender } = render(<App chatClient={backendFailure.client} />)

    await user.type(screen.getByLabelText('输入消息'), '后端错误')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(backendFailure.getRequestCount()).toBe(1))
    backendFailure.fail(new ChatBackendError('后端返回 502', 502, 'PROVIDER_ERROR', 'req-error'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('后端错误'))
    expect(screen.getByRole('alert')).toHaveTextContent('后端返回 502')

    const networkFailure = createControlledClient()
    rerender(<App chatClient={networkFailure.client} />)
    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    await user.type(screen.getByLabelText('输入消息'), '网络失败')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(networkFailure.getRequestCount()).toBe(1))
    networkFailure.fail(new ChatNetworkError('无法连接后端。'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('网络失败'))
    expect(screen.getByRole('alert')).toHaveTextContent('无法连接后端。')

    const interrupted = createControlledClient()
    rerender(<App chatClient={interrupted.client} />)
    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    await user.type(screen.getByLabelText('输入消息'), 'SSE 断连')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(interrupted.getRequestCount()).toBe(1))
    interrupted.fail(Object.assign(new Error('SSE 断连'), { name: 'ChatStreamInterruptedError' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('SSE 已断连'))
    expect(screen.getByRole('alert')).toHaveTextContent('SSE 断连')
  })

  it('creates a new session and ignores callbacks from the previous session', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '旧会话')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    await user.click(screen.getByRole('button', { name: '新建会话' }))

    controlled.emitDelta('旧回答不应出现', 0)
    controlled.emitRequestId('old-session-request-id', 0)

    expect(screen.getByText('本地会话 2')).toBeInTheDocument()
    expect(screen.getByText('开始一段普通对话')).toBeInTheDocument()
    expect(screen.queryByText('旧回答不应出现')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'old-session-request-id' })).not.toBeInTheDocument()
  })

  it('clears the current session and increments the clear count', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '清空我')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    controlled.emitDelta('回答')
    controlled.finish()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))

    await user.click(screen.getByRole('button', { name: '清空当前会话' }))

    expect(screen.getByText('开始一段普通对话')).toBeInTheDocument()
    const clearMetric = screen.getByText('清空次数').parentElement
    expect(clearMetric && within(clearMetric).getByText('1')).toBeInTheDocument()
  })
})
