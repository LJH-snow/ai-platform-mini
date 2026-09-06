import { adaptMultiAgentHistoryDetail, adaptMultiAgentRunResponse } from './adapter.ts'
import {
  MultiAgentStreamFormatError,
  readMultiAgentSse,
  type MultiAgentStreamEvent,
} from './stream.ts'
import type {
  MultiAgentRunApiRequest,
  MultiAgentRunApiResponse,
  MultiAgentRunHistoryApiDetail,
  MultiAgentRunHistoryApiSummary,
  MultiAgentSubtaskApiResult,
} from './api-types.ts'
import {
  DEFAULT_MULTI_AGENT_MAX_CONCURRENCY,
  DEFAULT_MULTI_AGENT_MAX_SUBTASKS,
  DEFAULT_MULTI_AGENT_TIMEOUT_SECONDS,
  MAX_MULTI_AGENT_MAX_CONCURRENCY,
  MAX_MULTI_AGENT_MAX_SUBTASKS,
  MIN_MULTI_AGENT_MAX_CONCURRENCY,
  MIN_MULTI_AGENT_MAX_SUBTASKS,
  type MultiAgentRun,
  type MultiAgentRunDetail,
  type MultiAgentRunInput,
  type MultiAgentRunSummary,
} from './types.ts'

export type MultiAgentClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

export type MultiAgentClient = {
  runMultiAgent: (input: MultiAgentRunInput, signal: AbortSignal) => Promise<MultiAgentRun>
  streamMultiAgent: (
    input: MultiAgentRunInput,
    handlers: { onEvent: (event: MultiAgentStreamEvent) => void },
    signal: AbortSignal,
  ) => Promise<void>
  listRuns: (limit?: number, signal?: AbortSignal) => Promise<MultiAgentRunSummary[]>
  getRun: (runId: string, signal?: AbortSignal) => Promise<MultiAgentRunDetail>
}

export class MultiAgentBackendError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null) {
    super(message)
    this.name = 'MultiAgentBackendError'
    this.status = status
    this.code = code
  }
}

export class MultiAgentNetworkError extends Error {
  readonly code: string | null

  constructor(message = '无法连接多 Agent 服务，请检查网络后重试。', code: string | null = null) {
    super(message)
    this.name = 'MultiAgentNetworkError'
    this.code = code
  }
}

export class MultiAgentResponseError extends Error {
  constructor(message = '多 Agent 服务返回了无法识别的响应。') {
    super(message)
    this.name = 'MultiAgentResponseError'
  }
}

type ErrorPayload = { detail?: unknown }

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  if (!baseUrl) {
    return path
  }
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

const assertRunParameters = (input: MultiAgentRunInput): void => {
  if (!input.message || !input.message.trim()) {
    throw new RangeError('任务描述不能为空。')
  }
  const maxSubtasks = input.maxSubtasks ?? DEFAULT_MULTI_AGENT_MAX_SUBTASKS
  if (
    !Number.isInteger(maxSubtasks) ||
    maxSubtasks < MIN_MULTI_AGENT_MAX_SUBTASKS ||
    maxSubtasks > MAX_MULTI_AGENT_MAX_SUBTASKS
  ) {
    throw new RangeError(
      `max_subtasks 必须是 ${MIN_MULTI_AGENT_MAX_SUBTASKS} 到 ${MAX_MULTI_AGENT_MAX_SUBTASKS} 之间的整数。`,
    )
  }
  const maxConcurrency = input.maxConcurrency ?? DEFAULT_MULTI_AGENT_MAX_CONCURRENCY
  if (
    !Number.isInteger(maxConcurrency) ||
    maxConcurrency < MIN_MULTI_AGENT_MAX_CONCURRENCY ||
    maxConcurrency > MAX_MULTI_AGENT_MAX_CONCURRENCY
  ) {
    throw new RangeError(
      `max_concurrency 必须是 ${MIN_MULTI_AGENT_MAX_CONCURRENCY} 到 ${MAX_MULTI_AGENT_MAX_CONCURRENCY} 之间的整数。`,
    )
  }
  if (
    input.totalTimeoutSeconds !== undefined &&
    input.totalTimeoutSeconds !== null &&
    (typeof input.totalTimeoutSeconds !== 'number' ||
      !Number.isFinite(input.totalTimeoutSeconds) ||
      input.totalTimeoutSeconds <= 0)
  ) {
    throw new RangeError('total_timeout 必须是大于 0 的数字。')
  }
  if (
    input.totalTokenBudget !== undefined &&
    input.totalTokenBudget !== null &&
    (!Number.isInteger(input.totalTokenBudget) || input.totalTokenBudget < 1)
  ) {
    throw new RangeError('total_token_budget 必须是大于 0 的整数。')
  }
}

const runRequestBody = (input: MultiAgentRunInput): MultiAgentRunApiRequest => {
  assertRunParameters(input)
  return {
    message: input.message,
    ...(input.supervisorModel ? { supervisor_model: input.supervisorModel } : {}),
    max_subtasks: input.maxSubtasks ?? DEFAULT_MULTI_AGENT_MAX_SUBTASKS,
    max_concurrency: input.maxConcurrency ?? DEFAULT_MULTI_AGENT_MAX_CONCURRENCY,
    failure_policy: input.failurePolicy ?? 'fail_fast',
    total_timeout: input.totalTimeoutSeconds ?? DEFAULT_MULTI_AGENT_TIMEOUT_SECONDS,
    ...(input.totalTokenBudget !== undefined && input.totalTokenBudget !== null
      ? { total_token_budget: input.totalTokenBudget }
      : {}),
  }
}

const safeBackendMessage = (status: number): string => {
  if (status === 401 || status === 403) {
    return '多 Agent 请求未通过鉴权，请检查运行时凭据。'
  }
  if (status === 404) {
    return '多 Agent Run 不存在或无权访问。'
  }
  if (status === 408 || status === 504) {
    return '多 Agent 请求超时，请稍后重试。'
  }
  if (status === 429) {
    return '多 Agent 请求过于频繁，请稍后重试。'
  }
  if (status === 503) {
    return 'Run 历史暂不可用，请稍后重试。'
  }
  if (status >= 500) {
    return '多 Agent 服务暂时不可用，请稍后重试。'
  }
  return `多 Agent 请求失败（HTTP ${status}）。`
}

const getErrorPayload = async (response: Response): Promise<ErrorPayload> => {
  try {
    const value = (await response.json()) as ErrorPayload
    return value
  } catch {
    return {}
  }
}

const authHeaders = (apiKey: string | undefined, accept: string): Record<string, string> => ({
  Accept: accept,
  'Content-Type': 'application/json',
  ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
})

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isNullableString = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

const isNullableBoundedInteger = (value: unknown): value is number | null =>
  value === null || (typeof value === 'number' && Number.isInteger(value) && value >= 0)

const isApiSubtaskResult = (value: unknown): value is MultiAgentSubtaskApiResult => {
  if (!isRecord(value)) return false
  return (
    typeof value.task_id === 'string' &&
    typeof value.status === 'string' &&
    typeof value.output === 'string' &&
    (value.error === undefined || isNullableString(value.error)) &&
    (value.error_code === undefined || isNullableString(value.error_code)) &&
    typeof value.agent_role === 'string' &&
    typeof value.token_usage === 'number' &&
    typeof value.steps_taken === 'number' &&
    (value.duration_ms === undefined || isNullableBoundedInteger(value.duration_ms))
  )
}

const isApiRunResponse = (value: unknown): value is MultiAgentRunApiResponse => {
  if (!isRecord(value)) return false
  return (
    typeof value.run_id === 'string' &&
    typeof value.status === 'string' &&
    typeof value.final_output === 'string' &&
    Array.isArray(value.subtask_results) &&
    value.subtask_results.every(isApiSubtaskResult) &&
    typeof value.total_token_usage === 'number' &&
    (value.error === undefined || isNullableString(value.error)) &&
    (value.error_code === undefined || isNullableString(value.error_code)) &&
    (value.duration_ms === undefined || isNullableBoundedInteger(value.duration_ms))
  )
}

const hasApiHistoryFields = (value: Record<string, unknown>): boolean => {
  return (
    typeof value.run_id === 'string' &&
    (value.request_id === undefined || isNullableString(value.request_id)) &&
    typeof value.api_key_prefix === 'string' &&
    typeof value.api_key_name === 'string' &&
    typeof value.status === 'string' &&
    typeof value.stop_reason === 'string' &&
    (value.started_at === undefined || isNullableString(value.started_at)) &&
    (value.completed_at === undefined || isNullableString(value.completed_at)) &&
    (value.duration_ms === undefined ||
      value.duration_ms === null ||
      typeof value.duration_ms === 'number') &&
    (value.total_tokens === undefined || isNullableBoundedInteger(value.total_tokens))
  )
}

const isApiHistorySummary = (value: unknown): value is MultiAgentRunHistoryApiSummary => {
  if (!isRecord(value)) return false
  return hasApiHistoryFields(value) && typeof value.subtask_count === 'number'
}

const isApiHistoryDetail = (value: unknown): value is MultiAgentRunHistoryApiDetail => {
  if (!isRecord(value)) return false
  const response: unknown = value.response
  return hasApiHistoryFields(value) && isRecord(response)
}

const throwForStatus = async (response: Response): Promise<never> => {
  const payload = await getErrorPayload(response)
  const detail = payload.detail
  throw new MultiAgentBackendError(
    typeof detail === 'string' && detail ? detail : safeBackendMessage(response.status),
    response.status,
    null,
  )
}

export function createMultiAgentClient(options: MultiAgentClientOptions = {}): MultiAgentClient {
  const fetchImpl = options.fetchImpl ?? fetch

  const requestJson = async (
    path: string,
    init: RequestInit,
    signal: AbortSignal,
  ): Promise<unknown> => {
    let response: Response
    try {
      response = await fetchImpl(joinUrl(options.apiBaseUrl, path), {
        ...init,
        signal,
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error
      throw new MultiAgentNetworkError()
    }
    if (!response.ok) await throwForStatus(response)
    try {
      return (await response.json()) as unknown
    } catch {
      throw new MultiAgentResponseError()
    }
  }

  return {
    async runMultiAgent(input, signal) {
      const payload = await requestJson(
        '/api/v1/multi-agent/runs',
        {
          method: 'POST',
          headers: authHeaders(options.apiKey, 'application/json'),
          body: JSON.stringify(runRequestBody(input)),
        },
        signal,
      )
      if (!isApiRunResponse(payload)) throw new MultiAgentResponseError()
      return adaptMultiAgentRunResponse(payload)
    },
    async streamMultiAgent(input, handlers, signal) {
      const body = runRequestBody(input)
      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/multi-agent/runs/stream'), {
          method: 'POST',
          headers: authHeaders(options.apiKey, 'text/event-stream'),
          body: JSON.stringify(body),
          signal,
        })
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        throw new MultiAgentNetworkError()
      }
      if (!response.ok) await throwForStatus(response)
      if (!response.body) throw new MultiAgentStreamFormatError()
      try {
        for await (const event of readMultiAgentSse(response)) {
          if (event.event === 'stream_error') {
            throw new MultiAgentNetworkError(
              '多 Agent 流启动失败，请稍后重试。',
              event.error_code ?? null,
            )
          }
          handlers.onEvent(event)
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error
        if (error instanceof MultiAgentStreamFormatError) throw error
        if (error instanceof MultiAgentNetworkError) throw error
        throw new MultiAgentNetworkError()
      }
    },
    async listRuns(limit = 50, signal) {
      const capped = Math.min(Math.max(Math.floor(limit), 1), 200)
      const payload = await requestJson(
        `/api/v1/multi-agent/runs?limit=${capped}`,
        { method: 'GET', headers: authHeaders(options.apiKey, 'application/json') },
        signal ?? new AbortController().signal,
      )
      if (!Array.isArray(payload) || !payload.every(isApiHistorySummary)) {
        throw new MultiAgentResponseError()
      }
      return payload.map(
        (item): MultiAgentRunSummary => ({
          runId: item.run_id,
          requestId: item.request_id ?? null,
          apiKeyPrefix: item.api_key_prefix,
          apiKeyName: item.api_key_name,
          status: item.status,
          stopReason: item.stop_reason,
          startedAt: item.started_at ?? null,
          completedAt: item.completed_at ?? null,
          durationMs:
            typeof item.duration_ms === 'number' && Number.isFinite(item.duration_ms)
              ? item.duration_ms
              : null,
          totalTokens: item.total_tokens ?? null,
          subtaskCount: item.subtask_count,
        }),
      )
    },
    async getRun(runId, signal) {
      const payload = await requestJson(
        `/api/v1/multi-agent/runs/${encodeURIComponent(runId)}`,
        { method: 'GET', headers: authHeaders(options.apiKey, 'application/json') },
        signal ?? new AbortController().signal,
      )
      if (!isApiHistoryDetail(payload)) throw new MultiAgentResponseError()
      return adaptMultiAgentHistoryDetail(payload)
    },
  }
}
