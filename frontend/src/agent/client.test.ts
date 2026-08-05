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
