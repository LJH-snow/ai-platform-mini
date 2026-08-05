import { describe, expect, it, vi } from 'vitest'
import { AgentBackendError, AgentNetworkError, createAgentClient } from './client.ts'

const successPayload = {
  run_id: 'run-api-1',
  status: 'completed',
  answer: '4',
  stop_reason: 'direct_answer',
  steps: [],
  events: [],
  usage: { prompt_tokens: null, completion_tokens: null, total_tokens: null, estimated: false },
}

describe('createAgentClient', () => {
  it('posts a synchronous agent run and returns an adapted domain result', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(successPayload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const client = createAgentClient({
      apiBaseUrl: 'http://localhost:8000/',
      apiKey: 'runtime-key',
      fetchImpl,
    })

    const result = await client.runAgent(
      {
        message: '2+2',
        history: [{ role: 'user', content: '之前的问题' }],
      },
      new AbortController().signal,
    )

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/agent/runs',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'application/json',
          Authorization: 'Bearer runtime-key',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          message: '2+2',
          history: [{ role: 'user', content: '之前的问题' }],
        }),
      }),
    )
    expect(result.runId).toBe('run-api-1')
  })

  it('normalizes HTTP and network errors without exposing raw backend details', async () => {
    const backendFetch = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'PROVIDER_ERROR',
          message: 'Traceback /Users/admin/app.py api_key=secret provider raw body',
        }),
        { status: 502, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(
      createAgentClient({ fetchImpl: backendFetch }).runAgent(
        { message: '失败', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toEqual(
      expect.objectContaining<Partial<AgentBackendError>>({
        name: 'AgentBackendError',
        status: 502,
        code: 'PROVIDER_ERROR',
        message: 'Agent 服务暂时不可用，请稍后重试。',
      }),
    )

    const networkFetch = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(
      createAgentClient({ fetchImpl: networkFetch }).runAgent(
        { message: '网络', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(AgentNetworkError)
  })

  it('turns a startup stream_error without run metadata into a network error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('event: stream_error\ndata: {"error_code":"provider_unavailable"}\n\n', {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    const client = createAgentClient({ fetchImpl })

    await expect(
      client.streamAgent?.(
        { message: '启动', history: [] },
        { onEvent: vi.fn() },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(AgentNetworkError)
  })

  it('accepts omitted optional RAG fields without rejecting the whole run', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...successPayload,
          steps: [
            {
              index: 1,
              decision_kind: 'tool_call',
              tool_names: ['knowledge_search'],
              tool_succeeded: true,
              tool_calls: [
                {
                  call_id: 'search-1',
                  name: 'knowledge_search',
                  succeeded: true,
                  truncated: false,
                  error_code: null,
                  error_message: null,
                  rag: {
                    status: 'success_with_sources',
                    references: [{ document_id: 'doc-1' }],
                  },
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const result = await createAgentClient({ fetchImpl }).runAgent(
      { message: '查询', history: [] },
      new AbortController().signal,
    )

    expect(result.steps[0]?.toolCalls[0]?.rag?.references).toEqual([
      {
        documentId: 'doc-1',
        chunkId: null,
        chunkIndex: null,
        content: null,
        distance: null,
        truncated: false,
      },
    ])
  })

  it.each([
    {
      label: 'unknown RAG status',
      rag: { status: 'future_status', references: [] },
    },
    {
      label: 'unknown RAG error code',
      rag: { status: 'failed', error_code: 'future_error', references: [] },
    },
  ])('rejects $label instead of failing open', async ({ rag }) => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...successPayload,
          steps: [
            {
              index: 1,
              decision_kind: 'tool_call',
              tool_names: ['knowledge_search'],
              tool_succeeded: true,
              tool_calls: [
                {
                  call_id: 'search-1',
                  name: 'knowledge_search',
                  succeeded: true,
                  truncated: false,
                  error_code: null,
                  error_message: null,
                  rag,
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(
      createAgentClient({ fetchImpl }).runAgent(
        { message: '查询', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toEqual(
      expect.objectContaining({
        name: 'AgentResponseError',
        message: 'Agent 服务返回了无法识别的响应。',
      }),
    )
  })

  it('rejects unexpected RAG data on calculator calls', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...successPayload,
          steps: [
            {
              index: 1,
              decision_kind: 'tool_call',
              tool_names: ['calculator'],
              tool_succeeded: true,
              tool_calls: [
                {
                  call_id: 'calculator-1',
                  name: 'calculator',
                  succeeded: true,
                  truncated: false,
                  error_code: null,
                  error_message: null,
                  rag: { status: 'success_with_sources', references: [] },
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(
      createAgentClient({ fetchImpl }).runAgent(
        { message: '2+2', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(Error)
  })

  it('rejects malformed step payloads as a controlled response error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...successPayload,
          steps: [{ index: 1, decision_kind: 'tool_call', tool_names: 'calculator' }],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(
      createAgentClient({ fetchImpl }).runAgent(
        { message: '格式异常', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toEqual(
      expect.objectContaining({
        name: 'AgentResponseError',
        message: 'Agent 服务返回了无法识别的响应。',
      }),
    )
  })

  it('preserves AbortError so the UI can report client-side cancellation accurately', async () => {
    const abortError = new DOMException('Aborted', 'AbortError')
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(abortError)

    await expect(
      createAgentClient({ fetchImpl }).runAgent(
        { message: '停止', history: [] },
        new AbortController().signal,
      ),
    ).rejects.toBe(abortError)
  })
})
