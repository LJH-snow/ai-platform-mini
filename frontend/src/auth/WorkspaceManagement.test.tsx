import '@testing-library/jest-dom/vitest'

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuthClient } from './client.ts'
import { WorkspaceManagement } from './WorkspaceManagement.tsx'
import type { WorkspaceSummary } from './types.ts'

type WorkspaceClient = Pick<
  AuthClient,
  'listWorkspaces' | 'renameWorkspace' | 'listMembers' | 'listKeys'
>

const sampleWorkspace: WorkspaceSummary = {
  id: 'ws-1',
  name: 'Team Alpha',
  role: 'owner',
  member_count: 1,
}

const createClient = (overrides: Partial<WorkspaceClient> = {}): AuthClient =>
  ({
    listWorkspaces: vi.fn(async () => [sampleWorkspace]),
    renameWorkspace: vi.fn(),
    listMembers: vi.fn(async () => []),
    listKeys: vi.fn(async () => []),
    ...overrides,
  }) as AuthClient

afterEach(() => {
  cleanup()
})

describe('WorkspaceManagement rename', () => {
  it('renames the current workspace when the owner saves a new name', async () => {
    const renameWorkspace = vi.fn(async () => ({
      ...sampleWorkspace,
      name: 'Team Renamed',
    }))
    const listWorkspaces = vi.fn(async () => [sampleWorkspace])
    render(
      <WorkspaceManagement
        client={createClient({ renameWorkspace, listWorkspaces })}
        apiKey="sk-user"
        currentWorkspaceId="ws-1"
        onWorkspaceChange={vi.fn()}
      />,
    )

    fireEvent.change(await screen.findByLabelText('新名称'), {
      target: { value: 'Team Renamed' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }))

    expect(await screen.findByText('工作空间名称已更新')).toBeInTheDocument()
    expect(renameWorkspace).toHaveBeenCalledWith('sk-user', 'ws-1', 'Team Renamed')
    await waitFor(() => {
      expect(listWorkspaces).toHaveBeenCalledTimes(2)
    })
  })

  it('does not show the rename form for members', async () => {
    const listWorkspaces = vi.fn(async () => [
      {
        id: 'ws-1',
        name: 'Team Alpha',
        role: 'member',
        member_count: 2,
      },
    ])
    render(
      <WorkspaceManagement
        client={createClient({ listWorkspaces })}
        apiKey="sk-user"
        currentWorkspaceId="ws-1"
        onWorkspaceChange={vi.fn()}
      />,
    )

    await screen.findByText('Team Alpha')
    expect(screen.queryByLabelText('新名称')).not.toBeInTheDocument()
  })
})
