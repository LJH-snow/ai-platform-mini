import '@testing-library/jest-dom/vitest'

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App.tsx'
import { AgentNetworkError, type AgentClient } from './agent/client.ts'
import type { AgentRunInput } from './agent/types.ts'
import type { AgentStreamEvent } from './agent/stream.ts'
import { ChatBackendError, ChatNetworkError, type ChatClient } from './chat/client.ts'
import type {
  ChatApiMessage,
  ChatMessage,
  ChatStreamHandlers,
  ChatStreamResult,
  ConversationSummary,
} from './chat/types.ts'

type ControlledRequest = {
  handlers: ChatStreamHandlers
  messages: ChatApiMessage[]
  threadId: string | null
  signal: AbortSignal
  resolve: (result: ChatStreamResult) => void
  reject: (error: Error) => void
}

const createControlledClient = (
  options: { historyMessages?: ChatMessage[]; conversations?: ConversationSummary[] } = {},
): {
  client: ChatClient
  emitDelta: (content: string, index?: number) => void
  emitRequestId: (requestId: string, index?: number) => void
  emitThreadId: (threadId: string, index?: number) => void
  finish: (index?: number, threadId?: string | null) => void
  fail: (error: Error, index?: number) => void
  getRequest: (index?: number) => ControlledRequest
  getRequestCount: () => number
  listThreadMessages: ReturnType<typeof vi.fn>
  listConversations: ReturnType<typeof vi.fn>
} => {
  const requests: ControlledRequest[] = []
  const listThreadMessages = vi.fn(
    async (_threadId: string, _signal?: AbortSignal): Promise<ChatMessage[]> =>
      options.historyMessages ?? [],
  )
  const listConversations = vi.fn(
    async (): Promise<ConversationSummary[]> => options.conversations ?? [],
  )
  const client: ChatClient = {
    streamChat: vi.fn(
      (
        messages: ChatApiMessage[],
        handlers: ChatStreamHandlers,
        signal: AbortSignal,
        threadId: string | null = null,
      ): Promise<ChatStreamResult> => {
        return new Promise((resolve, reject) => {
          requests.push({ handlers, messages, threadId, signal, resolve, reject })
        })
      },
    ),
    listThreadMessages,
    listConversations,
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
    emitThreadId: (threadId, index) => getRequest(index).handlers.onThreadId?.(threadId),
    finish: (index, threadId = null) => {
      const request = getRequest(index)
      request.handlers.onRequestId('req-test-123')
      request.resolve({ requestId: 'req-test-123', threadId })
    },
    fail: (error, index) => getRequest(index).reject(error),
    getRequest,
    getRequestCount: () => requests.length,
    listThreadMessages,
    listConversations,
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
  getRequest: (index?: number) => ControlledAgentRequest
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
    getRequest: (index = requests.length - 1) => {
      const request = requests[index]
      if (!request) throw new Error(`Missing controlled Agent request at index ${index}`)
      return request
    },
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
  sessionStorage.clear()
  vi.restoreAllMocks()
})

const renderConsole = (ui: Parameters<typeof render>[0]) => {
  const result = render(ui)
  fireEvent.click(screen.getByRole('button', { name: '对话工作台' }))
  return result
}

describe('App', () => {
  it('renders the Phase 6 realtime Agent surface and preserves the Chat SSE copy', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(
      screen.getByRole('heading', { name: '把模型能力，变成可观察的应用。' }),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '对话工作台' }))
    expect(screen.getByText('WORKSPACE · REAL-TIME AI')).toBeInTheDocument()

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
      renderConsole(<App agentClient={controlled.client} />)

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
    renderConsole(<App agentClient={controlled.client} />)

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
    renderConsole(<App agentClient={createNetworkFailingAgentClient()} />)

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
            toolCount: 1,
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
                argumentCount: 1,
                inputSummary: '阶段六前端可访问性',
                outputSummary: '找到 1 条来源',
                resultChars: 8,
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
    const { container } = renderConsole(<App agentClient={agentClient} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '检查可访问性')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))

    await waitFor(() => expect(screen.getByText('基于来源的回答')).toBeInTheDocument())

    const stepButton = screen.getByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    expect(screen.getByText('工具数量：1')).toBeVisible()
    const toolButton = screen.getByRole('button', { name: /工具调用 知识搜索.*成功/ })
    await user.click(toolButton)
    expect(screen.getByText('参数数量：1')).toBeVisible()
    expect(screen.getByText('输入摘要：阶段六前端可访问性')).toBeVisible()
    expect(screen.getByText('输出摘要：找到 1 条来源')).toBeVisible()
    expect(screen.getByText('结果字符数：8')).toBeVisible()
    expect(screen.getByText('状态：success_with_sources · 来源数量：1')).toBeVisible()

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
    renderConsole(<App chatClient={controlled.client} />)

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
    renderConsole(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '重复测试')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    expect(screen.getByRole('button', { name: '停止请求' })).toBeInTheDocument()
    expect(controlled.getRequestCount()).toBe(1)
  })

  it('stops the active request and ignores late delta and request id callbacks', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

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
    const { rerender } = renderConsole(<App chatClient={backendFailure.client} />)

    await user.type(screen.getByLabelText('输入消息'), '后端错误')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(backendFailure.getRequestCount()).toBe(1))
    backendFailure.fail(new ChatBackendError('后端返回 502', 502, 'PROVIDER_ERROR', 'req-error'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('后端错误'))
    expect(screen.getByRole('alert')).toHaveTextContent('Chat 服务暂时不可用，请稍后重试。')

    const networkFailure = createControlledClient()
    rerender(<App chatClient={networkFailure.client} />)
    fireEvent.click(screen.getByRole('button', { name: '对话工作台' }))
    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    await user.type(screen.getByLabelText('输入消息'), '网络失败')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(networkFailure.getRequestCount()).toBe(1))
    networkFailure.fail(new ChatNetworkError('无法连接后端。'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('网络失败'))
    expect(screen.getByRole('alert')).toHaveTextContent('无法连接 Chat 服务，请稍后重试。')

    const interrupted = createControlledClient()
    rerender(<App chatClient={interrupted.client} />)
    fireEvent.click(screen.getByRole('button', { name: '对话工作台' }))
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
    renderConsole(<App chatClient={controlled.client} />)

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
    renderConsole(<App chatClient={controlled.client} />)

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
    renderConsole(<App chatClient={controlled.client} />)

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
    renderConsole(<App chatClient={controlled.client} />)

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
    renderConsole(<App chatClient={controlled.client} />)

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

  it('opens knowledge-base Q&A as an Agent Run with the RAG preset', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/v1/ready')) {
          return new Response(
            JSON.stringify({
              rag: {
                enabled: true,
                status: 'ready',
                database: 'ok',
                database_reason: null,
                embedding: 'ok',
                embedding_reason: null,
                embedding_model: 'nomic-embed-text',
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(JSON.stringify({ models: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }),
    )
    const previousRuntimeConfig = window.__AI_PLATFORM_RUNTIME_CONFIG__
    window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
      apiBaseUrl: 'http://localhost',
      apiKey: 'sk-test',
    }
    const user = userEvent.setup()
    const controlled = createControlledAgentClient()
    render(<App agentClient={controlled.client} />)

    try {
      await user.click(screen.getByRole('button', { name: '知识库' }))
      const openButton = await screen.findByRole('button', { name: /去知识库问答/ })
      await waitFor(() => expect(openButton).toBeEnabled())
      await user.click(openButton)

      const modeGroup = screen.getByRole('group', { name: '请求模式' })
      expect(within(modeGroup).getByRole('button', { name: 'Agent Run 模式' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
      expect(within(modeGroup).getByRole('button', { name: '普通 Chat SSE 模式' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )

      const modeStatus = screen.getByRole('group', { name: '当前请求模式状态' })
      expect(within(modeStatus).getByText('Agent Run 模式')).toBeInTheDocument()
      expect(within(modeStatus).getByText('RAG Agent preset')).toBeInTheDocument()

      const input = screen.getByLabelText('输入消息') as HTMLTextAreaElement
      expect(input.value).toContain('必须先调用 knowledge_search')
      expect(input.value).toContain('不要使用未检索到的知识进行回答')

      await user.click(screen.getByRole('button', { name: '运行 Agent' }))
      await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
      expect(controlled.getRequest().input.preset).toBe('rag')

      controlled.emit(agentEvent('run-kb', 'run_started', 0))
      controlled.emit(agentEvent('run-kb', 'run_completed', 1, { status: 'completed' }))
      controlled.finish(0)
      await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))

      await user.click(screen.getByRole('button', { name: '普通 Chat SSE 模式' }))
      expect(
        within(screen.getByRole('group', { name: '当前请求模式状态' })).queryByText(
          'RAG Agent preset',
        ),
      ).not.toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
      if (previousRuntimeConfig === undefined) {
        delete window.__AI_PLATFORM_RUNTIME_CONFIG__
      } else {
        window.__AI_PLATFORM_RUNTIME_CONFIG__ = previousRuntimeConfig
      }
    }
  })

  it('keeps Chat SSE free of Agent Tool Trace and labels the mode clearly', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

    const modeStatus = screen.getByRole('group', { name: '当前请求模式状态' })
    expect(within(modeStatus).getByText('Chat SSE 模式')).toBeInTheDocument()
    expect(
      screen.getByText('普通对话，不执行工具调用，不会产生 Agent Tool Trace 或 RAG 来源。'),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Chat SSE 模式' })).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Agent 步骤时间线' })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '普通问题')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    controlled.emitDelta('普通回答')
    controlled.finish()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))

    expect(screen.getByRole('heading', { name: 'Chat SSE 模式' })).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Agent 步骤时间线' })).not.toBeInTheDocument()
    expect(screen.queryByText(/knowledge_search/)).not.toBeInTheDocument()
  })

  it('shows a real empty-sources state for no_relevant_sources, distinct from the model answer', async () => {
    const user = userEvent.setup()
    const agentClient: AgentClient = {
      runAgent: vi.fn().mockResolvedValue({
        runId: 'run-norelevant',
        status: 'completed',
        answer: '知识库没有相关内容。',
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
            toolCount: 1,
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
                argumentCount: 1,
                inputSummary: '知识搜索',
                outputSummary: 'no relevant sources',
                resultChars: 8,
                errorCode: null,
                errorMessage: null,
                truncated: false,
                rag: {
                  status: 'no_relevant_sources',
                  warning: null,
                  errorCode: 'no_relevant_context',
                  references: [],
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
    renderConsole(<App agentClient={agentClient} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '什么是智能体？')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(screen.getByText('知识库没有相关内容。')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /步骤 1.*工具调用/ }))
    await user.click(screen.getByRole('button', { name: /知识搜索.*成功/ }))

    expect(screen.getByText('当前知识库没有关于该问题的相关内容')).toBeInTheDocument()
    expect(screen.getAllByText(/来源数量：0/).length).toBeGreaterThan(0)
    expect(screen.queryByText('参考来源：来源暂不可用')).not.toBeInTheDocument()
    expect(screen.getByText(/这不是数据库或 Embedding 故障/)).toBeInTheDocument()
    expect(screen.getByText('知识库没有相关内容。')).toBeInTheDocument()
  })

  it('renders the conversation and trace panels as distinct scroll containers', async () => {
    const user = userEvent.setup()
    const chat = createControlledClient()
    const agentClient: AgentClient = {
      runAgent: vi.fn().mockResolvedValue({
        runId: 'run-scroll',
        status: 'completed',
        answer: '滚动容器回答',
        stopReason: null,
        steps: [],
        events: [],
        usage: {
          promptTokens: null,
          completionTokens: null,
          totalTokens: null,
          estimated: false,
        },
      }),
    }
    renderConsole(<App chatClient={chat.client} agentClient={agentClient} />)

    await user.type(screen.getByLabelText('输入消息'), '你好')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(chat.getRequestCount()).toBe(1))
    chat.emitDelta('回答')
    chat.finish()
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))

    const conversationPanel = screen.getByLabelText('会话')
    expect(conversationPanel).toHaveClass('conversationPanel')
    expect(conversationPanel.querySelector('.messageList')).not.toBeNull()

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '运行 Agent')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(screen.getByText('滚动容器回答')).toBeInTheDocument())

    const tracePanel = screen.getByLabelText('Agent Trace')
    expect(tracePanel).toHaveClass('tracePanel')
    expect(tracePanel.querySelector('.traceContent')).not.toBeNull()
  })

  it('restores, updates, and clears the Chat thread id with new sessions', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-restored')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '第一轮')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBe('thread-restored')

    controlled.emitThreadId('thread-live', 0)
    controlled.finish(0, 'thread-live')
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-live')

    await user.type(screen.getByLabelText('输入消息'), '第二轮')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
    expect(controlled.getRequest(1).threadId).toBe('thread-live')
    controlled.finish(1, 'thread-live')
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))

    await user.click(screen.getByRole('button', { name: '新建会话' }))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull()

    await user.type(screen.getByLabelText('输入消息'), '第三轮')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(3))
    expect(controlled.getRequest(2).threadId).toBeNull()
    controlled.finish(2)
  })

  it('passes the current thread id to Agent runs and stores stream thread ids', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'agent-thread-1')
    const user = userEvent.setup()
    const controlled = createControlledAgentClient()
    renderConsole(<App agentClient={controlled.client} />)

    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), 'Agent 问题')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).input.threadId).toBe('agent-thread-1')

    controlled.emit(agentEvent('run-1', 'run_started', 0, { thread_id: 'agent-thread-2' }), 0)
    controlled.emit(
      agentEvent('run-1', 'run_completed', 1, {
        status: 'completed',
        thread_id: 'agent-thread-2',
      }),
      0,
    )
    controlled.finish(0)

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已完成'))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('agent-thread-2')
  })

  it('clears the thread id when clearing the current session', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-clear')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '清空线程')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBe('thread-clear')

    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull()

    await user.type(screen.getByLabelText('输入消息'), '新线程')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
    expect(controlled.getRequest(1).threadId).toBeNull()
  })

  it('keeps the backend-provided thread id after a Chat failure', async () => {
    const user = userEvent.setup()
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

    await user.type(screen.getByLabelText('输入消息'), '失败线程')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    controlled.fail(
      new ChatBackendError('后端失败', 502, 'PROVIDER_ERROR', 'req-error', 'thread-chat-error'),
    )
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/失败/))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-chat-error')
  })

  it('keeps the backend-provided thread id after an Agent failure', async () => {
    const user = userEvent.setup()
    const controlled = createControlledAgentClient()
    renderConsole(<App agentClient={controlled.client} />)
    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), 'Agent 失败线程')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    controlled.getRequest().reject(new AgentNetworkError(undefined, 'thread-agent-error'))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/失败/))
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-agent-error')
  })

  it('restores thread history on mount and sends it as current messages', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-history')
    const user = userEvent.setup()
    const controlled = createControlledClient({
      historyMessages: [
        { id: 'server-1', role: 'user', content: '历史问题' },
        { id: 'server-2', role: 'assistant', content: '历史回答' },
      ],
    })
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() =>
      expect(controlled.listThreadMessages).toHaveBeenCalledWith(
        'thread-history',
        expect.any(AbortSignal),
      ),
    )
    await waitFor(() => expect(screen.getByText('历史问题')).toBeInTheDocument())
    expect(screen.getByText('历史回答')).toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '继续')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBe('thread-history')
    expect(controlled.getRequest(0).messages).toEqual([
      { role: 'user', content: '历史问题' },
      { role: 'assistant', content: '历史回答' },
      { role: 'user', content: '继续' },
    ])
  })

  it('keeps the thread id and stays usable when history restore fails', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-failed')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    controlled.listThreadMessages.mockRejectedValueOnce(new ChatNetworkError())
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('会话历史恢复失败，可继续提问。'),
    )
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-failed')

    await user.type(screen.getByLabelText('输入消息'), '还能提问')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBe('thread-failed')
  })

  it.each([
    ['HTTP 500', new ChatBackendError('服务暂时不可用', 500, 'PROVIDER_ERROR', 'req-500')],
    ['HTTP 429', new ChatBackendError('请求过于频繁', 429, 'RATE_LIMITED', 'req-429')],
  ])('keeps the thread id when history restore fails with %s', async (_label, error) => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-transient')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    controlled.listThreadMessages.mockRejectedValueOnce(error)
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('会话历史恢复失败，可继续提问。'),
    )
    expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-transient')

    await user.type(screen.getByLabelText('输入消息'), '还能提问')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBe('thread-transient')
  })

  it('clears an expired thread id when history restore returns CONVERSATION_NOT_FOUND', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-expired')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    controlled.listThreadMessages.mockRejectedValueOnce(
      new ChatBackendError('会话不存在', 404, 'CONVERSATION_NOT_FOUND', 'req-history-404'),
    )
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() => expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull())
    expect(screen.getByRole('status')).toHaveTextContent('原会话已失效，已准备好新会话。')
    expect(screen.queryByText('会话历史恢复失败，可继续提问。')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '新会话问题')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBeNull()
  })

  it('clears an expired thread id when history restore returns a plain 404', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-expired-404')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    controlled.listThreadMessages.mockRejectedValueOnce(
      new ChatBackendError('会话不存在', 404, null, 'req-history-404'),
    )
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() => expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull())
    expect(screen.getByRole('status')).toHaveTextContent('原会话已失效，已准备好新会话。')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '新会话问题')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBeNull()
  })

  it('clears an expired thread id when history restore reports CONVERSATION_NOT_FOUND without 404', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-expired-code')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    controlled.listThreadMessages.mockRejectedValueOnce(
      new ChatBackendError('会话不存在', 500, 'CONVERSATION_NOT_FOUND', 'req-history-code'),
    )
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() => expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull())
    expect(screen.getByRole('status')).toHaveTextContent('原会话已失效，已准备好新会话。')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '新会话问题')
    await user.click(screen.getByRole('button', { name: '发送消息' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
    expect(controlled.getRequest(0).threadId).toBeNull()
  })

  it('aborts a pending history restore when starting a new session', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-pending')
    const user = userEvent.setup()
    const controlled = createControlledClient()
    let resolveHistory: (messages: ChatMessage[]) => void = () => {}
    controlled.listThreadMessages.mockImplementation(
      (_threadId: string, signal?: AbortSignal) =>
        new Promise<ChatMessage[]>((resolve) => {
          signal?.addEventListener('abort', () => resolve([]))
          resolveHistory = resolve
        }),
    )
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() => expect(controlled.listThreadMessages).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: '新建会话' }))

    expect(sessionStorage.getItem('ai-platform-thread-id')).toBeNull()
    expect(controlled.listThreadMessages.mock.calls[0]?.[1]?.aborted).toBe(true)

    resolveHistory([])
    await waitFor(() => expect(screen.queryByText('已恢复会话历史。')).not.toBeInTheDocument())
    expect(screen.queryByText('会话历史恢复失败，可继续提问。')).not.toBeInTheDocument()
  })

  it('lists older conversations and restores the selected thread', async () => {
    sessionStorage.setItem('ai-platform-thread-id', 'thread-recent')
    const previousRuntimeConfig = window.__AI_PLATFORM_RUNTIME_CONFIG__
    window.__AI_PLATFORM_RUNTIME_CONFIG__ = {
      apiBaseUrl: 'http://localhost',
      apiKey: 'sk-test',
    }
    const user = userEvent.setup()
    const controlled = createControlledClient({
      conversations: [
        {
          thread_id: 'thread-recent',
          title: '最近的问题',
          created_at: '2026-08-07T00:00:00Z',
          updated_at: '2026-08-07T01:00:00Z',
        },
        {
          thread_id: 'thread-older',
          title: '更早的问题',
          created_at: '2026-08-01T00:00:00Z',
          updated_at: '2026-08-02T00:00:00Z',
        },
      ],
    })
    controlled.listThreadMessages.mockImplementation(async (threadId: string) =>
      threadId === 'thread-older' ? [{ id: 'old-1', role: 'user', content: '更早的历史消息' }] : [],
    )

    try {
      renderConsole(<App chatClient={controlled.client} />)

      await waitFor(() => expect(controlled.listConversations).toHaveBeenCalled())
      expect(screen.getAllByText('最近的问题').length).toBeGreaterThan(0)
      expect(screen.getByText('更早的问题')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: /更早的问题/ }))
      await waitFor(() =>
        expect(controlled.listThreadMessages).toHaveBeenCalledWith(
          'thread-older',
          expect.any(AbortSignal),
        ),
      )
      await waitFor(() => expect(screen.getByText('更早的历史消息')).toBeInTheDocument())
      expect(sessionStorage.getItem('ai-platform-thread-id')).toBe('thread-older')

      await user.type(screen.getByLabelText('输入消息'), '继续')
      await user.click(screen.getByRole('button', { name: '发送消息' }))
      await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
      expect(controlled.getRequest(0).threadId).toBe('thread-older')
    } finally {
      if (previousRuntimeConfig === undefined) {
        delete window.__AI_PLATFORM_RUNTIME_CONFIG__
      } else {
        window.__AI_PLATFORM_RUNTIME_CONFIG__ = previousRuntimeConfig
      }
    }
  })

  it('does not request history when no thread id exists', async () => {
    const controlled = createControlledClient()
    renderConsole(<App chatClient={controlled.client} />)

    await waitFor(() => expect(controlled.listThreadMessages).not.toHaveBeenCalled())
  })
})
