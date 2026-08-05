import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import {
  AgentBackendError,
  AgentNetworkError,
  AgentResponseError,
  type AgentClient,
} from './agent/client.ts'
import type { AgentRun, AgentRunInput, AgentRunStatus, AgentToolStatus } from './agent/types.ts'
import type { ChatClient } from './chat/client.ts'
import type { ChatStreamResult } from './chat/types.ts'

const idleChatClient: ChatClient = {
  streamChat: vi.fn(() => new Promise<ChatStreamResult>(() => undefined)),
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

const createRun = ({
  status = 'completed',
  answer = '计算结果是 4。',
  toolName = 'calculator',
  toolStatus = 'succeeded',
  empty = false,
}: {
  status?: AgentRunStatus
  answer?: string | null
  toolName?: string
  toolStatus?: AgentToolStatus
  empty?: boolean
} = {}): AgentRun => ({
  runId: 'run-real-123',
  status,
  answer,
  stopReason:
    status === 'timed_out'
      ? 'deadline_exceeded'
      : status === 'cancelled'
        ? 'external_cancelled'
        : status === 'failed'
          ? 'model_error'
          : 'direct_answer',
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
          summary: `模型决定调用：${toolName}。`,
          toolCalls: [
            {
              id: `step-1-tool-0-${toolName}`,
              name: toolName,
              known: toolName === 'calculator',
              status: toolStatus,
              stepIndex: 1,
              startedAt: null,
              completedAt: null,
              durationMs: null,
              inputSummary: null,
              outputSummary: null,
              errorCode: null,
              errorMessage:
                toolStatus === 'succeeded' ? null : '工具调用未成功。后端未提供错误详情。',
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
        {
          id: 'step-2-final_answer',
          index: 2,
          decisionKind: 'final_answer',
          status: status === 'completed' ? 'completed' : status,
          startedAt: null,
          completedAt: null,
          durationMs: null,
          toolNames: [],
          summary: '模型生成最终回答。',
          toolCalls: [],
          events: [],
        },
      ],
  events: [],
  usage: {
    promptTokens: null,
    completionTokens: null,
    totalTokens: null,
    estimated: false,
  },
})

const startAgentRun = async (controlled: ReturnType<typeof createControlledAgentClient>) => {
  const user = userEvent.setup()
  render(<App chatClient={idleChatClient} agentClient={controlled.client} />)
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

describe('Agent Trace integration', () => {
  it('shows synchronous loading first, then an explicit empty trace with matching answer status', async () => {
    const controlled = createControlledAgentClient()
    await startAgentRun(controlled)

    const conversation = screen.getByRole('article', { name: '会话' })
    const trace = screen.getByRole('complementary', { name: 'Agent Trace' })
    expect(within(conversation).getByRole('status')).toHaveTextContent('Agent 运行中')
    expect(within(trace).getByText('等待同步结果')).toBeInTheDocument()
    expect(within(trace).getByText('完成后加载 Trace，非实时')).toBeInTheDocument()

    controlled.getRequest().resolve(createRun({ empty: true }))

    await waitFor(() =>
      expect(within(conversation).getByRole('status')).toHaveTextContent('已完成'),
    )
    expect(screen.getByText('计算结果是 4。')).toBeInTheDocument()
    expect(within(trace).getByText('后端返回空 Trace')).toBeInTheDocument()
    expect(within(trace).getByText('Run ID：run-real-123')).toBeInTheDocument()
    expect(within(trace).getByText('总 Token：后端未提供')).toBeInTheDocument()
  })

  it('renders and toggles a real calculator step and tool summary without invented timing or payloads', async () => {
    const controlled = createControlledAgentClient()
    const user = await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun())

    const stepButton = await screen.findByRole('button', { name: /步骤 1.*工具调用/ })
    expect(stepButton).toHaveAttribute('aria-expanded', 'false')
    await user.click(stepButton)
    expect(stepButton).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('开始时间：后端未提供')).toBeInTheDocument()
    expect(screen.getByText('耗时：后端未提供')).toBeInTheDocument()

    const toolButton = screen.getByRole('button', { name: /calculator.*成功/ })
    await user.click(toolButton)
    expect(toolButton).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('输入摘要：后端未提供')).toBeInTheDocument()
    expect(screen.getByText('输出摘要：后端未提供')).toBeInTheDocument()
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
      expect(screen.getByText(toolLabel)).toBeInTheDocument()
      if (status === 'failed') {
        expect(screen.getByRole('button', { name: '重新运行' })).toBeInTheDocument()
        await user.click(screen.getByRole('button', { name: '重新运行' }))
        await waitFor(() => expect(controlled.getRequestCount()).toBe(2))
      }
    },
  )

  it('labels an unknown tool without treating it as calculator', async () => {
    const controlled = createControlledAgentClient()
    await startAgentRun(controlled)
    controlled.getRequest().resolve(createRun({ toolName: 'future_tool' }))

    expect(await screen.findByText('未知工具')).toBeInTheDocument()
    expect(screen.getByText('future_tool')).toBeInTheDocument()
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
