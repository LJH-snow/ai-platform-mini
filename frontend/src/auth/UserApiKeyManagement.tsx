import { type FormEvent, type JSX, useEffect, useState } from 'react'
import type { AuthClient } from './client.ts'
import type { CreatedUserApiKey, UserApiKeySummary } from './types.ts'

interface UserApiKeyManagementProps {
  client: AuthClient
  apiKey: string
}

const STATUS_LABELS: Record<string, string> = {
  active: '有效',
  revoked: '已撤销',
}

export function UserApiKeyManagement({ client, apiKey }: UserApiKeyManagementProps): JSX.Element {
  const [keys, setKeys] = useState<UserApiKeySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const [newKeyName, setNewKeyName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createdKey, setCreatedKey] = useState<CreatedUserApiKey | null>(null)
  const [revokingPrefix, setRevokingPrefix] = useState<string | null>(null)

  const loadKeys = async () => {
    if (!apiKey) {
      setLoading(false)
      setKeys([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const result = await client.listKeys(apiKey)
      setKeys(Array.isArray(result) ? result : [])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '加载 API Key 失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadKeys()
  }, [apiKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault()
    const name = newKeyName.trim()
    if (!name) return
    setCreating(true)
    setMessage(null)
    setCreatedKey(null)
    try {
      const created = await client.createKey(apiKey, name)
      setCreatedKey(created)
      setNewKeyName('')
      await loadKeys()
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '创建 API Key 失败')
    } finally {
      setCreating(false)
    }
  }

  const handleRevoke = async (prefix: string) => {
    if (!window.confirm(`确定撤销 Key ${prefix} 吗？撤销后该 Key 将无法继续调用模型。`)) {
      return
    }
    setRevokingPrefix(prefix)
    setMessage(null)
    try {
      await client.revokeKey(apiKey, prefix)
      setMessage(`已撤销 Key ${prefix}。`)
      setCreatedKey(null)
      await loadKeys()
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : '撤销 API Key 失败')
    } finally {
      setRevokingPrefix(null)
    }
  }

  const copyCreatedKey = async () => {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey.raw_key)
      setMessage('原始 Key 已复制。')
    } catch {
      setMessage('复制失败，请手动复制。')
    }
  }

  return (
    <section
      className="workspaceManagementSection userKeyManagementSection"
      aria-label="我的 API Key"
    >
      <h3>我的 API Key</h3>
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

      {!apiKey ? (
        <p className="formHint">登录后可以在这里创建和管理自己的 API Key。</p>
      ) : (
        <>
          {createdKey ? (
            <div className="secretKeyNotice">
              <strong>请立即复制并保存</strong>
              <p>关闭或刷新页面后，原始 Key 不会再次返回。</p>
              <code>{createdKey.raw_key}</code>
              <button type="button" onClick={() => void copyCreatedKey()}>
                复制原始 Key
              </button>
            </div>
          ) : null}

          <form className="createWorkspaceForm" onSubmit={handleCreate}>
            <input
              type="text"
              placeholder="用途名称（如 local-cli）"
              value={newKeyName}
              onChange={(event) => setNewKeyName(event.target.value)}
              disabled={creating}
              maxLength={128}
            />
            <button type="submit" disabled={creating || !newKeyName.trim()}>
              {creating ? '创建中…' : '创建用户 Key'}
            </button>
          </form>

          {loading ? (
            <p>加载 API Key 列表…</p>
          ) : (
            <div className="keyTable" role="table" aria-label="我的 API Key 列表">
              <div className="keyRow keyHeader">
                <span>名称</span>
                <span>前缀</span>
                <span>状态</span>
                <span>创建时间</span>
                <span>操作</span>
              </div>
              {keys.map((item) => (
                <div className="keyRow" key={item.key_hash_prefix}>
                  <span>{item.name}</span>
                  <code>{item.key_hash_prefix}…</code>
                  <span className={`statusText status-${item.status}`}>
                    {STATUS_LABELS[item.status] ?? item.status}
                  </span>
                  <span>{item.created_at ?? '—'}</span>
                  <span>
                    {item.status === 'active' ? (
                      <button
                        type="button"
                        className="dangerButton"
                        disabled={revokingPrefix === item.key_hash_prefix}
                        onClick={() => void handleRevoke(item.key_hash_prefix)}
                      >
                        {revokingPrefix === item.key_hash_prefix ? '撤销中…' : '撤销'}
                      </button>
                    ) : (
                      '—'
                    )}
                  </span>
                </div>
              ))}
              {keys.length === 0 ? <p className="formHint">暂无 API Key。</p> : null}
            </div>
          )}
        </>
      )}
    </section>
  )
}
