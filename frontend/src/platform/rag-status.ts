import { useEffect, useState } from 'react'

export type RagRuntimeStatus =
  | { kind: 'loading' }
  | { kind: 'ready'; embeddingModel: string | null }
  | { kind: 'disabled' }
  | { kind: 'database_unavailable'; reason: string | null }
  | { kind: 'embedding_unavailable'; reason: string | null }
  | { kind: 'unavailable'; reason: string | null }
  | { kind: 'error' }

export type RagStatusResponse = {
  enabled: boolean
  status: string
  database: string
  database_reason: string | null
  embedding: string
  embedding_reason: string | null
  embedding_model: string | null
}

const joinUrl = (baseUrl: string | undefined, path: string): string => {
  const base = (baseUrl ?? '').replace(/\/$/, '')
  return `${base}${path}`
}

export const isRagStatusResponse = (value: unknown): value is RagStatusResponse => {
  if (typeof value !== 'object' || value === null) return false
  const rag = value as Record<string, unknown>
  return (
    typeof rag.enabled === 'boolean' &&
    typeof rag.status === 'string' &&
    typeof rag.database === 'string' &&
    (rag.database_reason === null || typeof rag.database_reason === 'string') &&
    typeof rag.embedding === 'string' &&
    (rag.embedding_reason === null || typeof rag.embedding_reason === 'string') &&
    (rag.embedding_model === null || typeof rag.embedding_model === 'string')
  )
}

export const normalizeRagStatus = (value: unknown): RagRuntimeStatus | null => {
  if (!isRagStatusResponse(value)) return null
  if (!value.enabled) return { kind: 'disabled' }
  if (value.database !== 'ok') {
    return { kind: 'database_unavailable', reason: value.database_reason }
  }
  if (value.embedding !== 'ok') {
    return { kind: 'embedding_unavailable', reason: value.embedding_reason }
  }
  return { kind: 'ready', embeddingModel: value.embedding_model }
}

export const errorRagStatus = (): RagRuntimeStatus => ({ kind: 'error' })

export async function fetchRagStatus(
  apiBaseUrl: string | undefined,
  fallbackEnabled: boolean | undefined,
  fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<RagRuntimeStatus> {
  let response: Response
  try {
    response = await fetchImpl(joinUrl(apiBaseUrl, '/api/v1/ready'), {
      headers: { Accept: 'application/json' },
      signal,
    })
  } catch {
    return errorRagStatus()
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    return errorRagStatus()
  }

  if (typeof payload !== 'object' || payload === null) {
    return errorRagStatus()
  }

  const rag = (payload as { rag?: unknown }).rag
  if (rag !== undefined) {
    return normalizeRagStatus(rag) ?? errorRagStatus()
  }

  // Legacy/dev fallback: an explicit true value is honored, but an empty
  // runtime config must not downgrade a reachable backend to "disabled".
  if (fallbackEnabled === true) {
    return { kind: 'ready', embeddingModel: null }
  }
  return errorRagStatus()
}

export function useRagRuntimeStatus(
  apiBaseUrl: string | undefined,
  fallbackEnabled: boolean | undefined,
  fetchImpl: typeof fetch = fetch,
): RagRuntimeStatus {
  const [status, setStatus] = useState<RagRuntimeStatus>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    void fetchRagStatus(apiBaseUrl, fallbackEnabled, fetchImpl, controller.signal).then(
      (nextStatus) => {
        if (!cancelled) setStatus(nextStatus)
      },
    )
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [apiBaseUrl, fallbackEnabled, fetchImpl])

  return status
}
