export type AgentRunStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'stopped'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'unknown'

export type AgentLiveStatus =
  | 'connecting'
  | 'running'
  | 'waiting'
  | 'tool_running'
  | 'tool_completed'
  | 'tool_failed'
  | 'rag_loading'
  | 'rag_completed'
  | 'completed'
  | 'failed'
  | 'timeout'
  | 'cancelled'
  | 'connection_lost'
  | 'response_format_error'
  | 'client_stopped'

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
  sequence?: number
}

export type AgentRagStatus =
  | 'loading'
  | 'success_with_sources'
  | 'no_relevant_sources'
  | 'knowledge_base_empty'
  | 'rag_unavailable'
  | 'embedding_failed'
  | 'output_unavailable'
  | 'failed'

export type AgentRagErrorCode =
  | 'invalid_query'
  | 'no_relevant_context'
  | 'knowledge_base_empty'
  | 'rag_storage_unavailable'
  | 'embedding_unavailable'
  | 'embedding_failed'
  | 'rag_unavailable'
  | 'output_truncated'
  | 'output_malformed'
  | 'failed'

export type AgentRagReference = {
  documentId: string | null
  chunkId: string | null
  chunkIndex: number | null
  content: string | null
  distance: number | null
  truncated: boolean
}

export type AgentRag = {
  status: AgentRagStatus
  warning: string | null
  errorCode: AgentRagErrorCode | null
  references: AgentRagReference[]
}

export type AgentToolCall = {
  id: string
  name: string
  callId?: string | null
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
  truncated: boolean | null
  rag: AgentRag | null
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
  requestId?: string | null
  lastSequence?: number
}

export type AgentRunInput = {
  message: string
  history: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>
}
