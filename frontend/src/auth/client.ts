import type {
  LoginResponse,
  MeResponse,
  MemberSummary,
  RegisterResponse,
  WorkspaceSummary,
} from './types.ts'

export class AuthApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'AuthApiError'
    this.status = status
  }
}

interface AuthClientOptions {
  apiBaseUrl?: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

const errorMessage = (status: number): string => {
  if (status === 401) return '邮箱或密码不正确。'
  if (status === 409) return '该邮箱已注册。'
  if (status === 422) return '输入格式不正确。'
  if (status === 503) return '登录功能暂不可用（需要 PostgreSQL 存储）。'
  if (status >= 500) return '后台服务暂时不可用，请稍后重试。'
  return `请求失败（HTTP ${status}）。`
}

export const createAuthClient = (options: AuthClientOptions) => {
  const fetchImpl = options.fetchImpl ?? fetch

  const request = async <T>(path: string, init: RequestInit = {}, apiKey?: string): Promise<T> => {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
    }
    if (apiKey) {
      headers['Authorization'] = `Bearer ${apiKey}`
    }
    let response: Response
    try {
      response = await fetchImpl(joinUrl(options.apiBaseUrl, path), {
        ...init,
        headers: { ...headers, ...init.headers },
      })
    } catch {
      throw new AuthApiError('网络连接失败。', 0)
    }
    if (!response.ok) {
      throw new AuthApiError(errorMessage(response.status), response.status)
    }
    return (await response.json()) as T
  }

  return {
    /** Register a new user. Returns user, default workspace, and API key. */
    register: (email: string, displayName: string, password: string) =>
      request<RegisterResponse>('/api/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          email,
          display_name: displayName,
          password,
        }),
      }),

    /** Login with email/password. Returns user, workspaces, and API key. */
    login: (email: string, password: string) =>
      request<LoginResponse>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      }),

    /** Get current user profile and workspaces. */
    me: (apiKey: string) => request<MeResponse>('/api/v1/auth/me', {}, apiKey),

    /** List workspaces for the authenticated user. */
    listWorkspaces: (apiKey: string) =>
      request<WorkspaceSummary[]>('/api/v1/workspaces', {}, apiKey),

    /** Create a new workspace. */
    createWorkspace: (apiKey: string, name: string) =>
      request<WorkspaceSummary>(
        '/api/v1/workspaces',
        { method: 'POST', body: JSON.stringify({ name }) },
        apiKey,
      ),

    /** Add a member to a workspace. */
    addMember: (apiKey: string, workspaceId: string, email: string, role: string) =>
      request<MemberSummary>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
        { method: 'POST', body: JSON.stringify({ email, role }) },
        apiKey,
      ),

    /** List members of a workspace. */
    listMembers: (apiKey: string, workspaceId: string) =>
      request<MemberSummary[]>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
        {},
        apiKey,
      ),

    /** Update a member's role. */
    updateMemberRole: (apiKey: string, workspaceId: string, userId: string, role: string) =>
      request<{ status: string; role: string }>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
        { method: 'PUT', body: JSON.stringify({ role }) },
        apiKey,
      ),

    /** Remove a member from a workspace. */
    removeMember: (apiKey: string, workspaceId: string, userId: string) =>
      request<{ status: string }>(
        `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/members/${encodeURIComponent(userId)}`,
        { method: 'DELETE' },
        apiKey,
      ),
  }
}

export type AuthClient = ReturnType<typeof createAuthClient>
