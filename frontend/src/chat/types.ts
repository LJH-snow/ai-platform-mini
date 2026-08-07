export type ChatRole = 'system' | 'user' | 'assistant'

export type ChatMessage = {
  id: string
  role: ChatRole
  content: string
}

export type ChatApiMessage = Pick<ChatMessage, 'role' | 'content'>

export type ConversationHistoryMessage = {
  id: number
  thread_id: string
  role: ChatRole
  content: string
  token_count: number
  created_at: string | null
}

export type ConversationSummary = {
  thread_id: string
  title: string
  created_at: string | null
  updated_at: string | null
}

export type ChatStreamHandlers = {
  onDelta: (content: string) => void
  onRequestId: (requestId: string) => void
  onThreadId?: (threadId: string) => void
}

export type ChatStreamResult = {
  requestId: string | null
  threadId: string | null
}
