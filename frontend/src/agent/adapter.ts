import type {
  AgentEventApiSummary,
  AgentRunApiResponse,
  AgentRunApiStatus,
  AgentStepApiSummary,
} from './api-types.ts'
import type {
  AgentRun,
  AgentRunStatus,
  AgentToolCall,
  AgentToolStatus,
  AgentTraceEvent,
  AgentTraceStep,
} from './types.ts'

const KNOWN_TOOLS = new Set(['calculator'])
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
const SENSITIVE_ASSIGNMENT =
  /\b(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*([^\s,;]+)/gi
const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi
const INTERNAL_PATH = /(?:\/(?:Users|home|var|private|opt|srv)\/[^\s:]+|[A-Za-z]:\\[^\s:]+)/g
const STACK_LINE = /^(?:Traceback\b|\s*at\s+|\s*File\s+"|\s*Caused by:)/i

export function sanitizeSummary(value: string, maxLength = 240): string {
  const sanitized = value
    .split(/\r?\n/)
    .filter((line) => !STACK_LINE.test(line))
    .join(' ')
    .replace(SENSITIVE_ASSIGNMENT, '$1=[已隐藏]')
    .replace(BEARER_TOKEN, 'Bearer [已隐藏]')
    .replace(INTERNAL_PATH, '[内部路径已隐藏]')
    .replace(/\s+/g, ' ')
    .trim()

  if (sanitized.length <= maxLength) {
    return sanitized
  }

  return `${sanitized.slice(0, Math.max(0, maxLength - 1))}…`
}

const mapRunStatus = (status: AgentRunApiStatus | null): AgentRunStatus | null => {
  if (status === null) {
    return null
  }

  return status
}

const getStepStatus = (step: AgentStepApiSummary, runStatus: AgentRunStatus): AgentRunStatus => {
  if (step.decision_kind === 'invalid' || step.tool_succeeded === false) {
    return 'failed'
  }
  if (step.tool_succeeded === true || step.decision_kind === 'final_answer') {
    return 'completed'
  }
  if (['failed', 'cancelled', 'timed_out', 'stopped'].includes(runStatus)) {
    return runStatus
  }
  return 'unknown'
}

const getToolStatus = (succeeded: boolean | null, runStatus: AgentRunStatus): AgentToolStatus => {
  if (succeeded === true) {
    return 'succeeded'
  }
  if (succeeded === false) {
    return 'failed'
  }
  if (runStatus === 'timed_out' || runStatus === 'cancelled' || runStatus === 'failed') {
    return runStatus
  }
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

  return {
    errorCode: null,
    errorMessage: messages[status],
  }
}

export function normalizeStopReason(value: string | null): string | null {
  if (value === null) {
    return null
  }

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
  if (step.decision_kind === 'final_answer') {
    return '模型生成最终回答。'
  }
  if (step.decision_kind === 'invalid') {
    return '模型决策格式无效。'
  }
  if (toolNames.length === 0) {
    return '模型决定调用工具，但后端未提供工具名称。'
  }
  return `模型决定调用：${toolNames.join('、')}。`
}

const adaptStep = (
  step: AgentStepApiSummary,
  runStatus: AgentRunStatus,
  events: AgentTraceEvent[],
): AgentTraceStep => {
  const toolStatus = getToolStatus(step.tool_succeeded, runStatus)
  const safeToolNames = step.tool_names.map((name) => sanitizeSummary(name, 80) || 'unknown_tool')
  const toolCalls = safeToolNames.map((name, toolIndex): AgentToolCall => {
    const error = getToolError(toolStatus)
    return {
      id: `step-${step.index}-tool-${toolIndex}`,
      name,
      known: KNOWN_TOOLS.has(name),
      status: toolStatus,
      stepIndex: step.index,
      startedAt: null,
      completedAt: null,
      durationMs: null,
      inputSummary: null,
      outputSummary: null,
      ...error,
    }
  })

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
