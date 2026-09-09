import { describe, expect, it, vi } from 'vitest'

import { createAuthClient } from './client.ts'

const okJson = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

describe('auth API key client', () => {
  it('lists keys with the current user bearer token', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson([
        {
          key_hash_prefix: 'abc12345',
          name: 'local-cli',
          status: 'active',
          created_at: '2026-09-09T00:00:00Z',
          last_used_at: null,
        },
      ]),
    )
    const client = createAuthClient({
      apiBaseUrl: 'http://localhost:8000',
      fetchImpl,
    })

    const keys = await client.listKeys('sk-user')

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/auth/keys',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sk-user' }),
      }),
    )
    expect(keys[0].key_hash_prefix).toBe('abc12345')
  })

  it('creates a key with a name and returns the raw secret once', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        key_hash_prefix: 'def67890',
        name: 'demo',
        status: 'active',
        created_at: '2026-09-09T00:00:00Z',
        last_used_at: null,
        raw_key: 'sk-demo-secret',
      }),
    )
    const client = createAuthClient({ fetchImpl })

    const created = await client.createKey('sk-user', 'demo')

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/auth/keys',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'demo' }),
        headers: expect.objectContaining({ Authorization: 'Bearer sk-user' }),
      }),
    )
    expect(created.raw_key).toBe('sk-demo-secret')
  })

  it('revokes a key by hash prefix with an encoded path', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        key_hash_prefix: 'abc 123',
        revoked: true,
      }),
    )
    const client = createAuthClient({ fetchImpl })

    const revoked = await client.revokeKey('sk-user', 'abc 123')

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/auth/keys/abc%20123',
      expect.objectContaining({
        method: 'DELETE',
        headers: expect.objectContaining({ Authorization: 'Bearer sk-user' }),
      }),
    )
    expect(revoked.revoked).toBe(true)
  })
})
