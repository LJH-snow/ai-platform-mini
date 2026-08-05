import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.tsx'
import { AgentNetworkError, type AgentClient } from './agent/client.ts'
import type { AgentRunInput } from './agent/types.ts'
import type { AgentStreamEvent } from './agent/stream.ts'
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

const createNetworkFailingAgentClient = (): AgentClient => ({
  runAgent: async () => {
    throw new AgentNetworkError()
  },
  streamAgent: async () => {
    throw new AgentNetworkError()
  },
})

type ControlledAgentRequest = {
  input: AgentRunInput
  onEvent: (event: AgentStreamEvent) => void
  signal: AbortSignal
  resolve: () => void
  reject: (error: Error) => void
}

const createControlledAgentClient = (): {
  client: AgentClient
  emit: (event: AgentStreamEvent, index?: number) => void
  finish: (index?: number) => void
  getRequestCount: () => number
} => {
  const requests: ControlledAgentRequest[] = []
  const client: AgentClient = {
    runAgent: async () => {
      throw new Error('Controlled Agent client does not support non-streaming runs')
    },
    streamAgent: vi.fn(
      (
        input: AgentRunInput,
        handlers: { onEvent: (event: AgentStreamEvent) => void },
        signal: AbortSignal,
      ): Promise<void> =>
        new Promise((resolve, reject) => {
          requests.push({ input, onEvent: handlers.onEvent, signal, resolve, reject })
        }),
    ),
  }

  const getRequest = (index = requests.length - 1): ControlledAgentRequest => {
    const request = requests[index]
    if (!request) throw new Error(`Missing controlled Agent request at index ${index}`)
    return request
  }

  return {
    client,
    emit: (event, index) => getRequest(index).onEvent(event),
    finish: (index) => getRequest(index).resolve(),
    getRequestCount: () => requests.length,
  }
}

const agentEvent = (
  runId: string,
  event: AgentStreamEvent['event'],
  sequence: number,
  extra: Partial<AgentStreamEvent> = {},
): AgentStreamEvent => ({
  event,
  run_id: runId,
  sequence,
  ...extra,
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('renders the Phase 6 realtime Agent surface and preserves the Chat SSE copy', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(screen.getByText('Agent Console · Phase 6')).toBeInTheDocument()
    expect(
      screen.getByText(
        '普通模式继续使用真实 Chat SSE；Agent 模式使用真实 Agent SSE，Trace 实时更新；回答支持后端真实 answer_delta 增量。',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('开始一段普通对话')).toBeInTheDocument()
    expect(
      screen.getByText('输入问题后，前端会真实调用 Chat SSE，并将回答增量显示在这里。'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('普通回答为实时 Chat SSE；Enter 换行，Ctrl/⌘ + Enter 发送。'),
    ).toBeInTheDocument()
    const modeGroup = screen.getByRole('group', { name: '请求模式' })
    expect(within(modeGroup).getByRole('button', { name: '普通 Chat SSE 模式' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(within(modeGroup).getByRole('button', { name: 'Agent Run 模式' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
    expect(
      screen.getByText('普通回答为实时 Chat SSE；Enter 换行，Ctrl/⌘ + Enter 发送。'),
    ).toHaveStyle({ color: '#64728a' })

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))

    expect(screen.getByText('运行一次真实 Agent')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Agent 模式通过真实 Agent SSE 实时更新回答与 Trace；后端 answer_delta 会按增量显示。',
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Agent 使用真实 Agent SSE 实时更新回答与 Trace，后端 answer_delta 会增量显示；Enter 换行，Ctrl/⌘ + Enter 运行。',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('等待 Agent SSE')).toBeInTheDocument()
    expect(
      screen.getByText('切换到 Agent Run 模式后发起真实 Agent SSE，Trace 将随事件实时更新。'),
    ).toBeInTheDocument()
    expect(screen.getByText('实时 Agent SSE')).toBeInTheDocument()
    expect(screen.queryByText(/同步请求|完成后加载 Trace|非实时/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Run ID：/)).not.toBeInTheDocument()
  })

  it.each(['tool_completed', 'tool_failed'] as const)(
    'keeps the Agent SSE active after %s until a run terminal event',
    async (toolEvent) => {
      const user = userEvent.setup()
      const controlled = createControlledAgentClient()
      render(<App agentClient={controlled.client} />)

      await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
      const input = screen.getByLabelText('输入消息')
      await user.type(input, '继续执行多步骤 Agent')
      await user.click(screen.getByRole('button', { name: '运行 Agent' }))
      await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

      controlled.emit(agentEvent('run-1', 'run_started', 0))
      controlled.emit(agentEvent('run-1', 'step_started', 1, { step_index: 1 }))
      controlled.emit(
        agentEvent('run-1', 'tool_started', 2, {
          step_index: 1,
          call_id: 'tool-1',
          tool_name: 'calculator',
        }),
      )
      controlled.emit(
        agentEvent('run-1', toolEvent, 3, {
          step_index: 1,
          call_id: 'tool-1',
          tool_name: 'calculator',
          succeeded: toolEvent === 'tool_completed',
        }),
      )

      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveTextContent(
          toolEvent === 'tool_completed' ? '工具调用完成' : '工具调用失败',
        ),
      )
      expect(input).toBeDisabled()
      expect(screen.getByRole('button', { name: '停止请求' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '运行 Agent' })).not.toBeInTheDocument()

      controlled.emit(agentEvent('run-1', 'answer_delta', 4, { delta: '后续回答' }))
      await waitFor(() => expect(screen.getByText('后续回答')).toBeInTheDocument())
    },
  )

  it('isolates the stream reducer when starting a second Agent Run in the same session', async () => {
    const user = userEvent.setup()
    const controlled = createControlledAgentClient()
    render(<App agentClient={controlled.client} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '第一次运行')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    controlled.emit(agentEvent('run-1', 'run_started', 0))
    controlled.emit(agentEvent('run-1', 'step_started', 1, { step_index: 1 }))
    controlled.emit(agentEvent('run-1', 'answer_delta', 2, { delta: '第一次回答' }))
    controlled.emit(agentEvent('run-1', 'run_completed', 3, { status: 'completed' }))
    controlled.finish(0)

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('已完成')
      expect(screen.getByText('Run ID：run-1')).toBeInTheDocument()
    })

    await user.type(screen.getByLabelText('输入消息'), '第二次运行')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(2))

    controlled.emit(agentEvent('run-2', 'run_started', 0), 1)
    controlled.emit(agentEvent('run-2', 'answer_delta', 1, { delta: '第二次回答' }), 1)
    controlled.emit(agentEvent('run-2', 'run_completed', 2, { status: 'completed' }), 1)
    controlled.finish(1)

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('已完成')
      expect(screen.getByText('Run ID：run-2')).toBeInTheDocument()
      expect(screen.getByText('第二次回答')).toBeInTheDocument()
    })
    expect(screen.queryByText('步骤 1')).not.toBeInTheDocument()
    expect(screen.getByText('后端返回空 Trace')).toBeInTheDocument()
  })

  it('shows the Agent retry action when an Agent network request fails', async () => {
    const user = userEvent.setup()
    render(<App agentClient={createNetworkFailingAgentClient()} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '网络失败')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('连接已断开'))
    const errorNotice = screen.getByRole('alert')
    expect(errorNotice).toHaveTextContent('无法连接 Agent 服务，请稍后重试。')
    expect(within(errorNotice).getByRole('button', { name: '重新运行 Agent' })).toBeInTheDocument()
  })

  it('uses semantic list and definition-list structures for accessible Agent content', async () => {
    const user = userEvent.setup()
    const agentClient: AgentClient = {
      runAgent: vi.fn().mockResolvedValue({
        runId: 'run-a11y',
        status: 'completed',
        answer: '基于来源的回答',
        stopReason: null,
        steps: [
          {
            id: 'step-1',
            index: 1,
            decisionKind: 'tool_call',
            status: 'completed',
            startedAt: '2026-08-05T00:00:00Z',
            completedAt: '2026-08-05T00:00:01Z',
            durationMs: 1000,
            toolNames: ['knowledge_search'],
            summary: '查询知识库',
            events: [],
            toolCalls: [
              {
                id: 'tool-1',
                name: 'knowledge_search',
                known: true,
                status: 'succeeded',
                stepIndex: 1,
                startedAt: '2026-08-05T00:00:00Z',
                completedAt: '2026-08-05T00:00:01Z',
                durationMs: 1000,
                inputSummary: '阶段六前端可访问性',
                outputSummary: '找到 1 条来源',
                errorCode: null,
                errorMessage: null,
                truncated: false,
                rag: {
                  status: 'success_with_sources',
                  warning: null,
                  errorCode: null,
                  references: [
                    {
                      documentId: 'doc-1',
                      chunkId: 'chunk-1',
                      chunkIndex: 0,
                      content: '语义结构应与内容类型匹配。',
                      distance: 0.12,
                      truncated: false,
                    },
                  ],
                },
              },
            ],
          },
        ],
        events: [],
        usage: {
          promptTokens: null,
          completionTokens: null,
          totalTokens: null,
          estimated: false,
        },
      }),
    }
    const { container } = render(<App agentClient={agentClient} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '检查可访问性')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))

    await waitFor(() => expect(screen.getByText('基于来源的回答')).toBeInTheDocument())

    expect(screen.getByRole('list', { name: '消息列表' })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Agent 步骤时间线' })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: '步骤 1 工具摘要' })).toBeInTheDocument()
    expect(container.querySelector('dl.srFacts')).toHaveAttribute('aria-label', '步骤时间信息')
    expect(
      [...container.querySelectorAll('div[aria-label]')].filter(
        (element) => !element.hasAttribute('role'),
      ),
    ).toHaveLength(0)
    expect(container.querySelector('.traceFacts dt')).toHaveStyle({ color: '#526177' })
    expect(container.querySelector('.ragReferenceFacts dt')).toHaveStyle({ color: '#526177' })
    expect(container.querySelector('.message-user > .messageRole')).toHaveStyle({
      color: '#ffffff',
      opacity: '1',
    })
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
    expect(screen.getByRole('button', { name: '复制 Request ID req-test-123' })).toBeInTheDocument()
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
    expect(screen.getByRole('alert')).toHaveTextContent('Chat 服务暂时不可用，请稍后重试。')

    const networkFailure = createControlledClient()
    rerender(<App chatClient={networkFailure.client} />)
    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    await user.type(screen.getByLabelText('输入消息'), '网络失败')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(networkFailure.getRequestCount()).toBe(1))
    networkFailure.fail(new ChatNetworkError('无法连接后端。'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('网络失败'))
    expect(screen.getByRole('alert')).toHaveTextContent('无法连接 Chat 服务，请稍后重试。')

    const interrupted = createControlledClient()
    rerender(<App chatClient={interrupted.client} />)
    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    await user.type(screen.getByLabelText('输入消息'), 'SSE 断连')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(interrupted.getRequestCount()).toBe(1))
    interrupted.fail(Object.assign(new Error('SSE 断连'), { name: 'ChatStreamInterruptedError' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('SSE 已断连'))
    expect(screen.getByRole('alert')).toHaveTextContent('Chat SSE 连接已断开，可重试。')
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

  it('supports keyboard send, preserves Enter for multiline input, and lets the keyboard stop Chat', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    const input = screen.getByLabelText('输入消息')
    await user.type(input, '第一行')
    await user.keyboard('{Enter}')
    await user.type(input, '第二行')
    expect(input).toHaveValue('第一行\n第二行')

    await user.keyboard('{Control>}{Enter}{/Control}')
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(screen.getByRole('button', { name: '停止请求' })).toBeInTheDocument()

    screen.getByRole('button', { name: '停止请求' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('status')).toHaveTextContent('已停止')
  })

  it('does not announce every Chat delta and supports safe copy success and failure feedback', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '复制测试')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    controlled.emitDelta('增量内容')
    expect(screen.getByRole('status')).not.toHaveTextContent('增量内容')
    controlled.finish()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /复制 Request ID/ })).toBeInTheDocument(),
    )

    await user.click(screen.getByRole('button', { name: '复制 Request ID req-test-123' }))
    expect(writeText).toHaveBeenCalledWith('req-test-123')
    expect(screen.getByText('Request ID已复制。')).toBeInTheDocument()

    writeText.mockRejectedValueOnce(new Error('denied'))
    await user.click(screen.getByRole('button', { name: '复制 Request ID req-test-123' }))
    expect(screen.getByText('Request ID复制失败，请手动选择文本。')).toBeInTheDocument()
  })

  it('offers Chat retry and removes the previous failed assistant state', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    render(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '请重试')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    controlled.fail(
      new ChatBackendError('内部 Provider response /secret', 502, 'PROVIDER_ERROR', 'req-failed'),
    )
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '重试 Chat' })).toBeInTheDocument(),
    )
    expect(screen.queryByText('内部 Provider response /secret')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重试 Chat' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
    expect(screen.queryByText('Chat 服务暂时不可用，请稍后重试。')).not.toBeInTheDocument()
  })
})
