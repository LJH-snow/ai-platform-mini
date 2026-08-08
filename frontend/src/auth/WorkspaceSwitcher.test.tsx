import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkspaceSwitcher } from './WorkspaceSwitcher.tsx'
import type { AuthClient } from './client.ts'
import { AuthApiError } from './client.ts'

const createClient = (listWorkspaces: AuthClient['listWorkspaces']): AuthClient =>
  ({ listWorkspaces }) as AuthClient

afterEach(() => {
  cleanup()
})

describe('WorkspaceSwitcher', () => {
  it('lists workspaces for a user-bound key and auto-selects the first', async () => {
    const listWorkspaces = vi.fn(async () => [
      { id: 'ws-1', name: 'A Workspace', role: 'owner', member_count: 1 },
    ])
    const onWorkspaceChange = vi.fn()
    render(
      <WorkspaceSwitcher
        client={createClient(listWorkspaces)}
        apiKey="sk-user"
        currentWorkspaceId={null}
        onWorkspaceChange={onWorkspaceChange}
      />,
    )

    await screen.findByRole('combobox', { name: '切换工作区' })
    expect(screen.getByText('A Workspace (owner)')).toBeInTheDocument()
    expect(onWorkspaceChange).toHaveBeenCalledWith('ws-1')
  })

  it('hides silently for a legacy key (401 is the normal state)', async () => {
    const listWorkspaces = vi.fn(async () => {
      throw new AuthApiError('Not authenticated as a user.', 401)
    })
    render(
      <WorkspaceSwitcher
        client={createClient(listWorkspaces)}
        apiKey="sk-legacy"
        currentWorkspaceId={null}
        onWorkspaceChange={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.queryByText('加载工作区失败')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('shows the error for non-401 failures', async () => {
    const listWorkspaces = vi.fn(async () => {
      throw new AuthApiError('backend down', 503)
    })
    render(
      <WorkspaceSwitcher
        client={createClient(listWorkspaces)}
        apiKey="sk-user"
        currentWorkspaceId={null}
        onWorkspaceChange={vi.fn()}
      />,
    )

    expect(await screen.findByText('加载工作区失败')).toBeInTheDocument()
  })

  it('hides without a configured key', async () => {
    render(
      <WorkspaceSwitcher
        client={createClient(vi.fn())}
        apiKey=""
        currentWorkspaceId={null}
        onWorkspaceChange={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.queryByText('加载中…')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
})
