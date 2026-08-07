import { type FormEvent, type JSX, useState } from 'react'
import type { AuthClient } from './client.ts'

interface LoginPageProps {
  client: AuthClient
  onLogin: (apiKey: string) => void
  onSwitchToRegister: () => void
}

export function LoginPage({ client, onLogin, onSwitchToRegister }: LoginPageProps): JSX.Element {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const result = await client.login(email, password)
      onLogin(result.api_key)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '登录失败。')
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
        <h1>登录</h1>
        <form onSubmit={handleSubmit}>
          <label htmlFor="login-email">邮箱</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={loading}
          />
          <label htmlFor="login-password">密码</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
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
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
        <p className="authSwitch">
          没有账号？{' '}
          <button type="button" className="linkButton" onClick={onSwitchToRegister}>
            注册
          </button>
        </p>
      </div>
    </main>
  )
}
