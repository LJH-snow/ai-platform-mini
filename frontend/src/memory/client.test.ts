import { afterEach, describe, expect, it, vi } from 'vitest'

import { createMemoryClient } from './client.ts'

const memoryPayload = {
  id: 'memory-1',
  content: '汇报时先给结论',
  source: 'explicit',
  kind: 'instruction',
  confidence: 0.95,
  metadata: { channel: 'api' },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:01Z',
  last_used_at: null,
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createMemoryClient', () => {
  it('lists and normalizes memory items', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify([memoryPayload]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = createMemoryClient({ fetchImpl, apiKey: 'sk-test' })
    const items = await client.list('汇报')

    expect(items[0]).toMatchObject({
      id: 'memory-1',
      content: '汇报时先给结论',
      kind: 'instruction',
      confidence: 0.95,
      metadata: { channel: 'api' },
    })
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(String(call[0])).toContain('/api/v1/memory')
    expect(String(call[0])).toContain('q=')
    expect((call[1]!.headers as Record<string, string>).Authorization).toBe('Bearer sk-test')
  })

  it('creates memory with explicit source defaults', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(memoryPayload), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const client = createMemoryClient({ fetchImpl })
    const created = await client.create({ content: '汇报时先给结论', kind: 'instruction' })

    expect(created.id).toBe('memory-1')
    const call = fetchImpl.mock.calls[0] as [RequestInfo, RequestInit | undefined]
    expect(call[1]?.method).toBe('POST')
    const body = JSON.parse(String(call[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({
      content: '汇报时先给结论',
      kind: 'instruction',
      source: 'explicit',
    })
  })

  it('updates and deletes memory', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ...memoryPayload, content: '先给结论再展开' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const client = createMemoryClient({ fetchImpl })

    const updated = await client.update('memory-1', { content: '先给结论再展开' })
    await client.delete('memory-1')

    expect(updated.content).toBe('先给结论再展开')
    expect(fetchImpl.mock.calls[0]![0]).toContain('/memory/memory-1')
    expect(fetchImpl.mock.calls[0]![1]!.method).toBe('PATCH')
    expect(fetchImpl.mock.calls[1]![0]).toContain('/memory/memory-1')
    expect(fetchImpl.mock.calls[1]![1]!.method).toBe('DELETE')
  })

  it('surfaces backend validation errors', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ message: 'content must not be empty' }), { status: 422 }),
      )

    const client = createMemoryClient({ fetchImpl })

    await expect(client.create({ content: '' })).rejects.toThrow('content must not be empty')
  })
})

