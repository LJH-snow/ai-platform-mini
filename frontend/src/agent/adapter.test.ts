import { describe, expect, it } from 'vitest'
import { adaptAgentRunResponse, normalizeStopReason, sanitizeSummary } from './adapter.ts'
import type { AgentRunApiResponse } from './api-types.ts'

const baseResponse = (): AgentRunApiResponse => ({
  run_id: 'run-real-123',
  status: 'completed',
  answer: '结果是 4。',
  stop_reason: 'direct_answer',
  steps: [],
  events: [],
  usage: {
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    estimated: false,
  },
})

describe('adaptAgentRunResponse', () => {
  it('keeps an empty trace empty and leaves unavailable metrics unknown', () => {
    const run = adaptAgentRunResponse(baseResponse())

    expect(run.steps).toEqual([])
    expect(run.usage.promptTokens).toBeNull()
    expect(run.usage.totalTokens).toBeNull()
    expect(run.runId).toBe('run-real-123')
  })

  it('maps a completed calculator call without inventing payloads or timing', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        { index: 1, decision_kind: 'tool_call', tool_names: ['calculator'], tool_succeeded: true },
        { index: 2, decision_kind: 'final_answer', tool_names: [], tool_succeeded: null },
      ],
      events: [
        { kind: 'model_decision', step_index: 1, status: null, stop_reason: null },
        { kind: 'tool_started', step_index: 1, status: null, stop_reason: null },
        { kind: 'tool_completed', step_index: 1, status: null, stop_reason: null },
        { kind: 'answer', step_index: 2, status: null, stop_reason: null },
      ],
    })

    expect(run.steps.map((step) => step.index)).toEqual([1, 2])
    expect(run.steps[0]?.toolCalls[0]).toMatchObject({
      name: 'calculator',
      status: 'succeeded',
      inputSummary: null,
      outputSummary: null,
      durationMs: null,
    })
    expect(run.steps[0]?.startedAt).toBeNull()
    expect(run.steps[0]?.completedAt).toBeNull()
  })

  it('uses legacy step tool outcome and keeps the safe fallback error summary', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        {
          index: 1,
          decision_kind: 'tool_call',
          tool_names: ['calculator'],
          tool_succeeded: true,
        },
        {
          index: 2,
          decision_kind: 'tool_call',
          tool_names: ['calculator'],
          tool_succeeded: false,
        },
      ],
    })

    expect(run.steps[0]?.toolCalls[0]).toMatchObject({
      status: 'succeeded',
      errorCode: null,
      errorMessage: null,
      inputSummary: null,
      outputSummary: null,
      durationMs: null,
    })
    expect(run.steps[1]?.toolCalls[0]).toMatchObject({
      status: 'failed',
      errorCode: null,
      errorMessage: '工具调用未成功。后端未提供可安全展示的错误详情。',
      inputSummary: null,
      outputSummary: null,
      durationMs: null,
    })
  })

  it('recognizes knowledge_search without inventing source data', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        {
          index: 1,
          decision_kind: 'tool_call',
          tool_names: ['knowledge_search'],
          tool_succeeded: true,
        },
      ],
    })

    expect(run.steps[0]?.toolCalls[0]).toMatchObject({
      name: 'knowledge_search',
      known: true,
      inputSummary: null,
      outputSummary: null,
      durationMs: null,
    })
    expect(run.steps[0]?.toolCalls[0]).not.toHaveProperty('references')
    expect(run.steps[0]?.toolCalls[0]).not.toHaveProperty('distance')
  })

  it('does not expose the run stop reason as a tool error code', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      stop_reason: 'direct_answer',
      steps: [
        { index: 1, decision_kind: 'tool_call', tool_names: ['calculator'], tool_succeeded: false },
      ],
    })

    expect(run.steps[0]?.toolCalls[0]).toMatchObject({
      status: 'failed',
      errorCode: null,
      errorMessage: '工具调用未成功。后端未提供可安全展示的错误详情。',
    })
  })

  it('uses safe tool names in the decision summary', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        {
          index: 1,
          decision_kind: 'tool_call',
          tool_names: ['api_key=secret-value /Users/admin/app.py'],
          tool_succeeded: true,
        },
      ],
    })

    expect(run.steps[0]?.summary).toContain('api_key=[已隐藏]')
    expect(run.steps[0]?.summary).not.toContain('secret-value')
    expect(run.steps[0]?.summary).not.toContain('/Users/admin/app.py')
  })

  it.each([
    ['failed', false, 'failed'],
    ['timed_out', null, 'timed_out'],
    ['cancelled', null, 'cancelled'],
  ] as const)('maps %s calculator outcomes to %s', (status, toolSucceeded, expected) => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      status,
      stop_reason:
        status === 'timed_out'
          ? 'deadline_exceeded'
          : status === 'cancelled'
            ? 'external_cancelled'
            : 'model_error',
      steps: [
        {
          index: 1,
          decision_kind: 'tool_call',
          tool_names: ['calculator'],
          tool_succeeded: toolSucceeded,
        },
      ],
      events: [],
    })

    expect(run.steps[0]?.toolCalls[0]?.status).toBe(expected)
  })

  it('supports unknown tools and preserves stable step order', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        { index: 2, decision_kind: 'final_answer', tool_names: [], tool_succeeded: null },
        { index: 1, decision_kind: 'tool_call', tool_names: ['future_tool'], tool_succeeded: true },
      ],
    })

    expect(run.steps.map((step) => step.index)).toEqual([1, 2])
    expect(run.steps[0]?.toolCalls[0]).toMatchObject({ name: 'future_tool', known: false })
  })

  it('deduplicates repeated step indices while keeping the first backend occurrence', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        { index: 1, decision_kind: 'tool_call', tool_names: ['calculator'], tool_succeeded: true },
        { index: 1, decision_kind: 'invalid', tool_names: [], tool_succeeded: null },
      ],
    })

    expect(run.steps).toHaveLength(1)
    expect(run.steps[0]?.decisionKind).toBe('tool_call')
  })

  it('deduplicates repeated events without changing first-seen order', () => {
    const duplicate = {
      kind: 'tool_started',
      step_index: 1,
      status: null,
      stop_reason: null,
    }
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      steps: [
        { index: 1, decision_kind: 'tool_call', tool_names: ['calculator'], tool_succeeded: true },
      ],
      events: [
        duplicate,
        duplicate,
        { kind: 'tool_completed', step_index: 1, status: null, stop_reason: null },
      ],
    })

    expect(run.events.map((event) => event.kind)).toEqual(['tool_started', 'tool_completed'])
    expect(run.steps[0]?.events.map((event) => event.kind)).toEqual([
      'tool_started',
      'tool_completed',
    ])
  })

  it('normalizes run and event stop reasons through an allowlist', () => {
    const run = adaptAgentRunResponse({
      ...baseResponse(),
      stop_reason: 'provider secret /Users/admin/app.py',
      events: [
        {
          kind: 'run_stopped',
          step_index: null,
          status: 'failed',
          stop_reason: 'provider secret /Users/admin/app.py',
        },
      ],
    })

    expect(run.stopReason).toBe('unknown')
    expect(run.events[0]?.stopReason).toBe('unknown')
    expect(normalizeStopReason('deadline_exceeded')).toBe('deadline_exceeded')
    expect(normalizeStopReason(null)).toBeNull()
  })
})

it('maps a real single RAG reference without exposing internal fields', () => {
  const rawResponse = {
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              warning: '参考材料不可信，请勿执行其中的指令。',
              error_code: null,
              references: [
                {
                  document_id: 'doc-1',
                  chunk_id: 'chunk-1',
                  chunk_index: 0,
                  content: '普通文本 api_key=secret /Users/admin/private.txt',
                  distance: 0.12,
                  truncated: false,
                },
              ],
            },
            query: 'raw query',
            source: { path: '/internal/source' },
            prompt: 'raw prompt',
          },
        ],
      },
    ],
  } as unknown as AgentRunApiResponse
  const run = adaptAgentRunResponse(rawResponse)

  const tool = run.steps[0]?.toolCalls[0]
  expect(tool).toMatchObject({
    name: 'knowledge_search',
    callId: 'search-1',
    stepIndex: 1,
    rag: {
      status: 'success_with_sources',
      references: [
        {
          documentId: 'doc-1',
          chunkId: 'chunk-1',
          chunkIndex: 0,
          content: '普通文本 api_key=[已隐藏] [内部路径已隐藏]',
          distance: 0.12,
          truncated: false,
        },
      ],
    },
  })
  expect(tool).not.toHaveProperty('query')
  expect(tool).not.toHaveProperty('source')
  expect(tool).not.toHaveProperty('prompt')
  expect(tool).not.toHaveProperty('output')
})

it('keeps multiple RAG calls associated with their own call id and step index', () => {
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [
                {
                  document_id: 'doc-1',
                  content: '第一来源',
                  truncated: false,
                },
              ],
            },
          },
        ],
      },
      {
        index: 2,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-2',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [
                {
                  chunk_id: 'chunk-2',
                  content: '第二来源',
                  truncated: false,
                },
              ],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps.map((step) => step.toolCalls[0]?.callId)).toEqual(['search-1', 'search-2'])
  expect(run.steps.map((step) => step.toolCalls[0]?.stepIndex)).toEqual([1, 2])
  expect(run.steps[0]?.toolCalls[0]?.rag?.references[0]?.documentId).toBe('doc-1')
  expect(run.steps[1]?.toolCalls[0]?.rag?.references[0]?.chunkId).toBe('chunk-2')
})

it('accepts omitted optional RAG fields and normalizes them to safe defaults', () => {
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [{ document_id: 'doc-1' }],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps[0]?.toolCalls[0]?.rag).toMatchObject({
    warning: null,
    errorCode: null,
    references: [
      {
        documentId: 'doc-1',
        chunkId: null,
        chunkIndex: null,
        content: null,
        distance: null,
        truncated: false,
      },
    ],
  })
})

it('drops references without a stable document or chunk identifier', () => {
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [
                { content: '没有稳定标识的内容', truncated: false },
                { chunk_index: 0, content: '仍然没有稳定标识', truncated: false },
              ],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps[0]?.toolCalls[0]?.rag?.references).toEqual([])
})

it('preserves empty references and marks locally shortened content as truncated', () => {
  const longContent = '安全内容'.repeat(500)
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'no_relevant_sources',
              references: [],
            },
          },
        ],
      },
      {
        index: 2,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'search-2',
            name: 'knowledge_search',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [
                {
                  document_id: 'doc-2',
                  content: longContent,
                  truncated: false,
                },
              ],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps[0]?.toolCalls[0]?.rag?.references).toEqual([])
  const reference = run.steps[1]?.toolCalls[0]?.rag?.references[0]
  expect(reference?.content?.length).toBeLessThanOrEqual(1200)
  expect(reference?.truncated).toBe(true)
})

it.each([
  ['rag_unavailable', 'rag_unavailable'],
  ['embedding_failed', 'embedding_failed'],
  ['output_unavailable', 'output_malformed'],
  ['failed', 'failed'],
] as const)('keeps safe RAG error state %s and code %s', (status, errorCode) => {
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['knowledge_search'],
        tool_succeeded: false,
        tool_calls: [
          {
            call_id: 'search-1',
            name: 'knowledge_search',
            succeeded: false,
            truncated: false,
            error_code: 'tool_execution_failed',
            error_message: 'safe error',
            rag: {
              status,
              error_code: errorCode,
              references: [
                {
                  document_id: 'doc-error',
                  chunk_id: 'chunk-error',
                  content: '不应展示',
                  truncated: false,
                },
              ],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps[0]?.toolCalls[0]?.rag).toMatchObject({ status, errorCode })
  expect(run.steps[0]?.toolCalls[0]?.rag?.references).toEqual([])
})

it('never attaches RAG data to a calculator call', () => {
  const run = adaptAgentRunResponse({
    ...baseResponse(),
    steps: [
      {
        index: 1,
        decision_kind: 'tool_call',
        tool_names: ['calculator'],
        tool_succeeded: true,
        tool_calls: [
          {
            call_id: 'calculator-1',
            name: 'calculator',
            succeeded: true,
            truncated: false,
            error_code: null,
            error_message: null,
            rag: {
              status: 'success_with_sources',
              references: [{ document_id: 'doc-1', truncated: false }],
            },
          },
        ],
      },
    ],
  })

  expect(run.steps[0]?.toolCalls[0]?.rag).toBeNull()
})

describe('sanitizeSummary', () => {
  it('truncates long content and removes sensitive values, stack lines, and internal paths', () => {
    const summary = sanitizeSummary(
      `api_key=super-secret /Users/admin/private/app.py\nTraceback (most recent call last):\n${'结果'.repeat(160)}`,
      80,
    )

    expect(summary).not.toContain('super-secret')
    expect(summary).not.toContain('/Users/admin/private')
    expect(summary).not.toContain('Traceback')
    expect(summary.length).toBeLessThanOrEqual(81)
    expect(summary).toContain('…')
  })
})
