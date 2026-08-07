// ── Types (mirror the backend OpenAPI contracts) ───────────────────────────

export type PromptVersionSummary = {
  version: number
  is_active: boolean
}

export type PromptSummary = {
  name: string
  active_version: number | null
  versions: PromptVersionSummary[]
}

export type PromptVersion = {
  name: string
  version: number
  content: string
  is_active: boolean
}

export type ToolInfo = {
  name: string
  description: string
  parameters_schema: Record<string, unknown>
  enabled_by_default: boolean
  owner: string
  enabled: boolean
}

export type AgentSummary = {
  id: string
  workspace_id: string
  name: string
  model: string
  prompt_ref: string
  temperature: number
  max_steps: number
  enabled: boolean
  tool_names: string[]
}

export type RunRecordSummary = {
  run_id: string
  model: string
  status: string
  stop_reason: string
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  total_tokens: number | null
  tool_count: number
  rag_reference_count: number
}

export type RunRecordDetail = RunRecordSummary & {
  response: Record<string, unknown>
}

export type UsageTrendPoint = {
  usage_date: string
  total_tokens: number
  request_count: number
}

export type UsageRankingEntry = {
  name: string
  total_tokens: number
  request_count: number
}

export type UsageDashboard = {
  trend: UsageTrendPoint[]
  model_ranking: UsageRankingEntry[]
  key_ranking: UsageRankingEntry[]
}

export type AgentDraft = {
  name: string
  model: string
  prompt_ref: string
  tool_names: string[]
  temperature: number
  max_steps: number
}

export class ConfigApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ConfigApiError'
    this.status = status
  }
}

type ConfigClientOptions = {
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
  if (status === 404) return '资源不存在或无权访问。'
  if (status >= 500) return '服务暂时不可用，请稍后重试。'
  return `请求失败（HTTP ${status}）。`
}

const asObject = (payload: unknown): Record<string, unknown> =>
  typeof payload === 'object' && payload !== null
    ? (payload as Record<string, unknown>)
    : {}

const asArray = (payload: unknown): unknown[] =>
  Array.isArray(payload) ? payload : []

const asString = (value: unknown, fallback: string): string =>
  typeof value === 'string' ? value : fallback

const asNumber = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback

const asBoolean = (value: unknown, fallback: boolean): boolean =>
  typeof value === 'boolean' ? value : fallback

const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

const normalizePromptSummary = (payload: unknown): PromptSummary[] =>
  asArray(payload).map((entry) => {
    const item = asObject(entry)
    const versions = asArray(item.versions).map((version): PromptVersionSummary => {
      const v = asObject(version)
      return {
        version: asNumber(v.version, 0),
        is_active: asBoolean(v.is_active, false),
      }
    })
    return {
      name: asString(item.name, ''),
      active_version:
        typeof item.active_version === 'number' ? item.active_version : null,
      versions,
    }
  })

const normalizePromptVersion = (payload: unknown): PromptVersion => {
  const item = asObject(payload)
  return {
    name: asString(item.name, ''),
    version: asNumber(item.version, 0),
    content: asString(item.content, ''),
    is_active: asBoolean(item.is_active, false),
  }
}

const normalizeTools = (payload: unknown): ToolInfo[] =>
  asArray(payload).map((entry) => {
    const item = asObject(entry)
    return {
      name: asString(item.name, ''),
      description: asString(item.description, ''),
      parameters_schema: asObject(item.parameters_schema),
      enabled_by_default: asBoolean(item.enabled_by_default, false),
      owner: asString(item.owner, ''),
      enabled: asBoolean(item.enabled, false),
    }
  })

const normalizeTool = (payload: unknown): ToolInfo => {
  const item = asObject(payload)
  return {
    name: asString(item.name, ''),
    description: asString(item.description, ''),
    parameters_schema: asObject(item.parameters_schema),
    enabled_by_default: asBoolean(item.enabled_by_default, false),
    owner: asString(item.owner, ''),
    enabled: asBoolean(item.enabled, false),
  }
}

const normalizeRunSummaries = (payload: unknown): RunRecordSummary[] =>
  asArray(payload).map((entry) => {
    const item = asObject(entry)
    return {
      run_id: asString(item.run_id, ''),
      model: asString(item.model, ''),
      status: asString(item.status, ''),
      stop_reason: asString(item.stop_reason, ''),
      started_at: typeof item.started_at === 'string' ? item.started_at : null,
      completed_at: typeof item.completed_at === 'string' ? item.completed_at : null,
      duration_ms:
        typeof item.duration_ms === 'number' ? item.duration_ms : null,
      total_tokens:
        typeof item.total_tokens === 'number' ? item.total_tokens : null,
      tool_count: asNumber(item.tool_count, 0),
      rag_reference_count: asNumber(item.rag_reference_count, 0),
    }
  })

const normalizeUsageDashboard = (payload: unknown): UsageDashboard => {
  const item = asObject(payload)
  const trend = asArray(item.trend).map((entry): UsageTrendPoint => {
    const point = asObject(entry)
    return {
      usage_date: asString(point.usage_date, ''),
      total_tokens: asNumber(point.total_tokens, 0),
      request_count: asNumber(point.request_count, 0),
    }
  })
  const ranking = (value: unknown): UsageRankingEntry[] =>
    asArray(value).map((entry): UsageRankingEntry => {
      const item2 = asObject(entry)
      return {
        name: asString(item2.name, ''),
        total_tokens: asNumber(item2.total_tokens, 0),
        request_count: asNumber(item2.request_count, 0),
      }
    })
  return {
    trend,
    model_ranking: ranking(item.model_ranking),
    key_ranking: ranking(item.key_ranking),
  }
}

const normalizeRunDetail = (payload: unknown): RunRecordDetail => {
  const item = asObject(payload)
  const summary = normalizeRunSummaries([payload])[0]
  return {
    ...summary,
    response: asObject(item.response),
  }
}

const normalizeAgents = (payload: unknown): AgentSummary[] =>
  asArray(payload).map((entry) => {
    const item = asObject(entry)
    return {
      id: asString(item.id, ''),
      workspace_id: asString(item.workspace_id, ''),
      name: asString(item.name, ''),
      model: asString(item.model, ''),
      prompt_ref: asString(item.prompt_ref, ''),
      temperature: asNumber(item.temperature, 0.7),
      max_steps: asNumber(item.max_steps, 10),
      enabled: asBoolean(item.enabled, true),
      tool_names: asStringList(item.tool_names),
    }
  })

// ── Client ─────────────────────────────────────────────────────────────────

export const createConfigClient = (options: ConfigClientOptions = {}) => {
  const fetchImpl = options.fetchImpl ?? fetch

  const request = async <T>(
    method: string,
    path: string,
    body: unknown = null,
    normalize: (payload: unknown) => T,
  ): Promise<T> => {
    const headers: Record<string, string> = {
      Accept: 'application/json',
    }
    if (options.apiKey) {
      headers.Authorization = `Bearer ${options.apiKey}`
    }
    if (body !== null) {
      headers['Content-Type'] = 'application/json'
    }
    let response: Response
    try {
      response = await fetchImpl(joinUrl(options.apiBaseUrl, path), {
        method,
        headers,
        body: body === null ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ConfigApiError('网络请求失败，请检查后端服务。', 0)
    }
    if (!response.ok) {
      throw new ConfigApiError(errorMessage(response.status), response.status)
    }
    const text = await response.text()
    if (!text) {
      return normalize(null)
    }
    try {
      return normalize(JSON.parse(text))
    } catch {
      throw new ConfigApiError('响应格式无效。', 0)
    }
  }

  return {
    listPrompts: () => request('GET', '/api/v1/prompts', null, normalizePromptSummary),
    getPromptVersions: (name: string) =>
      request(
        'GET',
        `/api/v1/prompts/${encodeURIComponent(name)}/versions`,
        null,
        (payload) => asArray(payload).map(normalizePromptVersion),
      ),
    createPromptVersion: (name: string, content: string) =>
      request(
        'POST',
        `/api/v1/prompts/${encodeURIComponent(name)}/versions`,
        { content },
        normalizePromptVersion,
      ),
    activatePrompt: (name: string, version: number) =>
      request(
        'POST',
        `/api/v1/prompts/${encodeURIComponent(name)}/activate`,
        { version },
        normalizePromptVersion,
      ),
    listTools: () => request('GET', '/api/v1/tools', null, normalizeTools),
    setToolEnabled: (toolName: string, enabled: boolean) =>
      request(
        'PUT',
        `/api/v1/tools/${encodeURIComponent(toolName)}`,
        { enabled },
        normalizeTool,
      ),
    listAgents: () => request('GET', '/api/v1/agents', null, normalizeAgents),
    createAgent: (draft: AgentDraft) =>
      request('POST', '/api/v1/agents', draft, (payload) => normalizeAgents([payload])[0]),
    updateAgent: (agentId: string, draft: AgentDraft) =>
      request(
        'PUT',
        `/api/v1/agents/${encodeURIComponent(agentId)}`,
        draft,
        (payload) => normalizeAgents([payload])[0],
      ),
    deleteAgent: (agentId: string) =>
      request('DELETE', `/api/v1/agents/${encodeURIComponent(agentId)}`, null, () => null),
    listRuns: (agentId?: string) =>
      request(
        'GET',
        agentId
          ? `/api/v1/runs?agent_id=${encodeURIComponent(agentId)}`
          : '/api/v1/runs',
        null,
        normalizeRunSummaries,
      ),
    getRun: (runId: string) =>
      request('GET', `/api/v1/runs/${encodeURIComponent(runId)}`, null, normalizeRunDetail),
    getUsageDashboard: (days = 7) =>
      request(
        'GET',
        `/api/v1/usage/dashboard?days=${days}`,
        null,
        normalizeUsageDashboard,
      ),
  }
}

export type ConfigClient = ReturnType<typeof createConfigClient>
