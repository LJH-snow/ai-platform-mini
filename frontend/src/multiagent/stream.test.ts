import { describe, expect, it } from 'vitest'

import {
  MultiAgentStreamFormatError,
  parseMultiAgentStreamEvent,
  readMultiAgentSse,
} from './stream.ts'

const started = JSON.stringify({
  run_id: 'run-1',
  sequence: 0,
  occurred_at: '2026-09-06T00:00:00Z',
})

describe('parseMultiAgentStreamEvent', () => {
  it('parses a minimal run_started event', () => {
    const event = parseMultiAgentStreamEvent('run_started', started)
    expect(event?.event).toBe('run_started')
    expect(event?.run_id).toBe('run-1')
    expect(event?.sequence).toBe(0)
  })

  it('parses subtasks_planned with subtask projections', () => {
    const event = parseMultiAgentStreamEvent(
      'subtasks_planned',
      JSON.stringify({
        run_id: 'run-1',
        sequence: 1,
        reasoning: 'plan',
        subtasks: [{ id: 't1', agent_role: 'writer', description: 'do', depends_on: [] }],
      }),
    )
    expect(event?.subtasks).toHaveLength(1)
    expect(event?.subtasks?.[0].id).toBe('t1')
    expect(event?.reasoning).toBe('plan')
  })

  it('parses a terminal event with results', () => {
    const event = parseMultiAgentStreamEvent(
      'run_completed',
      JSON.stringify({
        run_id: 'run-1',
        sequence: 5,
        final_output: 'done',
        total_token_usage: 42,
        duration_ms: 7,
        subtask_results: [{ task_id: 't1', status: 'completed', agent_role: 'writer' }],
      }),
    )
    expect(event?.final_output).toBe('done')
    expect(event?.total_token_usage).toBe(42)
    expect(event?.subtask_results).toHaveLength(1)
  })

  it('parses a subtask_answer_delta event with inner agent fields', () => {
    const event = parseMultiAgentStreamEvent(
      'subtask_answer_delta',
      JSON.stringify({
        run_id: 'run-1',
        sequence: 2,
        task_id: 't1',
        agent_role: 'writer',
        output_summary: 'first chunk',
        agent_event_kind: 'answer_delta',
        step_index: 3,
      }),
    )
    expect(event?.event).toBe('subtask_answer_delta')
    expect(event?.task_id).toBe('t1')
    expect(event?.output_summary).toBe('first chunk')
    expect(event?.agent_event_kind).toBe('answer_delta')
    expect(event?.step_index).toBe(3)
  })

  it('returns null for unknown event names', () => {
    expect(parseMultiAgentStreamEvent('answer_delta', started)).toBeNull()
  })

  it('throws when run_id is missing', () => {
    expect(() =>
      parseMultiAgentStreamEvent('run_started', JSON.stringify({ sequence: 0 })),
    ).toThrow(MultiAgentStreamFormatError)
  })

  it('throws on malformed JSON', () => {
    expect(() => parseMultiAgentStreamEvent('run_started', 'nope{')).toThrow(
      MultiAgentStreamFormatError,
    )
  })

  it('parses stream_error without run_id or sequence', () => {
    const event = parseMultiAgentStreamEvent(
      'stream_error',
      JSON.stringify({ error_code: 'stream_setup_failed', request_id: 'req-1' }),
    )
    expect(event?.event).toBe('stream_error')
    expect(event?.run_id).toBe('')
    expect(event?.sequence).toBe(-1)
  })

  it('throws when stream_error lacks an error code', () => {
    expect(() => parseMultiAgentStreamEvent('stream_error', JSON.stringify({}))).toThrow(
      MultiAgentStreamFormatError,
    )
  })
})

describe('readMultiAgentSse', () => {
  it('yields framed events in order', async () => {
    const payload =
      `event: run_started\ndata: ${started}\n\n` +
      `event: run_completed\ndata: ${JSON.stringify({ run_id: 'run-1', sequence: 1, final_output: 'done' })}\n\n`
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(payload))
        controller.close()
      },
    })
    const values = []
    for await (const value of readMultiAgentSse(new Response(body))) values.push(value)
    expect(values.map((item) => item.event)).toEqual(['run_started', 'run_completed'])
    expect(values[1].sequence).toBe(1)
  })
})
