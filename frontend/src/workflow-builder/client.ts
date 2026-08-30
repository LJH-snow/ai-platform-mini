import {
  WORKFLOW_BUILDER_NODE_TYPES,
  type WorkflowBuilderDefinition,
  type WorkflowBuilderEdge,
  type WorkflowBuilderNode,
  type WorkflowBuilderNodeResult,
  type WorkflowBuilderNodeType,
  type WorkflowBuilderRun,
  type WorkflowBuilderUpdateInput,
  type WorkflowBuilderWorkflow,
  type WorkflowBuilderWorkflowInput,
} from './types.ts'

export class WorkflowBuilderApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'WorkflowBuilderApiError'
    this.status = status
  }
}

export class WorkflowBuilderNetworkError extends Error {
  constructor(message = '无法连接 Workflow Builder 服务。') {
    super(message)
    this.name = 'WorkflowBuilderNetworkError'
  }
}

type WorkflowBuilderClientOptions = {
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

const asNodeType = (value: unknown): WorkflowBuilderNodeType | null => {
  if (
    typeof value === 'string' &&
    (WORKFLOW_BUILDER_NODE_TYPES as readonly string[]).includes(value)
  ) {
    return value as WorkflowBuilderNodeType
  }
  return null
}

const normalizeDefinition = (payload: unknown): WorkflowBuilderDefinition => {
  const item = asRecord(payload)
  const nodes = asArray(item.nodes).flatMap((entry): WorkflowBuilderNode[] => {
    const node = asRecord(entry)
    const type = asNodeType(node.type)
    const id = asString(node.id)
    if (type === null || !id) return []
    return [
      {
        id,
        type,
        config: asRecord(node.config),
      },
    ]
  })
  const edges = asArray(item.edges).flatMap((entry): WorkflowBuilderEdge[] => {
    const edge = asRecord(entry)
    const from = asString(edge.from)
    const to = asString(edge.to)
    if (!from || !to) return []
    return [{ from, to }]
  })
  return {
    nodes,
    edges,
    version: asNumber(item.version, 1),
  }
}

const normalizeWorkflow = (payload: unknown): WorkflowBuilderWorkflow => {
  const item = asRecord(payload)
  const status = asString(item.status)
  const normalizedStatus: WorkflowBuilderWorkflow['status'] =
    status === 'published' ? 'published' : 'draft'
  return {
    id: asString(item.id),
    workspace_id: asString(item.workspace_id),
    name: asString(item.name),
    description: asString(item.description),
    status: normalizedStatus,
    version: asNumber(item.version, 1),
    definition: normalizeDefinition(item.definition),
    created_by: asNullableString(item.created_by),
    created_at: asNullableString(item.created_at),
    updated_at: asNullableString(item.updated_at),
  }
}

const normalizeWorkflowList = (payload: unknown): WorkflowBuilderWorkflow[] =>
  asArray(payload).map((entry) => normalizeWorkflow(entry))

const normalizeNodeResult = (payload: unknown): WorkflowBuilderNodeResult => {
  const item = asRecord(payload)
  const type = asNodeType(item.type) ?? 'llm'
  const status = asString(item.status) === 'completed' ? 'completed' : 'failed'
  return {
    node_id: asString(item.node_id),
    type,
    status,
    started_at: asString(item.started_at),
    duration_ms: asNumber(item.duration_ms, 0),
    input_summary: asNullableString(item.input_summary),
    output_summary: asNullableString(item.output_summary),
    error: asNullableString(item.error),
  }
}

const normalizeRun = (payload: unknown): WorkflowBuilderRun => {
  const item = asRecord(payload)
  const status = asString(item.status)
  const normalizedStatus: WorkflowBuilderRun['status'] =
    status === 'running'
      ? 'running'
      : status === 'cancelled'
        ? 'cancelled'
        : status === 'failed'
          ? 'failed'
          : 'completed'
  return {
    id: asString(item.id),
    workflow_id: asString(item.workflow_id),
    workspace_id: asString(item.workspace_id),
    status: normalizedStatus,
    inputs: asRecord(item.inputs),
    definition: normalizeDefinition(item.definition),
    node_results: asArray(item.node_results).map(normalizeNodeResult),
    error: asNullableString(item.error),
    total_duration_ms: typeof item.total_duration_ms === 'number' ? item.total_duration_ms : null,
    created_at: asNullableString(item.created_at),
    completed_at: asNullableString(item.completed_at),
  }
}

const normalizeRunList = (payload: unknown): WorkflowBuilderRun[] =>
  asArray(payload).map((entry) => normalizeRun(entry))

const responseErrorMessage = async (response: Response): Promise<string> => {
  try {
    const text = await response.text()
    const payload = JSON.parse(text) as {
      message?: unknown
      detail?: unknown
    }
    if (typeof payload.message === 'string' && payload.message.trim()) {
      return payload.message
    }
    if (typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail
    }
    if (Array.isArray(payload.detail)) {
      const first = payload.detail[0] as { msg?: unknown } | undefined
      if (first && typeof first.msg === 'string') {
        return first.msg
      }
    }
  } catch {
    // Body may be empty or non-JSON; use the status fallback below.
  }
  if (response.status === 401 || response.status === 403) {
    return 'Workflow Builder 请求未通过鉴权，请检查 API Key。'
  }
  if (response.status === 404) {
    return '流程或运行记录不存在。'
  }
  if (response.status === 409) {
    return '流程状态冲突，已发布流程需要先取消发布。'
  }
  if (response.status === 422) {
    return '流程定义校验失败，请检查节点和连线。'
  }
  if (response.status >= 500) {
    return 'Workflow Builder 服务暂时不可用，请稍后重试。'
  }
  return `Workflow Builder 请求失败（HTTP ${response.status}）。`
}

export const createWorkflowBuilderClient = (options: WorkflowBuilderClientOptions = {}) => {
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
      throw new WorkflowBuilderNetworkError()
    }

    if (!response.ok) {
      throw new WorkflowBuilderApiError(await responseErrorMessage(response), response.status)
    }

    const text = await response.text()
    if (!text) {
      return normalize(null)
    }
    try {
      return normalize(JSON.parse(text))
    } catch {
      throw new WorkflowBuilderApiError('Workflow Builder 返回了无效响应。', 0)
    }
  }

  return {
    listWorkflows: () =>
      request('GET', '/api/v1/workflow-builder/workflows', null, normalizeWorkflowList),
    getWorkflow: (workflowId: string) =>
      request(
        'GET',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}`,
        null,
        normalizeWorkflow,
      ),
    createWorkflow: (input: WorkflowBuilderWorkflowInput) =>
      request('POST', '/api/v1/workflow-builder/workflows', input, normalizeWorkflow),
    updateWorkflow: (workflowId: string, input: WorkflowBuilderUpdateInput) =>
      request(
        'PUT',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}`,
        input,
        normalizeWorkflow,
      ),
    publishWorkflow: (workflowId: string) =>
      request(
        'POST',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}/publish`,
        null,
        normalizeWorkflow,
      ),
    unpublishWorkflow: (workflowId: string) =>
      request(
        'POST',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}/unpublish`,
        null,
        normalizeWorkflow,
      ),
    deleteWorkflow: (workflowId: string) =>
      request(
        'DELETE',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}`,
        null,
        () => null,
      ),
    runWorkflow: (workflowId: string, inputs: Record<string, unknown>) =>
      request(
        'POST',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}/runs`,
        { inputs },
        normalizeRun,
      ),
    listRuns: (workflowId: string, limit = 50) =>
      request(
        'GET',
        `/api/v1/workflow-builder/workflows/${encodeURIComponent(workflowId)}/runs?limit=${limit}`,
        null,
        normalizeRunList,
      ),
    getRun: (runId: string) =>
      request(
        'GET',
        `/api/v1/workflow-builder/workflows/runs/${encodeURIComponent(runId)}`,
        null,
        normalizeRun,
      ),
  }
}

export type WorkflowBuilderClient = ReturnType<typeof createWorkflowBuilderClient>
