import { afterEach, describe, expect, it, vi } from 'vitest'

import { createWorkflowClient, WorkflowApiError, WorkflowNetworkError } from './client.ts'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createWorkflowClient', () => {
  it('uploads a PDF with topic and returns parsed status', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: 'abc-123',
          status: 'pending_approval',
          stage: 'awaiting_approval',
          filename: 'report.pdf',
          report_topic: 'Finance',
          page_count: 12,
          references: 3,
          draft_summary: 'Summary here',
          model: 'qwen3:4b',
          prompt_tokens: 100,
          completion_tokens: 200,
          revision_count: 0,
          error_code: null,
          error_message: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:01Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const client = createWorkflowClient({ fetchImpl, apiKey: 'sk-test' })
    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' })
    const result = await client.uploadPdf(file, 'Finance')

    expect(result.threadId).toBe('abc-123')
    expect(result.status).toBe('pending_approval')
    expect(result.stage).toBe('awaiting_approval')
    expect(result.filename).toBe('report.pdf')
    expect(result.reportTopic).toBe('Finance')
    expect(result.pageCount).toBe(12)
    expect(result.references).toBe(3)
    expect(result.draftSummary).toBe('Summary here')
    expect(result.report).toBeNull()
    expect(result.model).toBe('qwen3:4b')
    expect(result.promptTokens).toBe(100)
    expect(result.completionTokens).toBe(200)
    expect(result.revisionCount).toBe(0)
    expect(result.createdAt).toBe('2026-01-01T00:00:00Z')

    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/api/v1/workflows/pdf-report')
    expect(call[1]?.method).toBe('POST')
    expect((call[1]?.headers as Record<string, string>)?.Authorization).toBe('Bearer sk-test')
    expect(call[1]?.body instanceof FormData).toBe(true)
  })

  it('queries status by thread id', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: 'abc-123',
          status: 'completed',
          stage: 'completed',
          report: 'Final report',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const client = createWorkflowClient({ fetchImpl, apiKey: 'sk-test' })
    const result = await client.getStatus('abc-123')

    expect(result.status).toBe('completed')
    expect(result.report).toBe('Final report')
    expect(fetchImpl.mock.calls[0][0]).toContain('/api/v1/workflows/abc-123')
  })

  it('approves a pending workflow', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: 'abc-123',
          status: 'completed',
          stage: 'completed',
          report: 'Approved report',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const client = createWorkflowClient({ fetchImpl })
    const result = await client.approve('abc-123')

    expect(result.status).toBe('completed')
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/api/v1/workflows/abc-123/approve')
    expect(call[1]?.method).toBe('POST')
  })

  it('rejects with feedback', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          thread_id: 'abc-123',
          status: 'rejected',
          stage: 'rejected',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const client = createWorkflowClient({ fetchImpl })
    const result = await client.reject('abc-123', 'Need more data')

    expect(result.status).toBe('rejected')
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[0]).toContain('/api/v1/workflows/abc-123/reject')
    expect(call[1]?.method).toBe('POST')
    expect(call[1]?.body).toBe(JSON.stringify({ feedback: 'Need more data' }))
  })

  it('throws WorkflowApiError on 401', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ code: 'UNAUTHORIZED' }), { status: 401 }))

    const client = createWorkflowClient({ fetchImpl })
    await expect(client.getStatus('abc')).rejects.toBeInstanceOf(WorkflowApiError)
    await expect(client.getStatus('abc')).rejects.toThrow('鉴权')
  })

  it('throws WorkflowApiError on 404', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ code: 'WORKFLOW_NOT_FOUND' }), { status: 404 }),
      )

    const client = createWorkflowClient({ fetchImpl })
    await expect(client.getStatus('abc')).rejects.toBeInstanceOf(WorkflowApiError)
    await expect(client.getStatus('abc')).rejects.toThrow('不存在')
  })

  it('throws WorkflowApiError on 409', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ code: 'CONFLICT' }), { status: 409 }))

    const client = createWorkflowClient({ fetchImpl })
    await expect(client.approve('abc')).rejects.toBeInstanceOf(WorkflowApiError)
    await expect(client.approve('abc')).rejects.toThrow('冲突')
  })

  it('throws WorkflowNetworkError on fetch failure', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new Error('offline'))

    const client = createWorkflowClient({ fetchImpl })
    await expect(client.getStatus('abc')).rejects.toBeInstanceOf(WorkflowNetworkError)
  })

  it('throws WorkflowApiError on non-JSON success response', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('not json', { status: 200 }))

    const client = createWorkflowClient({ fetchImpl })
    await expect(client.getStatus('abc')).rejects.toBeInstanceOf(WorkflowApiError)
  })
})

describe('listRuns', () => {
  it('returns the raw array without single-status parsing', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            thread_id: 't-2',
            status: 'completed',
            stage: 'completed',
            filename: 'two.pdf',
            report_topic: null,
            created_at: '2026-01-02T00:00:00Z',
          },
          {
            thread_id: 't-1',
            status: 'pending_approval',
            stage: 'awaiting_approval',
            filename: 'one.pdf',
            report_topic: 'Topic',
            created_at: '2026-01-01T00:00:00Z',
          },
        ]),
        { status: 200 },
      ),
    )

    const client = createWorkflowClient({
      apiBaseUrl: 'http://test',
      apiKey: 'k',
      fetchImpl,
    })
    const runs = await client.listRuns(20)

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://test/api/v1/workflows?limit=20',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer k' }) }),
    )
    expect(runs).toHaveLength(2)
    expect(runs[0]).toEqual({
      threadId: 't-2',
      status: 'completed',
      stage: 'completed',
      filename: 'two.pdf',
      reportTopic: null,
      createdAt: '2026-01-02T00:00:00Z',
    })
  })

  it('returns an empty list when the payload is not an array', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 }),
    )
    const client = createWorkflowClient({ apiBaseUrl: 'http://test', apiKey: 'k', fetchImpl })

    await expect(client.listRuns(20)).rejects.toThrow(WorkflowApiError)
  })
})
