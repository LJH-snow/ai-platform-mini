import '@testing-library/jest-dom/vitest'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthClient } from './client.ts'
import { UserApiKeyManagement } from './UserApiKeyManagement.tsx'
import type { CreatedUserApiKey, UserApiKeySummary } from './types.ts'

type KeyClient = Pick<AuthClient, 'listKeys' | 'createKey' | 'revokeKey'>

const sampleKey: UserApiKeySummary = {
  key_hash_prefix: 'abc12345',
  name: 'local-cli',
  status: 'active',
  created_at: '2026-09-09T00:00:00Z',
  last_used_at: null,
}

const createClient = (overrides: Partial<KeyClient> = {}): AuthClient =>
  ({
    listKeys: vi.fn(async () => [sampleKey]),
    createKey: vi.fn(),
    revokeKey: vi.fn(),
    ...overrides,
  }) as AuthClient

afterEach(() => {
  cleanup()
})

describe('UserApiKeyManagement', () => {
  it('shows a hint when no API key is configured', async () => {
    const listKeys = vi.fn()
    render(<UserApiKeyManagement client={createClient({ listKeys })} apiKey="" />)

    expect(screen.getByText('登录后可以在这里创建和管理自己的 API Key。')).toBeInTheDocument()
    await waitFor(() => {
      expect(listKeys).not.toHaveBeenCalled()
    })
  })

  it('lists the current user keys', async () => {
    render(<UserApiKeyManagement client={createClient()} apiKey="sk-user" />)

    expect(await screen.findByText('local-cli')).toBeInTheDocument()
    expect(screen.getByText('有效')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '撤销' })).toBeInTheDocument()
  })

  it('creates a key and shows the raw secret once', async () => {
    const listKeys = vi.fn(async () => [sampleKey])
    const createKey = vi.fn(
      async (): Promise<CreatedUserApiKey> => ({
        ...sampleKey,
        raw_key: 'sk-created-secret',
      }),
    )
    const client = createClient({ listKeys, createKey })
    render(<UserApiKeyManagement client={client} apiKey="sk-user" />)

    fireEvent.change(screen.getByPlaceholderText('用途名称（如 local-cli）'), {
      target: { value: 'ci' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建用户 Key' }))

    expect(await screen.findByText('sk-created-secret')).toBeInTheDocument()
    expect(createKey).toHaveBeenCalledWith('sk-user', 'ci')
    await waitFor(() => {
      expect(listKeys).toHaveBeenCalledTimes(2)
    })
  })

  it('revokes a key after confirm and refreshes the list', async () => {
    const listKeys = vi.fn(async () => [sampleKey])
    const revokeKey = vi.fn(async () => ({
      key_hash_prefix: sampleKey.key_hash_prefix,
      revoked: true,
    }))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const client = createClient({ listKeys, revokeKey })
    render(<UserApiKeyManagement client={client} apiKey="sk-user" />)

    fireEvent.click(await screen.findByRole('button', { name: '撤销' }))

    expect(await screen.findByText(/已撤销 Key/i)).toBeInTheDocument()
    expect(revokeKey).toHaveBeenCalledWith('sk-user', sampleKey.key_hash_prefix)
    await waitFor(() => {
      expect(listKeys).toHaveBeenCalledTimes(2)
    })
  })

  it('does not revoke when the user cancels the confirmation', async () => {
    const revokeKey = vi.fn()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<UserApiKeyManagement client={createClient({ revokeKey })} apiKey="sk-user" />)

    fireEvent.click(await screen.findByRole('button', { name: '撤销' }))

    await waitFor(() => {
      expect(revokeKey).not.toHaveBeenCalled()
    })
  })
})
