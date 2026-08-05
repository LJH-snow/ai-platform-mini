import type {
  AgentEventApiSummary,
  AgentRagApiErrorCode,
  AgentRagApiStatus,
  AgentRagApiSummary,
  AgentRagReferenceApiSummary,
  AgentRunApiResponse,
  AgentRunApiStatus,
  AgentStepApiSummary,
  AgentToolApiErrorCode,
  AgentToolCallApiSummary,
} from './api-types.ts'
import type {
  AgentRag,
  AgentRagErrorCode,
  AgentRagReference,
  AgentRagStatus,
  AgentRun,
  AgentRunStatus,
  AgentToolCall,
  AgentToolStatus,
  AgentTraceEvent,
  AgentTraceStep,
} from './types.ts'

const KNOWN_TOOLS = new Set(['calculator', 'knowledge_search'])
const SAFE_STOP_REASONS = new Set([
  'direct_answer',
  'max_steps',
  'deadline_exceeded',
  'external_cancelled',
  'model_error',
  'invalid_decision',
  'token_budget_exceeded',
  'quota_exceeded',
])
const RAG_STATUSES = new Set<AgentRagApiStatus>([
  'success_with_sources',
  'no_relevant_sources',
  'knowledge_base_empty',
  'rag_unavailable',
  'embedding_failed',
  'output_unavailable',
  'failed',
])
const RAG_ERROR_CODES = new Set<AgentRagApiErrorCode>([
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
const TOOL_ERROR_CODES = new Set<AgentToolApiErrorCode>([
  'invalid_tool_arguments',
  'tool_permission_denied',
  'tool_timeout',
  'tool_output_too_large',
  'tool_not_found',
  'tool_execution_failed',
])
const SENSITIVE_ASSIGNMENT =
  /\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*([^\s,;]+)/gi
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi
const INTERNAL_PATH = /(?:\/(?:Users|home|var|private|opt|srv)\/[^\s:]+|[A-Za-z]:\\[^\s:]+)/g
const STACK_LINE = /^(?:Traceback\b|\s*at\s+|\s*File\s+"|\s*Caused by:)/i
const MAX_RAG_CONTENT_LENGTH = 1200
const MAX_RAG_IDENTIFIER_LENGTH = 256

const sanitizeText = (value: string): string =>
  value
    .split(/\r?\n/)
    .filter((line) => !STACK_LINE.test(line))
    .join(' ')
    .replace(SENSITIVE_ASSIGNMENT, '$1=[已隐藏]')
    .replace(BEARER_TOKEN, 'Bearer [已隐藏]')
    .replace(INTERNAL_PATH, '[内部路径已隐藏]')
    .replace(/\s+/g, ' ')
    .trim()

export function sanitizeSummary(value: string, maxLength = 240): string {
  const sanitized = sanitizeText(value)
  if (sanitized.length <= maxLength) return sanitized
  return `${sanitized.slice(0, Math.max(0, maxLength - 1))}…`
}

type SafeString = {
  value: string | null
  truncated: boolean
}

const safeString = (value: unknown, maxLength: number): string | null =>
  typeof value === 'string' ? sanitizeSummary(value, maxLength) || null : null

const safeRagString = (value: unknown, maxLength: number): SafeString => {
  if (typeof value !== 'string') return { value: null, truncated: false }
  const sanitized = sanitizeText(value)
  if (!sanitized) return { value: null, truncated: false }
  if (sanitized.length <= maxLength) return { value: sanitized, truncated: false }
  return { value: `${sanitized.slice(0, Math.max(0, maxLength - 1))}…`, truncated: true }
}

const safeNullableInteger = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 1_000_000
    ? value
    : null

const safeNullableDistance = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 2 ? value : null

const normalizeRagStatus = (value: AgentRagApiStatus): AgentRagStatus =>
  RAG_STATUSES.has(value) ? value : 'failed'

const normalizeRagErrorCode = (
  value: AgentRagApiErrorCode | null | undefined,
): AgentRagErrorCode | null =>
  value !== null && value !== undefined && RAG_ERROR_CODES.has(value) ? value : null

const normalizeToolErrorCode = (value: AgentToolApiErrorCode | null): string | null =>
  value !== null && TOOL_ERROR_CODES.has(value) ? value : null

const adaptRagReference = (reference: AgentRagReferenceApiSummary): AgentRagReference | null => {
  const documentId = safeRagString(reference.document_id, MAX_RAG_IDENTIFIER_LENGTH).value
  const chunkId = safeRagString(reference.chunk_id, MAX_RAG_IDENTIFIER_LENGTH).value
  const chunkIndex = safeNullableInteger(reference.chunk_index)
  const safeContent = safeRagString(reference.content, MAX_RAG_CONTENT_LENGTH)
  const distance = safeNullableDistance(reference.distance)
  const hasStableIdentifier = documentId !== null || chunkId !== null
  if (!hasStableIdentifier) return null

  return {
    documentId,
    chunkId,
    chunkIndex,
    content: safeContent.value,
    distance,
    truncated: reference.truncated === true || safeContent.truncated,
  }
}

const adaptRag = (rag: AgentRagApiSummary | null | undefined): AgentRag | null => {
  if (!rag) return null
  const status = normalizeRagStatus(rag.status)
  return {
    status,
    warning: safeString(rag.warning, 256),
    errorCode: normalizeRagErrorCode(rag.error_code),
    references:
      status === 'success_with_sources' && Array.isArray(rag.references)
        ? rag.references.flatMap((reference) => {
            const adapted = adaptRagReference(reference)
            return adapted ? [adapted] : []
          })
        : [],
  }
}

const mapRunStatus = (status: AgentRunApiStatus | null): AgentRunStatus | null => status

const getStepStatus = (step: AgentStepApiSummary, runStatus: AgentRunStatus): AgentRunStatus => {
  if (step.decision_kind === 'invalid' || step.tool_succeeded === false) return 'failed'
  if (step.tool_succeeded === true || step.decision_kind === 'final_answer') return 'completed'
  if (['failed', 'cancelled', 'timed_out', 'stopped'].includes(runStatus)) return runStatus
  return 'unknown'
}

const getToolStatus = (succeeded: boolean | null, runStatus: AgentRunStatus): AgentToolStatus => {
  if (succeeded === true) return 'succeeded'
  if (succeeded === false) return 'failed'
  if (runStatus === 'timed_out' || runStatus === 'cancelled' || runStatus === 'failed')
    return runStatus
  return 'unknown'
}

const getToolError = (
  status: AgentToolStatus,
): Pick<AgentToolCall, 'errorCode' | 'errorMessage'> => {
  if (status === 'succeeded' || status === 'running' || status === 'unknown') {
    return { errorCode: null, errorMessage: null }
  }
  const messages: Record<'failed' | 'timed_out' | 'cancelled', string> = {
    failed: '工具调用未成功。后端未提供可安全展示的错误详情。',
    timed_out: '工具调用因运行超时而未完成。',
    cancelled: '工具调用因运行取消而未完成。',
  }
  return { errorCode: null, errorMessage: messages[status] }
}

export function normalizeStopReason(value: string | null): string | null {
  if (value === null) return null
  const normalized = sanitizeSummary(value, 80).toLowerCase()
  return SAFE_STOP_REASONS.has(normalized) ? normalized : 'unknown'
}

const adaptEvent = (event: AgentEventApiSummary): AgentTraceEvent => {
  const status = mapRunStatus(event.status)
  const kind = sanitizeSummary(event.kind, 80) || 'unknown_event'
  const stopReason = normalizeStopReason(event.stop_reason)
  return {
    id: [kind, event.step_index ?? 'run', status ?? '', stopReason ?? ''].join(':'),
    kind,
    stepIndex: event.step_index,
    status,
    stopReason,
  }
}

const deduplicateEvents = (events: AgentEventApiSummary[]): AgentTraceEvent[] => {
  const seen = new Set<string>()
  const result: AgentTraceEvent[] = []
  for (const event of events) {
    const adapted = adaptEvent(event)
    if (!seen.has(adapted.id)) {
      seen.add(adapted.id)
      result.push(adapted)
    }
  }
  return result
}

const getStepSummary = (step: AgentStepApiSummary, toolNames: string[]): string => {
  if (step.decision_kind === 'final_answer') return '模型生成最终回答。'
  if (step.decision_kind === 'invalid') return '模型决策格式无效。'
  if (toolNames.length === 0) return '模型决定调用工具，但后端未提供工具名称。'
  return `模型决定调用：${toolNames.join('、')}。`
}

const adaptToolCall = (
  call: AgentToolCallApiSummary,
  stepIndex: number,
  toolIndex: number,
  runStatus: AgentRunStatus,
): AgentToolCall => {
  const name = safeString(call.name, 80) || 'unknown_tool'
  const status = getToolStatus(call.succeeded, runStatus)
  const fallbackError = getToolError(status)
  const errorMessage =
    status === 'succeeded' || status === 'running' || status === 'unknown'
      ? null
      : safeString(call.error_message, 240) || fallbackError.errorMessage
  return {
    id: `step-${stepIndex}-tool-${toolIndex}`,
    name,
    callId: safeString(call.call_id, 128),
    known: KNOWN_TOOLS.has(name),
    status,
    stepIndex,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    inputSummary: null,
    outputSummary: null,
    errorCode: status === 'succeeded' ? null : normalizeToolErrorCode(call.error_code),
    errorMessage,
    truncated: typeof call.truncated === 'boolean' ? call.truncated : null,
    rag: name === 'knowledge_search' ? adaptRag(call.rag) : null,
  }
}

const adaptLegacyToolCall = (
  name: string,
  stepIndex: number,
  toolIndex: number,
  runStatus: AgentRunStatus,
  succeeded: boolean | null,
): AgentToolCall => {
  const safeName = sanitizeSummary(name, 80) || 'unknown_tool'
  const status = getToolStatus(succeeded, runStatus)
  return {
    id: `step-${stepIndex}-tool-${toolIndex}`,
    name: safeName,
    callId: null,
    known: KNOWN_TOOLS.has(safeName),
    status,
    stepIndex,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    inputSummary: null,
    outputSummary: null,
    ...getToolError(status),
    truncated: null,
    rag: null,
  }
}

const adaptStep = (
  step: AgentStepApiSummary,
  runStatus: AgentRunStatus,
  events: AgentTraceEvent[],
): AgentTraceStep => {
  const hasRealToolCalls = Array.isArray(step.tool_calls) && step.tool_calls.length > 0
  const toolCalls = hasRealToolCalls
    ? step.tool_calls!.map((call, toolIndex) =>
        adaptToolCall(call, step.index, toolIndex, runStatus),
      )
    : step.tool_names.map((name, toolIndex) =>
        adaptLegacyToolCall(name, step.index, toolIndex, runStatus, step.tool_succeeded),
      )
  const safeToolNames = toolCalls.map((tool) => tool.name)
  return {
    id: `step-${step.index}-${step.decision_kind}`,
    index: step.index,
    decisionKind: step.decision_kind,
    status: getStepStatus(step, runStatus),
    startedAt: null,
    completedAt: null,
    durationMs: null,
    toolNames: safeToolNames,
    summary: getStepSummary(step, safeToolNames),
    toolCalls,
    events: events.filter((event) => event.stepIndex === step.index),
  }
}

export function adaptAgentRunResponse(response: AgentRunApiResponse): AgentRun {
  const status = mapRunStatus(response.status) ?? 'unknown'
  const events = deduplicateEvents(response.events)
  const seenStepIndexes = new Set<number>()
  const steps = response.steps
    .map((step, originalIndex) => ({ step, originalIndex }))
    .filter(({ step }) => {
      if (seenStepIndexes.has(step.index)) return false
      seenStepIndexes.add(step.index)
      return true
    })
    .sort(
      (left, right) =>
        left.step.index - right.step.index || left.originalIndex - right.originalIndex,
    )
    .map(({ step }) => adaptStep(step, status, events))

  return {
    runId: response.run_id || null,
    status,
    answer: response.answer,
    stopReason: normalizeStopReason(response.stop_reason),
    steps,
    events,
    usage: {
      promptTokens: response.usage.prompt_tokens,
      completionTokens: response.usage.completion_tokens,
      totalTokens: response.usage.total_tokens,
      estimated: response.usage.estimated,
    },
  }
}
