import { describe, expect, it } from 'vitest'
import { AgentStreamFormatError, parseAgentStreamEvent, readAgentSse } from './stream.ts'

const event = (sequence: number, extra: Record<string, unknown> = {}): string =>
  JSON.stringify({ run_id: 'run-1', sequence, ...extra })

describe('Agent SSE parser', () => {
  it('parses multiline data and ignores unknown events', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('event: unknown\ndata: {}\n\nevent: run_started\ndata: '),
        )
        controller.enqueue(new TextEncoder().encode(event(0, { request_id: 'req-1' }).slice(0, -1)))
        controller.enqueue(new TextEncoder().encode('\ndata: }\n\n'))
        controller.close()
      },
    })
    const values = []
    for await (const value of readAgentSse(new Response(body))) values.push(value)
    expect(values).toHaveLength(1)
    expect(values[0]?.event).toBe('run_started')
    expect(values[0]?.request_id).toBe('req-1')
  })

  it('rejects malformed JSON and missing stable fields', () => {
    expect(() => parseAgentStreamEvent('run_started', '{bad')).toThrow(AgentStreamFormatError)
    expect(() => parseAgentStreamEvent('run_started', JSON.stringify({ sequence: 1 }))).toThrow(
      AgentStreamFormatError,
    )
  })

  it('ignores unknown event names without inventing a domain event', () => {
    expect(parseAgentStreamEvent('future_event', event(1))).toBeNull()
  })

  it('validates event field types, RAG references, and stream errors', () => {
    expect(() => parseAgentStreamEvent('tool_completed', event(1, { succeeded: 'yes' }))).toThrow(
      AgentStreamFormatError,
    )
    expect(() =>
      parseAgentStreamEvent('tool_completed', event(1, { rag: { status: 'loading' } })),
    ).toThrow(AgentStreamFormatError)
    expect(parseAgentStreamEvent('stream_error', JSON.stringify({ error_code: 'failed' }))).toEqual(
      {
        event: 'stream_error',
        run_id: '',
        sequence: -1,
        error_code: 'failed',
      },
    )
    expect(
      parseAgentStreamEvent(
        'stream_error',
        JSON.stringify({ run_id: '', error_code: 'stream_failed' }),
      ),
    ).toMatchObject({ event: 'stream_error', run_id: '', sequence: -1 })
    expect(
      parseAgentStreamEvent(
        'stream_error',
        JSON.stringify({ run_id: 'run-1', sequence: 2, error_code: 'stream_failed' }),
      ),
    ).toMatchObject({ event: 'stream_error', sequence: 2, error_code: 'stream_failed' })
    expect(() =>
      parseAgentStreamEvent('run_started', JSON.stringify({ error_code: 'failed' })),
    ).toThrow(AgentStreamFormatError)
  })

  it('parses step_planned metadata and accepts legacy events without it', () => {
    expect(
      parseAgentStreamEvent(
        'step_planned',
        event(1, {
          step_index: 1,
          decision_kind: 'tool_call',
          tool_names: ['calculator'],
          tool_count: 1,
          summary: '计算表达式。',
          argument_count: 1,
          input_summary: '12 + 8',
          output_summary: '20',
          result_chars: 2,
        }),
      ),
    ).toMatchObject({
      event: 'step_planned',
      decision_kind: 'tool_call',
      tool_names: ['calculator'],
      tool_count: 1,
      summary: '计算表达式。',
      argument_count: 1,
      input_summary: '12 + 8',
      output_summary: '20',
      result_chars: 2,
    })
    expect(parseAgentStreamEvent('step_started', event(2))).toMatchObject({
      event: 'step_started',
    })
  })

  it('rejects tool_names beyond the service payload limits', () => {
    expect(() =>
      parseAgentStreamEvent(
        'step_planned',
        event(1, { tool_names: Array.from({ length: 33 }, (_, index) => `tool-${index}`) }),
      ),
    ).toThrow(AgentStreamFormatError)
    expect(() =>
      parseAgentStreamEvent('step_planned', event(2, { tool_names: ['x'.repeat(129)] })),
    ).toThrow(AgentStreamFormatError)
  })

  it('parses answer deltas and rejects invalid delta values', () => {
    expect(parseAgentStreamEvent('answer_delta', event(1, { delta: '真实' }))).toMatchObject({
      event: 'answer_delta',
      delta: '真实',
    })
    expect(parseAgentStreamEvent('answer_delta', event(2, { delta: '' }))).toMatchObject({
      delta: '',
    })
    expect(parseAgentStreamEvent('answer_delta', event(3, { delta: null }))).toMatchObject({
      delta: null,
    })
    expect(() => parseAgentStreamEvent('answer_delta', event(4))).toThrow(AgentStreamFormatError)
    expect(() => parseAgentStreamEvent('answer_delta', event(5, { delta: 42 }))).toThrow(
      AgentStreamFormatError,
    )
  })
})
