import { useEffect, useMemo, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type AgentDraft,
  type AgentSummary,
  type BenchmarkRun,
  type ConfigClient,
  type PromptSummary,
  type ToolInfo,
} from './config-client.ts'

type AgentStudioProps = {
  client: ConfigClient
}

const EMPTY_DRAFT: AgentDraft = {
  name: '',
  model: '',
  prompt_ref: '',
  tool_names: [],
  temperature: 0.7,
  max_steps: 10,
}

export function AgentStudio({ client }: AgentStudioProps): JSX.Element {
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [prompts, setPrompts] = useState<PromptSummary[]>([])
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [draft, setDraft] = useState<AgentDraft>(EMPTY_DRAFT)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [benchmarkRuns, setBenchmarkRuns] = useState<BenchmarkRun[]>([])
  const [benchmarkLoading, setBenchmarkLoading] = useState(false)

  const loadBenchmarkRuns = async (agentId: string): Promise<void> => {
    try {
      setBenchmarkRuns(await client.listBenchmarkRuns(agentId))
    } catch {
      setBenchmarkRuns([])
    }
  }

  const runBenchmark = async (agent: AgentSummary): Promise<void> => {
    setError(null)
    setNotice(null)
    setBenchmarkLoading(true)
    try {
      const run = await client.runBenchmark(agent.id)
      setNotice(
        `Benchmark 完成：工具准确率 ${(run.tool_call_accuracy ?? 0).toFixed(2)}，` +
          `完成率 ${(run.task_completion_rate ?? 0).toFixed(2)}。`,
      )
      await loadBenchmarkRuns(agent.id)
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : 'Benchmark 运行失败。')
    } finally {
      setBenchmarkLoading(false)
    }
  }

  const loadAll = async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const [agentItems, promptItems, toolItems] = await Promise.all([
        client.listAgents(),
        client.listPrompts(),
        client.listTools(),
      ])
      setAgents(agentItems)
      setPrompts(promptItems)
      setTools(toolItems)
    } catch (caught) {
      setError(
        caught instanceof ConfigApiError ? caught.message : 'Agent 配置加载失败。',
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadAll()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  const enabledTools = useMemo(() => tools.filter((tool) => tool.enabled), [tools])

  const startEdit = (agent: AgentSummary): void => {
    setEditingId(agent.id)
    void loadBenchmarkRuns(agent.id)
    setDraft({
      name: agent.name,
      model: agent.model,
      prompt_ref: agent.prompt_ref,
      tool_names: agent.tool_names,
      temperature: agent.temperature,
      max_steps: agent.max_steps,
    })
    setError(null)
    setNotice(null)
  }

  const resetForm = (): void => {
    setEditingId(null)
    setDraft(EMPTY_DRAFT)
    setError(null)
    setNotice(null)
  }

  const toggleTool = (name: string): void => {
    setDraft((current) => ({
      ...current,
      tool_names: current.tool_names.includes(name)
        ? current.tool_names.filter((item) => item !== name)
        : [...current.tool_names, name],
    }))
  }

  const saveAgent = async (): Promise<void> => {
    if (!draft.name.trim() || !draft.model.trim()) {
      setError('名称与模型为必填项。')
      return
    }
    setError(null)
    setNotice(null)
    try {
      if (editingId === null) {
        await client.createAgent(draft)
        resetForm()
        setNotice('Agent 已创建。')
      } else {
        await client.updateAgent(editingId, draft)
        resetForm()
        setNotice('Agent 已更新。')
      }
      await loadAll()
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '保存失败。')
    }
  }

  const deleteAgent = async (agent: AgentSummary): Promise<void> => {
    setError(null)
    try {
      await client.deleteAgent(agent.id)
      if (editingId === agent.id) resetForm()
      setNotice(`Agent「${agent.name}」已删除。`)
      await loadAll()
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '删除失败。')
    }
  }

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Agent Studio</h2>
        <p>Agent = 模型 + Prompt（版本）+ 工具白名单，运行时按 agent_id 解析。</p>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}
      {notice !== null && <p className="inlineNotice" role="status">{notice}</p>}

      {!loading && (
        <>
          <div className="studioLayout">
            <aside className="agentList" aria-label="Agent 列表">
              <h3>Agent 列表</h3>
              {agents.length === 0 && <p>暂无 Agent。</p>}
              {agents.map((agent) => (
                <div key={agent.id} className="agentItem">
                  <button type="button" onClick={() => startEdit(agent)}>
                    <strong>{agent.name}</strong>
                    <span>
                      {agent.model}
                      {agent.enabled ? '' : '（停用）'}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="dangerLink"
                    onClick={() => void deleteAgent(agent)}
                  >
                    删除
                  </button>
                </div>
              ))}
            </aside>

            <div className="agentForm">
              <h3>{editingId === null ? '新建 Agent' : '编辑 Agent'}</h3>
              <label>
                名称
                <input
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                />
              </label>
              <label>
                模型
                <input
                  value={draft.model}
                  placeholder="如 qwen3:4b"
                  onChange={(event) => setDraft({ ...draft, model: event.target.value })}
                />
              </label>
              <label>
                Prompt 模板
                <select
                  value={draft.prompt_ref}
                  onChange={(event) =>
                    setDraft({ ...draft, prompt_ref: event.target.value })
                  }
                >
                  <option value="">（内置协议）</option>
                  {prompts.map((prompt) => (
                    <option key={prompt.name} value={prompt.name}>
                      {prompt.name}
                      {prompt.active_version === null
                        ? ''
                        : `（当前 v${prompt.active_version}）`}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset>
                <legend>工具白名单</legend>
                {enabledTools.length === 0 && <p>当前 workspace 没有可用工具。</p>}
                {enabledTools.map((tool) => (
                  <label key={tool.name} className="checkboxRow">
                    <input
                      type="checkbox"
                      checked={draft.tool_names.includes(tool.name)}
                      onChange={() => toggleTool(tool.name)}
                    />
                    {tool.name}
                  </label>
                ))}
              </fieldset>
              <div className="numberRow">
                <label>
                  温度
                  <input
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={draft.temperature}
                    onChange={(event) =>
                      setDraft({ ...draft, temperature: Number(event.target.value) })
                    }
                  />
                </label>
                <label>
                  最大步数
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={draft.max_steps}
                    onChange={(event) =>
                      setDraft({ ...draft, max_steps: Number(event.target.value) })
                    }
                  />
                </label>
              </div>
              <div className="buttonRow">
                <button type="button" onClick={() => void saveAgent()}>
                  {editingId === null ? '创建' : '保存修改'}
                </button>
                {editingId !== null && (
                  <button type="button" onClick={resetForm}>
                    取消编辑
                  </button>
                )}
              </div>
            </div>
          </div>

          {editingId !== null && (
            <div className="benchmarkPanel">
              <h3>Benchmark</h3>
              <p>
                对当前 Agent 运行 golden 任务集（真实执行），输出工具准确率、完成率、
                平均步数与延迟。
              </p>
              <button
                type="button"
                disabled={benchmarkLoading}
                onClick={() => {
                  const agent = agents.find((item) => item.id === editingId)
                  if (agent !== undefined) void runBenchmark(agent)
                }}
              >
                {benchmarkLoading ? '运行中…' : '运行 Benchmark'}
              </button>
              {benchmarkRuns.length > 0 && (
                <table className="benchmarkTable">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>任务集</th>
                      <th>工具准确率</th>
                      <th>完成率</th>
                      <th>平均步数</th>
                      <th>平均延迟</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {benchmarkRuns.map((run) => (
                      <tr key={run.id}>
                        <td>{run.id}</td>
                        <td>{run.task_set}</td>
                        <td>
                          {run.tool_call_accuracy === null
                            ? '--'
                            : run.tool_call_accuracy.toFixed(2)}
                        </td>
                        <td>
                          {run.task_completion_rate === null
                            ? '--'
                            : run.task_completion_rate.toFixed(2)}
                        </td>
                        <td>
                          {run.average_steps === null ? '--' : run.average_steps.toFixed(2)}
                        </td>
                        <td>
                          {run.average_latency_ms === null
                            ? '--'
                            : `${Math.round(run.average_latency_ms)} ms`}
                        </td>
                        <td>{run.created_at === null ? '--' : run.created_at.slice(0, 19)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
