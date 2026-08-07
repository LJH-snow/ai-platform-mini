import { useEffect, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type ConfigClient,
  type ToolInfo,
} from './config-client.ts'

type ToolCenterProps = {
  client: ConfigClient
}

export function ToolCenter({ client }: ToolCenterProps): JSX.Element {
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const loadTools = async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      setTools(await client.listTools())
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '工具列表加载失败。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTools()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  const toggleEnabled = async (tool: ToolInfo): Promise<void> => {
    setError(null)
    setNotice(null)
    try {
      const updated = await client.setToolEnabled(tool.name, !tool.enabled)
      if (updated === null) return
      setTools((current) =>
        current.map((item) => (item.name === updated.name ? updated : item)),
      )
      setNotice(
        `工具「${updated.name}」已${updated.enabled ? '启用' : '禁用'}（本 workspace）。`,
      )
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '切换失败。')
    }
  }

  const toggleSchema = (name: string): void => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Tool Center</h2>
        <p>workspace 级工具启用开关与 JSON Schema 展示。</p>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}
      {notice !== null && <p className="inlineNotice" role="status">{notice}</p>}

      {!loading && tools.length === 0 && error === null && (
        <p>暂无工具。请先在后端 seed 内置工具（应用启动时自动完成）。</p>
      )}

      {!loading && (
        <ul className="toolList">
          {tools.map((tool) => (
            <li key={tool.name} className="toolItem">
              <div className="toolHeader">
                <div>
                  <strong>{tool.name}</strong>
                  <span className="toolOwner">{tool.owner}</span>
                </div>
                <label className="switchRow">
                  <input
                    type="checkbox"
                    checked={tool.enabled}
                    onChange={() => void toggleEnabled(tool)}
                  />
                  {tool.enabled ? '已启用' : '已禁用'}
                </label>
              </div>
              <p className="toolDescription">{tool.description}</p>
              <button type="button" onClick={() => toggleSchema(tool.name)}>
                {expanded.has(tool.name) ? '收起 Schema' : '展开 Schema'}
              </button>
              {expanded.has(tool.name) && (
                <pre className="schemaView">
                  {JSON.stringify(tool.parameters_schema, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
