import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MultiAgentPanel } from './MultiAgentPanel.tsx'
import type { MultiAgentClient } from './client.ts'
import type { MultiAgentStreamEvent } from './stream.ts'

afterEach(() => {
  cleanup()
})

const liveEvents = (runId: string): MultiAgentStreamEvent[] => [
  { event: 'run_started', run_id: runId, sequence: 0 },
  {
    event: 'subtasks_planned',
    run_id: runId,
    sequence: 1,
    reasoning: 'split',
    subtasks: [{ id: 't1', agent_role: 'writer', description: 'draft', depends_on: [] }],
  },
  {
    event: 'subtask_completed',
    run_id: runId,
    sequence: 2,
    task_id: 't1',
    agent_role: 'writer',
    output_summary: 'draft done',
    token_usage: 5,
    duration_ms: 2,
  },
  {
    event: 'run_completed',
    run_id: runId,
    sequence: 3,
    final_output: 'final report',
    total_token_usage: 5,
    duration_ms: 2,
  },
]

const createFakeClient = (events: MultiAgentStreamEvent[]): MultiAgentClient => ({
  runMultiAgent: vi.fn(),
  streamMultiAgent: vi.fn(
    async (
      _input,
      handlers: { onEvent: (event: MultiAgentStreamEvent) => void },
    ): Promise<void> => {
      for (const event of events) handlers.onEvent(event)
    },
  ),
  listRuns: vi.fn(async () => [
    {
      runId: 'run-h1',
      requestId: 'req-h1',
      apiKeyPrefix: 'cafef00d',
      apiKeyName: 'test',
      status: 'completed',
      stopReason: 'completed',
      startedAt: null,
      completedAt: null,
      durationMs: 1,
      totalTokens: 2,
      subtaskCount: 1,
    },
  ]),
  getRun: vi.fn(async () => ({
    runId: 'run-h1',
    requestId: 'req-h1',
    apiKeyPrefix: 'cafef00d',
    apiKeyName: 'test',
    status: 'completed',
    stopReason: 'completed',
    startedAt: null,
    completedAt: null,
    durationMs: 1,
    totalTokens: 2,
    subtaskCount: 1,
    response: {
      status: 'completed',
      finalOutput: 'stored report',
      errorCode: null,
      totalTokenUsage: 2,
      durationMs: 1,
      subtaskResults: [
        {
          taskId: 't1',
          status: 'completed',
          agentRole: 'writer',
          output: 'stored out',
          errorCode: null,
          tokenUsage: 2,
          durationMs: 1,
        },
      ],
    },
  })),
})

describe('MultiAgentPanel', () => {
  it('streams a run into a timeline with a summary', async () => {
    const user = userEvent.setup()
    render(<MultiAgentPanel client={createFakeClient(liveEvents('run-live-1'))} apiKeyConfigured />)

    await user.type(screen.getByLabelText('任务描述'), '写一份报告')
    await user.click(screen.getByRole('button', { name: '运行多 Agent' }))

    expect(await screen.findByText('final report')).toBeVisible()
    expect(screen.getByText('t1')).toBeVisible()
    expect(screen.getByText(/draft done/)).toBeVisible()
    expect(screen.getByText('汇总输出')).toBeVisible()
  })

  it('replays a history run from the history tab', async () => {
    const user = userEvent.setup()
    render(<MultiAgentPanel client={createFakeClient([])} apiKeyConfigured />)

    await user.click(screen.getByRole('button', { name: '历史' }))
    expect(await screen.findByText('run-h1'.slice(0, 8))).toBeVisible()
    await user.click(screen.getByRole('button', { name: '查看' }))

    expect(await screen.findByText('stored report')).toBeVisible()
    expect(screen.getByText('Run 回放')).toBeVisible()
  })

  it('disables running without an api key', () => {
    render(<MultiAgentPanel client={createFakeClient([])} apiKeyConfigured={false} />)
    expect(screen.getByText('未配置 API Key，无法运行多 Agent。')).toBeVisible()
  })
})
