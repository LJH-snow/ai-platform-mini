import { describe, expect, it, vi } from 'vitest'

import { PlatformApiError, createPlatformClient } from './client.ts'

const response = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

describe('platform client', () => {
  it('loads and normalizes the real model directory response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      response(200, {
        data: [
          { id: 'qwen3:4b', owned_by: 'ollama' },
          { id: 'gpt-4o-mini', owned_by: 'openai' },
          { id: '', owned_by: 'ignored' },
        ],
      }),
    )

    const models = await createPlatformClient({
      apiBaseUrl: 'http://localhost:8000/',
      apiKey: 'sk-test',
      fetchImpl,
    }).listModels()

    expect(fetchImpl).toHaveBeenCalledWith('http://localhost:8000/api/v1/models', {
      headers: { Accept: 'application/json', Authorization: 'Bearer sk-test' },
    })
    expect(models).toEqual([
      { id: 'qwen3:4b', provider: 'ollama' },
      { id: 'gpt-4o-mini', provider: 'openai' },
    ])
  })

  it('maps authentication failures to a safe user-facing error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(403, { detail: 'secret' }))

    await expect(createPlatformClient({ fetchImpl }).listModels()).rejects.toEqual(
      new PlatformApiError('请先配置有效的普通用户 API Key。', 403),
    )
  })

  it('maps network failures without exposing the original error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new Error('connection secret'))

    await expect(createPlatformClient({ fetchImpl }).listModels()).rejects.toEqual(
      new PlatformApiError('无法连接模型服务，请确认后端和 Ollama 已启动。', 0),
    )
  })
})
