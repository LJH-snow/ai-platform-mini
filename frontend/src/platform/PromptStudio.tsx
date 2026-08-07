import { useEffect, useMemo, useState, type JSX } from 'react'

import { ConfigApiError, type ConfigClient, type PromptSummary } from './config-client.ts'

type PromptStudioProps = {
  client: ConfigClient
  onUsePrompt: (prompt: string) => void
}

export function PromptStudio({ client, onUsePrompt }: PromptStudioProps): JSX.Element {
  const [prompts, setPrompts] = useState<PromptSummary[]>([])
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [versions, setVersions] = useState<
    Array<{ version: number; content: string; is_active: boolean }>
  >([])
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const loadPrompts = async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const items = await client.listPrompts()
      setPrompts(items)
      if (selectedName === null && items.length > 0) {
        setSelectedName(items[0].name)
      }
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : 'Prompt 列表加载失败。')
    } finally {
      setLoading(false)
    }
  }

  const loadVersions = async (name: string): Promise<void> => {
    setError(null)
    try {
      const items = await client.getPromptVersions(name)
      setVersions(items)
      const active = items.find((item) => item.is_active)
      setDraft(active?.content ?? items[0]?.content ?? '')
    } catch (caught) {
      setVersions([])
      setDraft('')
      setError(caught instanceof ConfigApiError ? caught.message : '版本列表加载失败。')
    }
  }

  useEffect(() => {
    void loadPrompts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  useEffect(() => {
    if (selectedName !== null) {
      void loadVersions(selectedName)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedName])

  const selected = useMemo(
    () => prompts.find((prompt) => prompt.name === selectedName) ?? null,
    [prompts, selectedName],
  )

  const saveVersion = async (): Promise<void> => {
    if (selectedName === null || !draft.trim()) return
    setNotice(null)
    setError(null)
    try {
      await client.createPromptVersion(selectedName, draft)
      setNotice('新版本已保存。')
      await loadVersions(selectedName)
      await loadPrompts()
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '保存失败。')
    }
  }

  const activateVersion = async (version: number): Promise<void> => {
    if (selectedName === null) return
    setError(null)
    try {
      await client.activatePrompt(selectedName, version)
      setNotice(`版本 v${version} 已设为当前版本。`)
      await loadVersions(selectedName)
      await loadPrompts()
    } catch (caught) {
      setError(caught instanceof ConfigApiError ? caught.message : '激活失败。')
    }
  }

  const useActivePrompt = (): void => {
    if (selectedName === null) return
    const content = versions.find((item) => item.is_active)?.content ?? draft
    if (content) onUsePrompt(content)
  }

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Prompt Studio</h2>
        <p>服务端模板版本管理：保存即新版本，"设为当前版本"即回滚/切换。</p>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}
      {notice !== null && <p className="inlineNotice" role="status">{notice}</p>}

      {!loading && prompts.length === 0 && error === null && (
        <p>暂无 Prompt 模板。请先在后端 seed 内置模板（应用启动时自动完成）。</p>
      )}

      {prompts.length > 0 && (
        <div className="studioLayout">
          <aside className="promptList" aria-label="Prompt 模板列表">
            {prompts.map((prompt) => (
              <button
                key={prompt.name}
                type="button"
                className={prompt.name === selectedName ? 'active' : ''}
                onClick={() => setSelectedName(prompt.name)}
              >
                <strong>{prompt.name}</strong>
                <span>
                  {prompt.active_version === null
                    ? '未激活'
                    : `当前 v${prompt.active_version}`}
                </span>
              </button>
            ))}
          </aside>

          {selected !== null && (
            <div className="promptEditor">
              <h3>{selected.name}</h3>
              <textarea
                aria-label="Prompt 内容"
                value={draft}
                rows={12}
                onChange={(event) => setDraft(event.target.value)}
              />
              <div className="buttonRow">
                <button type="button" onClick={() => void saveVersion()}>
                  保存为新版本
                </button>
                <button type="button" onClick={useActivePrompt}>
                  使用当前版本
                </button>
              </div>

              <h4>版本历史</h4>
              <ul className="versionList">
                {versions.map((item) => (
                  <li key={item.version}>
                    <span>
                      v{item.version}
                      {item.is_active ? '（当前）' : ''}
                    </span>
                    <button
                      type="button"
                      disabled={item.is_active}
                      onClick={() => void activateVersion(item.version)}
                    >
                      设为当前版本
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
