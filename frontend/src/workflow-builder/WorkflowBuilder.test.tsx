import '@testing-library/jest-dom/vitest'

import type { ReactNode } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ConfigClient } from '../platform/config-client.ts'
import { createEmptyDefinition } from './canvas.ts'
import type { WorkflowBuilderClient } from './client.ts'
import type { WorkflowBuilderRun, WorkflowBuilderWorkflow } from './types.ts'
import { WorkflowBuilder } from './WorkflowBuilder.tsx'

type CreateWorkflowInput = Parameters<WorkflowBuilderClient['createWorkflow']>[0]

vi.mock('@xyflow/react', () => {
  const ReactFlow = vi.fn(() => <div data-testid="react-flow" />)
  const Noop = () => null

  return {
    ReactFlow,
    ReactFlowProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
    Background: Noop,
    Controls: Noop,
    MiniMap: Noop,
    Handle: Noop,
    Position: { Left: 'left', Right: 'right' },
    addEdge: (
      connection: { source?: string | null; target?: string | null },
      edges: Array<{ id: string }>,
    ) => [
      ...edges,
      {
        id: `${connection.source ?? ''}->${connection.target ?? ''}`,
        ...connection,
      },
    ],
    applyNodeChanges: (_changes: unknown, nodes: unknown[]) => nodes,
    applyEdgeChanges: (_changes: unknown, edges: unknown[]) => edges,
    useReactFlow: () => ({
      screenToFlowPosition: (position: { x: number; y: number }) => position,
    }),
  }
})

const draftWorkflow: WorkflowBuilderWorkflow = {
  id: 'wf-1',
  workspace_id: 'ws-1',
  name: '测试流程',
  description: '说明',
  status: 'draft',
  version: 1,
  definition: createEmptyDefinition(),
  created_by: 'user-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:01Z',
}

const completedRun: WorkflowBuilderRun = {
  id: 'run-1',
  workflow_id: 'wf-1',
  workspace_id: 'ws-1',
  status: 'completed',
  inputs: { text: '你好，请分析一下' },
  definition: draftWorkflow.definition,
  node_results: [
    {
      node_id: 'input-1',
      type: 'input',
      status: 'completed',
      started_at: '2026-01-01T00:00:00Z',
      duration_ms: 1,
      input_summary: null,
      output_summary: '输入摘要',
      error: null,
    },
    {
      node_id: 'output-1',
      type: 'output',
      status: 'completed',
      started_at: '2026-01-01T00:00:00Z',
      duration_ms: 3,
      input_summary: null,
      output_summary: '分析结果',
      error: null,
    },
  ],
  error: null,
  total_duration_ms: 6,
  created_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:00:01Z',
}

const createConfigClient = (overrides: Partial<ConfigClient> = {}): ConfigClient =>
  ({
    listTools: vi.fn(async () => []),
    listAgents: vi.fn(async () => []),
    ...overrides,
  }) as ConfigClient

const createBuilderClient = (
  overrides: Partial<WorkflowBuilderClient> = {},
): WorkflowBuilderClient => ({
  listWorkflows: vi.fn(async () => []),
  getWorkflow: vi.fn(),
  createWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
  publishWorkflow: vi.fn(),
  unpublishWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(async () => null),
  runWorkflow: vi.fn(),
  listRuns: vi.fn(async () => []),
  getRun: vi.fn(),
  ...overrides,
})

afterEach(() => {
  cleanup()
})

describe('WorkflowBuilder', () => {
  it('loads workflows and opens a draft on canvas', async () => {
    const listRuns = vi.fn(async () => [])
    const client = createBuilderClient({
      listWorkflows: vi.fn(async () => [draftWorkflow]),
      listRuns,
    })
    const user = userEvent.setup()
    render(<WorkflowBuilder apiKeyConfigured client={client} configClient={createConfigClient()} />)

    await user.click(await screen.findByRole('button', { name: /测试流程/ }))

    expect(screen.getByLabelText('流程名称')).toHaveValue('测试流程')
    expect(screen.getByLabelText('说明')).toHaveValue('说明')
    expect(screen.getByTestId('react-flow')).toBeInTheDocument()
    expect(listRuns).toHaveBeenCalledWith('wf-1')
  })

  it('creates and saves a new workflow draft', async () => {
    const created = { ...draftWorkflow, id: 'new-1', name: '新建流程' }
    const createWorkflow = vi.fn(async (_input: CreateWorkflowInput) => created)
    const client = createBuilderClient({
      listWorkflows: vi.fn(async () => []),
      createWorkflow,
    })
    const user = userEvent.setup()
    render(<WorkflowBuilder apiKeyConfigured client={client} configClient={createConfigClient()} />)

    await user.click(screen.getByRole('button', { name: '新建流程' }))
    await user.type(screen.getByLabelText('流程名称'), '新建流程')
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    await waitFor(() => expect(createWorkflow).toHaveBeenCalledTimes(1))
    const input = createWorkflow.mock.calls[0]![0]
    expect(input.name).toBe('新建流程')
    expect(input.definition.nodes).toHaveLength(2)
    expect(screen.getByText('流程已创建并保存。')).toBeInTheDocument()
  })

  it('publishes a validated draft and updates the status', async () => {
    const published = { ...draftWorkflow, status: 'published' as const, version: 2 }
    const updateWorkflow = vi.fn(async () => draftWorkflow)
    const publishWorkflow = vi.fn(async () => published)
    const client = createBuilderClient({
      listWorkflows: vi.fn(async () => [draftWorkflow]),
      updateWorkflow,
      publishWorkflow,
    })
    const user = userEvent.setup()
    render(<WorkflowBuilder apiKeyConfigured client={client} configClient={createConfigClient()} />)

    await user.click(await screen.findByRole('button', { name: /测试流程/ }))
    await user.click(screen.getByRole('button', { name: '发布' }))

    await waitFor(() => expect(publishWorkflow).toHaveBeenCalledWith('wf-1'))
    expect(updateWorkflow).toHaveBeenCalledWith(
      'wf-1',
      expect.objectContaining({ name: '测试流程' }),
    )
    expect(screen.getByText('流程已发布，当前版本 v2。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已发布' })).toBeInTheDocument()
  })

  it('shows local validation errors before saving', async () => {
    const invalidDefinition = createEmptyDefinition()
    invalidDefinition.nodes[1]!.config.output_template = ''
    const invalidWorkflow = {
      ...draftWorkflow,
      id: 'invalid-1',
      name: '无效流程',
      definition: invalidDefinition,
    }
    const updateWorkflow = vi.fn()
    const client = createBuilderClient({
      listWorkflows: vi.fn(async () => [invalidWorkflow]),
      updateWorkflow,
    })
    const user = userEvent.setup()
    render(<WorkflowBuilder apiKeyConfigured client={client} configClient={createConfigClient()} />)

    await user.click(await screen.findByRole('button', { name: /无效流程/ }))
    await user.click(screen.getByRole('button', { name: '保存草稿' }))

    expect(await screen.findByText(/缺少 output_template/)).toBeInTheDocument()
    expect(updateWorkflow).not.toHaveBeenCalled()
  })

  it('runs a workflow and shows node result timeline', async () => {
    const updateWorkflow = vi.fn(async () => draftWorkflow)
    const runWorkflow = vi.fn(async () => completedRun)
    const client = createBuilderClient({
      listWorkflows: vi.fn(async () => [draftWorkflow]),
      updateWorkflow,
      listRuns: vi.fn(async () => [completedRun]),
      runWorkflow,
    })
    const user = userEvent.setup()
    render(<WorkflowBuilder apiKeyConfigured client={client} configClient={createConfigClient()} />)

    await user.click(await screen.findByRole('button', { name: /测试流程/ }))
    await user.click(screen.getByRole('button', { name: '开始试运行' }))

    await waitFor(() =>
      expect(runWorkflow).toHaveBeenCalledWith('wf-1', {
        text: '你好，请分析一下',
      }),
    )
    expect(updateWorkflow).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('输出：分析结果')).toBeInTheDocument()
    expect(screen.getByText('输出：输入摘要')).toBeInTheDocument()
  })
})
