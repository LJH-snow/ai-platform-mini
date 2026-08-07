import { useEffect, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type ConfigClient,
  type RunRecordDetail,
} from './config-client.ts'

type RunDetailProps = {
  client: ConfigClient
  runId: string
  onBack: () => void
}

type StepTool = {
  call_id: string
  name: string
  succeeded: boolean | null
  error_code: string | null
  error_message: string | null
  input_summary: string | null
  output_summary: string | null
  rag: {
    status: string
    warning: string
    references: Array<{ document_id: string | null; chunk_id: string | null; content: string | null }>
  } | null
}

type Step = {
  index: number
  decision_kind: string
  tool_names: string[]
  summary: string | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  tool_calls: StepTool[] | null
}

const asStepList = (value: unknown): Step[] => {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry): Step[] => {
    if (typeof entry !== 'object' || entry === null) return []
    const step = entry as Record<string, unknown>
    return [
      {
        index: typeof step.index === 'number' ? step.index : 0,
        decision_kind:
          typeof step.decision_kind === 'string' ? step.decision_kind : 'invalid',
        tool_names: Array.isArray(step.tool_names)
          ? step.tool_names.filter((name): name is string => typeof name === 'string')
          : [],
        summary: typeof step.summary === 'string' ? step.summary : null,
        started_at: typeof step.started_at === 'string' ? step.started_at : null,
        completed_at: typeof step.completed_at === 'string' ? step.completed_at : null,
        duration_ms: typeof step.duration_ms === 'number' ? step.duration_ms : null,
        tool_calls: Array.isArray(step.tool_calls)
          ? (step.tool_calls as unknown[]).flatMap((tool): StepTool[] => {
              if (typeof tool !== 'object' || tool === null) return []
              const item = tool as Record<string, unknown>
              return [
                {
                  call_id: typeof item.call_id === 'string' ? item.call_id : '',
                  name: typeof item.name === 'string' ? item.name : 'unknown',
                  succeeded: typeof item.succeeded === 'boolean' ? item.succeeded : null,
                  error_code: typeof item.error_code === 'string' ? item.error_code : null,
                  error_message:
                    typeof item.error_message === 'string' ? item.error_message : null,
                  input_summary:
                    typeof item.input_summary === 'string' ? item.input_summary : null,
                  output_summary:
                    typeof item.output_summary === 'string' ? item.output_summary : null,
                  rag:
                    typeof item.rag === 'object' && item.rag !== null
                      ? (item.rag as StepTool['rag'])
                      : null,
                },
              ]
            })
          : null,
      },
    ]
  })
}

const statusLabel = (status: string): string => {
  const labels: Record<string, string> = {
    completed: '已完成',
    failed: '失败',
    timed_out: '超时',
    cancelled: '已取消',
    stopped: '已停止',
    running: '运行中',
  }
  return labels[status] ?? status
}

export function RunDetail({ client, runId, onBack }: RunDetailProps): JSX.Element {
  const [run, setRun] = useState<RunRecordDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    client
      .getRun(runId)
      .then((detail) => {
        if (cancelled) return
        setRun(detail)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ConfigApiError ? caught.message : 'Run 详情加载失败。',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, runId])

  const steps = run === null ? [] : asStepList(run.response.steps)
  const answer =
    run !== null && typeof run.response.answer === 'string'
      ? (run.response.answer as string)
      : null

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Agent Run 回放</h2>
        <button type="button" onClick={onBack}>
          ← 返回
        </button>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}

      {run !== null && (
        <>
          <dl className="runMeta">
            <div>
              <dt>Run ID</dt>
              <dd>{run.run_id}</dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd>{run.model}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>{statusLabel(run.status)}</dd>
            </div>
            <div>
              <dt>耗时</dt>
              <dd>{run.duration_ms === null ? '--' : `${Math.round(run.duration_ms)} ms`}</dd>
            </div>
            <div>
              <dt>Token</dt>
              <dd>{run.total_tokens === null ? '--' : String(run.total_tokens)}</dd>
            </div>
          </dl>

          <h3>步骤时间线</h3>
          {steps.length === 0 && <p>该 Run 没有可展示的步骤。</p>}
          <ol className="runTimeline">
            {steps.map((step) => (
              <li key={step.index} className="timelineStep">
                <div className="stepHeader">
                  <strong>Step {step.index}</strong>
                  <span>{step.decision_kind}</span>
                  {step.summary !== null && <span>{step.summary}</span>}
                </div>
                {step.tool_calls !== null && step.tool_calls.length > 0 && (
                  <ul className="toolCallList">
                    {step.tool_calls.map((tool) => (
                      <li key={tool.call_id}>
                        <strong>{tool.name}</strong>
                        {tool.succeeded === false && <span>（失败）</span>}
                        {tool.input_summary !== null && (
                          <p>输入：{tool.input_summary}</p>
                        )}
                        {tool.output_summary !== null && (
                          <p>输出：{tool.output_summary}</p>
                        )}
                        {tool.rag !== null && (
                          <details>
                            <summary>
                              RAG 来源（{tool.rag.references.length} 条，
                              {tool.rag.status}）
                            </summary>
                            <ul>
                              {tool.rag.references.map((reference, index) => (
                                <li key={index}>
                                  {reference.content !== null && (
                                    <blockquote>{reference.content}</blockquote>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>

          {answer !== null && (
            <div className="finalAnswer">
              <h3>最终回答</h3>
              <p>{answer}</p>
            </div>
          )}
        </>
      )}
    </section>
  )
}
