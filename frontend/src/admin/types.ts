export type AdminApiKey = {
  key_hash_prefix: string
  name: string
  status: string
  is_admin: boolean
  created_at: string | null
  last_used_at: string | null
}

export type CreatedAdminApiKey = {
  key_hash_prefix: string
  name: string
  raw_key: string
  created_at: string | null
}

export type UsageAggregation = {
  model: string
  request_count: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export type AgentRunSummary = {
  run_id: string
  request_id: string
  api_key_prefix: string
  api_key_name: string
  model: string
  status: string
  stop_reason: string
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  total_tokens: number | null
  tool_count: number
  rag_reference_count: number
}

export type AgentRunRecord = AgentRunSummary & {
  response: Record<string, unknown>
}

export type PlanAdmin = {
  id: string
  name: string
  version: number
  daily_token_limit: number | null
  monthly_token_limit: number | null
  max_agents: number | null
  max_documents: number | null
  max_members: number | null
  features: Record<string, boolean>
}

export type SubscriptionAdmin = {
  id: string
  workspace_id: string
  plan_id: string
  plan_name: string | null
  status: string
  started_at: string | null
  expired_at: string | null
}
