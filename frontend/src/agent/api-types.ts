import type { ChatApiMessage } from '../chat/types.ts'

export type AgentRunApiStatus = 'completed' | 'stopped' | 'failed' | 'cancelled' | 'timed_out'

export type AgentRunApiRequest = {
  message: string
  history: ChatApiMessage[]
}

export type AgentStepApiSummary = {
  index: number
  decision_kind: 'final_answer' | 'tool_call' | 'invalid'
  tool_names: string[]
  tool_succeeded: boolean | null
}

export type AgentEventApiSummary = {
  kind: string
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
  steps: AgentStepApiSummary[]
  events: AgentEventApiSummary[]
  usage: AgentUsageApiSummary
}
