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
})
