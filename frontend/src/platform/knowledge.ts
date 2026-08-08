import type {
  KnowledgeDocument,
  KnowledgeDocumentPreview,
  KnowledgeDocumentsResponse,
  KnowledgeTask,
  KnowledgeTaskStatus,
} from './knowledge-types.ts'

export type { KnowledgeDocumentPreview, KnowledgeTask } from './knowledge-types.ts'

export class KnowledgeApiError extends Error {
  readonly status: number
  readonly code: string | null

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.name = 'KnowledgeApiError'
    this.status = status
    this.code = code
  }
}

type KnowledgeClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

export const isKnowledgeDocument = (value: unknown): value is KnowledgeDocument => {
  if (typeof value !== 'object' || value === null) return false
  const document = value as Partial<KnowledgeDocument>
  return (
    typeof document.document_id === 'string' &&
    typeof document.filename === 'string' &&
    typeof document.text_characters === 'number' &&
    typeof document.chunk_count === 'number' &&
    typeof document.content_sha256 === 'string' &&
    typeof document.embedding_model === 'string' &&
    (typeof document.created_at === 'string' || document.created_at === null) &&
    (document.safety_verdict === null ||
      document.safety_verdict === undefined ||
      document.safety_verdict === 'clean' ||
      document.safety_verdict === 'suspicious' ||
      document.safety_verdict === 'malicious')
  )
}

const taskStatuses = new Set<KnowledgeTaskStatus>(['queued', 'processing', 'completed', 'failed'])

export const isKnowledgeTask = (value: unknown): value is KnowledgeTask => {
  if (typeof value !== 'object' || value === null) return false
  const task = value as Partial<KnowledgeTask>
  return (
    typeof task.task_id === 'string' &&
    typeof task.status === 'string' &&
    taskStatuses.has(task.status as KnowledgeTaskStatus) &&
    (typeof task.document_id === 'string' || task.document_id === null) &&
    (task.filename === undefined || typeof task.filename === 'string') &&
    (task.document === null || task.document === undefined || isKnowledgeDocument(task.document)) &&
    (task.error === undefined || typeof task.error === 'string' || task.error === null) &&
    (task.status_url === undefined ||
      typeof task.status_url === 'string' ||
      task.status_url === null) &&
    (task.created_at === undefined || typeof task.created_at === 'string') &&
    (task.updated_at === undefined || typeof task.updated_at === 'string')
  )
}

export const isKnowledgeDocumentPreview = (value: unknown): value is KnowledgeDocumentPreview => {
  if (typeof value !== 'object' || value === null) return false
  const preview = value as Partial<KnowledgeDocumentPreview>
  return (
    typeof preview.document_id === 'string' &&
    typeof preview.filename === 'string' &&
    (typeof preview.text === 'string' || typeof preview.content === 'string') &&
    typeof preview.truncated === 'boolean'
  )
}

const parseError = async (response: Response): Promise<KnowledgeApiError> => {
  try {
    const payload = (await response.json()) as { message?: unknown; code?: unknown }
    if (typeof payload.message === 'string') {
      return new KnowledgeApiError(
        payload.message,
        response.status,
        typeof payload.code === 'string' ? payload.code : null,
      )
    }
  } catch {
    // Fall through to status-based message.
  }

  const message =
    response.status === 401 || response.status === 403
      ? '请先配置有效的普通用户 API Key。'
      : response.status === 413
        ? 'PDF 文件超过上传限制。'
        : response.status >= 500
          ? '知识库服务暂时不可用，请确认数据库和 Ollama 已启动。'
          : `知识库请求失败（HTTP ${response.status}）。`
  return new KnowledgeApiError(message, response.status)
}

export const createKnowledgeClient = (options: KnowledgeClientOptions = {}) => {
  const fetchImpl = options.fetchImpl ?? fetch
  const headers = (): HeadersInit => ({
    Accept: 'application/json',
    ...(options.apiKey ? { Authorization: `Bearer ${options.apiKey}` } : {}),
  })

  return {
    async listDocuments(): Promise<KnowledgeDocument[]> {
      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/rag/documents'), {
          headers: headers(),
        })
      } catch {
        throw new KnowledgeApiError('无法连接知识库服务，请确认后端已启动。', 0)
      }
      if (!response.ok) throw await parseError(response)

      const payload = (await response.json()) as KnowledgeDocumentsResponse
      if (!Array.isArray(payload.data) || !payload.data.every(isKnowledgeDocument)) {
        throw new KnowledgeApiError('知识库返回的数据格式不完整。', response.status)
      }
      return payload.data
    },

    async uploadPdf(file: File): Promise<KnowledgeTask> {
      const body = new FormData()
      body.append('file', file, file.name)
      let response: Response
      try {
        response = await fetchImpl(joinUrl(options.apiBaseUrl, '/api/v1/rag/documents'), {
          method: 'POST',
          headers: headers(),
          body,
        })
      } catch {
        throw new KnowledgeApiError('无法连接知识库服务，请确认后端已启动。', 0)
      }
      if (!response.ok) throw await parseError(response)
      const payload: unknown = await response.json()
      if (!isKnowledgeTask(payload)) {
        throw new KnowledgeApiError('知识库返回的数据格式不完整。', response.status)
      }
      return payload
    },

    async getTask(taskId: string): Promise<KnowledgeTask> {
      let response: Response
      try {
        response = await fetchImpl(
          joinUrl(options.apiBaseUrl, `/api/v1/rag/tasks/${encodeURIComponent(taskId)}`),
          { headers: headers() },
        )
      } catch {
        throw new KnowledgeApiError('无法连接知识库服务，请确认后端已启动。', 0)
      }
      if (!response.ok) throw await parseError(response)
      const payload: unknown = await response.json()
      if (!isKnowledgeTask(payload)) {
        throw new KnowledgeApiError('知识库返回的数据格式不完整。', response.status)
      }
      return payload
    },

    async deleteDocument(documentId: string): Promise<void> {
      let response: Response
      try {
        response = await fetchImpl(
          joinUrl(options.apiBaseUrl, `/api/v1/rag/documents/${encodeURIComponent(documentId)}`),
          { method: 'DELETE', headers: headers() },
        )
      } catch {
        throw new KnowledgeApiError('无法连接知识库服务，请确认后端已启动。', 0)
      }
      if (!response.ok) throw await parseError(response)
    },

    async getDocumentPreview(documentId: string): Promise<KnowledgeDocumentPreview> {
      let response: Response
      try {
        response = await fetchImpl(
          joinUrl(
            options.apiBaseUrl,
            `/api/v1/rag/documents/${encodeURIComponent(documentId)}/preview`,
          ),
          { headers: headers() },
        )
      } catch {
        throw new KnowledgeApiError('无法连接知识库服务，请确认后端已启动。', 0)
      }
      if (!response.ok) throw await parseError(response)
      const payload: unknown = await response.json()
      if (!isKnowledgeDocumentPreview(payload)) {
        throw new KnowledgeApiError('知识库返回的数据格式不完整。', response.status)
      }
      return payload
    },
  }
}

export type KnowledgeClient = ReturnType<typeof createKnowledgeClient>

export type KnowledgeTaskPollingOptions = {
  intervalMs?: number
  maxAttempts?: number
}

const terminalStatuses = new Set<KnowledgeTaskStatus>(['completed', 'failed'])

export const waitForKnowledgeTask = async (
  client: Pick<KnowledgeClient, 'getTask'>,
  initialTask: KnowledgeTask,
  onUpdate?: (task: KnowledgeTask) => void,
  options: KnowledgeTaskPollingOptions = {},
): Promise<KnowledgeTask> => {
  const intervalMs = Math.max(0, options.intervalMs ?? 500)
  const maxAttempts = Math.max(1, options.maxAttempts ?? 60)
  let current = initialTask
  onUpdate?.(current)

  for (let attempt = 0; !terminalStatuses.has(current.status); attempt += 1) {
    if (attempt >= maxAttempts) {
      throw new KnowledgeApiError('PDF 入库任务轮询超时，请稍后刷新文档列表。', 408)
    }
    if (intervalMs > 0) {
      await new Promise<void>((resolve) => setTimeout(resolve, intervalMs))
    }
    current = await client.getTask(current.task_id)
    onUpdate?.(current)
  }

  if (current.status === 'failed') {
    throw new KnowledgeApiError(current.error ?? 'PDF 入库失败，请稍后重试。', 422)
  }
  return current
}
