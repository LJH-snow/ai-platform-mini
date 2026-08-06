import type { AgentRag, AgentRun, AgentRunStatus, AgentToolCall, AgentTraceStep } from './types.ts'
import { localizeStepSummary } from './adapter.ts'
import { isKnownTool } from './tool-name.ts'
import type { AgentStreamEvent } from './stream.ts'
import type { AgentRagApiSummary, AgentRagReferenceApiSummary } from './api-types.ts'

export type AgentStreamState = {
  run: AgentRun | null
  terminal: boolean
  lastSequence: number
  requestId: string | null
  answerDeltaSeen: boolean
}

const safeTimestamp = (value: string | null | undefined): string | null =>
  typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null

const durationMs = (startedAt: string | null, completedAt: string | null): number | null => {
  if (startedAt === null || completedAt === null) return null
  return Math.max(0, Date.parse(completedAt) - Date.parse(startedAt))
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
  toolCount: null,
  summary: '模型正在分析任务，判断是否需要调用工具。',
  toolCalls: [],
  events: [],
})

const makeRun = (event: AgentStreamEvent, requestId: string | null): AgentRun => ({
  runId: event.run_id,
  threadId: event.thread_id ?? null,
  status: 'running',
  answer: null,
  stopReason: null,
  startedAt: safeTimestamp(event.occurred_at),
  completedAt: null,
  durationMs: null,
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

const decisionKind = (value: AgentStreamEvent['decision_kind']): AgentTraceStep['decisionKind'] =>
  value === 'final_answer' || value === 'tool_call' || value === 'invalid' ? value : 'unknown'

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
    known: isKnownTool(event.tool_name ?? current?.name ?? ''),
    status: toolStatus(event),
    stepIndex: step.index,
    startedAt:
      event.event === 'tool_started' || event.event === 'rag_started'
        ? (safeTimestamp(event.occurred_at) ?? current?.startedAt ?? null)
        : (current?.startedAt ?? null),
    completedAt:
      event.event === 'tool_completed' || event.event === 'tool_failed'
        ? safeTimestamp(event.occurred_at)
        : (current?.completedAt ?? null),
    durationMs:
      event.event === 'tool_completed' || event.event === 'tool_failed'
        ? durationMs(current?.startedAt ?? null, safeTimestamp(event.occurred_at))
        : (current?.durationMs ?? null),
    argumentCount: event.argument_count ?? current?.argumentCount ?? null,
    inputSummary: event.input_summary ?? current?.inputSummary ?? null,
    outputSummary: event.output_summary ?? current?.outputSummary ?? null,
    resultChars: event.result_chars ?? current?.resultChars ?? null,
    errorCode: event.error_code ?? current?.errorCode ?? null,
    errorMessage:
      event.event === 'tool_failed'
        ? '工具调用未成功。后端未提供可安全展示的错误详情。'
        : (current?.errorMessage ?? null),
    truncated: current?.truncated ?? null,
    cached: event.cached === true || current?.cached === true,
    rag: current?.rag ?? null,
  }
  if (event.rag) tool.rag = normalizeRag(event.rag)
  return {
    ...step,
    toolNames: [...new Set([...step.toolNames, tool.name])],
    toolCount: event.tool_count ?? step.toolCount,
    decisionKind: 'tool_call',
    toolCalls: [...step.toolCalls.filter((item) => item.callId !== callId), tool],
  }
}

export const initialAgentStreamState: AgentStreamState = {
  run: null,
  terminal: false,
  lastSequence: -1,
  requestId: null,
  answerDeltaSeen: false,
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
  let answerDeltaSeen = state.answerDeltaSeen
  if (event.thread_id !== undefined && event.thread_id !== null) {
    run = { ...run, threadId: event.thread_id }
  }
  if (event.event === 'run_started')
    run = { ...run, requestId, startedAt: safeTimestamp(event.occurred_at) ?? run.startedAt }

  const stepIndex = event.step_index
  if (stepIndex !== null && stepIndex !== undefined) {
    let step = findStep(run, stepIndex)
    if (event.event === 'step_started')
      step = {
        ...step,
        status: 'running',
        startedAt: safeTimestamp(event.occurred_at) ?? step.startedAt,
      }
    if (event.event === 'step_planned')
      step = {
        ...step,
        status: 'running',
        decisionKind: decisionKind(event.decision_kind),
        toolNames: event.tool_names ?? step.toolNames,
        toolCount: event.tool_count ?? step.toolCount,
        summary:
          localizeStepSummary(
            event.decision_kind,
            event.tool_names ?? step.toolNames,
            event.tool_count ?? step.toolCount,
          ) ??
          event.summary ??
          step.summary,
      }
    if (event.event === 'step_completed') {
      const completedAt = safeTimestamp(event.occurred_at)
      step = {
        ...step,
        status: event.status ? status(event.status) : 'completed',
        completedAt,
        durationMs: durationMs(step.startedAt, completedAt),
      }
    }
    if (
      event.event === 'tool_started' ||
      event.event === 'rag_started' ||
      event.event === 'tool_completed' ||
      event.event === 'tool_failed'
    )
      step = updateTool(step, event)
    run = upsertStep(run, step)
  }
  if (event.event === 'answer_delta') {
    if (event.delta) {
      answerDeltaSeen = true
      run = {
        ...run,
        answer: state.answerDeltaSeen ? `${run.answer ?? ''}${event.delta}` : event.delta,
      }
    }
  }
  if (event.event === 'assistant_message' && !answerDeltaSeen)
    run = { ...run, answer: event.answer ?? run.answer }
  if (event.cumulative_token_usage !== undefined && event.cumulative_token_usage !== null) {
    run = {
      ...run,
      usage: { ...run.usage, totalTokens: event.cumulative_token_usage },
    }
  }
  if (
    event.event === 'run_completed' ||
    event.event === 'run_failed' ||
    event.event === 'run_timed_out' ||
    event.event === 'run_cancelled' ||
    event.event === 'run_stopped'
  ) {
    terminal = true
    const completedAt = safeTimestamp(event.occurred_at)
    run = {
      ...run,
      completedAt,
      durationMs: durationMs(run.startedAt ?? null, completedAt),
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
    if (
      event.event === 'run_completed' &&
      run.answer === null &&
      !answerDeltaSeen &&
      typeof event.answer === 'string' &&
      event.answer.length > 0
    ) {
      run = { ...run, answer: event.answer }
    }
  }
  const traceEvent = {
    id: `${event.run_id}:${event.sequence}`,
    kind: event.event,
    stepIndex: stepIndex ?? null,
    status: event.status ? status(event.status) : null,
    stopReason: event.stop_reason ?? null,
    sequence: event.sequence,
    occurredAt: safeTimestamp(event.occurred_at),
    decisionKind: event.decision_kind ? decisionKind(event.decision_kind) : null,
    toolNames: event.tool_names ?? [],
    toolCount: event.tool_count ?? null,
    summary: event.summary ?? null,
    argumentCount: event.argument_count ?? null,
    inputSummary: event.input_summary ?? null,
    outputSummary: event.output_summary ?? null,
    resultChars: event.result_chars ?? null,
  }
  run = { ...run, events: [...run.events, traceEvent], requestId, lastSequence: event.sequence }
  if (stepIndex !== null && stepIndex !== undefined) {
    const currentStep = findStep(run, stepIndex)
    run = upsertStep(run, { ...currentStep, events: [...currentStep.events, traceEvent] })
  }
  return { run, terminal, lastSequence: event.sequence, requestId, answerDeltaSeen }
}

export function mergeSynchronousRun(state: AgentStreamState, response: AgentRun): AgentStreamState {
  return {
    run: response,
    terminal: true,
    lastSequence: response.lastSequence ?? state.lastSequence,
    requestId: response.requestId ?? state.requestId,
    answerDeltaSeen: false,
  }
}
