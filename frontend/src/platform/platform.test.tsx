import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dashboard } from './Dashboard.tsx'
import { KnowledgeBase } from './KnowledgeBase.tsx'
import { ModelCatalog } from './ModelCatalog.tsx'
import { AgentStudio } from './AgentStudio.tsx'
import type { ConfigClient } from './config-client.ts'
import { PromptStudio } from './PromptStudio.tsx'
import { RunDetail } from './RunDetail.tsx'
import { RunList } from './RunList.tsx'
import { ToolCenter } from './ToolCenter.tsx'
import { UsageDashboardPage } from './UsageDashboard.tsx'
import type { PlatformClient } from './client.ts'

const createClient = (listModels: PlatformClient['listModels']): PlatformClient => ({
  listModels,
})

afterEach(() => {
  cleanup()
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

const createConfigClient = (overrides: Partial<ConfigClient> = {}): ConfigClient => ({
  listPrompts: vi.fn(async () => [
    {
      name: 'custom_prompt',
      active_version: 2,
      versions: [
        { version: 1, is_active: false },
        { version: 2, is_active: true },
      ],
    },
  ]),
  getPromptVersions: vi.fn(async () => [
    { name: 'custom_prompt', version: 1, content: 'v1 content', is_active: false },
    { name: 'custom_prompt', version: 2, content: 'v2 content', is_active: true },
  ]),
  createPromptVersion: vi.fn(async () => ({
    name: 'custom_prompt',
    version: 3,
    content: '',
    is_active: false,
  })),
  activatePrompt: vi.fn(async () => ({
    name: 'custom_prompt',
    version: 2,
    content: '',
    is_active: true,
  })),
  listTools: vi.fn(async () => [
    {
      name: 'calculator',
      description: 'Evaluate arithmetic expressions.',
      parameters_schema: { type: 'object' },
      enabled_by_default: true,
      owner: 'builtin',
      enabled: true,
      can_manage: true,
    },
  ]),
  setToolEnabled: vi.fn(async () => ({
    name: 'calculator',
    description: 'Evaluate arithmetic expressions.',
    parameters_schema: { type: 'object' },
    enabled_by_default: true,
    owner: 'builtin',
    enabled: true,
    can_manage: true,
  })),
  listAgents: vi.fn(async () => []),
  createAgent: vi.fn(async () => ({
    id: 'agent-1',
    workspace_id: 'ws-1',
    name: 'a',
    model: 'm',
    prompt_ref: '',
    temperature: 0.7,
    max_steps: 10,
    enabled: true,
    tool_names: [],
  })),
  updateAgent: vi.fn(async () => ({
    id: 'agent-1',
    workspace_id: 'ws-1',
    name: 'a',
    model: 'm',
    prompt_ref: '',
    temperature: 0.7,
    max_steps: 10,
    enabled: true,
    tool_names: [],
  })),
  deleteAgent: vi.fn(async () => null),
  listRuns: vi.fn(async () => []),
  getUsageDashboard: vi.fn(async () => ({
    trend: [],
    model_ranking: [],
    key_ranking: [],
  })),
  runBenchmark: vi.fn(async () => ({
    id: 1,
    agent_id: 'agent-1',
    task_set: 'default',
    tool_call_accuracy: 1.0,
    task_completion_rate: 1.0,
    average_steps: 1.0,
    average_latency_ms: 12.0,
    task_count: 3,
    completed_count: 3,
    created_at: null,
  })),
  listBenchmarkRuns: vi.fn(async () => []),
  downloadUsageExport: vi.fn(async () => new Blob(['csv'])),
  getRun: vi.fn(async () => ({
    run_id: 'run-1',
    model: 'm',
    status: 'completed',
    stop_reason: 'direct_answer',
    started_at: null,
    completed_at: null,
    duration_ms: 1,
    total_tokens: 2,
    tool_count: 0,
    rag_reference_count: 0,
    response: {},
  })),
  ...overrides,
})

describe('PromptStudio', () => {
  it('loads prompts, shows the active version, and injects it into the console', async () => {
    const user = userEvent.setup()
    const onUsePrompt = vi.fn()
    render(<PromptStudio client={createConfigClient()} onUsePrompt={onUsePrompt} />)

    await screen.findByRole('button', { name: /custom_prompt/ })
    const editor = screen.getByLabelText('Prompt 内容')
    await waitFor(() => expect(editor).toHaveValue('v2 content'))
    expect(screen.getByText('当前 v2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '使用当前版本' }))
    expect(onUsePrompt).toHaveBeenCalledWith('v2 content')
  })

  it('saves a new version through the API', async () => {
    const user = userEvent.setup()
    const createPromptVersion = vi.fn(async () => ({
      name: 'custom_prompt',
      version: 3,
      content: 'v3 content',
      is_active: false,
    }))
    render(
      <PromptStudio
        client={createConfigClient({ createPromptVersion })}
        onUsePrompt={vi.fn()}
      />,
    )

    await screen.findByRole('button', { name: /custom_prompt/ })
    const editor = screen.getByLabelText('Prompt 内容')
    await waitFor(() => expect(editor).toHaveValue('v2 content'))
    await user.clear(editor)
    await user.type(editor, 'v3 content')
    await user.click(screen.getByRole('button', { name: '保存为新版本' }))

    expect(createPromptVersion).toHaveBeenCalledWith('custom_prompt', 'v3 content')
    expect(await screen.findByRole('status')).toHaveTextContent('新版本已保存')
  })

  it('activates an older version (rollback) through the API', async () => {
    const user = userEvent.setup()
    const activatePrompt = vi.fn(async () => ({
      name: 'custom_prompt',
      version: 1,
      content: 'v1 content',
      is_active: true,
    }))
    render(
      <PromptStudio
        client={createConfigClient({ activatePrompt })}
        onUsePrompt={vi.fn()}
      />,
    )

    await screen.findByRole('button', { name: /custom_prompt/ })
    const buttons = await screen.findAllByRole('button', { name: '设为当前版本' })
    await user.click(buttons[0])

    expect(activatePrompt).toHaveBeenCalledWith('custom_prompt', 1)
    expect(await screen.findByRole('status')).toHaveTextContent('v1 已设为当前版本')
  })
})

describe('AgentStudio', () => {
  it('lists agents and creates a new one with tool whitelist', async () => {
    const user = userEvent.setup()
    const listAgents = vi.fn(async () => [
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
    const createAgent = vi.fn(async () => ({
      id: 'agent-2',
      workspace_id: 'ws-1',
      name: '新助手',
      model: 'qwen3:4b',
      prompt_ref: '',
      temperature: 0.7,
      max_steps: 10,
      enabled: true,
      tool_names: ['calculator'],
    }))
    render(<AgentStudio client={createConfigClient({ listAgents, createAgent })} />)

    await screen.findByText('研究助手')
    await user.type(screen.getByLabelText('名称'), '新助手')
    await user.type(screen.getByLabelText('模型'), 'qwen3:4b')
    await user.click(screen.getByRole('checkbox', { name: 'calculator' }))
    await user.click(screen.getByRole('button', { name: '创建' }))

    expect(createAgent).toHaveBeenCalledWith(
      expect.objectContaining({ name: '新助手', tool_names: ['calculator'] }),
    )
    expect(await screen.findByRole('status')).toHaveTextContent('Agent 已创建')
  })

  it('edits an existing agent and deletes it', async () => {
    const user = userEvent.setup()
    const listAgents = vi.fn(async () => [
      {
        id: 'agent-1',
        workspace_id: 'ws-1',
        name: '研究助手',
        model: 'qwen3:4b',
        prompt_ref: '',
        temperature: 0.7,
        max_steps: 10,
        enabled: true,
        tool_names: [],
      },
    ])
    const updateAgent = vi.fn(async () => ({
      id: 'agent-1',
      workspace_id: 'ws-1',
      name: '研究助手',
      model: 'qwen3:4b',
      prompt_ref: '',
      temperature: 0.7,
      max_steps: 5,
      enabled: true,
      tool_names: [],
    }))
    const deleteAgent = vi.fn(async () => null)
    render(
      <AgentStudio client={createConfigClient({ listAgents, updateAgent, deleteAgent })} />,
    )

    await screen.findByText('研究助手')
    await user.click(screen.getByRole('button', { name: /研究助手/ }))
    await user.clear(screen.getByLabelText('最大步数'))
    await user.type(screen.getByLabelText('最大步数'), '5')
    await user.click(screen.getByRole('button', { name: '保存修改' }))

    expect(updateAgent).toHaveBeenCalledWith(
      'agent-1',
      expect.objectContaining({ max_steps: 5 }),
    )
    expect(await screen.findByRole('status')).toHaveTextContent('Agent 已更新')

    await user.click(screen.getByRole('button', { name: '删除' }))
    expect(deleteAgent).toHaveBeenCalledWith('agent-1')
  })
})

describe('RunList', () => {
  it('renders runs and opens the replay page', async () => {
    const user = userEvent.setup()
    const listRuns = vi.fn(async () => [
      {
        run_id: 'run-1',
        model: 'qwen3:4b',
        status: 'completed',
        stop_reason: 'direct_answer',
        started_at: '2026-08-08T00:00:00Z',
        completed_at: null,
        duration_ms: 120,
        total_tokens: 42,
        tool_count: 2,
        rag_reference_count: 3,
      },
    ])
    const onOpenRun = vi.fn()
    render(
      <RunList
        client={createConfigClient({ listRuns })}
        onOpenRun={onOpenRun}
      />,
    )

    expect(await screen.findByText('qwen3:4b')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '回放' }))
    expect(onOpenRun).toHaveBeenCalledWith('run-1')
  })

  it('shows the empty state', async () => {
    render(<RunList client={createConfigClient()} onOpenRun={vi.fn()} />)
    expect(await screen.findByText(/暂无 Run 记录/)).toBeInTheDocument()
  })
})

describe('RunDetail', () => {
  it('renders the timeline, tool calls, and final answer from a stored run', async () => {
    const getRun = vi.fn(async () => ({
      run_id: 'run-1',
      model: 'qwen3:4b',
      status: 'completed',
      stop_reason: 'direct_answer',
      started_at: null,
      completed_at: null,
      duration_ms: 120,
      total_tokens: 42,
      tool_count: 1,
      rag_reference_count: 1,
      response: {
        answer: '最终回答内容',
        steps: [
          {
            index: 1,
            decision_kind: 'tool_call',
            summary: 'Planned 1 tool call(s): calculator.',
            tool_names: ['calculator'],
            tool_calls: [
              {
                call_id: 'call-1',
                name: 'calculator',
                succeeded: true,
                error_code: null,
                error_message: null,
                input_summary: 'expression: 2 + 2',
                output_summary: 'result: 4',
                rag: null,
              },
            ],
          },
          {
            index: 2,
            decision_kind: 'final_answer',
            summary: 'Final answer planned.',
            tool_names: [],
            tool_calls: null,
          },
        ],
      },
    }))
    const onBack = vi.fn()
    render(
      <RunDetail client={createConfigClient({ getRun })} runId="run-1" onBack={onBack} />,
    )

    await screen.findByText('Step 1')
    expect(screen.getByText('calculator')).toBeInTheDocument()
    expect(screen.getByText('输入：expression: 2 + 2')).toBeInTheDocument()
    expect(screen.getByText('最终回答内容')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('button', { name: '← 返回' }))
    expect(onBack).toHaveBeenCalled()
  })
})

describe('UsageDashboard', () => {
  it('renders the token trend and both rankings', async () => {
    const getUsageDashboard = vi.fn(async () => ({
      trend: [
        { usage_date: '2026-08-01', total_tokens: 1000, request_count: 5 },
        { usage_date: '2026-08-02', total_tokens: 2500, request_count: 8 },
      ],
      model_ranking: [
        { name: 'qwen3:4b', total_tokens: 3000, request_count: 10 },
        { name: 'gpt-4o-mini', total_tokens: 500, request_count: 3 },
      ],
      key_ranking: [
        { name: 'abcd1234', total_tokens: 3000, request_count: 10 },
      ],
    }))
    render(
      <UsageDashboardPage
        client={createConfigClient({ getUsageDashboard })}
      />,
    )

    expect(await screen.findByText('每日 Token 用量')).toBeInTheDocument()
    expect(screen.getByText('按模型')).toBeInTheDocument()
    expect(screen.getByText('qwen3:4b')).toBeInTheDocument()
    expect(screen.getByText('按 Key')).toBeInTheDocument()
    expect(screen.getByText('abcd1234')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '每日 Token 用量趋势' })).toBeInTheDocument()

    const progress = screen.getAllByRole('progressbar')
    expect(progress.length).toBe(3)
  })

  it('downloads the CSV export through the authenticated client', async () => {
    const user = userEvent.setup()
    const downloadUsageExport = vi.fn(async () => new Blob(['csv']))
    render(
      <UsageDashboardPage
        client={createConfigClient({ downloadUsageExport })}
      />,
    )
    await screen.findByText('每日 Token 用量')

    await user.click(screen.getByRole('button', { name: '导出 CSV' }))

    expect(downloadUsageExport).toHaveBeenCalledWith(7, 'csv')
  })

  it('switches the time range', async () => {
    const user = userEvent.setup()
    const getUsageDashboard = vi.fn(async () => ({
      trend: [],
      model_ranking: [],
      key_ranking: [],
    }))
    render(
      <UsageDashboardPage client={createConfigClient({ getUsageDashboard })} />,
    )
    await screen.findByText('每日 Token 用量')

    await user.selectOptions(screen.getByRole('combobox'), '30')

    expect(getUsageDashboard).toHaveBeenLastCalledWith(30)
  })
})

describe('AgentStudio benchmark', () => {
  it('runs a benchmark for the selected agent and shows the results table', async () => {
    const user = userEvent.setup()
    const listAgents = vi.fn(async () => [
      {
        id: 'agent-1',
        workspace_id: 'ws-1',
        name: '研究助手',
        model: 'qwen3:4b',
        prompt_ref: '',
        temperature: 0.7,
        max_steps: 10,
        enabled: true,
        tool_names: [],
      },
    ])
    const runBenchmark = vi.fn(async () => ({
      id: 7,
      agent_id: 'agent-1',
      task_set: 'default',
      tool_call_accuracy: 0.67,
      task_completion_rate: 1.0,
      average_steps: 1.0,
      average_latency_ms: 25.0,
      task_count: 3,
      completed_count: 3,
      created_at: '2026-08-08T00:00:00Z',
    }))
    const listBenchmarkRuns = vi.fn(async () => [
      {
        id: 7,
        agent_id: 'agent-1',
        task_set: 'default',
        tool_call_accuracy: 0.67,
        task_completion_rate: 1.0,
        average_steps: 1.0,
        average_latency_ms: 25.0,
        task_count: 3,
        completed_count: 3,
        created_at: '2026-08-08T00:00:00Z',
      },
    ])
    render(
      <AgentStudio
        client={createConfigClient({ listAgents, runBenchmark, listBenchmarkRuns })}
      />,
    )

    await screen.findByText('研究助手')
    await user.click(screen.getByRole('button', { name: /研究助手/ }))
    await user.click(screen.getByRole('button', { name: '运行 Benchmark' }))

    expect(runBenchmark).toHaveBeenCalledWith('agent-1')
    expect(await screen.findByText('0.67')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Benchmark 完成')
    expect(listBenchmarkRuns).toHaveBeenCalledWith('agent-1')
  })
})

describe('ToolCenter', () => {
  it('lists tools, expands the schema, and toggles enablement', async () => {
    const user = userEvent.setup()
    const listTools = vi.fn(async () => [
      {
        name: 'calculator',
        description: 'Evaluate arithmetic expressions.',
        parameters_schema: { type: 'object', properties: { expression: { type: 'string' } } },
        enabled_by_default: true,
        owner: 'builtin',
        enabled: true,
        can_manage: true,
      },
    ])
    const setToolEnabled = vi.fn(async () => ({
      name: 'calculator',
      description: 'Evaluate arithmetic expressions.',
      parameters_schema: { type: 'object' },
      enabled_by_default: true,
      owner: 'builtin',
      enabled: false,
      can_manage: true,
    }))
    render(<ToolCenter client={createConfigClient({ listTools, setToolEnabled })} />)

    await screen.findByText('calculator')
    await user.click(screen.getByRole('button', { name: '展开 Schema' }))
    expect(screen.getByText(/"expression"/)).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox'))
    expect(setToolEnabled).toHaveBeenCalledWith('calculator', false)
    expect(await screen.findByRole('status')).toHaveTextContent('已禁用')
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

  it('marks suspicious documents in the list', async () => {
    const listDocuments = vi.fn().mockResolvedValue([
      {
        document_id: 'doc-1',
        filename: 'suspicious.pdf',
        text_characters: 120,
        chunk_count: 2,
        content_sha256: 'a'.repeat(64),
        embedding_model: 'nomic-embed-text',
        created_at: null,
        safety_verdict: 'suspicious',
      },
      {
        document_id: 'doc-2',
        filename: 'clean.pdf',
        text_characters: 90,
        chunk_count: 1,
        content_sha256: 'b'.repeat(64),
        embedding_model: 'nomic-embed-text',
        created_at: null,
        safety_verdict: 'clean',
      },
    ])
    render(
      <KnowledgeBase
        apiKeyConfigured
        ragStatus={readyRagStatus}
        client={{ listDocuments, uploadPdf: vi.fn() }}
        maxUploadBytes={10_000_000}
        onOpenRagChat={vi.fn()}
      />,
    )

    expect(await screen.findByText('suspicious.pdf')).toBeInTheDocument()
    expect(screen.getByText('疑似注入')).toBeInTheDocument()
    expect(screen.getByText('clean.pdf')).toBeInTheDocument()
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
