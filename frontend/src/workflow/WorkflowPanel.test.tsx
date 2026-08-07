import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { WorkflowApiError, WorkflowNetworkError, type WorkflowClient } from './client.ts'
import { WorkflowPanel } from './WorkflowPanel.tsx'
import type { WorkflowStatus } from './types.ts'

const pendingStatus: WorkflowStatus = {
  threadId: 't-123',
  status: 'pending_approval',
  stage: 'awaiting_approval',
  filename: 'sample.pdf',
  reportTopic: 'Finance',
  pageCount: 10,
  retrievalQuery: 'query',
  references: 3,
  retrievalWarning: null,
  draftSummary: 'Draft summary here',
  report: null,
  model: 'qwen3:4b',
  promptTokens: 50,
  completionTokens: 100,
  revisionCount: 0,
  errorCode: null,
  errorMessage: null,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:01Z',
}

const completedStatus: WorkflowStatus = {
  ...pendingStatus,
  status: 'completed',
  stage: 'completed',
  report: 'Final report content',
}

const failedStatus: WorkflowStatus = {
  ...pendingStatus,
  status: 'failed',
  stage: 'failed',
  errorCode: 'WORKFLOW_EXECUTION_FAILED',
  errorMessage: 'Execution failed.',
}

const createClient = (overrides: Partial<WorkflowClient> = {}): WorkflowClient => ({
  uploadPdf: vi.fn().mockResolvedValue(pendingStatus),
  getStatus: vi.fn().mockResolvedValue(pendingStatus),
  approve: vi.fn().mockResolvedValue(completedStatus),
  reject: vi.fn().mockResolvedValue({
    ...pendingStatus,
    status: 'rejected',
    stage: 'rejected',
  }),
  ...overrides,
})

afterEach(() => {
  cleanup()
})

describe('WorkflowPanel', () => {
  it('shows upload form when idle', () => {
    render(<WorkflowPanel apiKeyConfigured client={createClient()} />)

    expect(screen.getByLabelText('选择 PDF 文件')).toBeInTheDocument()
    expect(screen.getByLabelText('报告主题（可选）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始生成报告' })).toBeDisabled()
  })

  it('warns when api key is missing', () => {
    render(<WorkflowPanel apiKeyConfigured={false} client={createClient()} />)

    expect(screen.getByText(/未配置 API Key/)).toBeInTheDocument()
  })

  it('uploads pdf and transitions to pending_approval', async () => {
    const client = createClient()
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const fileInput = screen.getByLabelText('选择 PDF 文件')
    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(fileInput, file)

    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(client.uploadPdf).toHaveBeenCalledWith(file, '')
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '批准生成最终报告' })).toBeInTheDocument()
    })

    expect(screen.getByText('Draft summary here')).toBeInTheDocument()
    expect(screen.getAllByText('sample.pdf').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('Finance').length).toBeGreaterThanOrEqual(1)
  })

  it('shows approve and reject buttons when pending', async () => {
    const client = createClient({ uploadPdf: vi.fn().mockResolvedValue(pendingStatus) })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '批准生成最终报告' })).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: '拒绝草稿并携带反馈重新分析' })).toBeInTheDocument()
  })

  it('approves workflow and shows completed report', async () => {
    const client = createClient({
      uploadPdf: vi.fn().mockResolvedValue(pendingStatus),
      approve: vi.fn().mockResolvedValue(completedStatus),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '批准生成最终报告' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: '批准生成最终报告' }))

    await waitFor(() => {
      expect(client.approve).toHaveBeenCalledWith('t-123')
    })

    await waitFor(() => {
      expect(screen.getByText('Final report content')).toBeInTheDocument()
    })
  })

  it('rejects workflow with feedback', async () => {
    const client = createClient({
      uploadPdf: vi.fn().mockResolvedValue(pendingStatus),
      reject: vi.fn().mockResolvedValue({
        ...pendingStatus,
        status: 'rejected',
        stage: 'rejected',
      }),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '拒绝草稿并携带反馈重新分析' })).toBeEnabled()
    })

    await user.type(
      screen.getByPlaceholderText('输入修改建议，例如：补充风险章节'),
      'Need more data',
    )
    await user.click(screen.getByRole('button', { name: '拒绝草稿并携带反馈重新分析' }))

    await waitFor(() => {
      expect(client.reject).toHaveBeenCalledWith('t-123', 'Need more data')
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '新建任务' })).toBeInTheDocument()
    })
  })

  it('shows error when rejecting without feedback', async () => {
    const client = createClient({ uploadPdf: vi.fn().mockResolvedValue(pendingStatus) })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '拒绝草稿并携带反馈重新分析' })).toBeEnabled()
    })

    await user.click(screen.getByRole('button', { name: '拒绝草稿并携带反馈重新分析' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/拒绝反馈不能为空/)
    })
    expect(client.reject).not.toHaveBeenCalled()
  })

  it('displays failed status with safe error info', async () => {
    const client = createClient({
      uploadPdf: vi.fn().mockResolvedValue(failedStatus),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByText('执行失败')).toBeInTheDocument()
    })

    expect(
      screen.getByText((content) => content.includes('WORKFLOW_EXECUTION_FAILED')),
    ).toBeInTheDocument()
    expect(screen.getByText('Execution failed.')).toBeInTheDocument()
  })

  it('shows network error with role=alert', async () => {
    const client = createClient({
      uploadPdf: vi.fn().mockRejectedValue(new WorkflowNetworkError()),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toHaveTextContent(/网络连接失败/)
    })
  })

  it('shows 401 error with role=alert', async () => {
    const client = createClient({
      uploadPdf: vi.fn().mockRejectedValue(new WorkflowApiError('Unauthorized', 401)),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toHaveTextContent(/鉴权失败/)
    })
  })

  it('resets panel on 新建任务 button', async () => {
    const client = createClient({ uploadPdf: vi.fn().mockResolvedValue(pendingStatus) })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '批准生成最终报告' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: '新建任务' }))

    expect(screen.queryByRole('button', { name: '批准生成最终报告' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始生成报告' })).toBeDisabled()
  })

  it('enters polling state for running workflow', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const runningStatus: WorkflowStatus = {
      ...pendingStatus,
      status: 'running',
      stage: 'starting',
    }
    const client = createClient({
      uploadPdf: vi.fn().mockResolvedValue(runningStatus),
      getStatus: vi.fn().mockResolvedValue(pendingStatus),
    })
    const user = userEvent.setup({ delay: null })
    render(<WorkflowPanel apiKeyConfigured client={client} />)

    const file = new File(['pdf'], 'sample.pdf', { type: 'application/pdf' })
    await user.upload(screen.getByLabelText('选择 PDF 文件'), file)
    await user.click(screen.getByRole('button', { name: '开始生成报告' }))

    // Allow React to flush the upload -> running state transition.
    await Promise.resolve()

    vi.advanceTimersByTime(4000)

    await waitFor(() => {
      expect(client.getStatus).toHaveBeenCalledWith('t-123', expect.any(AbortSignal))
    })

    vi.useRealTimers()
  })
})
