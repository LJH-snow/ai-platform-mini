import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { MemoryClient } from './client.ts'
import type { MemoryItem } from './types.ts'
import { MemoryPanel } from './MemoryPanel.tsx'

const memoryItem: MemoryItem = {
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

const createClient = (overrides: Partial<MemoryClient> = {}): MemoryClient => ({
  list: vi.fn(async () => []),
  create: vi.fn(async (_input) => memoryItem),
  update: vi.fn(async (_id, _input) => memoryItem),
  delete: vi.fn(async () => undefined),
  ...overrides,
})

afterEach(() => {
  cleanup()
})

describe('MemoryPanel', () => {
  it('loads and displays saved memory', async () => {
    const client = createClient({ list: vi.fn(async () => [memoryItem]) })
    render(<MemoryPanel apiKeyConfigured client={client} />)

    expect(await screen.findByText('汇报时先给结论')).toBeInTheDocument()
    expect(screen.getAllByText('指令').length).toBeGreaterThan(0)
    expect(screen.getByText('置信度 0.95')).toBeInTheDocument()
  })

  it('creates a new memory item', async () => {
    const create = vi.fn(async (_input: Parameters<MemoryClient['create']>[0]) => ({
      ...memoryItem,
      content: '用户偏好中文回答',
      kind: 'preference' as const,
    }))
    const client = createClient({ create })
    const user = userEvent.setup()
    render(<MemoryPanel apiKeyConfigured client={client} />)

    await user.type(screen.getByLabelText('内容'), '用户偏好中文回答')
    await user.selectOptions(screen.getByLabelText('类型'), 'preference')
    await user.click(screen.getByRole('button', { name: '保存记忆' }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    expect(create.mock.calls[0]![0]).toMatchObject({
      content: '用户偏好中文回答',
      kind: 'preference',
    })
    expect(await screen.findByText('记忆已保存。')).toBeInTheDocument()
  })

  it('edits and deletes memory', async () => {
    const update = vi.fn(
      async (_id: string, _input: Record<string, unknown>) => ({
        ...memoryItem,
        content: '先给结论再展开',
      }),
    )
    const del = vi.fn(async () => undefined)
    const client = createClient({
      list: vi.fn(async () => [memoryItem]),
      update,
      delete: del,
    })
    const user = userEvent.setup()
    render(<MemoryPanel apiKeyConfigured client={client} />)

    await user.click(await screen.findByRole('button', { name: '编辑' }))
    expect(screen.getByLabelText('内容')).toHaveValue('汇报时先给结论')
    await user.clear(screen.getByLabelText('内容'))
    await user.type(screen.getByLabelText('内容'), '先给结论再展开')
    await user.click(screen.getByRole('button', { name: '保存修改' }))

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('memory-1', expect.objectContaining({ content: '先给结论再展开' })),
    )

    await user.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(del).toHaveBeenCalledWith('memory-1'))
    expect(await screen.findByText('记忆已删除。')).toBeInTheDocument()
  })

  it('blocks empty memory content', async () => {
    const create = vi.fn()
    const client = createClient({ create })
    const user = userEvent.setup()
    render(<MemoryPanel apiKeyConfigured client={client} />)

    await user.click(screen.getByRole('button', { name: '保存记忆' }))

    expect(await screen.findByText('记忆内容不能为空。')).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })
})
