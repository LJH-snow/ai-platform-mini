import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchRagStatus, normalizeRagStatus, useRagRuntimeStatus } from './rag-status.ts'

const readyRag = {
  enabled: true,
  status: 'ready',
  database: 'ok',
  database_reason: null,
  embedding: 'ok',
  embedding_reason: null,
  embedding_model: 'nomic-embed-text',
}

const response = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('rag runtime status', () => {
  it('normalizes a ready backend capability payload', () => {
    expect(normalizeRagStatus(readyRag)).toEqual({
      kind: 'ready',
      embeddingModel: 'nomic-embed-text',
    })
  })

  it('reads the real backend RAG status even when the fallback config is empty', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(200, { rag: readyRag }))

    await expect(fetchRagStatus(undefined, undefined, fetchImpl)).resolves.toEqual({
      kind: 'ready',
      embeddingModel: 'nomic-embed-text',
    })
  })

  it('reports a disabled RAG status from the backend', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      response(200, {
        rag: {
          enabled: false,
          status: 'disabled',
          database: 'not_checked',
          database_reason: null,
          embedding: 'not_checked',
          embedding_reason: null,
          embedding_model: null,
        },
      }),
    )

    await expect(fetchRagStatus('http://localhost:8000', undefined, fetchImpl)).resolves.toEqual({
      kind: 'disabled',
    })
    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8000/api/v1/ready', {
      headers: { Accept: 'application/json' },
      signal: undefined,
    })
  })

  it('parses a database-unavailable body even when readiness returns 503', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      response(503, {
        rag: {
          enabled: true,
          status: 'database_unavailable',
          database: 'unavailable',
          database_reason: 'connection_failed',
          embedding: 'not_checked',
          embedding_reason: null,
          embedding_model: 'nomic-embed-text',
        },
      }),
    )

    await expect(fetchRagStatus(undefined, undefined, fetchImpl)).resolves.toEqual({
      kind: 'database_unavailable',
      reason: 'connection_failed',
    })
  })

  it('parses an embedding-unavailable status without inventing readiness', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      response(503, {
        rag: {
          enabled: true,
          status: 'embedding_unavailable',
          database: 'ok',
          database_reason: null,
          embedding: 'unavailable',
          embedding_reason: 'provider_error',
          embedding_model: 'nomic-embed-text',
        },
      }),
    )

    await expect(fetchRagStatus(undefined, undefined, fetchImpl)).resolves.toEqual({
      kind: 'embedding_unavailable',
      reason: 'provider_error',
    })
  })

  it('falls back to an explicit dev config when the backend omits rag status', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(200, { status: 'ready', checks: { provider: 'ok' } }))

    await expect(fetchRagStatus(undefined, true, fetchImpl)).resolves.toEqual({
      kind: 'ready',
      embeddingModel: null,
    })
  })

  it('does not turn an empty fallback config into a disabled RAG state', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(200, { status: 'ready', checks: { provider: 'ok' } }))

    await expect(fetchRagStatus(undefined, undefined, fetchImpl)).resolves.toEqual({
      kind: 'error',
    })
  })

  it('maps health-check network failures to a safe error state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new Error('connection secret'))

    await expect(fetchRagStatus(undefined, true, fetchImpl)).resolves.toEqual({ kind: 'error' })
  })

  it('starts in loading and resolves to the backend RAG state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(200, { rag: readyRag }))
    const { result } = renderHook(() => useRagRuntimeStatus(undefined, undefined, fetchImpl))

    expect(result.current.kind).toBe('loading')
    await waitFor(() => expect(result.current.kind).toBe('ready'))
  })
})
