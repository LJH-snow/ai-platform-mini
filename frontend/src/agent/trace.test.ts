import { describe, expect, it } from 'vitest'
import { compactAgentTraceEvents } from './trace.ts'
import type { AgentTraceEvent } from './types.ts'

const event = (id: string, kind: string): AgentTraceEvent => ({
  id,
  kind,
  stepIndex: 1,
  status: null,
  stopReason: null,
})

describe('compactAgentTraceEvents', () => {
  it('groups consecutive answer delta events without changing other event order', () => {
    const compacted = compactAgentTraceEvents([
      event('started', 'step_started'),
      event('delta-1', 'answer_delta'),
      event('delta-2', 'answer_delta'),
      event('delta-3', 'answer_delta'),
      event('completed', 'step_completed'),
      event('delta-4', 'answer_delta'),
    ])

    expect(compacted).toEqual([
      { event: event('started', 'step_started'), count: 1 },
      { event: event('delta-1', 'answer_delta'), count: 3 },
      { event: event('completed', 'step_completed'), count: 1 },
      { event: event('delta-4', 'answer_delta'), count: 1 },
    ])
  })
})
