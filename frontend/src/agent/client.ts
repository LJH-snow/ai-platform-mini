import { adaptAgentRunResponse } from './adapter.ts'
import type { AgentRunApiRequest, AgentRunApiResponse } from './api-types.ts'
import type { AgentRun, AgentRunInput } from './types.ts'

export type AgentClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

export type AgentClient = {
  runAgent: (input: AgentRunInput, signal: AbortSignal) => Promise<AgentRun>
}

export class AgentBackendError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null) {
    super(message)
    this.name = 'AgentBackendError'
    this.status = status
    this.code = code
  }
}

export class AgentNetworkError extends Error {
  constructor(message = '无法连接 Agent 服务，请检查网络后重试。') {
    super(message)
    this.name = 'AgentNetworkError'
  }
}

export class AgentResponseError extends Error {
  constructor(message = 'Agent 服务返回了无法识别的响应。') {
    super(message)
    this.name = 'AgentResponseError'
  }
}

type ErrorPayload = { code?: unknown }

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  if (!baseUrl) {
    return path
  }
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

const safeBackendMessage = (status: number): string => {
  if (status === 401 || status === 403) {
    return 'Agent 请求未通过鉴权，请检查运行时凭据。'
  }
  if (status === 408 || status === 504) {
    return 'Agent 请求超时，请稍后重试。'
  }
  if (status === 429) {
    return 'Agent 请求过于频繁，请稍后重试。'
  }
  if (status >= 500) {
    return 'Agent 服务暂时不可用，请稍后重试。'
  }
  return `Agent 请求失败（HTTP ${status}）。`
}

const getErrorCode = async (response: Response): Promise<string | null> => {
  try {
    const value = (await response.json()) as ErrorPayload
    return typeof value.code === 'string' ? value.code : null
  } catch {
    return null
  }
}

const isNullableString = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

const isNullableNumber = (value: unknown): value is number | null =>
  (typeof value === 'number' && Number.isFinite(value) && value >= 0) || value === null

const isApiStatus = (value: unknown): boolean =>
  ['completed', 'stopped', 'failed', 'cancelled', 'timed_out'].includes(String(value))

const isApiStep = (value: unknown): boolean => {
  if (typeof value !== 'object' || value === null) return false
  const step = value as Record<string, unknown>
  return (
    typeof step.index === 'number' &&
    Number.isInteger(step.index) &&
    step.index >= 1 &&
    ['final_answer', 'tool_call', 'invalid'].includes(String(step.decision_kind)) &&
    Array.isArray(step.tool_names) &&
    step.tool_names.every((name) => typeof name === 'string') &&
    (typeof step.tool_succeeded === 'boolean' || step.tool_succeeded === null)
  )
}

const isApiEvent = (value: unknown): boolean => {
  if (typeof value !== 'object' || value === null) return false
  const event = value as Record<string, unknown>
  const stepIndex = event.step_index
  return (
    typeof event.kind === 'string' &&
    (stepIndex === null ||
      (typeof stepIndex === 'number' && Number.isInteger(stepIndex) && stepIndex >= 1)) &&
    (event.status === null || isApiStatus(event.status)) &&
    isNullableString(event.stop_reason)
  )
}

const isApiResponse = (value: unknown): value is AgentRunApiResponse => {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const response = value as Record<string, unknown>
  const usage = response.usage
  if (typeof usage !== 'object' || usage === null) {
    return false
  }
  const usageRecord = usage as Record<string, unknown>
  return (
    typeof response.run_id === 'string' &&
    isApiStatus(response.status) &&
    isNullableString(response.answer) &&
    typeof response.stop_reason === 'string' &&
    Array.isArray(response.steps) &&
    response.steps.every(isApiStep) &&
    Array.isArray(response.events) &&
    response.events.every(isApiEvent) &&
    isNullableNumber(usageRecord.prompt_tokens) &&
    isNullableNumber(usageRecord.completion_tokens) &&
    isNullableNumber(usageRecord.total_tokens) &&
    typeof usageRecord.estimated === 'boolean'
  )
}

export function createAgentClient(options: AgentClientOptions = {}): AgentClient {
  const fetchImpl = options.fetchImpl ?? fetch

  return {
    async runAgent(input, signal) {
      const body: AgentRunApiRequest = {
        message: input.message,
        history: input.history,
      }

      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/agent/runs'), {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {}),
          },
          body: JSON.stringify(body),
          signal,
        })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          throw error
        }
        throw new AgentNetworkError()
      }

      if (!response.ok) {
        throw new AgentBackendError(
          safeBackendMessage(response.status),
          response.status,
          await getErrorCode(response),
        )
      }

      let payload: unknown
      try {
        payload = (await response.json()) as unknown
      } catch {
        throw new AgentResponseError()
      }
      if (!isApiResponse(payload)) {
        throw new AgentResponseError()
      }
      return adaptAgentRunResponse(payload)
    },
  }
}
