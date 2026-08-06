import { adaptAgentRunResponse } from './adapter.ts'
import { AgentStreamFormatError, readAgentSse, type AgentStreamEvent } from './stream.ts'
import type {
  AgentRagApiErrorCode,
  AgentRagApiStatus,
  AgentRunApiRequest,
  AgentRunApiResponse,
  AgentToolApiErrorCode,
} from './api-types.ts'
import {
  DEFAULT_AGENT_MAX_STEPS,
  DEFAULT_AGENT_TIMEOUT_SECONDS,
  DEFAULT_AGENT_TOKEN_BUDGET,
  MAX_AGENT_MAX_STEPS,
  MAX_AGENT_TIMEOUT_SECONDS,
  MAX_AGENT_TOKEN_BUDGET,
  MIN_AGENT_MAX_STEPS,
  MIN_AGENT_TOKEN_BUDGET,
  type AgentRun,
  type AgentRunInput,
} from './types.ts'

export type AgentClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

export type AgentClient = {
  runAgent: (input: AgentRunInput, signal: AbortSignal) => Promise<AgentRun>
  streamAgent?: (
    input: AgentRunInput,
    handlers: { onEvent: (event: AgentStreamEvent) => void },
    signal: AbortSignal,
  ) => Promise<void>
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

const assertAgentRunParameters = (input: AgentRunInput): void => {
  if (
    input.tokenBudget !== undefined &&
    (!Number.isInteger(input.tokenBudget) ||
      input.tokenBudget < MIN_AGENT_TOKEN_BUDGET ||
      input.tokenBudget > MAX_AGENT_TOKEN_BUDGET)
  ) {
    throw new RangeError(
      `token_budget 必须是 ${MIN_AGENT_TOKEN_BUDGET} 到 ${MAX_AGENT_TOKEN_BUDGET} 之间的整数。`,
    )
  }
  if (
    input.maxSteps !== undefined &&
    (!Number.isInteger(input.maxSteps) ||
      input.maxSteps < MIN_AGENT_MAX_STEPS ||
      input.maxSteps > MAX_AGENT_MAX_STEPS)
  ) {
    throw new RangeError(
      `max_steps 必须是 ${MIN_AGENT_MAX_STEPS} 到 ${MAX_AGENT_MAX_STEPS} 之间的整数。`,
    )
  }
  if (
    input.timeoutSeconds !== undefined &&
    (typeof input.timeoutSeconds !== 'number' ||
      !Number.isFinite(input.timeoutSeconds) ||
      input.timeoutSeconds <= 0 ||
      input.timeoutSeconds > MAX_AGENT_TIMEOUT_SECONDS)
  ) {
    throw new RangeError(
      `timeout_seconds 必须是大于 0 且不超过 ${MAX_AGENT_TIMEOUT_SECONDS} 的数字。`,
    )
  }
}

const agentRequestBody = (input: AgentRunInput): AgentRunApiRequest => {
  assertAgentRunParameters(input)
  return {
    message: input.message,
    history: input.history,
    token_budget: input.tokenBudget ?? DEFAULT_AGENT_TOKEN_BUDGET,
    max_steps: input.maxSteps ?? DEFAULT_AGENT_MAX_STEPS,
    timeout_seconds: input.timeoutSeconds ?? DEFAULT_AGENT_TIMEOUT_SECONDS,
    ...(input.preset ? { preset: input.preset } : {}),
  }
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

const isOptionalNullableString = (value: unknown): value is string | null | undefined =>
  value === undefined || isNullableString(value)

const isNullableNumber = (value: unknown): value is number | null =>
  (typeof value === 'number' && Number.isFinite(value) && value >= 0) || value === null

const isOptionalNullableNumber = (value: unknown): value is number | null | undefined =>
  value === undefined || isNullableNumber(value)

const isApiStatus = (value: unknown): boolean =>
  ['completed', 'stopped', 'failed', 'cancelled', 'timed_out'].includes(String(value))

const ragStatuses = new Set<AgentRagApiStatus>([
  'loading',
  'success_with_sources',
  'no_relevant_sources',
  'knowledge_base_empty',
  'rag_unavailable',
  'embedding_failed',
  'output_unavailable',
  'failed',
])
const ragErrorCodes = new Set<AgentRagApiErrorCode>([
  'invalid_query',
  'no_relevant_context',
  'knowledge_base_empty',
  'rag_storage_unavailable',
  'embedding_unavailable',
  'embedding_failed',
  'rag_unavailable',
  'output_truncated',
  'output_malformed',
  'failed',
])
const toolErrorCodes = new Set<AgentToolApiErrorCode>([
  'invalid_tool_arguments',
  'tool_permission_denied',
  'tool_timeout',
  'tool_output_too_large',
  'tool_not_found',
  'tool_execution_failed',
])

const isNullableBoolean = (value: unknown): value is boolean | null =>
  typeof value === 'boolean' || value === null

const isOptionalNullableBoolean = (value: unknown): value is boolean | null | undefined =>
  value === undefined || isNullableBoolean(value)

const isApiRagReference = (value: unknown): boolean => {
  if (typeof value !== 'object' || value === null) return false
  const reference = value as Record<string, unknown>
  return (
    isOptionalNullableString(reference.document_id) &&
    isOptionalNullableString(reference.chunk_id) &&
    isOptionalNullableNumber(reference.chunk_index) &&
    (reference.chunk_index === undefined ||
      reference.chunk_index === null ||
      (typeof reference.chunk_index === 'number' &&
        Number.isInteger(reference.chunk_index) &&
        reference.chunk_index <= 1_000_000)) &&
    isOptionalNullableString(reference.content) &&
    isOptionalNullableNumber(reference.distance) &&
    (reference.distance === undefined ||
      reference.distance === null ||
      (typeof reference.distance === 'number' && reference.distance <= 2)) &&
    isOptionalNullableBoolean(reference.truncated)
  )
}

const isApiRag = (value: unknown): boolean => {
  if (typeof value !== 'object' || value === null) return false
  const rag = value as Record<string, unknown>
  return (
    typeof rag.status === 'string' &&
    ragStatuses.has(rag.status as AgentRagApiStatus) &&
    isOptionalNullableString(rag.warning) &&
    (rag.error_code === undefined ||
      rag.error_code === null ||
      (typeof rag.error_code === 'string' &&
        ragErrorCodes.has(rag.error_code as AgentRagApiErrorCode))) &&
    Array.isArray(rag.references) &&
    rag.references.every(isApiRagReference)
  )
}

const isApiToolCall = (value: unknown): boolean => {
  if (typeof value !== 'object' || value === null) return false
  const call = value as Record<string, unknown>
  const ragIsAllowed =
    call.name === 'knowledge_search'
      ? call.rag === undefined || call.rag === null || isApiRag(call.rag)
      : call.rag === undefined || call.rag === null
  return (
    typeof call.call_id === 'string' &&
    typeof call.name === 'string' &&
    isNullableBoolean(call.succeeded) &&
    isNullableBoolean(call.truncated) &&
    (typeof call.error_code === 'string' || call.error_code === null) &&
    (call.error_code === null || toolErrorCodes.has(call.error_code as AgentToolApiErrorCode)) &&
    (typeof call.error_message === 'string' || call.error_message === null) &&
    ragIsAllowed
  )
}

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
    (typeof step.tool_succeeded === 'boolean' || step.tool_succeeded === null) &&
    (step.tool_calls === undefined ||
      step.tool_calls === null ||
      (Array.isArray(step.tool_calls) && step.tool_calls.every(isApiToolCall)))
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
      const body = agentRequestBody(input)

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
    async streamAgent(input, handlers, signal) {
      const body = agentRequestBody(input)
      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/agent/runs/stream'), {
          method: 'POST',
          headers: {
            Accept: 'text/event-stream',
            'Content-Type': 'application/json',
            ...(options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {}),
          },
          body: JSON.stringify(body),
          signal,
        })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        throw new AgentNetworkError()
      }
      if (!response.ok) {
        throw new AgentBackendError(
          safeBackendMessage(response.status),
          response.status,
          await getErrorCode(response),
        )
      }
      if (!response.body) throw new AgentStreamFormatError()
      try {
        for await (const event of readAgentSse(response)) {
          if (event.event === 'stream_error') throw new AgentNetworkError()
          handlers.onEvent(event)
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        if (error instanceof AgentStreamFormatError) throw error
        throw new AgentNetworkError()
      }
    },
  }
}
