import type { AgentTraceEvent } from './types.ts'

export type CompactAgentTraceEvent = {
  event: AgentTraceEvent
  count: number
}

export const compactAgentTraceEvents = (events: AgentTraceEvent[]): CompactAgentTraceEvent[] => {
  const compacted: CompactAgentTraceEvent[] = []

  for (const event of events) {
    const previous = compacted.at(-1)
    if (event.kind === 'answer_delta' && previous?.event.kind === 'answer_delta') {
      previous.count += 1
      continue
    }
    compacted.push({ event, count: 1 })
  }

  return compacted
}
