import { type FormEvent, type JSX, useEffect, useRef, useState } from 'react'
import type { AuthClient } from './client.ts'
import type { MemberSummary } from './types.ts'

interface MemberManagementProps {
  client: AuthClient
  apiKey: string
  workspaceId: string
  /** The current user's role in this workspace. */
  currentUserRole: string
}

const ROLE_LABELS: Record<string, string> = {
  owner: '所有者',
  admin: '管理员',
  member: '成员',
  viewer: '只读',
}

const ALLOWED_ROLES = ['admin', 'member', 'viewer'] as const
const CAN_MANAGE = new Set(['owner', 'admin'])

export function MemberManagement({
  client,
  apiKey,
  workspaceId,
  currentUserRole,
}: MemberManagementProps): JSX.Element | null {
  const [members, setMembers] = useState<MemberSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  // Add-member form state
  const [addEmail, setAddEmail] = useState('')
  const [addRole, setAddRole] = useState('member')
  const [adding, setAdding] = useState(false)

  const addFormRef = useRef<HTMLFormElement>(null)

  const loadMembers = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await client.listMembers(apiKey, workspaceId)
      setMembers(result)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载成员失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadMembers()
  }, [workspaceId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!CAN_MANAGE.has(currentUserRole)) return null

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault()
    setAdding(true)
    setMessage(null)
    try {
      await client.addMember(apiKey, workspaceId, addEmail, addRole)
      setMessage(`已添加 ${addEmail}`)
      setAddEmail('')
      setAddRole('member')
      await loadMembers()
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '添加成员失败')
    } finally {
      setAdding(false)
    }
  }

  const handleRoleChange = async (userId: string, role: string) => {
    setMessage(null)
    try {
      await client.updateMemberRole(apiKey, workspaceId, userId, role)
      setMessage('角色已更新')
      await loadMembers()
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '更新角色失败')
    }
  }

  const handleRemove = async (userId: string, email: string) => {
    if (!window.confirm(`确定要移除成员 ${email}？`)) return
    setMessage(null)
    try {
      await client.removeMember(apiKey, workspaceId, userId)
      setMessage(`已移除 ${email}`)
      await loadMembers()
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '移除成员失败')
    }
  }

  return (
    <section className="memberManagement" aria-label="成员管理">
      <h3>成员管理</h3>
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

      {/* Add member form */}
      <form ref={addFormRef} className="addMemberForm" onSubmit={handleAdd}>
        <input
          type="email"
          placeholder="成员邮箱"
          value={addEmail}
          onChange={(e) => setAddEmail(e.target.value)}
          required
          disabled={adding}
        />
        <select
          value={addRole}
          onChange={(e) => setAddRole(e.target.value)}
          aria-label="角色"
          disabled={adding}
        >
          {ALLOWED_ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABELS[r]}
            </option>
          ))}
        </select>
        <button type="submit" disabled={adding || !addEmail.trim()}>
          {adding ? '添加中…' : '添加成员'}
        </button>
      </form>

      {/* Member table */}
      {loading ? (
        <p>加载成员列表…</p>
      ) : (
        <table className="memberTable">
          <thead>
            <tr>
              <th>用户</th>
              <th>邮箱</th>
              <th>角色</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.display_name}</td>
                <td>{m.email}</td>
                <td>
                  {m.role === 'owner' ? (
                    <span>{ROLE_LABELS[m.role]}</span>
                  ) : (
                    <select
                      value={m.role}
                      onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                      aria-label={`${m.display_name} 的角色`}
                    >
                      {ALLOWED_ROLES.map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABELS[r]}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td>
                  {m.role !== 'owner' ? (
                    <button
                      type="button"
                      className="dangerButton"
                      onClick={() => handleRemove(m.user_id, m.email)}
                    >
                      移除
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
