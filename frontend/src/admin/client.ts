import type {
  AdminApiKey,
  AgentRunRecord,
  AgentRunSummary,
  CreatedAdminApiKey,
  UsageAggregation,
} from './types.ts'

export class AdminApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'AdminApiError'
    this.status = status
  }
}

type AdminClientOptions = {
  apiBaseUrl?: string
  apiKey: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

const errorMessage = (status: number): string => {
  if (status === 401 || status === 403) return '管理员 Key 无效或已失效。'
  if (status === 404) return '记录不存在。'
  if (status >= 500) return '后台服务暂时不可用，请稍后重试。'
  return `管理员请求失败（HTTP ${status}）。`
}

export const createAdminClient = (options: AdminClientOptions) => {
  const fetchImpl = options.fetchImpl ?? fetch

  const request = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
    let response: Response
    try {
      response = await fetchImpl(joinUrl(options.apiBaseUrl, path), {
        ...init,
        headers: {
          Accept: 'application/json',
          ...(init.body ? { 'Content-Type': 'application/json' } : {}),
          Authorization: `Bearer ${options.apiKey}`,
          ...init.headers,
        },
      })
    } catch {
      throw new AdminApiError('管理员后台网络连接失败。', 0)
    }

    if (!response.ok) throw new AdminApiError(errorMessage(response.status), response.status)
    return (await response.json()) as T
  }

  return {
    listKeys: () => request<AdminApiKey[]>('/admin/api-keys'),
    createKey: (name: string) =>
      request<CreatedAdminApiKey>('/admin/api-keys', {
        method: 'POST',
        body: JSON.stringify({ name }),
      }),
    revokeKey: (prefix: string) =>
      request<{ key_hash_prefix: string; revoked: boolean }>(`/admin/api-keys/${prefix}`, {
        method: 'DELETE',
      }),
    getDailyUsage: (prefix: string, date: string) =>
      request<UsageAggregation[]>(
        `/admin/usage/daily?key_hash_prefix=${encodeURIComponent(prefix)}&date=${encodeURIComponent(date)}`,
      ),
    getMonthlyUsage: (prefix: string, month: string) =>
      request<UsageAggregation[]>(
        `/admin/usage/monthly?key_hash_prefix=${encodeURIComponent(prefix)}&month=${encodeURIComponent(month)}`,
      ),
    listRuns: (limit = 50) =>
      request<AgentRunSummary[]>(`/admin/agent-runs?limit=${encodeURIComponent(String(limit))}`),
    getRun: (runId: string) =>
      request<AgentRunRecord>(`/admin/agent-runs/${encodeURIComponent(runId)}`),
  }
}

export type AdminClient = ReturnType<typeof createAdminClient>
