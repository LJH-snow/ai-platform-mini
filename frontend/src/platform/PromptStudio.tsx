import { useEffect, useMemo, useState, type JSX } from 'react'

import { defaultTemplates, STORAGE_KEY, type PromptTemplate } from './prompt-data.ts'

type PromptStudioProps = {
  onUsePrompt: (prompt: string) => void
}

const readTemplates = (): PromptTemplate[] => {
  if (typeof window === 'undefined') return defaultTemplates
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (!stored) return defaultTemplates
    const parsed: unknown = JSON.parse(stored)
    return Array.isArray(parsed) && parsed.length > 0
      ? (parsed as PromptTemplate[])
      : defaultTemplates
  } catch {
    return defaultTemplates
  }
}

export function PromptStudio({ onUsePrompt }: PromptStudioProps): JSX.Element {
  const [templates, setTemplates] = useState<PromptTemplate[]>(readTemplates)
  const [selectedId, setSelectedId] = useState(defaultTemplates[0].id)
  const [saved, setSaved] = useState(false)

  const selected = useMemo(
    () => templates.find((template) => template.id === selectedId) ?? templates[0],
    [selectedId, templates],
  )

  useEffect(() => {
    if (!selected && templates.length > 0) setSelectedId(templates[0].id)
  }, [selected, templates])

  const updateSelected = (patch: Partial<PromptTemplate>): void => {
    setTemplates((current) =>
      current.map((template) =>
        template.id === selected?.id ? { ...template, ...patch } : template,
      ),
    )
    setSaved(false)
  }

  const saveTemplates = (): void => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(templates))
    setSaved(true)
  }

  const restoreSelected = (): void => {
    const original = defaultTemplates.find((template) => template.id === selected?.id)
    if (original)
      setTemplates((current) => current.map((item) => (item.id === original.id ? original : item)))
    setSaved(false)
  }

  if (!selected) {
    return <section className="platformPage">暂无可用 Prompt 模板。</section>
  }

  return (
    <section className="platformPage" aria-labelledby="prompt-studio-title">
      <div className="pageIntro compactPageIntro">
        <div>
          <p className="pageKicker">PROMPT STUDIO · TEMPLATES</p>
          <h1 id="prompt-studio-title">Prompt Studio</h1>
          <p className="pageLead">
            把一次性的提示词整理成可复用的工作流入口，保存后可以直接带入真实对话。
          </p>
        </div>
        <span className="studioSavedState" role="status">
          {saved ? '已保存到本地' : '本地草稿'}
        </span>
      </div>

      <div className="promptStudioLayout">
        <aside className="promptLibrary" aria-label="Prompt 模板列表">
          <div className="promptLibraryHeader">
            <span className="sectionKicker">LIBRARY</span>
            <strong>{templates.length} templates</strong>
          </div>
          <div className="promptTemplateList">
            {templates.map((template) => (
              <button
                type="button"
                className={
                  template.id === selected.id
                    ? 'promptTemplate promptTemplateActive'
                    : 'promptTemplate'
                }
                key={template.id}
                onClick={() => {
                  setSelectedId(template.id)
                  setSaved(false)
                }}
                aria-pressed={template.id === selected.id}
              >
                <span>{template.name}</span>
                <small>{template.category}</small>
              </button>
            ))}
          </div>
        </aside>

        <article className="promptEditorCard">
          <div className="promptEditorHeader">
            <div>
              <span className="sectionKicker">EDIT TEMPLATE</span>
              <h2>{selected.name}</h2>
              <p>{selected.description}</p>
            </div>
            <span className="promptCategory">{selected.category}</span>
          </div>
          <label htmlFor="prompt-content">系统提示词</label>
          <textarea
            id="prompt-content"
            value={selected.prompt}
            onChange={(event) => updateSelected({ prompt: event.target.value })}
            rows={8}
          />
          <label htmlFor="prompt-example">演示问题</label>
          <textarea
            id="prompt-example"
            value={selected.example}
            onChange={(event) => updateSelected({ example: event.target.value })}
            rows={3}
          />
          <div className="promptEditorActions">
            <button type="button" onClick={() => onUsePrompt(selected.example)}>
              带入对话工作台 →
            </button>
            <button type="button" className="secondaryButton" onClick={restoreSelected}>
              恢复默认
            </button>
            <button type="button" className="secondaryButton" onClick={saveTemplates}>
              保存模板
            </button>
          </div>
        </article>
      </div>
    </section>
  )
}
