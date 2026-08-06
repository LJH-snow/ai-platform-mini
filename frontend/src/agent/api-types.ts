import type { ChatApiMessage } from '../chat/types.ts'

export type AgentRunApiStatus = 'completed' | 'stopped' | 'failed' | 'cancelled' | 'timed_out'

export type AgentRunApiRequest = {
  message: string
  history: ChatApiMessage[]
  timeout_seconds: number
  token_budget: number
  max_steps: number
}

export type AgentRagApiStatus =
  | 'loading'
  | 'success_with_sources'
  | 'no_relevant_sources'
  | 'knowledge_base_empty'
  | 'rag_unavailable'
  | 'embedding_failed'
  | 'output_unavailable'
  | 'failed'
  | (string & {})

export type AgentRagApiErrorCode =
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

export type AgentToolApiErrorCode =
  | 'invalid_tool_arguments'
  | 'tool_permission_denied'
  | 'tool_timeout'
  | 'tool_output_too_large'
  | 'tool_not_found'
  | 'tool_execution_failed'

export type AgentRagReferenceApiSummary = {
  document_id?: string | null
  chunk_id?: string | null
  chunk_index?: number | null
  content?: string | null
  distance?: number | null
  truncated?: boolean | null
}

export type AgentRagApiSummary = {
  status: AgentRagApiStatus
  warning?: string | null
  error_code?: AgentRagApiErrorCode | null
  references: AgentRagReferenceApiSummary[]
}

export type AgentToolCallApiSummary = {
  call_id: string
  name: string
  succeeded: boolean | null
  truncated: boolean | null
  cached?: boolean
  started_at?: string | null
  completed_at?: string | null
  duration_ms?: number | null
  argument_count?: number | null
  input_summary?: string | null
  output_summary?: string | null
  result_chars?: number | null
  error_code: AgentToolApiErrorCode | null
  error_message: string | null
  rag?: AgentRagApiSummary | null
}

export type AgentStepApiSummary = {
  index: number
  decision_kind: 'final_answer' | 'tool_call' | 'invalid'
  tool_names: string[]
  tool_count?: number | null
  summary?: string | null
  started_at?: string | null
  completed_at?: string | null
  duration_ms?: number | null
  tool_succeeded: boolean | null
  tool_calls?: AgentToolCallApiSummary[] | null
}

export type AgentEventApiSummary = {
  kind: string
  occurred_at?: string | null
  step_index: number | null
  status: AgentRunApiStatus | null
  stop_reason: string | null
}

export type AgentUsageApiSummary = {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  estimated: boolean
}

export type AgentRunApiResponse = {
  run_id: string
  status: AgentRunApiStatus
  answer: string | null
  stop_reason: string
  started_at?: string | null
  completed_at?: string | null
  duration_ms?: number | null
  steps: AgentStepApiSummary[]
  events: AgentEventApiSummary[]
  usage: AgentUsageApiSummary
}
