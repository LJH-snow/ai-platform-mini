import { useEffect, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type ConfigClient,
  type RunRecordSummary,
} from './config-client.ts'

type RunListProps = {
  client: ConfigClient
  onOpenRun: (runId: string) => void
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

const formatTime = (value: string | null): string => {
  if (value === null) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 19)
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function RunList({ client, onOpenRun }: RunListProps): JSX.Element {
  const [runs, setRuns] = useState<RunRecordSummary[]>([])
  const [agents, setAgents] = useState<Array<{ id: string; name: string }>>([])
  const [agentFilter, setAgentFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadRuns = async (agentId: string): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const items = await client.listRuns(agentId === '' ? undefined : agentId)
      setRuns(items)
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : 'Run 列表加载失败。')
      setRuns([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadRuns(agentFilter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentFilter, client])

  useEffect(() => {
    client
      .listAgents()
      .then((items) =>
        setAgents(items.map((agent) => ({ id: agent.id, name: agent.name }))),
      )
      .catch(() => setAgents([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Run 历史</h2>
        <label className="daysPicker">
          Agent 过滤：
          <select
            value={agentFilter}
            onChange={(event) => setAgentFilter(event.target.value)}
          >
            <option value="">全部</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}

      {!loading && runs.length === 0 && error === null && (
        <p>暂无 Run 记录。先运行一次 Agent 请求，再回到这里查看回放。</p>
      )}

      {!loading && runs.length > 0 && (
        <table className="runTable">
          <thead>
            <tr>
              <th>时间</th>
              <th>模型</th>
              <th>状态</th>
              <th>耗时</th>
              <th>Token</th>
              <th>工具</th>
              <th>RAG 引用</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id}>
                <td>{formatTime(run.started_at)}</td>
                <td>{run.model}</td>
                <td>{statusLabel(run.status)}</td>
                <td>
                  {run.duration_ms === null ? '--' : `${Math.round(run.duration_ms)} ms`}
                </td>
                <td>{run.total_tokens === null ? '--' : String(run.total_tokens)}</td>
                <td>{run.tool_count}</td>
                <td>{run.rag_reference_count}</td>
                <td>
                  <button type="button" onClick={() => onOpenRun(run.run_id)}>
                    回放
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
