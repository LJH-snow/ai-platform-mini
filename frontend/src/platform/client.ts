import type { PlatformModel, PlatformModelsResponse } from './types.ts'

export class PlatformApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'PlatformApiError'
    this.status = status
  }
}

type PlatformClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

const errorMessage = (status: number): string => {
  if (status === 401 || status === 403) return '请先配置有效的普通用户 API Key。'
  if (status === 404) return '模型目录接口暂不可用。'
  if (status >= 500) return '模型服务暂时不可用，请稍后重试。'
  return `模型目录请求失败（HTTP ${status}）。`
}

const normalizeModels = (payload: unknown): PlatformModel[] => {
  if (typeof payload !== 'object' || payload === null) return []
  const data = (payload as { data?: unknown }).data
  if (!Array.isArray(data)) return []

  return data.flatMap((entry): PlatformModel[] => {
    if (typeof entry !== 'object' || entry === null) return []
    const model = entry as { id?: unknown; owned_by?: unknown }
    if (typeof model.id !== 'string' || model.id.trim() === '') return []
    return [
      { id: model.id, provider: typeof model.owned_by === 'string' ? model.owned_by : 'unknown' },
    ]
  })
}

export const createPlatformClient = (options: PlatformClientOptions = {}) => {
  const fetchImpl = options.fetchImpl ?? fetch

  return {
    async listModels(): Promise<PlatformModel[]> {
      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/models'), {
          headers: {
            Accept: 'application/json',
            ...(options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {}),
          },
        })
      } catch {
        throw new PlatformApiError('无法连接模型服务，请确认后端和 Ollama 已启动。', 0)
      }

      if (!response.ok) throw new PlatformApiError(errorMessage(response.status), response.status)

      let payload: PlatformModelsResponse
      try {
        payload = (await response.json()) as PlatformModelsResponse
      } catch {
        throw new PlatformApiError('模型目录返回格式错误。', response.status)
      }

      return normalizeModels(payload)
    },
  }
}

export type PlatformClient = ReturnType<typeof createPlatformClient>
