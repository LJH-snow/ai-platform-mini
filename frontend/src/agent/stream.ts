import type { AgentRagApiSummary } from './api-types.ts'

export const AGENT_STREAM_EVENTS = [
  'run_started',
  'step_started',
  'step_planned',
  'step_completed',
  'tool_started',
  'rag_started',
  'tool_completed',
  'tool_failed',
  'answer_delta',
  'assistant_message',
  'run_completed',
  'run_failed',
  'run_timed_out',
  'run_cancelled',
  'run_stopped',
  'stream_error',
] as const

export type AgentStreamEventName = (typeof AGENT_STREAM_EVENTS)[number]

export const MAX_AGENT_CUMULATIVE_TOKEN_USAGE = 1_000_000_000

const isAgentStreamEventName = (value: string): value is AgentStreamEventName =>
  (AGENT_STREAM_EVENTS as readonly string[]).includes(value)

export type AgentStreamEvent = {
  event: AgentStreamEventName
  run_id: string
  thread_id?: string | null
  request_id?: string | null
  sequence: number
  occurred_at?: string | null
  step_index?: number | null
  call_id?: string | null
  tool_name?: string | null
  status?: string | null
  stop_reason?: string | null
  answer?: string | null
  delta?: string | null
  succeeded?: boolean | null
  cached?: boolean | null
  decision_kind?: 'final_answer' | 'tool_call' | 'invalid' | null
  tool_names?: string[] | null
  tool_count?: number | null
  summary?: string | null
  argument_count?: number | null
  input_summary?: string | null
  output_summary?: string | null
  result_chars?: number | null
  cumulative_token_usage?: number | null
  error_code?: string | null
  rag?: AgentRagApiSummary | null
  message?: string | null
}

export class AgentStreamFormatError extends Error {
  constructor(message = 'Agent SSE 返回了无法识别的事件。') {
    super(message)
    this.name = 'AgentStreamFormatError'
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isNullableString = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

const isNullableBoolean = (value: unknown): value is boolean | null =>
  typeof value === 'boolean' || value === null

const isNullableTimestamp = (value: unknown): value is string | null =>
  typeof value === 'string' || value === null

const isNullableBoundedInteger = (value: unknown, max: number): value is number | null =>
  value === null ||
  (typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= max)

const isValidToolNames = (value: unknown): value is string[] | null =>
  value === null ||
  (Array.isArray(value) &&
    value.length <= 32 &&
    value.every((name) => typeof name === 'string' && name.length <= 128))

const isSequence = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0

const isValidReference = (value: unknown): boolean => {
  if (!isRecord(value)) return false
  return (
    (value.document_id === undefined || isNullableString(value.document_id)) &&
    (value.chunk_id === undefined || isNullableString(value.chunk_id)) &&
    (value.chunk_index === undefined ||
      value.chunk_index === null ||
      (typeof value.chunk_index === 'number' && Number.isInteger(value.chunk_index))) &&
    (value.content === undefined || isNullableString(value.content)) &&
    (value.distance === undefined ||
      value.distance === null ||
      (typeof value.distance === 'number' && Number.isFinite(value.distance))) &&
    (value.truncated === undefined || isNullableBoolean(value.truncated))
  )
}

const isValidRag = (value: unknown): value is AgentRagApiSummary => {
  if (!isRecord(value)) return false
  return (
    typeof value.status === 'string' &&
    (value.warning === undefined || isNullableString(value.warning)) &&
    (value.error_code === undefined || isNullableString(value.error_code)) &&
    Array.isArray(value.references) &&
    value.references.every(isValidReference)
  )
}

export function parseAgentStreamEvent(eventName: string, data: string): AgentStreamEvent | null {
  if (!isAgentStreamEventName(eventName)) return null
  let value: unknown
  try {
    value = JSON.parse(data)
  } catch {
    throw new AgentStreamFormatError()
  }
  if (!isRecord(value)) throw new AgentStreamFormatError()
  const record = value
  if (eventName === 'stream_error') {
    if (
      (record.run_id !== undefined && typeof record.run_id !== 'string') ||
      (record.sequence !== undefined && !isSequence(record.sequence)) ||
      (record.thread_id !== undefined && !isNullableString(record.thread_id)) ||
      typeof record.error_code !== 'string' ||
      !record.error_code
    ) {
      throw new AgentStreamFormatError()
    }
    return {
      event: 'stream_error',
      run_id: record.run_id ?? '',
      sequence: record.sequence ?? -1,
      error_code: record.error_code,
      ...(typeof record.thread_id === 'string' || record.thread_id === null
        ? { thread_id: record.thread_id }
        : {}),
    }
  }
  if (typeof record.run_id !== 'string' || !record.run_id) {
    throw new AgentStreamFormatError()
  }
  if (!isSequence(record.sequence)) {
    throw new AgentStreamFormatError()
  }
  if (eventName === 'answer_delta' && !isNullableString(record.delta)) {
    throw new AgentStreamFormatError()
  }
  if (
    record.step_index !== undefined &&
    record.step_index !== null &&
    (typeof record.step_index !== 'number' ||
      !Number.isInteger(record.step_index) ||
      record.step_index < 1)
  ) {
    throw new AgentStreamFormatError()
  }
  if (
    (record.thread_id !== undefined && !isNullableString(record.thread_id)) ||
    (record.request_id !== undefined && !isNullableString(record.request_id)) ||
    (record.call_id !== undefined && !isNullableString(record.call_id)) ||
    (record.tool_name !== undefined && !isNullableString(record.tool_name)) ||
    (record.status !== undefined && !isNullableString(record.status)) ||
    (record.stop_reason !== undefined && !isNullableString(record.stop_reason)) ||
    (record.answer !== undefined && !isNullableString(record.answer)) ||
    (record.succeeded !== undefined && !isNullableBoolean(record.succeeded)) ||
    (record.decision_kind !== undefined &&
      record.decision_kind !== null &&
      record.decision_kind !== 'final_answer' &&
      record.decision_kind !== 'tool_call' &&
      record.decision_kind !== 'invalid') ||
    (record.tool_names !== undefined && !isValidToolNames(record.tool_names)) ||
    (record.tool_count !== undefined && !isNullableBoundedInteger(record.tool_count, 32)) ||
    (record.argument_count !== undefined &&
      !isNullableBoundedInteger(record.argument_count, 128)) ||
    (record.result_chars !== undefined && !isNullableBoundedInteger(record.result_chars, 8192)) ||
    (record.summary !== undefined && !isNullableString(record.summary)) ||
    (record.input_summary !== undefined && !isNullableString(record.input_summary)) ||
    (record.output_summary !== undefined && !isNullableString(record.output_summary)) ||
    (record.error_code !== undefined && !isNullableString(record.error_code)) ||
    (record.cumulative_token_usage !== undefined &&
      !isNullableBoundedInteger(record.cumulative_token_usage, MAX_AGENT_CUMULATIVE_TOKEN_USAGE)) ||
    (record.message !== undefined && !isNullableString(record.message)) ||
    (record.rag !== undefined && record.rag !== null && !isValidRag(record.rag))
  ) {
    throw new AgentStreamFormatError()
  }
  return {
    event: eventName,
    run_id: record.run_id,
    sequence: record.sequence,
    ...(typeof record.thread_id === 'string' || record.thread_id === null
      ? { thread_id: record.thread_id }
      : {}),
    ...(typeof record.request_id === 'string' || record.request_id === null
      ? { request_id: record.request_id }
      : {}),
    ...(isNullableTimestamp(record.occurred_at) ? { occurred_at: record.occurred_at } : {}),
    ...(typeof record.step_index === 'number' || record.step_index === null
      ? { step_index: record.step_index }
      : {}),
    ...(typeof record.call_id === 'string' || record.call_id === null
      ? { call_id: record.call_id }
      : {}),
    ...(typeof record.tool_name === 'string' || record.tool_name === null
      ? { tool_name: record.tool_name }
      : {}),
    ...(typeof record.status === 'string' || record.status === null
      ? { status: record.status }
      : {}),
    ...(typeof record.stop_reason === 'string' || record.stop_reason === null
      ? { stop_reason: record.stop_reason }
      : {}),
    ...(typeof record.answer === 'string' || record.answer === null
      ? { answer: record.answer }
      : {}),
    ...(eventName === 'answer_delta' ? { delta: record.delta as string | null } : {}),
    ...(typeof record.succeeded === 'boolean' || record.succeeded === null
      ? { succeeded: record.succeeded }
      : {}),
    ...(typeof record.cached === 'boolean' || record.cached === null
      ? { cached: record.cached }
      : {}),
    ...(record.decision_kind === 'final_answer' ||
    record.decision_kind === 'tool_call' ||
    record.decision_kind === 'invalid' ||
    record.decision_kind === null
      ? { decision_kind: record.decision_kind }
      : {}),
    ...(Array.isArray(record.tool_names) || record.tool_names === null
      ? { tool_names: record.tool_names as string[] | null }
      : {}),
    ...(typeof record.tool_count === 'number' || record.tool_count === null
      ? { tool_count: record.tool_count }
      : {}),
    ...(typeof record.summary === 'string' || record.summary === null
      ? { summary: record.summary }
      : {}),
    ...(typeof record.argument_count === 'number' || record.argument_count === null
      ? { argument_count: record.argument_count }
      : {}),
    ...(typeof record.input_summary === 'string' || record.input_summary === null
      ? { input_summary: record.input_summary }
      : {}),
    ...(typeof record.output_summary === 'string' || record.output_summary === null
      ? { output_summary: record.output_summary }
      : {}),
    ...(typeof record.result_chars === 'number' || record.result_chars === null
      ? { result_chars: record.result_chars }
      : {}),
    ...(typeof record.error_code === 'string' || record.error_code === null
      ? { error_code: record.error_code }
      : {}),
    ...(typeof record.cumulative_token_usage === 'number' || record.cumulative_token_usage === null
      ? { cumulative_token_usage: record.cumulative_token_usage }
      : {}),
    ...(isRecord(record.rag) || record.rag === null ? { rag: record.rag } : {}),
    ...(typeof record.message === 'string' || record.message === null
      ? { message: record.message }
      : {}),
  }
}

export async function* readAgentSse(response: Response): AsyncGenerator<AgentStreamEvent> {
  if (!response.body) throw new AgentStreamFormatError('Agent SSE 响应没有可读取的内容。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  const emit = (): AgentStreamEvent | null => {
    if (!eventName && dataLines.length === 0) return null
    const currentName = eventName
    const data = dataLines.join('\n')
    eventName = ''
    dataLines = []
    if (!currentName || !data) return null
    return parseAgentStreamEvent(currentName, data)
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
