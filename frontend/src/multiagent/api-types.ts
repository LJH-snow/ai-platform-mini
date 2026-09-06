export type MultiAgentRunApiRequest = {
  message: string
  supervisor_model?: string | null
  max_subtasks: number
  max_concurrency: number
  failure_policy: 'fail_fast' | 'skip' | 'retry_once'
  total_timeout: number | null
  total_token_budget?: number | null
}

export type MultiAgentSubtaskApiResult = {
  task_id: string
  status: string
  output: string
  error: string | null
  error_code: string | null
  agent_role: string
  token_usage: number
  steps_taken: number
  duration_ms: number | null
}

export type MultiAgentRunApiResponse = {
  run_id: string
  status: string
  final_output: string
  subtask_results: MultiAgentSubtaskApiResult[]
  total_token_usage: number
  error: string | null
  error_code: string | null
  duration_ms: number | null
}

export type MultiAgentStreamSubtask = {
  id: string
  agent_role: string
  description?: string | null
  depends_on?: string[] | null
}

export type MultiAgentStreamSubtaskResult = {
  task_id: string
  status: string
  agent_role?: string | null
  output?: string | null
  error_code?: string | null
  token_usage?: number | null
  duration_ms?: number | null
}

export type MultiAgentRunHistoryApiSummary = {
  run_id: string
  request_id: string | null
  api_key_prefix: string
  api_key_name: string
  status: string
  stop_reason: string
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  total_tokens: number | null
  subtask_count: number
}

export type MultiAgentRunHistoryApiDetail = Omit<
  MultiAgentRunHistoryApiSummary,
  'subtask_count'
> & {
  response: {
    status?: string | null
    final_output?: string | null
    error_code?: string | null
    total_token_usage?: number | null
    duration_ms?: number | null
    subtask_results?: MultiAgentStreamSubtaskResult[] | null
  }
}
