import { describe, expect, it, vi } from 'vitest'

import {
  MultiAgentBackendError,
  MultiAgentNetworkError,
  MultiAgentResponseError,
  createMultiAgentClient,
} from './client.ts'

const runPayload = {
  run_id: 'run-1',
  status: 'completed',
  final_output: 'report',
  subtask_results: [],
  total_token_usage: 7,
  error: null,
  error_code: null,
  duration_ms: 3,
}

const jsonResponse = (payload: unknown, status = 200): Response =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

const sseResponse = (frames: string): Response => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(frames))
      controller.close()
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

describe('createMultiAgentClient', () => {
  it('posts a synchronous run and adapts the response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(runPayload))
    const client = createMultiAgentClient({ apiBaseUrl: 'http://x/', apiKey: 'k', fetchImpl })
    const run = await client.runMultiAgent({ message: 'hello' }, new AbortController().signal)
    expect(run.runId).toBe('run-1')
    expect(run.finalOutput).toBe('report')
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('http://x/api/v1/multi-agent/runs')
    expect(init).toBeDefined()
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer k' })
  })

  it('rejects invalid run parameters before touching the network', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
    const client = createMultiAgentClient({ fetchImpl })
    await expect(
      client.runMultiAgent({ message: 'hi', maxSubtasks: 99 }, new AbortController().signal),
    ).rejects.toThrow(RangeError)
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it('maps HTTP errors to backend errors', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: 'nope' }, 404))
    const client = createMultiAgentClient({ fetchImpl })
    const failure = await client
      .runMultiAgent({ message: 'hi' }, new AbortController().signal)
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(MultiAgentBackendError)
    expect((failure as MultiAgentBackendError).status).toBe(404)
  })

  it('rejects malformed payloads', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ run_id: 42 }))
    const client = createMultiAgentClient({ fetchImpl })
    await expect(
      client.runMultiAgent({ message: 'hi' }, new AbortController().signal),
    ).rejects.toThrow(MultiAgentResponseError)
  })

  it('streams events to the handler in order', async () => {
    const frames =
      `event: run_started\ndata: ${JSON.stringify({ run_id: 'run-s', sequence: 0 })}\n\n` +
      `event: run_completed\ndata: ${JSON.stringify({ run_id: 'run-s', sequence: 1, final_output: 'done' })}\n\n`
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(sseResponse(frames))
    const client = createMultiAgentClient({ fetchImpl })
    const seen: string[] = []
    await client.streamMultiAgent(
      { message: 'hi' },
      { onEvent: (event) => seen.push(event.event) },
      new AbortController().signal,
    )
    expect(seen).toEqual(['run_started', 'run_completed'])
  })

  it('turns stream_error into a network error carrying the code', async () => {
    const frames = `event: stream_error\ndata: ${JSON.stringify({ error_code: 'stream_setup_failed' })}\n\n`
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(sseResponse(frames))
    const client = createMultiAgentClient({ fetchImpl })
    const failure = await client
      .streamMultiAgent(
        { message: 'hi' },
        { onEvent: () => undefined },
        new AbortController().signal,
      )
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(MultiAgentNetworkError)
    expect((failure as MultiAgentNetworkError).code).toBe('stream_setup_failed')
  })

  it('lists history summaries and fetches one detail', async () => {
    const summary = {
      run_id: 'run-h',
      request_id: 'req-h',
      api_key_prefix: 'cafef00d',
      api_key_name: 'test',
      status: 'completed',
      stop_reason: 'completed',
      started_at: null,
      completed_at: null,
      duration_ms: 1,
      total_tokens: 2,
      subtask_count: 0,
    }
    // The detail endpoint omits subtask_count; it is derived from results.
    const detailPayload: Record<string, unknown> = {
      ...summary,
      response: {
        status: 'completed',
        final_output: 'r',
        subtask_results: [{ task_id: 't1', status: 'completed', output: 'x' }],
      },
    }
    delete detailPayload.subtask_count
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse([summary]))
      .mockResolvedValueOnce(jsonResponse(detailPayload))
    const client = createMultiAgentClient({ fetchImpl })
    const items = await client.listRuns(10)
    expect(items).toHaveLength(1)
    expect(items[0].runId).toBe('run-h')
    const detail = await client.getRun('run-h')
    expect(detail.response.finalOutput).toBe('r')
    expect(detail.subtaskCount).toBe(1)
    const [listUrl] = fetchImpl.mock.calls[0]
    expect(listUrl).toBe('/api/v1/multi-agent/runs?limit=10')
  })

  it('fetches and parses a replay event timeline', async () => {
    const eventPayload = [
      { event: 'subtask_step_started', run_id: 'run-h', sequence: 0, task_id: 't1', step_index: 0 },
      {
        event: 'subtask_tool_completed',
        run_id: 'run-h',
        sequence: 1,
        task_id: 't1',
        tool_name: 'knowledge_search',
        call_id: 'call-1',
        output_summary: '3 sources',
      },
      {
        event: 'run_completed',
        run_id: 'run-h',
        sequence: 2,
        final_output: 'report',
        total_token_usage: 5,
        duration_ms: 2,
      },
    ]
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(eventPayload))
    const client = createMultiAgentClient({ apiBaseUrl: 'http://x', apiKey: 'k', fetchImpl })

    const events = await client.getRunEvents('run-h')
    expect(events).toHaveLength(3)
    expect(events[0]).toMatchObject({ event: 'subtask_step_started', step_index: 0 })
    expect(events[1]).toMatchObject({ tool_name: 'knowledge_search', call_id: 'call-1' })
    expect(events[2]).toMatchObject({ event: 'run_completed', final_output: 'report' })
    expect(fetchImpl.mock.calls[0][0]).toBe('http://x/api/v1/multi-agent/runs/run-h/events')
  })

  it('rejects malformed replay event timelines', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse([{ event: 'unknown', run_id: 'run-h', sequence: 0 }]))
    const client = createMultiAgentClient({ apiBaseUrl: 'http://x', apiKey: 'k', fetchImpl })
    await expect(client.getRunEvents('run-h')).rejects.toThrow(MultiAgentResponseError)
  })

  it('runs and lists multi-agent benchmark comparison runs', async () => {
    const benchmarkPayload = {
      id: 7,
      agent_id: 'agent-1',
      workspace_id: 'ws-1',
      task_set: 'multi_agent_compare',
      tool_call_accuracy: 0.5,
      task_completion_rate: 1,
      task_count: 3,
      completed_count: 3,
      created_at: null,
      metric_payload: { judgement: {} },
    }
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(benchmarkPayload))
      .mockResolvedValueOnce(jsonResponse([benchmarkPayload]))
    const client = createMultiAgentClient({ fetchImpl })

    const run = await client.runBenchmark('agent-1')
    expect(run.id).toBe(7)
    expect(run.toolCallAccuracy).toBe(0.5)

    const runs = await client.listBenchmarkRuns('agent-1')
    expect(runs).toHaveLength(1)
    expect(runs[0].agentId).toBe('agent-1')
    const [runUrl, runInit] = fetchImpl.mock.calls[0]
    expect(runUrl).toBe('/api/v1/multi-agent/benchmark')
    expect(runInit?.method).toBe('POST')
    const listUrl = fetchImpl.mock.calls[1][0]
    expect(listUrl).toBe('/api/v1/multi-agent/benchmark/runs?agent_id=agent-1')
  })
})
