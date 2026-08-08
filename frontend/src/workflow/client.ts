import type { WorkflowStatus } from './types.ts'

export class WorkflowApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'WorkflowApiError'
    this.status = status
  }
}

export class WorkflowNetworkError extends Error {
  constructor(message = '无法连接到工作流服务。') {
    super(message)
    this.name = 'WorkflowNetworkError'
  }
}

type WorkflowClientOptions = {
  apiBaseUrl?: string
  apiKey?: string
  fetchImpl?: typeof fetch
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

const safeErrorMessage = (status: number): string => {
  if (status === 401 || status === 403) return '工作流请求未通过鉴权，请检查 API Key。'
  if (status === 404) return '工作流不存在或已被删除。'
  if (status === 409) return '工作流状态冲突，请刷新后重试。'
  if (status === 413) return 'PDF 文件过大，请压缩后重试。'
  if (status === 429) return '请求过于频繁，请稍后重试。'
  if (status >= 500) return '工作流服务暂时不可用，请稍后重试。'
  return `工作流请求失败（HTTP ${status}），请稍后重试。`
}

const parseStatus = (payload: unknown): WorkflowStatus => {
  const defaults: WorkflowStatus = {
    threadId: '',
    status: 'failed',
    stage: 'failed',
    filename: null,
    reportTopic: null,
    pageCount: null,
    retrievalQuery: null,
    references: null,
    retrievalWarning: null,
    draftSummary: null,
    report: null,
    model: null,
    promptTokens: null,
    completionTokens: null,
    revisionCount: null,
    errorCode: null,
    errorMessage: null,
    createdAt: null,
    updatedAt: null,
  }

  if (typeof payload !== 'object' || payload === null) return defaults

  const record = payload as Record<string, unknown>
  const result: WorkflowStatus = { ...defaults }

  if (typeof record.thread_id === 'string') result.threadId = record.thread_id
  if (
    record.status === 'running' ||
    record.status === 'pending_approval' ||
    record.status === 'completed' ||
    record.status === 'rejected' ||
    record.status === 'failed'
  ) {
    result.status = record.status
  }
  if (
    record.stage === 'starting' ||
    record.stage === 'awaiting_approval' ||
    record.stage === 'completed' ||
    record.stage === 'rejected' ||
    record.stage === 'failed'
  ) {
    result.stage = record.stage
  }
  if (typeof record.filename === 'string') result.filename = record.filename
  if (typeof record.report_topic === 'string') result.reportTopic = record.report_topic
  if (typeof record.page_count === 'number') result.pageCount = record.page_count
  if (typeof record.retrieval_query === 'string') result.retrievalQuery = record.retrieval_query
  if (typeof record.references === 'number') result.references = record.references
  if (typeof record.retrieval_warning === 'string')
    result.retrievalWarning = record.retrieval_warning
  if (typeof record.draft_summary === 'string') result.draftSummary = record.draft_summary
  if (typeof record.report === 'string') result.report = record.report
  if (typeof record.model === 'string') result.model = record.model
  if (typeof record.prompt_tokens === 'number') result.promptTokens = record.prompt_tokens
  if (typeof record.completion_tokens === 'number')
    result.completionTokens = record.completion_tokens
  if (typeof record.revision_count === 'number') result.revisionCount = record.revision_count
  if (typeof record.error_code === 'string') result.errorCode = record.error_code
  if (typeof record.error_message === 'string') result.errorMessage = record.error_message
  if (typeof record.created_at === 'string') result.createdAt = record.created_at
  if (typeof record.updated_at === 'string') result.updatedAt = record.updated_at

  return result
}

const jsonRequest = async (
  fetchImpl: typeof fetch,
  baseUrl: string | undefined,
  apiKey: string | undefined,
  path: string,
  init: RequestInit = {},
): Promise<WorkflowStatus> => {
  let response: Response
  try {
    response = await fetchImpl(joinUrl(baseUrl, path), {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body && !(init.body instanceof FormData)
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
        ...init.headers,
      },
    })
    if (init.signal?.aborted) {
      throw new WorkflowNetworkError('请求已取消。')
    }
  } catch {
    throw new WorkflowNetworkError()
  }

  if (!response.ok) {
    throw new WorkflowApiError(safeErrorMessage(response.status), response.status)
  }

  try {
    const payload = await response.json()
    return parseStatus(payload)
  } catch {
    throw new WorkflowApiError('工作流服务返回了无法识别的响应。', 0)
  }
}

export const createWorkflowClient = (options: WorkflowClientOptions = {}) => {
  const fetchImpl = options.fetchImpl ?? fetch
  const { apiBaseUrl, apiKey } = options

  return {
    async uploadPdf(pdfFile: File, topic?: string, signal?: AbortSignal): Promise<WorkflowStatus> {
      const formData = new FormData()
      formData.append('file', pdfFile)
      if (topic?.trim()) {
        formData.append('topic', topic.trim())
      }

      return jsonRequest(fetchImpl, apiBaseUrl, apiKey, '/api/v1/workflows/pdf-report', {
        method: 'POST',
        body: formData,
        signal,
      })
    },

    async listRuns(limit = 20): Promise<WorkflowRunSummary[]> {
      const records = await jsonRequest(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/workflows?limit=${limit}`,
      )
      return Array.isArray(records)
        ? records.map((record: Record<string, unknown>): WorkflowRunSummary => ({
            threadId: typeof record.thread_id === 'string' ? record.thread_id : '',
            status:
              typeof record.status === 'string' ? record.status : 'failed',
            stage: typeof record.stage === 'string' ? record.stage : 'failed',
            filename: typeof record.filename === 'string' ? record.filename : null,
            reportTopic:
              typeof record.report_topic === 'string' ? record.report_topic : null,
            createdAt:
              typeof record.created_at === 'string' ? record.created_at : null,
          }))
        : []
    },

    async getStatus(threadId: string, signal?: AbortSignal): Promise<WorkflowStatus> {
      return jsonRequest(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/workflows/${encodeURIComponent(threadId)}`,
        { signal },
      )
    },

    async approve(threadId: string, signal?: AbortSignal): Promise<WorkflowStatus> {
      return jsonRequest(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/workflows/${encodeURIComponent(threadId)}/approve`,
        { method: 'POST', signal },
      )
    },

    async reject(
      threadId: string,
      feedback: string,
      signal?: AbortSignal,
    ): Promise<WorkflowStatus> {
      return jsonRequest(
        fetchImpl,
        apiBaseUrl,
        apiKey,
        `/api/v1/workflows/${encodeURIComponent(threadId)}/reject`,
        {
          method: 'POST',
          body: JSON.stringify({ feedback }),
          signal,
        },
      )
    },
  }
}

export type WorkflowRunSummary = {
  threadId: string
  status: string
  stage: string
  filename: string | null
  reportTopic: string | null
  createdAt: string | null
}

export type WorkflowClient = ReturnType<typeof createWorkflowClient>
