import { type FormEvent, type JSX, useState } from 'react'
import type { AuthClient } from './client.ts'

interface RegisterPageProps {
  client: AuthClient
  onRegister: (apiKey: string) => void
  onSwitchToLogin: () => void
}

export function RegisterPage({
  client,
  onRegister,
  onSwitchToLogin,
}: RegisterPageProps): JSX.Element {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (password.length < 6) {
      setError('密码至少需要 6 个字符。')
      return
    }
    setLoading(true)
    try {
      const result = await client.register(email, displayName, password)
      onRegister(result.api_key)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '注册失败。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="authPage">
      <div className="authCard">
        <div className="brandLockup">
          <span className="brandMark" aria-hidden="true">
            A
          </span>
          <div>
            <strong>AI Platform</strong>
            <span>MINI / LOCAL LAB</span>
          </div>
        </div>
        <h1>注册</h1>
        <form onSubmit={handleSubmit}>
          <label htmlFor="reg-email">邮箱</label>
          <input
            id="reg-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <label htmlFor="reg-display-name">显示名称</label>
          <input
            id="reg-display-name"
            type="text"
            autoComplete="name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            disabled={loading}
          />
          <label htmlFor="reg-password">密码</label>
          <input
            id="reg-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
            disabled={loading}
          />
          {error ? (
            <p className="authError" role="alert">
              {error}
            </p>
          ) : null}
          <button type="submit" disabled={loading}>
            {loading ? '注册中…' : '注册'}
          </button>
        </form>
        <p className="authSwitch">
          已有账号？{' '}
          <button type="button" className="linkButton" onClick={onSwitchToLogin}>
            登录
          </button>
        </p>
      </div>
    </main>
  )
}
