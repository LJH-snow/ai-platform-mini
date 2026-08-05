import { describe, expect, it } from 'vitest'
import { initialAgentStreamState, reduceAgentStream } from './reducer.ts'
import type { AgentStreamEvent } from './stream.ts'

const apply = (events: AgentStreamEvent[]) =>
  events.reduce(reduceAgentStream, initialAgentStreamState)

const e = (event: AgentStreamEvent['event'], sequence: number, extra = {}): AgentStreamEvent => ({
  event,
  run_id: 'run-1',
  request_id: 'req-1',
  sequence,
  ...extra,
})

describe('Agent stream reducer', () => {
  it('builds a multi-step calculator and RAG lifecycle', () => {
    const state = apply([
      e('run_started', 0),
      e('step_started', 1, { step_index: 1 }),
      e('tool_started', 2, { step_index: 1, call_id: 'calc-1', tool_name: 'calculator' }),
      e('tool_completed', 3, {
        step_index: 1,
        call_id: 'calc-1',
        tool_name: 'calculator',
        succeeded: true,
      }),
      e('step_completed', 4, { step_index: 1, status: 'completed' }),
      e('step_started', 5, { step_index: 2 }),
      e('rag_started', 6, {
        step_index: 2,
        call_id: 'rag-1',
        tool_name: 'knowledge_search',
        rag: { status: 'loading', references: [] },
      }),
      e('tool_completed', 7, {
        step_index: 2,
        call_id: 'rag-1',
        tool_name: 'knowledge_search',
        succeeded: true,
        rag: {
          status: 'success_with_sources',
          references: [
            {
              document_id: 'doc-1',
              chunk_id: 'chunk-1',
              content: '真实片段',
              chunk_index: 0,
              distance: 0.2,
            },
          ],
        },
      }),
      e('assistant_message', 8, { answer: '真实回答' }),
      e('step_completed', 9, { step_index: 2, status: 'completed' }),
      e('run_completed', 10, { status: 'completed', stop_reason: 'direct_answer' }),
    ])
    expect(state.terminal).toBe(true)
    expect(state.run?.answer).toBe('真实回答')
    expect(state.run?.steps).toHaveLength(2)
    expect(state.run?.steps[0]?.toolCalls[0]?.status).toBe('succeeded')
    expect(state.run?.steps[1]?.toolCalls[0]?.rag?.references).toHaveLength(1)
    expect(state.run?.status).toBe('completed')
  })

  it('rejects duplicates, older sequences and events from another run', () => {
    const started = e('run_started', 0)
    const first = reduceAgentStream(initialAgentStreamState, started)
    const duplicate = reduceAgentStream(first, started)
    const older = reduceAgentStream(first, e('assistant_message', 0, { answer: '伪造' }))
    const other = reduceAgentStream(first, {
      ...e('assistant_message', 2),
      run_id: 'run-2',
      answer: '错误',
    })
    expect(duplicate).toBe(first)
    expect(older).toBe(first)
    expect(other).toBe(first)
  })

  it('keeps exactly one terminal outcome', () => {
    const state = apply([
      e('run_started', 0),
      e('run_timed_out', 1, { status: 'timed_out' }),
      e('run_cancelled', 2, { status: 'cancelled' }),
    ])
    expect(state.run?.status).toBe('timed_out')
    expect(state.run?.events).toHaveLength(2)
  })

  it('accumulates answer deltas after tools and does not duplicate legacy answer', () => {
    const state = apply([
      e('run_started', 0),
      e('tool_started', 1, { step_index: 1, call_id: 'calc-1', tool_name: 'calculator' }),
      e('tool_completed', 2, {
        step_index: 1,
        call_id: 'calc-1',
        tool_name: 'calculator',
        succeeded: true,
      }),
      e('answer_delta', 3, { delta: '真实' }),
      e('answer_delta', 4, { delta: '' }),
      e('answer_delta', 5, { delta: '回答' }),
      e('assistant_message', 6, { answer: '真实回答' }),
      e('run_completed', 7, { status: 'completed' }),
    ])
    expect(state.run?.answer).toBe('真实回答')
    expect(state.run?.status).toBe('completed')
  })

  it('ignores duplicate and out-of-order answer deltas', () => {
    const state = apply([
      e('run_started', 0),
      e('answer_delta', 1, { delta: 'A' }),
      e('answer_delta', 2, { delta: 'B' }),
    ])
    expect(reduceAgentStream(state, e('answer_delta', 2, { delta: 'X' }))).toBe(state)
    expect(reduceAgentStream(state, e('answer_delta', 1, { delta: 'Y' }))).toBe(state)
    expect(state.run?.answer).toBe('AB')
  })

  it('lets the first real delta replace a legacy complete answer', () => {
    const state = apply([
      e('run_started', 0),
      e('assistant_message', 1, { answer: '旧完整回答' }),
      e('answer_delta', 2, { delta: '新' }),
      e('answer_delta', 3, { delta: '回答' }),
    ])
    expect(state.run?.answer).toBe('新回答')
  })

  it('preserves loading and safely downgrades unknown RAG statuses', () => {
    const state = apply([
      e('run_started', 0),
      e('rag_started', 1, {
        step_index: 1,
        call_id: 'rag-1',
        tool_name: 'knowledge_search',
        rag: { status: 'loading', references: [] },
      }),
      e('tool_completed', 2, {
        step_index: 1,
        call_id: 'rag-1',
        tool_name: 'knowledge_search',
        succeeded: true,
        rag: { status: 'future_status', references: [] },
      }),
    ])
    expect(state.run?.steps[0]?.toolCalls[0]?.rag?.status).toBe('failed')
    const loadingState = reduceAgentStream(
      initialAgentStreamState,
      e('rag_started', 1, {
        step_index: 1,
        call_id: 'rag-1',
        tool_name: 'knowledge_search',
        rag: { status: 'loading', references: [] },
      }),
    )
    expect(loadingState.run?.steps[0]?.toolCalls[0]?.rag?.status).toBe('loading')
  })
})
