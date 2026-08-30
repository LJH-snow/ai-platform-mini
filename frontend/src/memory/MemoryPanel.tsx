import type { FormEvent, JSX } from 'react'
import { useCallback, useEffect, useState } from 'react'
import { MemoryApiError, MemoryNetworkError, type MemoryClient } from './client.ts'
import type { MemoryItem, MemoryKind } from './types.ts'
import './MemoryPanel.css'

type MemoryPanelProps = {
  apiKeyConfigured: boolean
  client: MemoryClient
}

const kindLabels: Record<MemoryKind, string> = {
  fact: '事实',
  preference: '偏好',
  instruction: '指令',
}

const sourceLabels = {
  explicit: '显式保存',
  conversation: '会话',
  system: '系统',
}

const safeError = (error: unknown): string => {
  if (error instanceof MemoryApiError) return error.message
  if (error instanceof MemoryNetworkError) return error.message
  return '请求失败，请稍后重试。'
}

const formatTimestamp = (value: string | null): string => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function MemoryPanel({ apiKeyConfigured, client }: MemoryPanelProps): JSX.Element {
  const [items, setItems] = useState<MemoryItem[]>([])
  const [query, setQuery] = useState('')
  const [content, setContent] = useState('')
  const [kind, setKind] = useState<MemoryKind>('fact')
  const [confidence, setConfidence] = useState('1')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(
    async (search = ''): Promise<void> => {
      if (!apiKeyConfigured) {
        setItems([])
        setLoading(false)
        return
      }
      setLoading(true)
      setError(null)
      try {
        setItems(await client.list(search))
      } catch (caught) {
        setError(safeError(caught))
      } finally {
        setLoading(false)
      }
    },
    [apiKeyConfigured, client],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  const handleSearch = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    void refresh(query)
  }

  const resetForm = (): void => {
    setEditingId(null)
    setContent('')
    setKind('fact')
    setConfidence('1')
    setError(null)
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const trimmed = content.trim()
    if (!trimmed) {
      setError('记忆内容不能为空。')
      return
    }
    const parsedConfidence = Number(confidence)
    if (!Number.isFinite(parsedConfidence) || parsedConfidence < 0 || parsedConfidence > 1) {
      setError('置信度必须在 0 到 1 之间。')
      return
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      if (editingId) {
        await client.update(editingId, {
          content: trimmed,
          kind,
          confidence: parsedConfidence,
        })
        setNotice('记忆已更新。')
      } else {
        await client.create({
          content: trimmed,
          kind,
          confidence: parsedConfidence,
        })
        setNotice('记忆已保存。')
      }
      resetForm()
      await refresh(query)
    } catch (caught) {
      setError(safeError(caught))
    } finally {
      setSaving(false)
    }
  }

  const beginEdit = (item: MemoryItem): void => {
    setEditingId(item.id)
    setContent(item.content)
    setKind(item.kind)
    setConfidence(String(item.confidence))
    setError(null)
    setNotice(null)
  }

  const handleDelete = async (item: MemoryItem): Promise<void> => {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      await client.delete(item.id)
      if (editingId === item.id) resetForm()
      setNotice('记忆已删除。')
      await refresh(query)
    } catch (caught) {
      setError(safeError(caught))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="memoryPanel">
      <div className="memoryPanelHeader">
        <div>
          <h2>长期记忆</h2>
          <p>用户、工作空间和 API Key 之间严格隔离。</p>
        </div>
        <form className="memorySearch" onSubmit={handleSearch}>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="检索记忆"
            aria-label="检索长期记忆"
          />
          <button type="submit">检索</button>
        </form>
      </div>

      {notice ? <div className="memoryNotice">{notice}</div> : null}
      {error ? <div className="memoryError">{error}</div> : null}

      <div className="memoryLayout">
        <section className="memoryListSection" aria-label="长期记忆列表">
          <div className="memoryListToolbar">
            <strong>{items.length} 条</strong>
            {query.trim() ? (
              <button type="button" onClick={() => void refresh('')}>
                清除检索
              </button>
            ) : null}
          </div>
          {loading ? (
            <div className="memoryEmpty">加载中</div>
          ) : items.length === 0 ? (
            <div className="memoryEmpty">暂无记忆</div>
          ) : (
            <div className="memoryList">
              {items.map((item) => (
                <article className="memoryItem" key={item.id}>
                  <div className="memoryItemMain">
                    <div className="memoryItemMeta">
                      <span className={`memoryKind memoryKind-${item.kind}`}>
                        {kindLabels[item.kind]}
                      </span>
                      <span>{sourceLabels[item.source]}</span>
                      <span>置信度 {item.confidence.toFixed(2)}</span>
                      <span>最后使用 {formatTimestamp(item.last_used_at)}</span>
                    </div>
                    <p>{item.content}</p>
                  </div>
                  <div className="memoryItemActions">
                    <button type="button" onClick={() => beginEdit(item)}>
                      编辑
                    </button>
                    <button type="button" onClick={() => void handleDelete(item)}>
                      删除
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <form className="memoryForm" onSubmit={(event) => void handleSubmit(event)}>
          <h3>{editingId ? '编辑记忆' : '保存记忆'}</h3>
          <label>
            内容
            <textarea
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={5}
              maxLength={10000}
            />
          </label>
          <div className="memoryFormRow">
            <label>
              类型
              <select value={kind} onChange={(event) => setKind(event.target.value as MemoryKind)}>
                <option value="fact">事实</option>
                <option value="preference">偏好</option>
                <option value="instruction">指令</option>
              </select>
            </label>
            <label>
              置信度
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={confidence}
                onChange={(event) => setConfidence(event.target.value)}
              />
            </label>
          </div>
          <div className="memoryFormActions">
            <button type="submit" disabled={saving}>
              {saving ? '保存中' : editingId ? '保存修改' : '保存记忆'}
            </button>
            {editingId ? (
              <button type="button" onClick={resetForm}>
                取消
              </button>
            ) : null}
          </div>
        </form>
      </div>
    </div>
  )
}

