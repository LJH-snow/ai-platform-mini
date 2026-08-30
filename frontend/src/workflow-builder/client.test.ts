import { afterEach, describe, expect, it, vi } from 'vitest'

import { createWorkflowBuilderClient } from './client.ts'

const workflowPayload = {
  id: 'wf-1',
  workspace_id: 'ws-1',
  name: '测试流程',
  description: '说明',
  status: 'draft',
  version: 1,
  definition: {
    nodes: [
      {
        id: 'input-1',
        type: 'input' as const,
        config: { canvas_position: { x: 0, y: 0 } },
      },
      {
        id: 'output-1',
        type: 'output' as const,
        config: { output_template: '{{input.text}}' },
      },
    ],
    edges: [{ from: 'input-1', to: 'output-1' }],
    version: 1,
  },
  created_by: 'user-1',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:01Z',
}

const runPayload = {
  id: 'run-1',
  workflow_id: 'wf-1',
  workspace_id: 'ws-1',
  status: 'completed',
  inputs: { text: '你好' },
  definition: workflowPayload.definition,
  node_results: [
    {
      node_id: 'output-1',
      type: 'output',
      status: 'completed',
      started_at: '2026-01-01T00:00:00Z',
      duration_ms: 12,
      input_summary: '你好',
      output_summary: '分析结果',
      error: null,
    },
  ],
  error: null,
  total_duration_ms: 20,
  created_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:00:01Z',
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createWorkflowBuilderClient', () => {
  it('lists and normalizes workflows', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify([workflowPayload]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = createWorkflowBuilderClient({ fetchImpl, apiKey: 'sk-test' })
    const workflows = await client.listWorkflows()

    expect(workflows[0]).toMatchObject({
      id: 'wf-1',
      name: '测试流程',
      status: 'draft',
      version: 1,
    })
    expect(workflows[0]?.definition.nodes[1]?.config.output_template).toBe('{{input.text}}')
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/api/v1/workflow-builder/workflows')
    expect((call[1]!.headers as Record<string, string>).Authorization).toBe('Bearer sk-test')
  })

  it('creates a workflow with the full definition payload', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(workflowPayload), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = createWorkflowBuilderClient({ fetchImpl })
    const created = await client.createWorkflow({
      name: '测试流程',
      description: '说明',
      definition: workflowPayload.definition,
    })

    expect(created.id).toBe('wf-1')
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/api/v1/workflow-builder/workflows')
    expect(call[1]?.method).toBe('POST')
    const body = JSON.parse(String(call[1]?.body)) as {
      name?: unknown
      definition?: unknown
    }
    expect(body.name).toBe('测试流程')
    expect(body.definition).toEqual(workflowPayload.definition)
  })

  it('calls publish, unpublish, and delete routes', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...workflowPayload, status: 'published', version: 2 }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...workflowPayload, status: 'draft' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const client = createWorkflowBuilderClient({ fetchImpl })

    const published = await client.publishWorkflow('wf-1')
    const unpublished = await client.unpublishWorkflow('wf-1')
    await client.deleteWorkflow('wf-1')

    expect(published.status).toBe('published')
    expect(published.version).toBe(2)
    expect(unpublished.status).toBe('draft')
    expect(fetchImpl.mock.calls[0]![0]).toContain('/workflows/wf-1/publish')
    expect(fetchImpl.mock.calls[1]![0]).toContain('/workflows/wf-1/unpublish')
    expect(fetchImpl.mock.calls[2]![0]).toContain('/workflows/wf-1')
    expect(fetchImpl.mock.calls[2]![1]!.method).toBe('DELETE')
  })

  it('runs a workflow and normalizes node results', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(runPayload), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = createWorkflowBuilderClient({ fetchImpl })
    const run = await client.runWorkflow('wf-1', { text: '你好' })

    expect(run.status).toBe('completed')
    expect(run.node_results[0]).toMatchObject({
      node_id: 'output-1',
      type: 'output',
      status: 'completed',
      duration_ms: 12,
      output_summary: '分析结果',
    })
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/workflows/wf-1/runs')
    expect(JSON.parse(String(call[1]?.body))).toEqual({ inputs: { text: '你好' } })
  })

  it('surfaces backend validation messages from 422 responses', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ message: '条件表达式不合法' }), { status: 422 }),
      )

    const client = createWorkflowBuilderClient({ fetchImpl })

    await expect(
      client.createWorkflow({
        name: '流程',
        description: '',
        definition: workflowPayload.definition,
      }),
    ).rejects.toThrow('条件表达式不合法')
  })
})
