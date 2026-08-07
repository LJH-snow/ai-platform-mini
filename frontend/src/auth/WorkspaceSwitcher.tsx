import { type JSX, useEffect, useState } from 'react'
import type { AuthClient } from './client.ts'
import type { WorkspaceSummary } from './types.ts'

interface WorkspaceSwitcherProps {
  client: AuthClient
  apiKey: string
  currentWorkspaceId: string | null
  onWorkspaceChange: (workspaceId: string | null) => void
}

export function WorkspaceSwitcher({
  client,
  apiKey,
  currentWorkspaceId,
  onWorkspaceChange,
}: WorkspaceSwitcherProps): JSX.Element | null {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      if (!apiKey) return
      setLoading(true)
      setError(null)
      try {
        const result = await client.listWorkspaces(apiKey)
        if (!cancelled) {
          setWorkspaces(Array.isArray(result) ? result : [])
          // Auto-select first workspace if none selected
          if (!currentWorkspaceId && Array.isArray(result) && result.length > 0) {
            onWorkspaceChange(result[0].id)
          }
        }
      } catch {
        if (!cancelled) {
          setError('加载工作区失败')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
    // Only reload when apiKey or workspaceId changes.
    // onWorkspaceChange intentionally omitted — it's stable from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, apiKey, currentWorkspaceId])

  // Don't render for legacy keys (no workspaces)
  if (workspaces.length === 0 && !loading && !error) {
    return null
  }

  return (
    <div className="workspaceSwitcher">
      <span className="navSectionLabel">WORKSPACE</span>
      {loading ? (
        <span className="workspaceSwitcherHint">加载中…</span>
      ) : error ? (
        <span className="workspaceSwitcherHint">{error}</span>
      ) : (
        <select
          className="workspaceSelect"
          value={currentWorkspaceId ?? ''}
          onChange={(e) => onWorkspaceChange(e.target.value || null)}
          aria-label="切换工作区"
        >
          {workspaces.map((ws) => (
            <option key={ws.id} value={ws.id}>
              {ws.name} ({ws.role})
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
