export type KnowledgeDocument = {
  document_id: string
  filename: string
  text_characters: number
  chunk_count: number
  content_sha256: string
  embedding_model: string
  created_at: string | null
}

export type KnowledgeDocumentsResponse = {
  data: KnowledgeDocument[]
}

export type KnowledgeTaskStatus = 'queued' | 'processing' | 'completed' | 'failed'

export type KnowledgeTask = {
  task_id: string
  status: KnowledgeTaskStatus
  document_id: string | null
  filename?: string
  document: KnowledgeDocument | null
  error: string | null
  error_code?: string | null
  status_url: string | null
  created_at?: string
  updated_at?: string
}

export type KnowledgeDocumentPreview = {
  document_id: string
  filename: string
  content: string
  text?: string
  truncated: boolean
}
