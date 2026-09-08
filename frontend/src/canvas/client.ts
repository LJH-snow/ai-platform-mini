export type CanvasNodeConfig = {
  model?: string | null
  systemPrompt?: string | null
  maxSteps?: number | null
  tokenBudget?: number | null
}

export type CanvasNode = {
  id: string
  role: string
  description: string
  config: CanvasNodeConfig
  position: { x: number; y: number }
}

export type CanvasEdge = {
  id: string
  source: string
  target: string
}

export type CanvasDAG = {
  nodes: CanvasNode[]
  edges: CanvasEdge[]
}

export type CanvasConfig = {
  id: string
  workspaceId: string | null
  name: string
  description: string | null
  version: number
  dag: CanvasDAG
  orchestrationConfig: Record<string, unknown>
  createdAt: string | null
  updatedAt: string | null
  createdBy: string | null
}

export type CanvasConfigExport = {
  name: string
  description: string | null
  dag: CanvasDAG
  orchestrationConfig: Record<string, unknown>
}

export type CanvasClientConfig = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

export type CanvasClient = {
  listConfigs: (signal?: AbortSignal) => Promise<CanvasConfig[]>
  getConfig: (id: string, signal?: AbortSignal) => Promise<CanvasConfig>
  createConfig: (input: {
    name: string
    description?: string
    dag: CanvasDAG
    orchestrationConfig?: Record<string, unknown>
  }) => Promise<CanvasConfig>
  updateConfig: (
    id: string,
    input: {
      name?: string
      description?: string
      dag?: CanvasDAG
      orchestrationConfig?: Record<string, unknown>
    },
  ) => Promise<CanvasConfig>
  deleteConfig: (id: string) => Promise<void>
  exportConfig: (id: string, signal?: AbortSignal) => Promise<CanvasConfigExport>
  importConfig: (input: CanvasConfigExport) => Promise<CanvasConfig>
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  if (!baseUrl) return path
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

const authHeaders = (apiKey: string | undefined): Record<string, string> => ({
  Accept: 'application/json',
  'Content-Type': 'application/json',
  ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
})

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isCanvasNode = (value: unknown): value is CanvasNode => {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.role === 'string' &&
    typeof value.description === 'string' &&
    isRecord(value.position)
  )
}

const isCanvasEdge = (value: unknown): value is CanvasEdge => {
  if (!isRecord(value)) return false
  return (
    typeof value.id === 'string' &&
    typeof value.source === 'string' &&
    typeof value.target === 'string'
  )
}

const isCanvasDAG = (value: unknown): value is CanvasDAG => {
  if (!isRecord(value)) return false
  return (
    Array.isArray(value.nodes) &&
    value.nodes.every(isCanvasNode) &&
    Array.isArray(value.edges) &&
    value.edges.every(isCanvasEdge)
  )
}

const adaptConfig = (value: Record<string, unknown>): CanvasConfig => ({
  id: String(value.id ?? ''),
  workspaceId: (value.workspace_id as string | null) ?? null,
  name: String(value.name ?? ''),
  description: (value.description as string | null) ?? null,
  version: Number(value.version ?? 1),
  dag: isCanvasDAG(value.dag) ? value.dag : { nodes: [], edges: [] },
  orchestrationConfig: isRecord(value.orchestration_config) ? value.orchestration_config : {},
  createdAt: (value.created_at as string | null) ?? null,
  updatedAt: (value.updated_at as string | null) ?? null,
  createdBy: (value.created_by as string | null) ?? null,
})

export function createCanvasClient(options: CanvasClientConfig = {}): CanvasClient {
  const fetchImpl = options.fetchImpl ?? fetch
  const multiAgentBase = '/api/v1/multi-agent'

  const requestJson = async <T>(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<T> => {
    const response = await fetchImpl(joinUrl(options.apiBaseUrl, path), {
      ...init,
      signal,
    })
    if (!response.ok) {
      throw new Error(`Canvas request failed: ${response.status}`)
    }
    return (await response.json()) as T
  }

  return {
    async listConfigs(signal) {
      const payload = await requestJson<unknown[]>(
        `${multiAgentBase}/configs`,
        { method: 'GET', headers: authHeaders(options.apiKey) },
        signal,
      )
      return payload.filter(isRecord).map(adaptConfig)
    },
    async getConfig(id, signal) {
      const payload = await requestJson<Record<string, unknown>>(
        `${multiAgentBase}/configs/${encodeURIComponent(id)}`,
        { method: 'GET', headers: authHeaders(options.apiKey) },
        signal,
      )
      return adaptConfig(payload)
    },
    async createConfig(input) {
      const payload = await requestJson<Record<string, unknown>>(`${multiAgentBase}/configs`, {
        method: 'POST',
        headers: authHeaders(options.apiKey),
        body: JSON.stringify({
          name: input.name,
          description: input.description,
          dag: input.dag,
          orchestration_config: input.orchestrationConfig ?? {},
        }),
      })
      return adaptConfig(payload)
    },
    async updateConfig(id, input) {
      const body: Record<string, unknown> = {}
      if (input.name !== undefined) body.name = input.name
      if (input.description !== undefined) body.description = input.description
      if (input.dag !== undefined) body.dag = input.dag
      if (input.orchestrationConfig !== undefined)
        body.orchestration_config = input.orchestrationConfig
      const payload = await requestJson<Record<string, unknown>>(
        `${multiAgentBase}/configs/${encodeURIComponent(id)}`,
        {
          method: 'PUT',
          headers: authHeaders(options.apiKey),
          body: JSON.stringify(body),
        },
      )
      return adaptConfig(payload)
    },
    async deleteConfig(id) {
      await requestJson<void>(`${multiAgentBase}/configs/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        headers: authHeaders(options.apiKey),
      })
    },
    async exportConfig(id, signal) {
      return await requestJson<CanvasConfigExport>(
        `${multiAgentBase}/configs/${encodeURIComponent(id)}/export`,
        { method: 'GET', headers: authHeaders(options.apiKey) },
        signal,
      )
    },
    async importConfig(input) {
      const payload = await requestJson<Record<string, unknown>>(
        `${multiAgentBase}/configs/import`,
        {
          method: 'POST',
          headers: authHeaders(options.apiKey),
          body: JSON.stringify(input),
        },
      )
      return adaptConfig(payload)
    },
  }
}
