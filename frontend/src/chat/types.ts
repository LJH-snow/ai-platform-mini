export type ChatRole = 'system' | 'user' | 'assistant'

export type ChatMessage = {
  id: string
  role: ChatRole
  content: string
}

export type ChatApiMessage = Pick<ChatMessage, 'role' | 'content'>

export type ChatStreamHandlers = {
  onDelta: (content: string) => void
  onRequestId: (requestId: string) => void
}

export type ChatStreamResult = {
  requestId: string | null
}
