import { describe, expect, it, vi } from 'vitest'

import { ConfigApiError, createConfigClient } from './config-client.ts'

const okJson = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

describe('createConfigClient', () => {
  it('sends the bearer key and parses prompt summaries', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson([
        {
          name: 'custom_prompt',
          active_version: 2,
          versions: [
            { version: 1, is_active: false },
            { version: 2, is_active: true },
          ],
        },
      ]),
    )
    const client = createConfigClient({
      apiBaseUrl: 'http://localhost:8000',
      apiKey: 'sk-test',
      fetchImpl,
    })

    const prompts = await client.listPrompts()

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/prompts',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ Authorization: 'Bearer sk-test' }),
      }),
    )
    expect(prompts).toEqual([
      {
        name: 'custom_prompt',
        active_version: 2,
        versions: [
          { version: 1, is_active: false },
          { version: 2, is_active: true },
        ],
      },
    ])
  })

  it('posts a JSON body when creating a prompt version', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({ name: 'custom_prompt', version: 3, content: 'v3', is_active: false }),
    )
    const client = createConfigClient({ apiKey: 'sk-test', fetchImpl })

    const version = await client.createPromptVersion('custom_prompt', 'v3')

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/prompts/custom_prompt/versions',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ content: 'v3' }),
      }),
    )
    expect(version.version).toBe(3)
  })

  it('encodes prompt names in paths', async () => {
    const fetchImpl = vi.fn(async () => okJson([]))
    const client = createConfigClient({ fetchImpl })

    await client.activatePrompt('my prompt/名称', 2)

    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/v1/prompts/my%20prompt%2F%E5%90%8D%E7%A7%B0/activate',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ version: 2 }) }),
    )
  })

  it('normalizes tool enablement responses', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        name: 'calculator',
        description: 'Evaluate arithmetic expressions.',
        parameters_schema: { type: 'object' },
        enabled_by_default: true,
        owner: 'builtin',
        enabled: false,
      }),
    )
    const client = createConfigClient({ fetchImpl })

    const tool = await client.setToolEnabled('calculator', false)

    expect(tool?.enabled).toBe(false)
    expect(tool?.parameters_schema).toEqual({ type: 'object' })
  })

  it('normalizes agent responses and lists', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson([
        {
          id: 'agent-1',
          workspace_id: 'ws-1',
          name: '研究助手',
          model: 'qwen3:4b',
          prompt_ref: '',
          temperature: 0.7,
          max_steps: 10,
          enabled: true,
          tool_names: ['calculator'],
        },
      ]),
    )
    const client = createConfigClient({ fetchImpl })

    const agents = await client.listAgents()

    expect(agents).toEqual([
      {
        id: 'agent-1',
        workspace_id: 'ws-1',
        name: '研究助手',
        model: 'qwen3:4b',
        prompt_ref: '',
        temperature: 0.7,
        max_steps: 10,
        enabled: true,
        tool_names: ['calculator'],
      },
    ])
  })

  it('maps HTTP errors to ConfigApiError with status', async () => {
    const fetchImpl = vi.fn(async () => new Response('nope', { status: 404 }))
    const client = createConfigClient({ fetchImpl })

    await expect(client.listAgents()).rejects.toBeInstanceOf(ConfigApiError)
    await expect(client.listAgents()).rejects.toMatchObject({ status: 404 })
  })

  it('surfaces network failures as a stable error', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('fetch failed')
    })
    const client = createConfigClient({ fetchImpl })

    await expect(client.listTools()).rejects.toMatchObject({ status: 0 })
  })
})
