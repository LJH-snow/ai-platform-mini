export type WorkflowRunStatus = 'running' | 'pending_approval' | 'completed' | 'rejected' | 'failed'

export type WorkflowRunStage =
  | 'starting'
  | 'awaiting_approval'
  | 'completed'
  | 'rejected'
  | 'failed'

export type WorkflowStatus = {
  threadId: string
  status: WorkflowRunStatus
  stage: WorkflowRunStage
  filename: string | null
  reportTopic: string | null
  pageCount: number | null
  retrievalQuery: string | null
  references: number | null
  retrievalWarning: string | null
  draftSummary: string | null
  report: string | null
  model: string | null
  promptTokens: number | null
  completionTokens: number | null
  revisionCount: number | null
  errorCode: string | null
  errorMessage: string | null
  createdAt: string | null
  updatedAt: string | null
}
