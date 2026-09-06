import { useEffect, useRef, useState, type JSX } from 'react'

import { detailToRun } from './adapter.ts'
import {
  MultiAgentBackendError,
  MultiAgentNetworkError,
  MultiAgentResponseError,
  type MultiAgentClient,
} from './client.ts'
import {
  initialMultiAgentStreamState,
  mergeSynchronousRun,
  reduceMultiAgentStream,
} from './reducer.ts'
import { MultiAgentStreamFormatError } from './stream.ts'
import type {
  MultiAgentFailurePolicy,
  MultiAgentRun,
  MultiAgentRunDetail,
  MultiAgentRunSummary,
  MultiAgentSubtask,
} from './types.ts'

type MultiAgentPanelProps = {
  client: MultiAgentClient
  apiKeyConfigured: boolean
}

type PanelView = 'run' | 'history'

const runStatusLabel = (status: MultiAgentRun['status']): string => {
  switch (status) {
    case 'running':
      return '运行中'
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    case 'timed_out':
      return '超时'
    case 'budget_exceeded':
      return '超出预算'
    case 'idle':
      return '空闲'
    default:
      return '未知'
  }
}

const subtaskStatusLabel = (subtask: MultiAgentSubtask): string => {
  switch (subtask.status) {
    case 'pending':
      return '排队'
    case 'running':
      return '运行中'
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'skipped':
      return '已跳过'
    case 'cancelled':
      return '已取消'
    default:
      return '未知'
  }
}

const errorCodeLabel = (code: string): string => {
  const labels: Record<string, string> = {
    supervisor_failed: '拆分失败',
    subtask_failed: '子任务失败',
    dependency_deadlock: '依赖死锁',
    timeout: '超时',
    cancelled: '已取消',
    budget_exceeded: '超出预算',
    internal_error: '内部错误',
    stream_setup_failed: '流启动失败',
  }
  return labels[code] ?? code
}

const formatTime = (value: string | null): string => {
  if (value === null) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 19)
  return date.toLocaleString('zh-CN', { hour12: false })
}

const parseOptionalInt = (value: string): number | undefined => {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number.parseInt(trimmed, 10)
  return Number.isInteger(parsed) ? parsed : undefined
}

const parseOptionalFloat = (value: string): number | null | undefined => {
  const trimmed = value.trim()
  if (!trimmed) return undefined
  const parsed = Number.parseFloat(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

function Timeline({ run }: { run: MultiAgentRun }): JSX.Element {
  return (
    <>
      <h3>子任务时间线</h3>
      {run.subtasks.length === 0 && <p>暂无子任务。</p>}
      <ol className="runTimeline">
        {run.subtasks.map((subtask) => (
          <li key={subtask.id} className="timelineStep">
            <div className="stepHeader">
              <strong>{subtask.id}</strong>
              <span>{subtask.agentRole}</span>
              <span>{subtaskStatusLabel(subtask)}</span>
              {subtask.errorCode !== null && (
                <span>错误码：{errorCodeLabel(subtask.errorCode)}</span>
              )}
            </div>
            {subtask.description !== '' && <p>{subtask.description}</p>}
            {subtask.dependsOn.length > 0 && <p>依赖：{subtask.dependsOn.join('、')}</p>}
            {subtask.outputSummary !== null && <p>输出：{subtask.outputSummary}</p>}
            <p>
              Token：
              {subtask.tokenUsage === null ? '--' : String(subtask.tokenUsage)}
              {'　耗时：'}
              {subtask.durationMs === null ? '--' : `${subtask.durationMs} ms`}
            </p>
          </li>
        ))}
      </ol>
    </>
  )
}

export function MultiAgentPanel({ client, apiKeyConfigured }: MultiAgentPanelProps): JSX.Element {
  const [view, setView] = useState<PanelView>('run')
  const [message, setMessage] = useState('')
  const [maxSubtasks, setMaxSubtasks] = useState('5')
  const [maxConcurrency, setMaxConcurrency] = useState('3')
  const [failurePolicy, setFailurePolicy] = useState<MultiAgentFailurePolicy>('fail_fast')
  const [totalTimeout, setTotalTimeout] = useState('300')
  const [totalTokenBudget, setTotalTokenBudget] = useState('')
  const [supervisorModel, setSupervisorModel] = useState('')
  const [streamState, setStreamState] = useState(initialMultiAgentStreamState)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [history, setHistory] = useState<MultiAgentRunSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [selectedDetail, setSelectedDetail] = useState<MultiAgentRunDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      controllerRef.current?.abort()
    }
  }, [])

  const loadHistory = async (): Promise<void> => {
    setHistoryLoading(true)
    setHistoryError(null)
    setSelectedDetail(null)
    setDetailError(null)
    try {
      setHistory(await client.listRuns(50))
    } catch (caught) {
      setHistoryError(
        caught instanceof MultiAgentBackendError ? caught.message : '多 Agent 历史加载失败。',
      )
      setHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }

  const openDetail = async (runId: string): Promise<void> => {
    setDetailLoading(true)
    setDetailError(null)
    try {
      setSelectedDetail(await client.getRun(runId))
    } catch (caught) {
      setDetailError(
        caught instanceof MultiAgentBackendError ? caught.message : 'Run 详情加载失败。',
      )
      setSelectedDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const startRun = async (): Promise<void> => {
    if (running) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setStreamState(initialMultiAgentStreamState)
    setRunError(null)
    setRunning(true)
    try {
      await client.streamMultiAgent(
        {
          message,
          supervisorModel: supervisorModel.trim() || null,
          maxSubtasks: parseOptionalInt(maxSubtasks),
          maxConcurrency: parseOptionalInt(maxConcurrency),
          failurePolicy,
          totalTimeoutSeconds: parseOptionalFloat(totalTimeout) ?? null,
          totalTokenBudget: parseOptionalInt(totalTokenBudget) ?? null,
        },
        {
          onEvent: (event) => {
            setStreamState((state) => reduceMultiAgentStream(state, event))
          },
        },
        controller.signal,
      )
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        setRunError('运行已取消。')
        return
      }
      if (
        caught instanceof MultiAgentBackendError ||
        caught instanceof MultiAgentNetworkError ||
        caught instanceof MultiAgentResponseError ||
        caught instanceof MultiAgentStreamFormatError ||
        caught instanceof RangeError
      ) {
        setRunError(caught.message)
        return
      }
      setRunError('运行多 Agent 时发生未知错误。')
    } finally {
      setRunning(false)
    }
  }

  const stopRun = (): void => {
    controllerRef.current?.abort()
  }

  const runFallback = async (): Promise<void> => {
    // 同步端点兜底：流不可用时仍可拿到结构化结果。
    if (running) return
    const controller = new AbortController()
    controllerRef.current = controller
    setRunError(null)
    setRunning(true)
    try {
      const response = await client.runMultiAgent(
        {
          message,
          supervisorModel: supervisorModel.trim() || null,
          maxSubtasks: parseOptionalInt(maxSubtasks),
          maxConcurrency: parseOptionalInt(maxConcurrency),
          failurePolicy,
          totalTimeoutSeconds: parseOptionalFloat(totalTimeout) ?? null,
          totalTokenBudget: parseOptionalInt(totalTokenBudget) ?? null,
        },
        controller.signal,
      )
      setStreamState((state) => mergeSynchronousRun(state, response))
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        setRunError('运行已取消。')
        return
      }
      if (
        caught instanceof MultiAgentBackendError ||
        caught instanceof MultiAgentNetworkError ||
        caught instanceof MultiAgentResponseError ||
        caught instanceof RangeError
      ) {
        setRunError(caught.message)
        return
      }
      setRunError('运行多 Agent 时发生未知错误。')
    } finally {
      setRunning(false)
    }
  }

  const liveRun = streamState.run
  const detailRun = selectedDetail === null ? null : detailToRun(selectedDetail)

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>多 Agent 编排</h2>
        <div>
          <button
            type="button"
            className={view === 'run' ? 'modeActive' : 'secondaryButton'}
            aria-pressed={view === 'run'}
            onClick={() => setView('run')}
          >
            运行
          </button>
          <button
            type="button"
            className={view === 'history' ? 'modeActive' : 'secondaryButton'}
            aria-pressed={view === 'history'}
            onClick={() => {
              setView('history')
              void loadHistory()
            }}
          >
            历史
          </button>
        </div>
      </div>

      {view === 'run' && (
        <>
          <div className="composer">
            <label htmlFor="multi-agent-message">任务描述</label>
            <textarea
              id="multi-agent-message"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="例如：调研向量数据库选型并输出对比报告"
              rows={3}
            />
            <div className="composerActions">
              <label htmlFor="multi-agent-max-subtasks">最大子任务数</label>
              <input
                id="multi-agent-max-subtasks"
                type="number"
                min={1}
                max={10}
                value={maxSubtasks}
                onChange={(event) => setMaxSubtasks(event.target.value)}
              />
              <label htmlFor="multi-agent-max-concurrency">最大并发数</label>
              <input
                id="multi-agent-max-concurrency"
                type="number"
                min={1}
                max={10}
                value={maxConcurrency}
                onChange={(event) => setMaxConcurrency(event.target.value)}
              />
              <label htmlFor="multi-agent-failure-policy">失败策略</label>
              <select
                id="multi-agent-failure-policy"
                value={failurePolicy}
                onChange={(event) =>
                  setFailurePolicy(event.target.value as MultiAgentFailurePolicy)
                }
              >
                <option value="fail_fast">遇错即停</option>
                <option value="skip">跳过继续</option>
                <option value="retry_once">重试一次</option>
              </select>
              <label htmlFor="multi-agent-timeout">全局超时（秒）</label>
              <input
                id="multi-agent-timeout"
                type="number"
                min={1}
                value={totalTimeout}
                onChange={(event) => setTotalTimeout(event.target.value)}
              />
              <label htmlFor="multi-agent-token-budget">Token 预算</label>
              <input
                id="multi-agent-token-budget"
                type="number"
                min={1}
                placeholder="不限"
                value={totalTokenBudget}
                onChange={(event) => setTotalTokenBudget(event.target.value)}
              />
              <label htmlFor="multi-agent-supervisor-model">Supervisor 模型</label>
              <input
                id="multi-agent-supervisor-model"
                type="text"
                placeholder="默认"
                value={supervisorModel}
                onChange={(event) => setSupervisorModel(event.target.value)}
              />
            </div>
            <p className="formHint">
              实时事件流展示子任务状态；流不可用时可用同步模式兜底，结果一致。
            </p>
            <div className="composerActions">
              <button
                type="button"
                disabled={running || !apiKeyConfigured || message.trim() === ''}
                onClick={() => void startRun()}
              >
                {running ? '运行中…' : '运行多 Agent'}
              </button>
              {running && (
                <button type="button" className="secondaryButton" onClick={stopRun}>
                  取消
                </button>
              )}
              <button
                type="button"
                className="secondaryButton"
                disabled={running || !apiKeyConfigured || message.trim() === ''}
                onClick={() => void runFallback()}
              >
                同步运行
              </button>
            </div>
          </div>

          {!apiKeyConfigured && (
            <p className="inlineError" role="alert">
              未配置 API Key，无法运行多 Agent。
            </p>
          )}
          {runError !== null && (
            <p className="inlineError" role="alert">
              {runError}
            </p>
          )}

          {liveRun !== null && (
            <>
              <dl className="runMeta">
                <div>
                  <dt>Run ID</dt>
                  <dd>{liveRun.runId ?? '--'}</dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd>{runStatusLabel(liveRun.status)}</dd>
                </div>
                {liveRun.errorCode !== null && (
                  <div>
                    <dt>错误码</dt>
                    <dd>{errorCodeLabel(liveRun.errorCode)}</dd>
                  </div>
                )}
                <div>
                  <dt>Token</dt>
                  <dd>
                    {liveRun.totalTokenUsage === null ? '--' : String(liveRun.totalTokenUsage)}
                  </dd>
                </div>
                <div>
                  <dt>耗时</dt>
                  <dd>{liveRun.durationMs === null ? '--' : `${liveRun.durationMs} ms`}</dd>
                </div>
              </dl>

              {liveRun.reasoning !== null && <p>拆分思路：{liveRun.reasoning}</p>}
              <Timeline run={liveRun} />

              {streamState.terminal && liveRun.finalOutput !== null && (
                <div className="finalAnswer">
                  <h3>汇总输出</h3>
                  <p>{liveRun.finalOutput}</p>
                </div>
              )}
            </>
          )}
        </>
      )}

      {view === 'history' && (
        <>
          <div className="pageHeader">
            <h3>Run 历史</h3>
            <button type="button" className="secondaryButton" onClick={() => void loadHistory()}>
              刷新
            </button>
          </div>
          {historyLoading && <p>加载中…</p>}
          {historyError !== null && (
            <p className="inlineError" role="alert">
              {historyError}
            </p>
          )}
          {!historyLoading && history.length === 0 && historyError === null && (
            <p>暂无历史 Run。</p>
          )}
          <ul className="runTimeline">
            {history.map((item) => (
              <li key={item.runId} className="timelineStep">
                <div className="stepHeader">
                  <strong>{item.runId.slice(0, 8)}</strong>
                  <span>{runStatusLabel(item.status as MultiAgentRun['status'])}</span>
                  <span>子任务 {item.subtaskCount} 个</span>
                  <span>Token：{item.totalTokens === null ? '--' : String(item.totalTokens)}</span>
                  <span>{formatTime(item.startedAt)}</span>
                  <button
                    type="button"
                    className="secondaryButton"
                    onClick={() => void openDetail(item.runId)}
                  >
                    查看
                  </button>
                </div>
              </li>
            ))}
          </ul>

          {detailLoading && <p>详情加载中…</p>}
          {detailError !== null && (
            <p className="inlineError" role="alert">
              {detailError}
            </p>
          )}
          {detailRun !== null && (
            <>
              <h3>Run 回放</h3>
              <Timeline run={detailRun} />
              {detailRun.finalOutput !== null && (
                <div className="finalAnswer">
                  <h3>汇总输出</h3>
                  <p>{detailRun.finalOutput}</p>
                </div>
              )}
            </>
          )}
        </>
      )}
    </section>
  )
}
