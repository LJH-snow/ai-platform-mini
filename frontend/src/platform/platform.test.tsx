import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Dashboard } from './Dashboard.tsx'
import { KnowledgeBase } from './KnowledgeBase.tsx'
import { ModelCatalog } from './ModelCatalog.tsx'
import { PromptStudio } from './PromptStudio.tsx'
import { defaultTemplates, STORAGE_KEY } from './prompt-data.ts'
import type { PlatformClient } from './client.ts'

const createClient = (listModels: PlatformClient['listModels']): PlatformClient => ({
  listModels,
})

afterEach(() => {
  cleanup()
})

beforeEach(() => {
  const values = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    } satisfies Pick<Storage, 'clear' | 'getItem' | 'removeItem' | 'setItem'>,
  })
})

describe('Dashboard', () => {
  it('shows truthful platform state and sends quick-start navigation', async () => {
    const onNavigate = vi.fn()
    render(
      <Dashboard
        apiKeyConfigured
        modelCount={1}
        modelName="qwen3:4b"
        ragStatus={{ kind: 'ready', embeddingModel: 'nomic-embed-text' }}
        onNavigate={onNavigate}
      />,
    )

    expect(screen.getByText('qwen3:4b')).toBeInTheDocument()
    expect(screen.getByText('RAG 已启用')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: '运行 Agent Demo' }))
    expect(onNavigate).toHaveBeenCalledWith('console', 'agent')
  })
})

describe('ModelCatalog', () => {
  it('loads available models and exposes a refresh action', async () => {
    const listModels = vi.fn().mockResolvedValue([
      { id: 'qwen3:4b', provider: 'ollama' },
      { id: 'gpt-4o-mini', provider: 'openai' },
    ])
    render(<ModelCatalog apiKeyConfigured client={createClient(listModels)} />)

    expect(await screen.findByText('qwen3:4b')).toBeInTheDocument()
    expect(screen.getByText('gpt-4o-mini')).toBeInTheDocument()
    expect(listModels).toHaveBeenCalledTimes(1)

    await userEvent.setup().click(screen.getByRole('button', { name: '刷新目录' }))
    await waitFor(() => expect(listModels).toHaveBeenCalledTimes(2))
  })

  it('shows the key boundary without calling the model API', () => {
    const listModels = vi.fn()
    render(<ModelCatalog apiKeyConfigured={false} client={createClient(listModels)} />)

    expect(screen.getByText('需要普通用户 API Key')).toBeInTheDocument()
    expect(listModels).not.toHaveBeenCalled()
  })
})

describe('PromptStudio', () => {
  it('edits, saves, restores, and injects a prompt into the console flow', async () => {
    const user = userEvent.setup()
    const onUsePrompt = vi.fn()
    render(<PromptStudio onUsePrompt={onUsePrompt} />)

    const prompt = screen.getByLabelText('系统提示词')
    await user.clear(prompt)
    await user.type(prompt, '新的系统指令')
    await user.click(screen.getByRole('button', { name: '保存模板' }))

    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]')[0].prompt).toBe(
      '新的系统指令',
    )
    expect(screen.getByRole('status')).toHaveTextContent('已保存到本地')

    await user.click(screen.getByRole('button', { name: '恢复默认' }))
    expect(prompt).toHaveValue(defaultTemplates[0].prompt)

    await user.click(screen.getByRole('button', { name: '带入对话工作台 →' }))
    expect(onUsePrompt).toHaveBeenCalledWith(defaultTemplates[0].example)
  })

  it('switches template content and keeps the editor accessible', async () => {
    const user = userEvent.setup()
    render(<PromptStudio onUsePrompt={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: /面试模拟官/ }))
    expect(screen.getByRole('heading', { name: '面试模拟官' })).toBeInTheDocument()
    expect(screen.getByLabelText('演示问题')).toHaveValue(defaultTemplates[2].example)
    expect(screen.getByRole('button', { name: /面试模拟官/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })
})

describe('KnowledgeBase', () => {
  const readyRagStatus = { kind: 'ready' as const, embeddingModel: null }

  it('loads indexed documents and uploads a PDF', async () => {
    const listDocuments = vi.fn().mockResolvedValue([])
    const uploadPdf = vi.fn().mockResolvedValue({
      document_id: 'doc-1',
      filename: 'brief.pdf',
      text_characters: 120,
      chunk_count: 2,
      content_sha256: 'a'.repeat(64),
      embedding_model: 'nomic-embed-text',
      created_at: null,
    })
    const user = userEvent.setup()

    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={readyRagStatus}
        client={{ listDocuments, uploadPdf }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(await screen.findByText('当前知识库暂无文档')).toBeInTheDocument()
    const file = new File(['%PDF-fake'], 'brief.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)

    expect(await screen.findByText('brief.pdf')).toBeInTheDocument()
    expect(screen.getByText('1 个')).toBeInTheDocument()
    expect(uploadPdf).toHaveBeenCalledWith(file)
  })

  it('shows the RAG loading state without calling the document API', () => {
    const listDocuments = vi.fn()
    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={{ kind: 'loading' }}
        client={{ listDocuments, uploadPdf: vi.fn() }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(screen.getAllByText('正在检查 RAG 服务状态…').length).toBeGreaterThan(0)
    expect(listDocuments).not.toHaveBeenCalled()
  })

  it('shows a database-unavailable state without fabricating a document count', () => {
    const listDocuments = vi.fn()
    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={{ kind: 'database_unavailable', reason: 'connection_failed' }}
        client={{ listDocuments, uploadPdf: vi.fn() }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(
      screen.getAllByText('知识库数据库不可用，暂无法确认或操作知识库。').length,
    ).toBeGreaterThan(0)
    expect(screen.getByText('不可用')).toBeInTheDocument()
    expect(screen.queryByText(/0 个/)).not.toBeInTheDocument()
    expect(listDocuments).not.toHaveBeenCalled()
  })

  it('shows an embedding-unavailable state without calling the document API', () => {
    const listDocuments = vi.fn()
    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={{ kind: 'embedding_unavailable', reason: 'connection_failed' }}
        client={{ listDocuments, uploadPdf: vi.fn() }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(
      screen.getAllByText('Embedding 服务不可用，暂无法上传或检索文档。').length,
    ).toBeGreaterThan(0)
    expect(listDocuments).not.toHaveBeenCalled()
  })

  it('shows a safe error state when the health check request fails', () => {
    const listDocuments = vi.fn()
    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={{ kind: 'error' }}
        client={{ listDocuments, uploadPdf: vi.fn() }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(screen.getAllByText('无法确认 RAG 服务状态，健康检查请求失败。').length).toBeGreaterThan(
      0,
    )
    expect(listDocuments).not.toHaveBeenCalled()
  })
})
