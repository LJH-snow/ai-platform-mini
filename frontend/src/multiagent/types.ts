export const DEFAULT_MULTI_AGENT_MAX_SUBTASKS = 5
export const MAX_MULTI_AGENT_MAX_SUBTASKS = 10
export const MIN_MULTI_AGENT_MAX_SUBTASKS = 1
export const DEFAULT_MULTI_AGENT_MAX_CONCURRENCY = 3
export const MAX_MULTI_AGENT_MAX_CONCURRENCY = 10
export const MIN_MULTI_AGENT_MAX_CONCURRENCY = 1
export const DEFAULT_MULTI_AGENT_TIMEOUT_SECONDS = 300

export type MultiAgentRunStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'budget_exceeded'
  | 'unknown'

export type MultiAgentFailurePolicy = 'fail_fast' | 'skip' | 'retry_once'

export type MultiAgentSubtaskStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'cancelled'
  | 'unknown'

export type MultiAgentStepTrace = {
  index: number
  status: 'started' | 'completed'
  outputSummary: string | null
}

export type MultiAgentToolTrace = {
  name: string
  callId: string | null
  status: 'started' | 'completed' | 'failed'
  outputSummary: string | null
  stepIndex: number | null
}

export type MultiAgentSubtask = {
  id: string
  agentRole: string
  description: string
  dependsOn: string[]
  status: MultiAgentSubtaskStatus
  outputSummary: string | null
  errorCode: string | null
  tokenUsage: number | null
  durationMs: number | null
  answerDeltas: string[]
  steps: MultiAgentStepTrace[]
  tools: MultiAgentToolTrace[]
}

export type MultiAgentRun = {
  runId: string | null
  status: MultiAgentRunStatus
  finalOutput: string | null
  reasoning: string | null
  subtasks: MultiAgentSubtask[]
  totalTokenUsage: number | null
  durationMs: number | null
  errorCode: string | null
  requestId: string | null
  lastSequence: number
  startedAt: string | null
  completedAt: string | null
}

export type MultiAgentRunInput = {
  message: string
  supervisorModel?: string | null
  maxSubtasks?: number
  maxConcurrency?: number
  failurePolicy?: MultiAgentFailurePolicy
  totalTimeoutSeconds?: number | null
  totalTokenBudget?: number | null
}

export type MultiAgentRunSummary = {
  runId: string
  requestId: string | null
  apiKeyPrefix: string
  apiKeyName: string
  status: string
  stopReason: string
  startedAt: string | null
  completedAt: string | null
  durationMs: number | null
  totalTokens: number | null
  subtaskCount: number
}

export type MultiAgentHistorySubtaskResult = {
  taskId: string
  status: string
  agentRole: string
  output: string
  errorCode: string | null
  tokenUsage: number | null
  durationMs: number | null
}

export type MultiAgentHistoryResponse = {
  status: string
  finalOutput: string
  errorCode: string | null
  totalTokenUsage: number | null
  durationMs: number | null
  subtaskResults: MultiAgentHistorySubtaskResult[]
}

export type MultiAgentRunDetail = MultiAgentRunSummary & {
  response: MultiAgentHistoryResponse
}

export type MultiAgentBenchmarkRun = {
  id: number
  agentId: string
  workspaceId: string
  taskSet: string
  toolCallAccuracy: number | null
  taskCompletionRate: number | null
  taskCount: number
  completedCount: number
  createdAt: string | null
  metricPayload: Record<string, unknown>
}
