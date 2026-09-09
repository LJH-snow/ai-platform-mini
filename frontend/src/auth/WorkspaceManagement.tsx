import { type FormEvent, type JSX, useEffect, useState } from 'react'
import { type AuthClient } from './client.ts'
import type { WorkspaceSummary } from './types.ts'
import { MemberManagement } from './MemberManagement.tsx'

interface WorkspaceManagementProps {
  client: AuthClient
  apiKey: string
  currentWorkspaceId: string | null
  onWorkspaceChange: (workspaceId: string | null) => void
}

export function WorkspaceManagement({
  client,
  apiKey,
  currentWorkspaceId,
  onWorkspaceChange,
}: WorkspaceManagementProps): JSX.Element {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Create workspace form state
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [creating, setCreating] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const loadWorkspaces = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await client.listWorkspaces(apiKey)
      setWorkspaces(Array.isArray(result) ? result : [])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载工作空间失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadWorkspaces()
  }, [apiKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault()
    if (!newWorkspaceName.trim()) return
    setCreating(true)
    setMessage(null)
    try {
      const ws = await client.createWorkspace(apiKey, newWorkspaceName.trim())
      setMessage(`已创建工作空间 "${ws.name}"`)
      setNewWorkspaceName('')
      await loadWorkspaces()
      // Auto-switch to the new workspace
      onWorkspaceChange(ws.id)
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '创建工作空间失败')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="workspaceManagement">
      <section className="workspaceManagementSection">
        <h3>我的工作空间</h3>
        {message ? (
          <p className={message.includes('失败') ? 'authError' : 'authSuccess'} role="status">
            {message}
          </p>
        ) : null}
        {error ? (
          <p className="authError" role="alert">
            {error}
          </p>
        ) : null}

        {loading ? (
          <p>加载工作空间列表…</p>
        ) : (
          <ul className="workspaceList">
            {workspaces.map((ws) => (
              <li
                key={ws.id}
                className={ws.id === currentWorkspaceId ? 'workspaceItem workspaceItemActive' : 'workspaceItem'}
              >
                <button
                  type="button"
                  className="workspaceSelectButton"
                  onClick={() => onWorkspaceChange(ws.id)}
                >
                  <span>{ws.name}</span>
                  <span className="workspaceRole">{ws.role}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <form className="createWorkspaceForm" onSubmit={handleCreate}>
          <input
            type="text"
            placeholder="新工作空间名称"
            value={newWorkspaceName}
            onChange={(e) => setNewWorkspaceName(e.target.value)}
            required
            disabled={creating}
          />
          <button type="submit" disabled={creating || !newWorkspaceName.trim()}>
            {creating ? '创建中…' : '创建工作空间'}
          </button>
        </form>
      </section>

      {currentWorkspaceId ? (
        <MemberManagement
          client={client}
          apiKey={apiKey}
          workspaceId={currentWorkspaceId}
          currentUserRole={
            workspaces.find((ws) => ws.id === currentWorkspaceId)?.role ?? 'member'
          }
        />
      ) : null}
    </div>
  )
}
