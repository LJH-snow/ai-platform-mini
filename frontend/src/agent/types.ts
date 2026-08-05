export type AgentRunStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'stopped'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'unknown'

export type AgentToolStatus =
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'unknown'

export type AgentTraceEvent = {
  id: string
  kind: string
  stepIndex: number | null
  status: AgentRunStatus | null
  stopReason: string | null
}

export type AgentToolCall = {
  id: string
  name: string
  known: boolean
  status: AgentToolStatus
  stepIndex: number
  startedAt: string | null
  completedAt: string | null
  durationMs: number | null
  inputSummary: string | null
  outputSummary: string | null
  errorCode: string | null
  errorMessage: string | null
}

export type AgentTraceStep = {
  id: string
  index: number
  decisionKind: 'final_answer' | 'tool_call' | 'invalid' | 'unknown'
  status: AgentRunStatus
  startedAt: string | null
  completedAt: string | null
  durationMs: number | null
  toolNames: string[]
  summary: string
  toolCalls: AgentToolCall[]
  events: AgentTraceEvent[]
}

export type AgentUsage = {
  promptTokens: number | null
  completionTokens: number | null
  totalTokens: number | null
  estimated: boolean
}

export type AgentRun = {
  runId: string | null
  status: AgentRunStatus
  answer: string | null
  stopReason: string | null
  steps: AgentTraceStep[]
  events: AgentTraceEvent[]
  usage: AgentUsage
}

export type AgentRunInput = {
  message: string
  history: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>
}
