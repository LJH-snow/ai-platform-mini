import type { AgentRag, AgentRun, AgentRunStatus, AgentToolCall, AgentTraceStep } from './types.ts'
import type { AgentStreamEvent } from './stream.ts'
import type { AgentRagApiSummary, AgentRagReferenceApiSummary } from './api-types.ts'

export type AgentStreamState = {
  run: AgentRun | null
  terminal: boolean
  lastSequence: number
  requestId: string | null
}

const emptyUsage = {
  promptTokens: null,
  completionTokens: null,
  totalTokens: null,
  estimated: false,
}
const status = (value: string | null | undefined): AgentRunStatus =>
  ['completed', 'stopped', 'failed', 'cancelled', 'timed_out'].includes(value ?? '')
    ? value === 'completed' ||
      value === 'stopped' ||
      value === 'failed' ||
      value === 'cancelled' ||
      value === 'timed_out'
      ? value
      : 'unknown'
    : 'unknown'

const ragStatus = (value: string): AgentRag['status'] => {
  switch (value) {
    case 'loading':
    case 'success_with_sources':
    case 'no_relevant_sources':
    case 'knowledge_base_empty':
    case 'rag_unavailable':
    case 'embedding_failed':
    case 'output_unavailable':
    case 'failed':
      return value
    default:
      return 'failed'
  }
}

const ragReference = (
  value: AgentRagReferenceApiSummary,
): AgentRag['references'][number] | null => {
  const hasIdentifier =
    (typeof value.document_id === 'string' && value.document_id.length > 0) ||
    (typeof value.chunk_id === 'string' && value.chunk_id.length > 0)
  if (!hasIdentifier) return null
  return {
    documentId: typeof value.document_id === 'string' ? value.document_id : null,
    chunkId: typeof value.chunk_id === 'string' ? value.chunk_id : null,
    chunkIndex:
      typeof value.chunk_index === 'number' && Number.isInteger(value.chunk_index)
        ? value.chunk_index
        : null,
    content: typeof value.content === 'string' ? value.content : null,
    distance:
      typeof value.distance === 'number' && Number.isFinite(value.distance) ? value.distance : null,
    truncated: value.truncated === true,
  }
}

const normalizeRag = (value: AgentRagApiSummary): AgentRag => {
  const status = ragStatus(value.status)
  return {
    status,
    warning: typeof value.warning === 'string' ? value.warning : null,
    errorCode: typeof value.error_code === 'string' ? value.error_code : null,
    references:
      status === 'success_with_sources' && Array.isArray(value.references)
        ? value.references.flatMap((reference) => {
            const normalized = ragReference(reference)
            return normalized ? [normalized] : []
          })
        : [],
  }
}

const makeStep = (index: number): AgentTraceStep => ({
  id: `step-${index}-stream`,
  index,
  decisionKind: 'unknown',
  status: 'running',
  startedAt: null,
  completedAt: null,
  durationMs: null,
  toolNames: [],
  summary: '后端正在提供步骤信息。',
  toolCalls: [],
  events: [],
})

const makeRun = (event: AgentStreamEvent, requestId: string | null): AgentRun => ({
  runId: event.run_id,
  status: 'running',
  answer: null,
  stopReason: null,
  steps: [],
  events: [],
  usage: emptyUsage,
  requestId,
  lastSequence: event.sequence,
})

const findStep = (run: AgentRun, index: number): AgentTraceStep => {
  const existing = run.steps.find((step) => step.index === index)
  return existing ?? makeStep(index)
}

const upsertStep = (run: AgentRun, step: AgentTraceStep): AgentRun => ({
  ...run,
  steps: [...run.steps.filter((item) => item.index !== step.index), step].sort(
    (a, b) => a.index - b.index,
  ),
})
const toolStatus = (event: AgentStreamEvent): AgentToolCall['status'] =>
  event.event === 'tool_started' || event.event === 'rag_started'
    ? 'running'
    : event.succeeded === true
      ? 'succeeded'
      : event.succeeded === false
        ? 'failed'
        : 'unknown'

const updateTool = (step: AgentTraceStep, event: AgentStreamEvent): AgentTraceStep => {
  const callId = event.call_id ?? `step-${step.index}-tool-${event.tool_name ?? 'unknown'}`
  const current = step.toolCalls.find((tool) => tool.callId === callId)
  const tool: AgentToolCall = {
    id: current?.id ?? `${step.id}-${callId}`,
    name: event.tool_name ?? current?.name ?? 'unknown_tool',
    callId,
    known:
      (event.tool_name ?? current?.name) === 'calculator' ||
      (event.tool_name ?? current?.name) === 'knowledge_search',
    status: toolStatus(event),
    stepIndex: step.index,
    startedAt: null,
    completedAt: null,
    durationMs: null,
    inputSummary: null,
    outputSummary: null,
    errorCode: event.error_code ?? null,
    errorMessage:
      event.event === 'tool_failed' ? '工具调用未成功。后端未提供可安全展示的错误详情。' : null,
    truncated: null,
    rag: current?.rag ?? null,
  }
  if (event.rag) tool.rag = normalizeRag(event.rag)
  return {
    ...step,
    toolNames: [...new Set([...step.toolNames, tool.name])],
    decisionKind: 'tool_call',
    toolCalls: [...step.toolCalls.filter((item) => item.callId !== callId), tool],
  }
}

export const initialAgentStreamState: AgentStreamState = {
  run: null,
  terminal: false,
  lastSequence: -1,
  requestId: null,
}

export function reduceAgentStream(
  state: AgentStreamState,
  event: AgentStreamEvent,
): AgentStreamState {
  if (state.terminal || event.sequence <= state.lastSequence) return state
  if (state.run && state.run.runId !== event.run_id) return state
  const requestId = event.request_id ?? state.requestId
  let run = state.run ?? makeRun(event, requestId)
  let terminal = false
  if (event.event === 'run_started') run = { ...run, requestId }
  const stepIndex = event.step_index
  if (stepIndex !== null && stepIndex !== undefined) {
    let step = findStep(run, stepIndex)
    if (event.event === 'step_started') step = { ...step, status: 'running' }
    if (event.event === 'step_completed')
      step = { ...step, status: event.status ? status(event.status) : 'completed' }
    if (
      event.event === 'tool_started' ||
      event.event === 'rag_started' ||
      event.event === 'tool_completed' ||
      event.event === 'tool_failed'
    )
      step = updateTool(step, event)
    run = upsertStep(run, step)
  }
  if (event.event === 'assistant_message') run = { ...run, answer: event.answer ?? run.answer }
  if (
    event.event === 'run_completed' ||
    event.event === 'run_failed' ||
    event.event === 'run_timed_out' ||
    event.event === 'run_cancelled' ||
    event.event === 'run_stopped'
  ) {
    terminal = true
    run = {
      ...run,
      status: status(
        event.status ??
          (event.event === 'run_timed_out'
            ? 'timed_out'
            : event.event === 'run_cancelled'
              ? 'cancelled'
              : event.event === 'run_failed'
                ? 'failed'
                : event.event === 'run_completed'
                  ? 'completed'
                  : 'stopped'),
      ),
      stopReason: event.stop_reason ?? null,
    }
  }
  const traceEvent = {
    id: `${event.run_id}:${event.sequence}`,
    kind: event.event,
    stepIndex: stepIndex ?? null,
    status: event.status ? status(event.status) : null,
    stopReason: event.stop_reason ?? null,
    sequence: event.sequence,
  }
  run = { ...run, events: [...run.events, traceEvent], requestId, lastSequence: event.sequence }
  return { run, terminal, lastSequence: event.sequence, requestId }
}

export function mergeSynchronousRun(state: AgentStreamState, response: AgentRun): AgentStreamState {
  return {
    run: response,
    terminal: true,
    lastSequence: response.lastSequence ?? state.lastSequence,
    requestId: response.requestId ?? state.requestId,
  }
}
