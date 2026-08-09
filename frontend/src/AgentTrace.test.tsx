import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import {
  AgentBackendError,
  AgentNetworkError,
  AgentResponseError,
  type AgentClient,
} from './agent/client.ts'
import type {
  AgentRag,
  AgentRagReference,
  AgentRagStatus,
  AgentRun,
  AgentRunInput,
  AgentRunStatus,
  AgentToolStatus,
  AgentTraceStep,
  AgentUsage,
} from './agent/types.ts'
import type { AgentStreamEvent } from './agent/stream.ts'
import { isKnownTool, localizeToolName } from './agent/tool-name.ts'
import type { ChatClient } from './chat/client.ts'
import type { ChatStreamResult } from './chat/types.ts'

const idleChatClient: ChatClient = {
  streamChat: vi.fn(() => new Promise<ChatStreamResult>(() => undefined)),
  listThreadMessages: vi.fn(async () => []),
  listConversations: vi.fn(async () => []),
}

type ControlledAgentRequest = {
  input: AgentRunInput
  signal: AbortSignal
  resolve: (run: AgentRun) => void
  reject: (error: Error) => void
}

const createControlledAgentClient = () => {
  const requests: ControlledAgentRequest[] = []
  const client: AgentClient = {
    runAgent: vi.fn((input, signal) => {
      return new Promise<AgentRun>((resolve, reject) => {
        requests.push({ input, signal, resolve, reject })
      })
    }),
  }
  const getRequest = (index = requests.length - 1): ControlledAgentRequest => {
    const request = requests[index]
    if (!request) {
      throw new Error(`Missing agent request ${index}`)
    }
    return request
  }
  return { client, getRequest, getRequestCount: () => requests.length }
}

type ControlledAgentStreamRequest = {
  input: AgentRunInput
  signal: AbortSignal
  emit: (event: AgentStreamEvent) => void
  complete: () => void
}

const createControlledAgentStreamClient = () => {
  const requests: ControlledAgentStreamRequest[] = []
  const client: AgentClient = {
    runAgent: vi.fn(),
    streamAgent: vi.fn((input, handlers, signal) => {
      return new Promise<void>((resolve, reject) => {
        const request: ControlledAgentStreamRequest = {
          input,
          signal,
          emit: (event) => {
            handlers.onEvent(event)
            if (
              event.event === 'run_completed' ||
              event.event === 'run_failed' ||
              event.event === 'run_timed_out' ||
              event.event === 'run_cancelled' ||
              event.event === 'run_stopped'
            ) {
              resolve()
            }
          },
          complete: resolve,
        }
        requests.push(request)
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
          once: true,
        })
      })
    }),
  }
  const getRequest = (index = requests.length - 1): ControlledAgentStreamRequest => {
    const request = requests[index]
    if (!request) throw new Error(`Missing agent stream request ${index}`)
    return request
  }
  return { client, getRequest, getRequestCount: () => requests.length }
}

const createRun = ({
  status = 'completed',
  answer = '计算结果是 4。',
  toolName = 'calculator',
  toolStatus = 'succeeded',
  empty = false,
  rag = null,
  argumentCount = null,
  inputSummary = null,
  outputSummary = null,
  resultChars = null,
  stopReason = null,
  usage = {
    promptTokens: null,
    completionTokens: null,
    totalTokens: null,
    estimated: false,
  },
  finalAnswerStep = true,
}: {
  status?: AgentRunStatus
  answer?: string | null
  toolName?: string
  toolStatus?: AgentToolStatus
  empty?: boolean
  rag?: AgentRag | null
  argumentCount?: number | null
  inputSummary?: string | null
  outputSummary?: string | null
  resultChars?: number | null
  stopReason?: string | null
  usage?: AgentUsage
  finalAnswerStep?: boolean
} = {}): AgentRun => {
  const finalAnswerStepData: AgentTraceStep = {
    id: 'step-2-final_answer',
    index: 2,
    decisionKind: 'final_answer',
    status: status === 'completed' ? 'completed' : status,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    toolNames: [],
    toolCount: 0,
    summary: '模型生成最终回答。',
    toolCalls: [],
    events: [],
  }

  return {
    runId: 'run-real-123',
    status,
    answer,
    stopReason:
      stopReason ??
      (status === 'timed_out'
        ? 'deadline_exceeded'
        : status === 'cancelled'
          ? 'external_cancelled'
          : status === 'failed'
            ? 'model_error'
            : 'direct_answer'),
    steps: empty
      ? []
      : [
          {
            id: 'step-1-tool_call',
            index: 1,
            decisionKind: 'tool_call',
            status: toolStatus === 'succeeded' ? 'completed' : status,
            startedAt: null,
            completedAt: null,
            durationMs: null,
            toolNames: [toolName],
            toolCount: 1,
            summary: `模型决定调用：${localizeToolName(toolName)}。`,
            toolCalls: [
              {
                id: `step-1-tool-0-${toolName}`,
                name: toolName,
                known: isKnownTool(toolName),
                status: toolStatus,
                stepIndex: 1,
                startedAt: null,
                completedAt: null,
                durationMs: null,
                argumentCount,
                inputSummary,
                outputSummary,
                resultChars,
                errorCode: null,
                errorMessage:
                  toolStatus === 'succeeded' ? null : '工具调用未成功。后端未提供错误详情。',
                truncated: null,
                rag,
              },
            ],
            events: [
              {
                id: 'tool-started:1::',
                kind: 'tool_started',
                stepIndex: 1,
                status: null,
                stopReason: null,
              },
            ],
          },
          ...(finalAnswerStep ? [finalAnswerStepData] : []),
        ],
    events: [],
    usage,
  }
}

const startAgentRun = async (controlled: ReturnType<typeof createControlledAgentClient>) => {
  const user = userEvent.setup()
  renderConsole(<App chatClient={idleChatClient} agentClient={controlled.client} />)
  await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
  await user.type(screen.getByLabelText('输入消息'), '请计算 2+2')
  await user.click(screen.getByRole('button', { name: '运行 Agent' }))
  await waitFor(() => expect(controlled.getRequestCount()).toBe(1))
  return user
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const renderConsole = (ui: Parameters<typeof render>[0]) => {
  const result = render(ui)
  fireEvent.click(screen.getByRole('button', { name: '对话工作台' }))
  return result
}

describe('Agent Trace integration', () => {
  it('renders cumulative answer_delta output without announcing each delta', async () => {
    const controlled = createControlledAgentStreamClient()
    const user = userEvent.setup()
    renderConsole(<App chatClient={idleChatClient} agentClient={controlled.client} />)
    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '请回答')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    expect(screen.getByText('实时 Agent SSE')).toBeInTheDocument()
    expect(
      screen.getByText('正在接收后端真实 Agent SSE，Trace 将随事件实时更新。'),
    ).toBeInTheDocument()

    const request = controlled.getRequest()
    request.emit({ event: 'run_started', run_id: 'run-stream-1', sequence: 0 })
    request.emit({
      event: 'step_started',
      run_id: 'run-stream-1',
      sequence: 1,
      step_index: 1,
    })
    request.emit({
      event: 'answer_delta',
      run_id: 'run-stream-1',
      sequence: 2,
      delta: '真实',
    })
    await waitFor(() => expect(screen.getByText('真实')).toBeInTheDocument())
    const liveRegion = screen.getByRole('status')
    expect(liveRegion).toHaveTextContent('Agent 执行中。Agent Run 已开始。')

    request.emit({
      event: 'answer_delta',
      run_id: 'run-stream-1',
      sequence: 3,
      delta: '回答',
    })
    await waitFor(() => expect(screen.getByText('真实回答')).toBeInTheDocument())
    expect(liveRegion).toHaveTextContent('Agent 执行中。Agent Run 已开始。')

    request.emit({
      event: 'run_completed',
      run_id: 'run-stream-1',
      sequence: 4,
      status: 'completed',
      stop_reason: 'direct_answer',
    })
    await waitFor(() => expect(liveRegion).toHaveTextContent('已完成。Agent Run 已完成。'))
    expect(screen.getByText('真实回答')).toBeInTheDocument()
  })

  it('shows realtime Agent SSE waiting state, then updates Trace as events arrive', async () => {
    const controlled = createControlledAgentStreamClient()
    const user = userEvent.setup()
    renderConsole(<App chatClient={idleChatClient} agentClient={controlled.client} />)
    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '请计算 2+2')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    const conversation = screen.getByRole('article', { name: '会话' })
    const trace = screen.getByRole('complementary', { name: 'Agent Trace' })
    expect(within(conversation).getByRole('status')).toHaveTextContent('连接 Agent 中')
    expect(within(trace).getByText('实时 Agent SSE')).toBeInTheDocument()
    expect(
      within(trace).getByText('正在接收后端真实 Agent SSE，Trace 将随事件实时更新。'),
    ).toBeInTheDocument()
    expect(within(trace).queryByText(/同步请求|完成后加载 Trace|非实时/)).not.toBeInTheDocument()

    const request = controlled.getRequest()
    request.emit({ event: 'run_started', run_id: 'run-stream-2', sequence: 0 })

    await waitFor(() => expect(within(trace).getByText('Run ID：run-stream-2')).toBeInTheDocument())
    expect(within(trace).getByText('后端返回空 Trace')).toBeInTheDocument()

    request.emit({
      event: 'step_started',
      run_id: 'run-stream-2',
      sequence: 1,
      step_index: 1,
    })
    await waitFor(() =>
      expect(
        within(trace).getByRole('button', { name: /步骤 1，步骤信息待确认/ }),
      ).toBeInTheDocument(),
    )
    const pendingStepButton = within(trace).getByRole('button', {
      name: /步骤 1，步骤信息待确认/,
    })
    await user.click(pendingStepButton)
    expect(within(trace).getByText('模型正在分析任务，判断是否需要调用工具。')).toBeVisible()

    request.emit({
      event: 'run_completed',
      run_id: 'run-stream-2',
      sequence: 2,
      status: 'completed',
      stop_reason: 'direct_answer',
    })
    await waitFor(() =>
      expect(within(conversation).getByRole('status')).toHaveTextContent('已完成'),
    )
  })

  it('renders and toggles a real calculator step and tool summary without invented timing or payloads', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun())

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    expect(stepButton).toHaveAttribute('aria-expanded', 'false')
    expect(stepButton).toHaveAccessibleName(/展开/)
    const stepContent = document.getElementById(stepButton.getAttribute('aria-controls') ?? '')
    expect(stepContent).toBeInTheDocument()
    expect(stepContent).not.toBeVisible()
    stepButton.focus()
    await user.keyboard(' ')
    expect(stepButton).toHaveAttribute('aria-expanded', 'true')
    expect(stepButton).toHaveAccessibleName(/收起/)
    const traceFacts = stepContent?.querySelector('.traceFacts')
    expect(traceFacts).toBeInTheDocument()
    expect(within(traceFacts as HTMLElement).getByText('开始时间')).toBeVisible()
    expect([...traceFacts!.querySelectorAll('dd')].map((item) => item.textContent)).toEqual([
      '后端未提供',
      '后端未提供',
      '后端未提供',
    ])
    const srFacts = stepContent?.querySelector('.srFacts')
    expect(srFacts).toHaveAttribute('aria-label', '步骤时间信息')
    expect(srFacts?.querySelector('dt')).toHaveTextContent('开始时间')
    expect(srFacts?.querySelector('dt + dd')).toHaveTextContent('后端未提供')

    const toolButton = screen.getByRole('button', { name: /计算器.*成功/ })
    expect(toolButton).toHaveAccessibleName(/展开/)
    const toolContent = document.getElementById(toolButton.getAttribute('aria-controls') ?? '')
    expect(toolContent).toBeInTheDocument()
    expect(toolContent).not.toBeVisible()
    toolButton.focus()
    await user.keyboard(' ')
    expect(toolButton).toHaveAttribute('aria-expanded', 'true')
    expect(toolButton).toHaveAccessibleName(/收起/)
    expect(screen.getByText('输入摘要：后端未提供')).toBeVisible()
    expect(screen.getByText('输出摘要：后端未提供')).toBeVisible()
    toolButton.focus()
    await user.keyboard(' ')
    expect(toolContent).not.toBeVisible()
  })

  it('renders real tool metadata as text for unknown tools without creating HTML', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(
      createRun({
        toolName: 'mystery_tool',
        argumentCount: 3,
        inputSummary: '<literal>',
        outputSummary: 'ok',
        resultChars: 2,
      }),
    )

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    const toolButton = screen.getByRole('button', { name: /mystery_tool.*成功/ })
    await user.click(toolButton)

    expect(screen.getByText('参数数量：3')).toBeVisible()
    expect(screen.getByText('输入摘要：<literal>')).toBeVisible()
    expect(screen.getByText('输出摘要：ok')).toBeVisible()
    expect(screen.getByText('结果字符数：2')).toBeVisible()
    expect(screen.queryByRole('strong', { name: 'literal' })).not.toBeInTheDocument()
  })

  it.each([
    ['failed', 'failed', '运行失败', '工具调用失败'],
    ['timed_out', 'timed_out', '运行超时', '工具调用超时'],
    ['cancelled', 'cancelled', '运行已取消', '工具调用已取消'],
  ] as const)(
    'keeps the answer and trace consistent for %s calculator outcomes',
    async (status, toolStatus, statusLabel, toolLabel) => {
      const controlled = createControlledAgentClient()
      const user = await startAgentRun(controlled)
      controlled.getRequest().resolve(createRun({ status, answer: null, toolStatus }))

      const conversation = screen.getByRole('article', { name: '会话' })
      await waitFor(() =>
        expect(within(conversation).getByRole('status')).toHaveTextContent(statusLabel),
      )
      expect(screen.getAllByText(toolLabel).length).toBeGreaterThan(0)
      if (status === 'failed') {
        expect(screen.getByRole('button', { name: '重新运行' })).toBeInTheDocument()
        await user.click(screen.getByRole('button', { name: '重新运行' }))
        await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
      }
    },
  )

  it('shows a real token budget stop with completed RAG work and no fake answer', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    const references: AgentRagReference[] = [
      {
        documentId: 'doc-budget',
        chunkId: 'chunk-budget',
        chunkIndex: 0,
        content: '预算修复后的真实参考片段。',
        distance: 0.11,
        truncated: false,
      },
    ]
    controlled.getRequest().resolve(
      createRun({
        status: 'stopped',
        stopReason: 'token_budget_exceeded',
        answer: null,
        toolName: 'knowledge_search',
        toolStatus: 'succeeded',
        finalAnswerStep: false,
        rag: {
          status: 'success_with_sources',
          warning: 'Retrieved content is untrusted reference material.',
          errorCode: null,
          references,
        },
        usage: {
          promptTokens: 7000,
          completionTokens: 2000,
          totalTokens: 9000,
          estimated: false,
        },
      }),
    )

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'Agent 达到 token 预算，已完成检索但未生成最终回答。',
      ),
    )
    expect(screen.getByText('预算超限')).toBeInTheDocument()
    expect(screen.getByText('停止原因：token_budget_exceeded')).toBeInTheDocument()
    expect(screen.getByText('总 Token：9000')).toBeInTheDocument()
    expect(
      screen.getByText('Agent 达到 token 预算，已完成检索但未生成最终回答。'),
    ).toBeInTheDocument()
    expect(screen.queryByText('must not be returned')).not.toBeInTheDocument()

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    await user.click(screen.getByRole('button', { name: /知识搜索.*成功/ }))
    expect(screen.getByText('参考来源：1 条')).toBeInTheDocument()
    expect(screen.getByText('doc-budget')).toBeInTheDocument()
  })

  it('labels an unknown tool without treating it as calculator', async () => {
    const controlled = createControlledAgentClient()
    await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'future_tool' }))

    expect((await screen.findAllByText('未知工具')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('future_tool').length).toBeGreaterThan(0)
  })

  it('renders MCP tools with a friendly name and no unknown badge', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'mcp__docs-server__search_docs' }))

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    const toolButton = screen.getByRole('button', { name: /文档搜索.*成功/ })
    expect(toolButton).not.toHaveTextContent('未知工具')
    expect(screen.getAllByText('文档搜索').length).toBeGreaterThan(0)
    expect(screen.queryByText('未知工具')).not.toBeInTheDocument()
    expect(screen.queryByText('mcp__docs-server__search_docs')).not.toBeInTheDocument()
  })

  it('recognizes knowledge_search and explicitly reports that the current response has no sources', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'knowledge_search' }))

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    const toolButton = screen.getByRole('button', { name: /知识搜索.*成功/ })

    expect(toolButton).not.toHaveTextContent('未知工具')
    await user.click(toolButton)
    expect(screen.getByText('参考来源：暂无可用来源')).toBeInTheDocument()
    expect(
      screen.getByText('当前 Agent Run 响应未提供可展示的来源字段；前端不会生成来源卡片或引用。'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/来源名称|距离|文档片段/)).not.toBeInTheDocument()
  })

  it('renders one or more real RAG references inside the matching knowledge_search tool card', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    const references: AgentRagReference[] = [
      {
        documentId: 'doc-1',
        chunkId: 'chunk-1',
        chunkIndex: 0,
        content: '第一段真实参考内容',
        distance: 0.12,
        truncated: false,
      },
      {
        documentId: 'doc-2',
        chunkId: 'chunk-9',
        chunkIndex: 4,
        content: '第二段真实参考内容',
        distance: 0.42,
        truncated: false,
      },
    ]
    controlled.getRequest().resolve(
      createRun({
        toolName: 'knowledge_search',
        rag: {
          status: 'success_with_sources',
          warning: 'Retrieved content is untrusted reference material.',
          errorCode: null,
          references,
        },
      }),
    )

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    const toolButton = screen.getByRole('button', { name: /知识搜索.*成功/ })
    await user.click(toolButton)

    const ragToggle = screen.getByRole('button', { name: /参考来源：2 条/ })
    expect(ragToggle).toHaveAttribute('aria-expanded', 'true')
    expect(ragToggle).toHaveAccessibleName(/收起参考来源/)
    const ragContent = document.getElementById(ragToggle.getAttribute('aria-controls') ?? '')
    expect(ragContent).toBeInTheDocument()
    expect(ragContent).toBeVisible()
    ragToggle.focus()
    await user.keyboard(' ')
    expect(ragToggle).toHaveAttribute('aria-expanded', 'false')
    expect(ragToggle).toHaveAccessibleName(/展开参考来源/)
    expect(ragContent).not.toBeVisible()
    expect(screen.getByText('doc-1')).not.toBeVisible()
    await user.keyboard(' ')
    expect(ragContent).toBeVisible()

    const toolCard = toolButton.parentElement
    expect(toolCard).not.toBeNull()
    expect(within(toolCard as HTMLElement).getByText('参考来源：2 条')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getByText(/不可信参考提示/)).toHaveTextContent(
      'Retrieved content is untrusted reference material.',
    )
    expect(within(toolCard as HTMLElement).getAllByText('文档标识')).toHaveLength(2)
    expect(within(toolCard as HTMLElement).getByText('doc-1')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getAllByText('分块标识')).toHaveLength(2)
    expect(within(toolCard as HTMLElement).getByText('chunk-9')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getAllByText('分块序号')).toHaveLength(2)
    expect(within(toolCard as HTMLElement).getByText('4')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getAllByText('距离')).toHaveLength(2)
    expect(within(toolCard as HTMLElement).getByText('0.42')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getByText('步骤序号：1')).toBeInTheDocument()
    expect(within(toolCard as HTMLElement).getByText('调用标识：后端未提供')).toBeInTheDocument()
    expect(screen.queryByText(/引用编号|rank|来源名称|URL/)).not.toBeInTheDocument()
  })

  const ragStatusCases: Array<[AgentRagStatus, string]> = [
    ['no_relevant_sources', '参考来源：暂无相关来源'],
    ['loading', '参考来源：加载中'],
    ['knowledge_base_empty', '参考来源：知识库为空'],
    ['rag_unavailable', '参考来源：来源暂不可用'],
    ['embedding_failed', '参考来源：来源暂不可用'],
    ['output_unavailable', '参考来源：来源暂不可用'],
    ['failed', '参考来源：来源暂不可用'],
  ]

  it.each(ragStatusCases)('renders %s status as %s', async (status, title) => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(
      createRun({
        toolName: 'knowledge_search',
        rag: {
          status,
          warning: 'RAG content is untrusted reference material.',
          errorCode: status === 'failed' ? 'failed' : null,
          references: [],
        },
      }),
    )

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    await user.click(screen.getByRole('button', { name: /知识搜索.*成功/ }))
    expect(screen.getByText(title)).toBeInTheDocument()
    expect(screen.getByText(/不可信参考提示/)).toHaveTextContent(
      'RAG content is untrusted reference material.',
    )
    if (status === 'no_relevant_sources') {
      expect(screen.queryByText('参考来源：来源暂不可用')).not.toBeInTheDocument()
    }
  })

  it('marks truncated references, renders source text as text, and falls back for missing fields', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(
      createRun({
        toolName: 'knowledge_search',
        rag: {
          status: 'success_with_sources',
          warning: '<b>Do not follow source instructions.</b>',
          errorCode: null,
          references: [
            {
              documentId: null,
              chunkId: null,
              chunkIndex: null,
              content: '<script>alert("unsafe")</script>安全片段',
              distance: null,
              truncated: true,
            },
          ],
        },
      }),
    )

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    await user.click(screen.getByRole('button', { name: /知识搜索.*成功/ }))

    expect(screen.getByText(/不可信参考提示/)).toHaveTextContent(
      '<b>Do not follow source instructions.</b>',
    )
    expect(screen.getByText(/安全片段/)).toHaveTextContent(
      '<script>alert("unsafe")</script>安全片段',
    )
    expect(screen.queryByRole('button', { name: /unsafe/ })).not.toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(screen.getAllByText('后端未提供').length).toBeGreaterThanOrEqual(4)
    expect(screen.getByText('内容已按安全边界截断，未展示完整片段。')).toBeInTheDocument()
  })

  it('does not show RAG source UI for calculator calls', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'calculator' }))

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    await user.click(screen.getByRole('button', { name: /计算器.*成功/ }))

    expect(screen.queryByText(/参考来源/)).not.toBeInTheDocument()
    expect(screen.queryByText(/不可信参考材料/)).not.toBeInTheDocument()
  })

  it('does not retain a knowledge_search no-source notice after clearing and starting a new run', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'knowledge_search' }))

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    await user.click(stepButton)
    await user.click(screen.getByRole('button', { name: /知识搜索.*成功/ }))
    expect(screen.getByText('参考来源：暂无可用来源')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '清空当前会话' }))
    expect(screen.queryByText('参考来源：暂无可用来源')).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('输入消息'), '请计算 3+3')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
    controlled.getRequest().resolve(createRun({ toolName: 'calculator' }))

    expect((await screen.findAllByText('计算器')).length).toBeGreaterThan(0)
    expect(screen.queryByText('参考来源：暂无可用来源')).not.toBeInTheDocument()
  })

  it('cancels only the local request lifecycle and does not claim a backend terminal state', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)

    await user.click(screen.getByRole('button', { name: '停止请求' }))

    expect(controlled.getRequest().signal.aborted).toBe(true)
    expect(screen.getByRole('status')).toHaveTextContent('请求已取消')
    expect(screen.getByText('前端已停止等待，后端终态未知')).toBeInTheDocument()
    expect(screen.queryByText('Run ID：run-real-123')).not.toBeInTheDocument()
  })

  it('allows retrying an Agent request after the connection is lost', async () => {
    const controlled = createControlledAgentClient()
    await startAgentRun(controlled)
    controlled.getRequest().reject(new AgentNetworkError())

    expect(await screen.findByRole('button', { name: '重新运行 Agent' })).toBeInTheDocument()
  })

  it('allows retrying an Agent request after an SSE response format error', async () => {
    const controlled = createControlledAgentStreamClient()
    const user = userEvent.setup()
    renderConsole(<App chatClient={idleChatClient} agentClient={controlled.client} />)
    await user.click(screen.getByRole('button', { name: 'Agent Run 模式' }))
    await user.type(screen.getByLabelText('输入消息'), '触发格式错误')
    await user.click(screen.getByRole('button', { name: '运行 Agent' }))
    await waitFor(() => expect(controlled.getRequestCount()).toBe(1))

    controlled.getRequest().complete()

    expect(await screen.findByRole('button', { name: '重新运行 Agent' })).toBeInTheDocument()
  })

  it.each([
    ['HTTP', new AgentBackendError('Agent 服务暂时不可用，请稍后重试。', 502, null)],
    ['网络', new AgentNetworkError()],
    ['响应', new AgentResponseError()],
  ] as const)('shows an explicit unavailable trace after a %s failure', async (_kind, error) => {
    const controlled = createControlledAgentClient()
    await startAgentRun(controlled)
    controlled.getRequest().reject(error)

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/失败/))
    expect(screen.getByText('未收到 Trace')).toBeInTheDocument()
    expect(screen.getByText('本次 Agent 请求未收到可展示的 Trace。')).toBeInTheDocument()
    expect(screen.getByText(/没有可安全展示的运行或步骤数据/)).toBeInTheDocument()
    expect(screen.queryByText('等待 Agent Run')).not.toBeInTheDocument()
  })

  it('clears an unavailable trace when starting a new session', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().reject(new AgentNetworkError())

    await waitFor(() => expect(screen.getByText('未收到 Trace')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '新建会话' }))

    expect(screen.getByText('等待 Agent Run')).toBeInTheDocument()
    expect(screen.queryByText('未收到 Trace')).not.toBeInTheDocument()
  })
})
