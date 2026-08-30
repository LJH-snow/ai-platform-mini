export const MEMORY_KINDS = ['fact', 'preference', 'instruction'] as const
export const MEMORY_SOURCES = ['explicit', 'conversation', 'system'] as const

export type MemoryKind = (typeof MEMORY_KINDS)[number]
export type MemorySource = (typeof MEMORY_SOURCES)[number]

export type MemoryItem = {
  id: string
  content: string
  source: MemorySource
  kind: MemoryKind
  confidence: number
  metadata: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
  last_used_at: string | null
}

export type MemoryInput = {
  content: string
  source?: MemorySource
  kind?: MemoryKind
  confidence?: number
  metadata?: Record<string, unknown>
}

