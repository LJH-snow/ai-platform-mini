import { describe, expect, it } from 'vitest'

import {
  initialMultiAgentStreamState,
  mergeSynchronousRun,
  reduceMultiAgentStream,
} from './reducer.ts'
import type { MultiAgentStreamEvent } from './stream.ts'

const base = {
  run_id: 'run-1',
  request_id: 'req-1',
} as const

const started: MultiAgentStreamEvent = { event: 'run_started', sequence: 0, ...base }

const planned: MultiAgentStreamEvent = {
  event: 'subtasks_planned',
  sequence: 1,
  ...base,
  reasoning: 'split it',
  subtasks: [
    { id: 't1', agent_role: 'writer', description: 'draft', depends_on: [] },
    { id: 't2', agent_role: 'reviewer', description: 'check', depends_on: ['t1'] },
  ],
}

describe('reduceMultiAgentStream', () => {
  it('creates a running run on run_started', () => {
    const state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    expect(state.run?.runId).toBe('run-1')
    expect(state.run?.status).toBe('running')
    expect(state.terminal).toBe(false)
    expect(state.requestId).toBe('req-1')
  })

  it('plans subtasks as pending with dependencies', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, planned)
    expect(state.run?.subtasks.map((item) => item.id)).toEqual(['t1', 't2'])
    expect(state.run?.subtasks[1].dependsOn).toEqual(['t1'])
    expect(state.run?.subtasks[0].status).toBe('pending')
    expect(state.run?.reasoning).toBe('split it')
  })

  it('tracks subtask lifecycle with summaries and errors', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, planned)
    state = reduceMultiAgentStream(state, {
      event: 'subtask_started',
      sequence: 2,
      ...base,
      task_id: 't1',
      agent_role: 'writer',
    })
    expect(state.run?.subtasks[0].status).toBe('running')
    state = reduceMultiAgentStream(state, {
      event: 'subtask_completed',
      sequence: 3,
      ...base,
      task_id: 't1',
      agent_role: 'writer',
      output_summary: 'draft done',
      token_usage: 11,
      duration_ms: 5,
    })
    expect(state.run?.subtasks[0].status).toBe('completed')
    expect(state.run?.subtasks[0].outputSummary).toBe('draft done')
    state = reduceMultiAgentStream(state, {
      event: 'subtask_failed',
      sequence: 4,
      ...base,
      task_id: 't2',
      agent_role: 'reviewer',
      error_code: 'subtask_failed',
    })
    expect(state.run?.subtasks[1].status).toBe('failed')
    expect(state.run?.subtasks[1].errorCode).toBe('subtask_failed')
  })

  it('appends subtask answer deltas before completion', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, planned)
    state = reduceMultiAgentStream(state, {
      event: 'subtask_answer_delta',
      sequence: 2,
      ...base,
      task_id: 't1',
      agent_role: 'writer',
      output_summary: 'first chunk',
      agent_event_kind: 'answer_delta',
      step_index: 0,
    })
    state = reduceMultiAgentStream(state, {
      event: 'subtask_answer_delta',
      sequence: 3,
      ...base,
      task_id: 't1',
      agent_role: 'writer',
      output_summary: 'second chunk',
      agent_event_kind: 'answer_delta',
      step_index: 1,
    })
    expect(state.run?.subtasks[0].answerDeltas).toEqual(['first chunk', 'second chunk'])
  })

  it('records step traces for subtasks', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, planned)
    state = reduceMultiAgentStream(state, {
      event: 'subtask_step_started',
      sequence: 2,
      ...base,
      task_id: 't1',
      step_index: 0,
    })
    expect(state.run?.subtasks[0].steps).toEqual([
      { index: 0, status: 'started', outputSummary: null },
    ])
    state = reduceMultiAgentStream(state, {
      event: 'subtask_step_completed',
      sequence: 3,
      ...base,
      task_id: 't1',
      step_index: 0,
      output_summary: 'drafted',
    })
    expect(state.run?.subtasks[0].steps).toEqual([
      { index: 0, status: 'completed', outputSummary: 'drafted' },
    ])
  })

  it('records tool traces for subtasks', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, planned)
    state = reduceMultiAgentStream(state, {
      event: 'subtask_tool_started',
      sequence: 2,
      ...base,
      task_id: 't1',
      step_index: 0,
      tool_name: 'knowledge_search',
      call_id: 'call-1',
    })
    expect(state.run?.subtasks[0].tools).toEqual([
      {
        name: 'knowledge_search',
        callId: 'call-1',
        status: 'started',
        outputSummary: null,
        stepIndex: 0,
      },
    ])
    state = reduceMultiAgentStream(state, {
      event: 'subtask_tool_failed',
      sequence: 3,
      ...base,
      task_id: 't1',
      tool_name: 'knowledge_search',
      call_id: 'call-1',
      error_code: 'internal_error',
    })
    expect(state.run?.subtasks[0].tools[1]).toMatchObject({
      status: 'failed',
      name: 'knowledge_search',
      callId: 'call-1',
      outputSummary: null,
    })
  })

  it('closes the run on a terminal event with output and totals', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    state = reduceMultiAgentStream(state, {
      event: 'run_completed',
      sequence: 2,
      ...base,
      final_output: 'report',
      total_token_usage: 99,
      duration_ms: 12,
      subtask_results: [{ task_id: 't1', status: 'completed', agent_role: 'writer' }],
    })
    expect(state.terminal).toBe(true)
    expect(state.run?.status).toBe('completed')
    expect(state.run?.finalOutput).toBe('report')
    expect(state.run?.totalTokenUsage).toBe(99)
    // Terminal state is frozen: later events are ignored.
    const frozen = reduceMultiAgentStream(state, {
      event: 'subtask_started',
      sequence: 3,
      ...base,
      task_id: 't9',
    })
    expect(frozen).toBe(state)
  })

  it('maps each terminal kind to its status', () => {
    const kinds = [
      ['run_failed', 'failed'],
      ['run_timed_out', 'timed_out'],
      ['run_cancelled', 'cancelled'],
      ['run_budget_exceeded', 'budget_exceeded'],
    ] as const
    for (const [event, status] of kinds) {
      let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
      state = reduceMultiAgentStream(state, { event, sequence: 1, ...base })
      expect(state.run?.status).toBe(status)
      expect(state.terminal).toBe(true)
    }
  })

  it('ignores replays and foreign run ids', () => {
    let state = reduceMultiAgentStream(initialMultiAgentStreamState, started)
    const replayed = reduceMultiAgentStream(state, started)
    expect(replayed).toBe(state)
    const foreign = reduceMultiAgentStream(state, { ...started, run_id: 'run-2', sequence: 1 })
    expect(foreign).toBe(state)
  })

  it('merges a synchronous response as terminal', () => {
    const merged = mergeSynchronousRun(initialMultiAgentStreamState, {
      runId: 'run-9',
      status: 'completed',
      finalOutput: 'sync',
      reasoning: null,
      subtasks: [],
      totalTokenUsage: 3,
      durationMs: 1,
      errorCode: null,
      requestId: null,
      lastSequence: 0,
      startedAt: null,
      completedAt: null,
    })
    expect(merged.terminal).toBe(true)
    expect(merged.run?.finalOutput).toBe('sync')
  })
})
