import type {
  MultiAgentRunApiResponse,
  MultiAgentRunHistoryApiDetail,
  MultiAgentSubtaskApiResult,
} from './api-types.ts'
import type {
  MultiAgentHistorySubtaskResult,
  MultiAgentRun,
  MultiAgentRunDetail,
  MultiAgentRunStatus,
  MultiAgentSubtask,
  MultiAgentSubtaskStatus,
} from './types.ts'

export const adaptRunStatus = (value: string): MultiAgentRunStatus => {
  switch (value) {
    case 'completed':
    case 'failed':
    case 'cancelled':
    case 'timed_out':
    case 'budget_exceeded':
      return value
    case 'running':
      return 'running'
    default:
      return 'unknown'
  }
}

const adaptSubtaskStatus = (value: string): MultiAgentSubtaskStatus => {
  switch (value) {
    case 'pending':
    case 'running':
    case 'completed':
    case 'failed':
    case 'skipped':
    case 'cancelled':
      return value
    default:
      return 'unknown'
  }
}

const adaptSyncSubtask = (item: MultiAgentSubtaskApiResult): MultiAgentSubtask => ({
  id: item.task_id,
  agentRole: item.agent_role,
  description: '',
  dependsOn: [],
  status: adaptSubtaskStatus(item.status),
  outputSummary: item.output || null,
  errorCode: item.error_code ?? null,
  tokenUsage: item.token_usage,
  durationMs: item.duration_ms ?? null,
})

export function adaptMultiAgentRunResponse(payload: MultiAgentRunApiResponse): MultiAgentRun {
  return {
    runId: payload.run_id,
    status: adaptRunStatus(payload.status),
    finalOutput: payload.final_output || null,
    reasoning: null,
    subtasks: payload.subtask_results.map(adaptSyncSubtask),
    totalTokenUsage: payload.total_token_usage,
    durationMs: payload.duration_ms ?? null,
    errorCode: payload.error_code ?? null,
    requestId: null,
    lastSequence: 0,
    startedAt: null,
    completedAt: null,
  }
}

const adaptHistorySubtask = (item: MultiAgentHistorySubtaskResult): MultiAgentSubtask => ({
  id: item.taskId,
  agentRole: item.agentRole,
  description: '',
  dependsOn: [],
  status: adaptSubtaskStatus(item.status),
  outputSummary: item.output || null,
  errorCode: item.errorCode,
  tokenUsage: item.tokenUsage,
  durationMs: item.durationMs,
})

/** Convert a stored history detail into a replayable run for the timeline. */
export function adaptMultiAgentHistoryDetail(
  payload: MultiAgentRunHistoryApiDetail,
): MultiAgentRunDetail {
  const response = payload.response
  const subtaskResults = Array.isArray(response.subtask_results) ? response.subtask_results : []
  return {
    runId: payload.run_id,
    requestId: payload.request_id ?? null,
    apiKeyPrefix: payload.api_key_prefix,
    apiKeyName: payload.api_key_name,
    status: payload.status,
    stopReason: payload.stop_reason,
    startedAt: payload.started_at ?? null,
    completedAt: payload.completed_at ?? null,
    durationMs:
      typeof payload.duration_ms === 'number' && Number.isFinite(payload.duration_ms)
        ? payload.duration_ms
        : null,
    totalTokens: payload.total_tokens ?? null,
    subtaskCount: subtaskResults.length,
    response: {
      status: typeof response.status === 'string' ? response.status : payload.status,
      finalOutput: typeof response.final_output === 'string' ? response.final_output : '',
      errorCode: typeof response.error_code === 'string' ? response.error_code : null,
      totalTokenUsage:
        typeof response.total_token_usage === 'number' ? response.total_token_usage : null,
      durationMs: typeof response.duration_ms === 'number' ? response.duration_ms : null,
      subtaskResults: subtaskResults.map((item) => ({
        taskId: item.task_id,
        status: item.status,
        agentRole: typeof item.agent_role === 'string' ? item.agent_role : 'custom',
        output: typeof item.output === 'string' ? item.output : '',
        errorCode: typeof item.error_code === 'string' ? item.error_code : null,
        tokenUsage: typeof item.token_usage === 'number' ? item.token_usage : null,
        durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : null,
      })),
    },
  }
}

/** Project a history detail response onto the live-run shape for replay. */
export function detailToRun(detail: MultiAgentRunDetail): MultiAgentRun {
  return {
    runId: detail.runId,
    status: adaptRunStatus(detail.response.status),
    finalOutput: detail.response.finalOutput || null,
    reasoning: null,
    subtasks: detail.response.subtaskResults.map(adaptHistorySubtask),
    totalTokenUsage: detail.response.totalTokenUsage,
    durationMs: detail.response.durationMs,
    errorCode: detail.response.errorCode,
    requestId: detail.requestId,
    lastSequence: 0,
    startedAt: detail.startedAt,
    completedAt: detail.completedAt,
  }
}
