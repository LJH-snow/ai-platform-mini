import { MEMORY_KINDS, MEMORY_SOURCES } from './types.ts'
import type { MemoryInput, MemoryItem, MemoryKind, MemorySource } from './types.ts'

export class MemoryApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'MemoryApiError'
    this.status = status
  }
}

export class MemoryNetworkError extends Error {
  constructor(message = '无法连接记忆服务。') {
    super(message)
    this.name = 'MemoryNetworkError'
  }
}

type MemoryClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

const asRecord = (value: unknown): Record<string, unknown> =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])

const asString = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback

const asNumber = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback

const asNullableString = (value: unknown): string | null =>
  typeof value === 'string' ? value : null

const asKind = (value: unknown): MemoryKind | 'fact' => {
  if (typeof value === 'string' && (MEMORY_KINDS as readonly string[]).includes(value)) {
    return value as MemoryKind
  }
  return 'fact'
}

const asSource = (value: unknown): MemorySource | 'explicit' => {
  if (typeof value === 'string' && (MEMORY_SOURCES as readonly string[]).includes(value)) {
    return value as MemorySource
  }
  return 'explicit'
}

export const normalizeMemoryItem = (payload: unknown): MemoryItem => {
  const item = asRecord(payload)
  return {
    id: asString(item.id),
    content: asString(item.content),
    source: asSource(item.source),
    kind: asKind(item.kind),
    confidence: asNumber(item.confidence, 1),
    metadata: asRecord(item.metadata),
    created_at: asNullableString(item.created_at),
    updated_at: asNullableString(item.updated_at),
    last_used_at: asNullableString(item.last_used_at),
  }
}

const errorMessage = async (response: Response): Promise<string> => {
  try {
    const text = await response.text()
    const payload = JSON.parse(text) as { message?: unknown; detail?: unknown }
    if (typeof payload.message === 'string' && payload.message.trim()) {
      return payload.message
    }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail
    }
  } catch {
    // Keep the status fallback below.
  }
  if (response.status === 401 || response.status === 403) {
    return '记忆请求未通过鉴权，请检查 API Key。'
  }
  if (response.status === 404) return '记忆不存在或已被删除。'
  if (response.status === 422) return '记忆内容校验失败。'
  if (response.status === 429) return '请求过于频繁，请稍后重试。'
  if (response.status >= 500) return '记忆服务暂时不可用，请稍后重试。'
  return `记忆请求失败（HTTP ${response.status}）。`
}

const request = async <T>(
  fetchImpl: typeof fetch,
  baseUrl: string | undefined,
  apiKey: string | undefined,
  path: string,
  init: RequestInit = {},
  normalize: (payload: unknown) => T,
): Promise<T> => {
  let response: Response
  try {
    response = await fetchImpl(joinUrl(baseUrl, path), {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        ...init.headers,
      },
    })
  } catch {
    throw new MemoryNetworkError()
  }
  if (!response.ok) {
    throw new MemoryApiError(await errorMessage(response), response.status)
  }
  if (response.status === 204) {
    return normalize(null)
  }
  try {
    return normalize(await response.json())
  } catch {
    throw new MemoryApiError('记忆服务返回了无法识别的响应。', 0)
  }
}

export const createMemoryClient = (options: MemoryClientOptions = {}) => {
  const fetchImpl = options.fetchImpl ?? fetch
  const { apiBaseUrl, apiKey } = options

  return {
    async list(query = '', limit = 50, signal?: AbortSignal): Promise<MemoryItem[]> {
      const params = new URLSearchParams()
      params.set('limit', String(limit))
      if (query.trim()) params.set('q', query.trim())
      return request(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/memory?${params.toString()}`,
        { signal },
        (payload) => asArray(payload).map(normalizeMemoryItem),
      )
    },

    async create(input: MemoryInput): Promise<MemoryItem> {
      return request(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        '/api/v1/memory',
        {
          method: 'POST',
          body: JSON.stringify({
            content: input.content,
            source: input.source ?? 'explicit',
            kind: input.kind ?? 'fact',
            confidence: input.confidence ?? 1,
            metadata: input.metadata ?? {},
          }),
        },
        normalizeMemoryItem,
      )
    },

    async update(id: string, input: Partial<MemoryInput>): Promise<MemoryItem> {
      return request(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/memory/${encodeURIComponent(id)}`,
        {
          method: 'PATCH',
          body: JSON.stringify(input),
        },
        normalizeMemoryItem,
      )
    },

    async delete(id: string): Promise<void> {
      await request(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/memory/${encodeURIComponent(id)}`,
        { method: 'DELETE' },
        () => undefined,
      )
    },
  }
}

export type MemoryClient = ReturnType<typeof createMemoryClient>

