import type { MultiAgentStreamSubtask } from './api-types.ts'
import type { MultiAgentStreamEvent } from './stream.ts'
import type {
  MultiAgentRun,
  MultiAgentRunStatus,
  MultiAgentStepTrace,
  MultiAgentSubtask,
  MultiAgentSubtaskStatus,
} from './types.ts'

export type MultiAgentStreamState = {
  run: MultiAgentRun | null
  terminal: boolean
  lastSequence: number
  requestId: string | null
}

export const initialMultiAgentStreamState: MultiAgentStreamState = {
  run: null,
  terminal: false,
  lastSequence: -1,
  requestId: null,
}

const safeTimestamp = (value: string | null | undefined): string | null =>
  typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null

const terminalStatus = (event: MultiAgentStreamEvent): MultiAgentRunStatus => {
  switch (event.event) {
    case 'run_completed':
      return 'completed'
    case 'run_failed':
      return 'failed'
    case 'run_timed_out':
      return 'timed_out'
    case 'run_cancelled':
      return 'cancelled'
    case 'run_budget_exceeded':
      return 'budget_exceeded'
    default:
      return 'unknown'
  }
}

const subtaskStatusForEvent = (event: MultiAgentStreamEvent): MultiAgentSubtaskStatus => {
  switch (event.event) {
    case 'subtask_started':
      return 'running'
    case 'subtask_completed':
      return 'completed'
    case 'subtask_failed':
      return 'failed'
    case 'subtask_skipped':
      return 'skipped'
    default:
      return 'unknown'
  }
}

const makeRun = (event: MultiAgentStreamEvent, requestId: string | null): MultiAgentRun => ({
  runId: event.run_id,
  status: 'running',
  finalOutput: null,
  reasoning: null,
  subtasks: [],
  totalTokenUsage: null,
  durationMs: null,
  errorCode: null,
  requestId,
  lastSequence: event.sequence,
  startedAt: safeTimestamp(event.occurred_at),
  completedAt: null,
})

const plannedSubtask = (item: MultiAgentStreamSubtask): MultiAgentSubtask => ({
  id: item.id,
  agentRole: item.agent_role,
  description: typeof item.description === 'string' ? item.description : '',
  dependsOn: Array.isArray(item.depends_on) ? item.depends_on : [],
  status: 'pending',
  outputSummary: null,
  errorCode: null,
  tokenUsage: null,
  durationMs: null,
  answerDeltas: [],
  steps: [],
  tools: [],
})

const upsertSubtask = (run: MultiAgentRun, subtask: MultiAgentSubtask): MultiAgentRun => {
  if (!run.subtasks.some((item) => item.id === subtask.id)) {
    return { ...run, subtasks: [...run.subtasks, subtask] }
  }
  return {
    ...run,
    subtasks: run.subtasks.map((item) => (item.id === subtask.id ? { ...item, ...subtask } : item)),
  }
}

export function reduceMultiAgentStream(
  state: MultiAgentStreamState,
  event: MultiAgentStreamEvent,
): MultiAgentStreamState {
  if (state.terminal || event.sequence <= state.lastSequence) return state
  if (state.run && state.run.runId !== event.run_id) return state
  const requestId = event.request_id ?? state.requestId
  let run = state.run ?? makeRun(event, requestId)
  let terminal = false
  run = { ...run, requestId, lastSequence: event.sequence }

  if (event.event === 'subtasks_planned') {
    const planned = Array.isArray(event.subtasks) ? event.subtasks.map(plannedSubtask) : []
    run = {
      ...run,
      reasoning: typeof event.reasoning === 'string' ? event.reasoning : run.reasoning,
      subtasks: planned,
    }
  }

  if (
    event.event === 'subtask_started' ||
    event.event === 'subtask_completed' ||
    event.event === 'subtask_failed' ||
    event.event === 'subtask_skipped'
  ) {
    if (typeof event.task_id === 'string' && event.task_id) {
      const status = subtaskStatusForEvent(event)
      const current = run.subtasks.find((item) => item.id === event.task_id)
      run = upsertSubtask(run, {
        id: event.task_id,
        agentRole:
          typeof event.agent_role === 'string'
            ? event.agent_role
            : (current?.agentRole ?? 'custom'),
        description: current?.description ?? '',
        dependsOn: current?.dependsOn ?? [],
        status,
        outputSummary: typeof event.output_summary === 'string' ? event.output_summary : null,
        errorCode: typeof event.error_code === 'string' ? event.error_code : null,
        tokenUsage: typeof event.token_usage === 'number' ? event.token_usage : null,
        durationMs: typeof event.duration_ms === 'number' ? event.duration_ms : null,
        answerDeltas: current?.answerDeltas ?? [],
        steps: current?.steps ?? [],
        tools: current?.tools ?? [],
      })
    }
  }

  if (event.event === 'subtask_step_started' || event.event === 'subtask_step_completed') {
    if (typeof event.task_id === 'string' && event.task_id && event.step_index != null) {
      const status = event.event === 'subtask_step_started' ? 'started' : 'completed'
      run = {
        ...run,
        subtasks: run.subtasks.map((item) => {
          if (item.id !== event.task_id) return item
          const existing = item.steps.findIndex((step) => step.index === event.step_index)
          const step: MultiAgentStepTrace = {
            index: event.step_index as number,
            status,
            outputSummary: typeof event.output_summary === 'string' ? event.output_summary : null,
          }
          const steps =
            existing === -1
              ? [...item.steps, step]
              : item.steps.map((entry) => (entry.index === event.step_index ? step : entry))
          return { ...item, steps }
        }),
      }
    }
  }

  if (
    event.event === 'subtask_tool_started' ||
    event.event === 'subtask_tool_completed' ||
    event.event === 'subtask_tool_failed'
  ) {
    if (typeof event.task_id === 'string' && event.task_id && event.tool_name) {
      const status =
        event.event === 'subtask_tool_started'
          ? 'started'
          : event.event === 'subtask_tool_completed'
            ? 'completed'
            : 'failed'
      run = {
        ...run,
        subtasks: run.subtasks.map((item) =>
          item.id === event.task_id
            ? {
                ...item,
                tools: [
                  ...item.tools,
                  {
                    name: event.tool_name as string,
                    callId: event.call_id ?? null,
                    status,
                    outputSummary:
                      typeof event.output_summary === 'string' ? event.output_summary : null,
                    stepIndex: event.step_index ?? null,
                  },
                ],
              }
            : item,
        ),
      }
    }
  }

  if (event.event === 'subtask_answer_delta') {
    if (typeof event.task_id === 'string' && event.task_id) {
      const delta = typeof event.output_summary === 'string' ? event.output_summary : ''
      if (delta) {
        run = {
          ...run,
          subtasks: run.subtasks.map((item) =>
            item.id === event.task_id
              ? { ...item, answerDeltas: [...item.answerDeltas, delta] }
              : item,
          ),
        }
      }
    }
  }

  if (
    event.event === 'run_completed' ||
    event.event === 'run_failed' ||
    event.event === 'run_timed_out' ||
    event.event === 'run_cancelled' ||
    event.event === 'run_budget_exceeded'
  ) {
    terminal = true
    const completedAt = safeTimestamp(event.occurred_at)
    const results = Array.isArray(event.subtask_results) ? event.subtask_results : []
    let next = {
      ...run,
      status: terminalStatus(event),
      completedAt,
      durationMs: typeof event.duration_ms === 'number' ? event.duration_ms : run.durationMs,
      totalTokenUsage:
        typeof event.total_token_usage === 'number' ? event.total_token_usage : run.totalTokenUsage,
      errorCode: typeof event.error_code === 'string' ? event.error_code : null,
      finalOutput:
        typeof event.final_output === 'string' && event.final_output
          ? event.final_output
          : run.finalOutput,
    }
    for (const item of results) {
      const current = next.subtasks.find((subtask) => subtask.id === item.task_id)
      next = upsertSubtask(next, {
        id: item.task_id,
        agentRole:
          typeof item.agent_role === 'string' ? item.agent_role : (current?.agentRole ?? 'custom'),
        description: current?.description ?? '',
        dependsOn: current?.dependsOn ?? [],
        status:
          item.status === 'completed'
            ? 'completed'
            : item.status === 'failed'
              ? 'failed'
              : item.status === 'skipped'
                ? 'skipped'
                : item.status === 'cancelled'
                  ? 'cancelled'
                  : 'unknown',
        outputSummary:
          typeof item.output === 'string' && item.output
            ? item.output
            : (current?.outputSummary ?? null),
        errorCode: typeof item.error_code === 'string' ? item.error_code : null,
        tokenUsage: typeof item.token_usage === 'number' ? item.token_usage : null,
        durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : null,
        answerDeltas: current?.answerDeltas ?? [],
        steps: current?.steps ?? [],
        tools: current?.tools ?? [],
      })
    }
    run = next
  }

  return { run, terminal, lastSequence: event.sequence, requestId }
}

export function mergeSynchronousRun(
  state: MultiAgentStreamState,
  response: MultiAgentRun,
): MultiAgentStreamState {
  return {
    run: response,
    terminal: true,
    lastSequence: response.lastSequence ?? state.lastSequence,
    requestId: response.requestId ?? state.requestId,
  }
}
