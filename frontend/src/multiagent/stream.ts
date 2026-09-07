import type { MultiAgentStreamSubtask, MultiAgentStreamSubtaskResult } from './api-types.ts'

export const MULTI_AGENT_STREAM_EVENTS = [
  'run_started',
  'subtasks_planned',
  'subtask_started',
  'subtask_completed',
  'subtask_failed',
  'subtask_skipped',
  'subtask_answer_delta',
  'run_completed',
  'run_failed',
  'run_timed_out',
  'run_cancelled',
  'run_budget_exceeded',
  'stream_error',
] as const

export type MultiAgentStreamEventName = (typeof MULTI_AGENT_STREAM_EVENTS)[number]

export const MAX_MULTI_AGENT_TOKEN_USAGE = 1_000_000_000
const MAX_SUBTASKS_PER_EVENT = 16
const MAX_DEPENDS_PER_SUBTASK = 16

const isMultiAgentStreamEventName = (value: string): value is MultiAgentStreamEventName =>
  (MULTI_AGENT_STREAM_EVENTS as readonly string[]).includes(value)

export type MultiAgentStreamEvent = {
  event: MultiAgentStreamEventName
  run_id: string
  request_id?: string | null
  sequence: number
  occurred_at?: string | null
  task_id?: string | null
  agent_role?: string | null
  duration_ms?: number | null
  token_usage?: number | null
  output_summary?: string | null
  error_code?: string | null
  subtasks?: MultiAgentStreamSubtask[] | null
  reasoning?: string | null
  final_output?: string | null
  total_token_usage?: number | null
  subtask_results?: MultiAgentStreamSubtaskResult[] | null
  agent_event_kind?: string | null
  step_index?: number | null
}

export class MultiAgentStreamFormatError extends Error {
  constructor(message = '多 Agent SSE 返回了无法识别的事件。') {
    super(message)
    this.name = 'MultiAgentStreamFormatError'
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isNullableString = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

const isOptionalNullableString = (value: unknown): value is string | null | undefined =>
  value === undefined || isNullableString(value)

const isSequence = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0

const isNullableBoundedInteger = (value: unknown, max: number): value is number | null =>
  value === null ||
  (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= max)

const isOptionalNullableBoundedInteger = (
  value: unknown,
  max: number,
): value is number | null | undefined => value === undefined || isNullableBoundedInteger(value, max)

const isValidDependsOn = (value: unknown): value is string[] | null | undefined =>
  value === undefined ||
  value === null ||
  (Array.isArray(value) &&
    value.length <= MAX_DEPENDS_PER_SUBTASK &&
    value.every((item) => typeof item === 'string'))

const isValidSubtask = (value: unknown): value is MultiAgentStreamSubtask => {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    value.id.length > 0 &&
    typeof value.agent_role === 'string' &&
    (value.description === undefined || isNullableString(value.description)) &&
    isValidDependsOn(value.depends_on)
  )
}

const isValidSubtasks = (value: unknown): value is MultiAgentStreamSubtask[] | null => {
  if (value === null) return true
  return (
    Array.isArray(value) && value.length <= MAX_SUBTASKS_PER_EVENT && value.every(isValidSubtask)
  )
}

const isValidSubtaskResult = (value: unknown): value is MultiAgentStreamSubtaskResult => {
  if (!isRecord(value)) return false
  return (
    typeof value.task_id === 'string' &&
    value.task_id.length > 0 &&
    typeof value.status === 'string' &&
    (value.agent_role === undefined || isNullableString(value.agent_role)) &&
    (value.output === undefined || isNullableString(value.output)) &&
    (value.error_code === undefined || isNullableString(value.error_code)) &&
    isOptionalNullableBoundedInteger(value.token_usage, MAX_MULTI_AGENT_TOKEN_USAGE) &&
    isOptionalNullableBoundedInteger(value.duration_ms, MAX_MULTI_AGENT_TOKEN_USAGE)
  )
}

const isValidSubtaskResults = (value: unknown): value is MultiAgentStreamSubtaskResult[] | null => {
  if (value === null) return true
  return (
    Array.isArray(value) &&
    value.length <= MAX_SUBTASKS_PER_EVENT &&
    value.every(isValidSubtaskResult)
  )
}

export function parseMultiAgentStreamEvent(
  eventName: string,
  data: string,
): MultiAgentStreamEvent | null {
  if (!isMultiAgentStreamEventName(eventName)) return null
  let value: unknown
  try {
    value = JSON.parse(data)
  } catch {
    throw new MultiAgentStreamFormatError()
  }
  if (!isRecord(value)) throw new MultiAgentStreamFormatError()
  const record = value
  if (eventName === 'stream_error') {
    if (
      (record.run_id !== undefined && typeof record.run_id !== 'string') ||
      (record.sequence !== undefined && !isSequence(record.sequence)) ||
      typeof record.error_code !== 'string' ||
      !record.error_code
    ) {
      throw new MultiAgentStreamFormatError()
    }
    return {
      event: 'stream_error',
      run_id: typeof record.run_id === 'string' ? record.run_id : '',
      sequence: isSequence(record.sequence) ? record.sequence : -1,
      error_code: record.error_code,
      ...(typeof record.request_id === 'string' || record.request_id === null
        ? { request_id: record.request_id }
        : {}),
    }
  }
  if (typeof record.run_id !== 'string' || !record.run_id) {
    throw new MultiAgentStreamFormatError()
  }
  if (!isSequence(record.sequence)) throw new MultiAgentStreamFormatError()
  if (
    (record.task_id !== undefined && !isNullableString(record.task_id)) ||
    (record.agent_role !== undefined && !isNullableString(record.agent_role)) ||
    !isOptionalNullableBoundedInteger(record.duration_ms, MAX_MULTI_AGENT_TOKEN_USAGE) ||
    !isOptionalNullableBoundedInteger(record.token_usage, MAX_MULTI_AGENT_TOKEN_USAGE) ||
    (record.output_summary !== undefined && !isNullableString(record.output_summary)) ||
    (record.error_code !== undefined && !isNullableString(record.error_code)) ||
    (record.reasoning !== undefined && !isNullableString(record.reasoning)) ||
    (record.final_output !== undefined && !isNullableString(record.final_output)) ||
    !isOptionalNullableBoundedInteger(record.total_token_usage, MAX_MULTI_AGENT_TOKEN_USAGE) ||
    (record.agent_event_kind !== undefined && !isNullableString(record.agent_event_kind)) ||
    !isOptionalNullableBoundedInteger(record.step_index, MAX_MULTI_AGENT_TOKEN_USAGE) ||
    (record.subtasks !== undefined && !isValidSubtasks(record.subtasks)) ||
    (record.subtask_results !== undefined && !isValidSubtaskResults(record.subtask_results))
  ) {
    throw new MultiAgentStreamFormatError()
  }
  return {
    event: eventName,
    run_id: record.run_id,
    sequence: record.sequence,
    ...(isOptionalNullableString(record.request_id) ? { request_id: record.request_id } : {}),
    ...(isOptionalNullableString(record.occurred_at) ? { occurred_at: record.occurred_at } : {}),
    ...(isOptionalNullableString(record.task_id) ? { task_id: record.task_id } : {}),
    ...(isOptionalNullableString(record.agent_role) ? { agent_role: record.agent_role } : {}),
    ...(isOptionalNullableBoundedInteger(record.duration_ms, MAX_MULTI_AGENT_TOKEN_USAGE)
      ? { duration_ms: record.duration_ms }
      : {}),
    ...(isOptionalNullableBoundedInteger(record.token_usage, MAX_MULTI_AGENT_TOKEN_USAGE)
      ? { token_usage: record.token_usage }
      : {}),
    ...(isOptionalNullableString(record.output_summary)
      ? { output_summary: record.output_summary }
      : {}),
    ...(isOptionalNullableString(record.error_code) ? { error_code: record.error_code } : {}),
    ...(record.subtasks !== undefined && isValidSubtasks(record.subtasks)
      ? { subtasks: record.subtasks }
      : {}),
    ...(isOptionalNullableString(record.reasoning) ? { reasoning: record.reasoning } : {}),
    ...(isOptionalNullableString(record.final_output) ? { final_output: record.final_output } : {}),
    ...(isOptionalNullableBoundedInteger(record.total_token_usage, MAX_MULTI_AGENT_TOKEN_USAGE)
      ? { total_token_usage: record.total_token_usage }
      : {}),
    ...(isOptionalNullableString(record.agent_event_kind)
      ? { agent_event_kind: record.agent_event_kind }
      : {}),
    ...(isOptionalNullableBoundedInteger(record.step_index, MAX_MULTI_AGENT_TOKEN_USAGE)
      ? { step_index: record.step_index }
      : {}),
    ...(record.subtask_results !== undefined && isValidSubtaskResults(record.subtask_results)
      ? { subtask_results: record.subtask_results }
      : {}),
  }
}

export async function* readMultiAgentSse(
  response: Response,
): AsyncGenerator<MultiAgentStreamEvent> {
  if (!response.body) throw new MultiAgentStreamFormatError('多 Agent SSE 响应没有可读取的内容。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  const emit = (): MultiAgentStreamEvent | null => {
    if (!eventName && dataLines.length === 0) return null
    const currentName = eventName
    const data = dataLines.join('\n')
    eventName = ''
    dataLines = []
    if (!currentName || !data) return null
    return parseMultiAgentStreamEvent(currentName, data)
  }

  while (true) {
    const chunk = await reader.read()
    buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        const event = emit()
        if (event) yield event
      } else if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }
    if (chunk.done) break
  }
  if (buffer) {
    if (buffer.startsWith('data:')) dataLines.push(buffer.slice(5).trimStart())
    else if (buffer.startsWith('event:')) eventName = buffer.slice(6).trim()
  }
  const event = emit()
  if (event) yield event
}
