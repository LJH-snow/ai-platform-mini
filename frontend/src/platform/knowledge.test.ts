import { describe, expect, it, vi } from 'vitest'

import {
  KnowledgeApiError,
  createKnowledgeClient,
  isKnowledgeTask,
  waitForKnowledgeTask,
} from './knowledge.ts'
import type { KnowledgeDocument, KnowledgeTask } from './knowledge-types.ts'

const documentId = '550e8400-e29b-41d4-a716-446655440000'
const document: KnowledgeDocument = {
  document_id: documentId,
  filename: 'brief.pdf',
  text_characters: 12,
  chunk_count: 1,
  content_sha256: 'a'.repeat(64),
  embedding_model: 'nomic-embed-text',
  created_at: null,
  safety_verdict: null,
}

const response = (status: number, body?: unknown): Response =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  })

const task = (overrides: Partial<KnowledgeTask> = {}): KnowledgeTask => ({
  task_id: 'task-1',
  status: 'queued',
  document_id: documentId,
  document: null,
  error: null,
  status_url: null,
  ...overrides,
})

describe('knowledge client', () => {
  it('lists indexed documents with the user API key', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(200, { data: [document] }))

    const documents = await createKnowledgeClient({
      apiBaseUrl: 'http://localhost:8000/',
      apiKey: 'sk-test',
      fetchImpl,
    }).listDocuments()

    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8000/api/v1/rag/documents', {
      headers: { Accept: 'application/json', Authorization: 'Bearer sk-test' },
    })
    expect(documents[0]?.filename).toBe('brief.pdf')
  })

  it('uploads a PDF as multipart form data and accepts an async task response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(202, task()))
    const file = new File(['%PDF-fake'], 'brief.pdf', { type: 'application/pdf' })

    const result = await createKnowledgeClient({ fetchImpl, apiKey: 'sk-test' }).uploadPdf(file)

    expect(isKnowledgeTask(result)).toBe(true)
    const request = fetchImpl.mock.calls[0]?.[1]
    expect(request?.method).toBe('POST')
    expect(request?.headers).toEqual({
      Accept: 'application/json',
      Authorization: 'Bearer sk-test',
    })
    const body = request?.body
    expect(body).toBeInstanceOf(FormData)
    expect(body instanceof FormData ? body.get('file') : null).toBeInstanceOf(File)
  })

  it('deletes a document and loads its extracted text preview', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(204))
      .mockResolvedValueOnce(
        response(200, {
          document_id: documentId,
          filename: 'brief.pdf',
          content: '项目说明正文',
          truncated: false,
        }),
      )
    const client = createKnowledgeClient({ fetchImpl, apiKey: 'sk-test' })

    await client.deleteDocument(documentId)
    const preview = await client.getDocumentPreview(documentId)

    expect(fetchImpl.mock.calls[0]?.[0]).toBe(`/api/v1/rag/documents/${documentId}`)
    expect(fetchImpl.mock.calls[0]?.[1]).toMatchObject({
      method: 'DELETE',
      headers: { Accept: 'application/json', Authorization: 'Bearer sk-test' },
    })
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(`/api/v1/rag/documents/${documentId}/preview`)
    expect(preview.content).toBe('项目说明正文')
  })

  it('polls an async task until it completes and reports intermediate states', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(200, task({ status: 'processing' })))
      .mockResolvedValueOnce(response(200, task({ status: 'completed', document })))
    const updates: string[] = []

    const result = await waitForKnowledgeTask(
      createKnowledgeClient({ fetchImpl, apiKey: 'sk-test' }),
      task({ status: 'queued' }),
      (nextTask) => updates.push(nextTask.status),
      { intervalMs: 0, maxAttempts: 3 },
    )

    expect(result.document?.document_id).toBe(documentId)
    expect(updates).toEqual(['queued', 'processing', 'completed'])
    expect(fetchImpl).toHaveBeenCalledTimes(2)
  })

  it('preserves the backend error code for invalid PDFs', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        response(400, { code: 'RAG_DOCUMENT_INVALID', message: '上传文件不是有效的 PDF。' }),
      )
    const file = new File(['bad'], 'notes.pdf', { type: 'application/pdf' })

    await expect(createKnowledgeClient({ fetchImpl }).uploadPdf(file)).rejects.toEqual(
      new KnowledgeApiError('上传文件不是有效的 PDF。', 400, 'RAG_DOCUMENT_INVALID'),
    )
  })
})
