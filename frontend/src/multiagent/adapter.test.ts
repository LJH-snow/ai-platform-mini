import { describe, expect, it } from 'vitest'

import { adaptMultiAgentHistoryDetail, adaptMultiAgentRunResponse, detailToRun } from './adapter.ts'

describe('adaptMultiAgentRunResponse', () => {
  it('maps the sync response onto the domain run', () => {
    const run = adaptMultiAgentRunResponse({
      run_id: 'run-1',
      status: 'completed',
      final_output: 'report',
      subtask_results: [
        {
          task_id: 't1',
          status: 'completed',
          output: 'out',
          error: null,
          error_code: null,
          agent_role: 'writer',
          token_usage: 5,
          steps_taken: 1,
          duration_ms: 2,
        },
      ],
      total_token_usage: 5,
      error: null,
      error_code: null,
      duration_ms: 2,
    })
    expect(run.runId).toBe('run-1')
    expect(run.status).toBe('completed')
    expect(run.finalOutput).toBe('report')
    expect(run.subtasks[0].outputSummary).toBe('out')
  })

  it('maps unknown statuses to unknown instead of crashing', () => {
    const run = adaptMultiAgentRunResponse({
      run_id: 'run-1',
      status: 'weird',
      final_output: '',
      subtask_results: [],
      total_token_usage: 0,
      error: null,
      error_code: null,
      duration_ms: null,
    })
    expect(run.status).toBe('unknown')
    expect(run.finalOutput).toBeNull()
  })
})

describe('adaptMultiAgentHistoryDetail', () => {
  const detail = adaptMultiAgentHistoryDetail({
    run_id: 'run-2',
    request_id: 'req-2',
    api_key_prefix: 'cafef00d',
    api_key_name: 'test',
    status: 'failed',
    stop_reason: 'failed',
    started_at: null,
    completed_at: null,
    duration_ms: 4,
    total_tokens: 9,
    response: {
      status: 'failed',
      final_output: '',
      error_code: 'subtask_failed',
      total_token_usage: 9,
      duration_ms: 4,
      subtask_results: [
        { task_id: 't1', status: 'failed', agent_role: 'writer', error_code: 'subtask_failed' },
      ],
    },
  })

  it('keeps the tenant-safe summary fields', () => {
    expect(detail.runId).toBe('run-2')
    expect(detail.apiKeyPrefix).toBe('cafef00d')
    expect(detail.subtaskCount).toBe(1)
  })

  it('projects the stored response for replay', () => {
    const run = detailToRun(detail)
    expect(run.status).toBe('failed')
    expect(run.errorCode).toBe('subtask_failed')
    expect(run.subtasks[0].errorCode).toBe('subtask_failed')
  })
})
