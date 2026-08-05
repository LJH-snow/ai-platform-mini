import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ChatBackendError,
  ChatNetworkError,
  ChatStreamInterruptedError,
  createChatClient,
} from './client.ts'
import type { ChatApiMessage } from './types.ts'

const createSseResponse = (chunks: string[], requestId = 'req-sse-123'): Response => {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder()
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      controller.close()
    },
  })

  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'X-Request-ID': requestId,
    },
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('createChatClient', () => {
  it('sends the OpenAI-compatible request and merges split SSE events', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        createSseResponse([
          'data: {"choices":[{"delta":{"content":"你好"}}]}\r',
          '\n\r\ndata: {"choices":[{"delta":{"content":"，世界"}}]}\r\n\r\ndata: [DONE]\r\n\r\n',
        ]),
      )
    const deltas: string[] = []
    const requestIds: string[] = []
    const messages: ChatApiMessage[] = [{ role: 'user', content: '你好' }]

    const result = await createChatClient({
      apiBaseUrl: 'http://localhost:8000/',
      apiKey: 'runtime-key',
      fetchImpl,
    }).streamChat(
      messages,
      {
        onDelta: (content) => deltas.push(content),
        onRequestId: (requestId) => requestIds.push(requestId),
      },
      new AbortController().signal,
    )

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/v1/chat/completions?stream=true',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Accept: 'text/event-stream',
          Authorization: 'Bearer runtime-key',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({ messages, stream: true }),
      }),
    )
    expect(deltas).toEqual(['你好', '，世界'])
    expect(requestIds).toEqual(['req-sse-123'])
    expect(result).toEqual({ requestId: 'req-sse-123' })
  })

  it('maps backend JSON errors and preserves the request id', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'AUTHENTICATION_ERROR',
          message: 'Missing Authorization header.',
          request_id: 'req-auth-123',
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-auth-123' },
        },
      ),
    )
    const onRequestId = vi.fn()

    await expect(
      createChatClient({ fetchImpl }).streamChat(
        [{ role: 'user', content: '需要鉴权' }],
        { onDelta: vi.fn(), onRequestId },
        new AbortController().signal,
      ),
    ).rejects.toEqual(
      expect.objectContaining<Partial<ChatBackendError>>({
        name: 'ChatBackendError',
        status: 401,
        code: 'AUTHENTICATION_ERROR',
        requestId: 'req-auth-123',
        message: 'Missing Authorization header.',
      }),
    )
    expect(onRequestId).toHaveBeenCalledWith('req-auth-123')
  })

  it('distinguishes network failure, incomplete SSE, and reader errors', async () => {
    const networkFetch = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(
      createChatClient({ fetchImpl: networkFetch }).streamChat(
        [{ role: 'user', content: '网络' }],
        { onDelta: vi.fn(), onRequestId: vi.fn() },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(ChatNetworkError)

    const incompleteFetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        createSseResponse(['data: {"choices":[{"delta":{"content":"未完成"}}]}\n\n']),
      )
    await expect(
      createChatClient({ fetchImpl: incompleteFetch }).streamChat(
        [{ role: 'user', content: '断连' }],
        { onDelta: vi.fn(), onRequestId: vi.fn() },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(ChatStreamInterruptedError)

    let pullCount = 0
    const readerErrorStream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (pullCount === 0) {
          pullCount += 1
          controller.enqueue(
            new TextEncoder().encode('data: {"choices":[{"delta":{"content":"部分内容"}}]}\n\n'),
          )
          return
        }

        controller.error(new Error('reader failed'))
      },
    })
    const readerErrorFetch = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(readerErrorStream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    )
    await expect(
      createChatClient({ fetchImpl: readerErrorFetch }).streamChat(
        [{ role: 'user', content: '读取异常' }],
        { onDelta: vi.fn(), onRequestId: vi.fn() },
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(ChatStreamInterruptedError)
  })
})
