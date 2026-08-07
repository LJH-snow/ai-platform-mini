import type { ChangeEvent, FormEvent, JSX } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { WorkflowClient } from './client.ts'
import { WorkflowApiError, WorkflowNetworkError } from './client.ts'
import type { WorkflowStatus } from './types.ts'

type WorkflowPanelProps = {
  apiKeyConfigured: boolean
  client: WorkflowClient
}

type PanelState =
  | 'idle'
  | 'uploading'
  | 'polling'
  | 'pending_approval'
  | 'completed'
  | 'rejected'
  | 'failed'

const stageLabels: Record<WorkflowStatus['stage'], string> = {
  starting: '解析与检索中',
  awaiting_approval: '等待审批',
  completed: '已完成',
  rejected: '已拒绝',
  failed: '执行失败',
}

const statusLabels: Record<WorkflowStatus['status'], string> = {
  running: '运行中',
  pending_approval: '等待审批',
  completed: '已完成',
  rejected: '已拒绝',
  failed: '失败',
}

const safeErrorMessage = (error: unknown): string => {
  if (error instanceof WorkflowApiError) {
    if (error.status === 401 || error.status === 403) return '鉴权失败，请检查 API Key。'
    if (error.status === 404) return '工作流不存在或已被删除。'
    if (error.status === 409) return '状态冲突，请刷新后重试。'
    if (error.status === 413) return 'PDF 文件过大。'
    if (error.status >= 500) return '服务暂时不可用，请稍后重试。'
    return error.message
  }
  if (error instanceof WorkflowNetworkError) return '网络连接失败，请检查后端服务。'
  return '请求失败，请稍后重试。'
}

const POLL_INTERVAL_MS = 3000

export function WorkflowPanel({ apiKeyConfigured, client }: WorkflowPanelProps): JSX.Element {
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [topic, setTopic] = useState('')
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [workflow, setWorkflow] = useState<WorkflowStatus | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('工作流面板已准备就绪。')
  const [feedback, setFeedback] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isFetchingRef = useRef(false)
  const abortController = useRef<AbortController | null>(null)

  const stopPolling = useCallback((): void => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current)
      pollTimer.current = null
    }
    if (abortController.current) {
      abortController.current.abort()
      abortController.current = null
    }
    isFetchingRef.current = false
  }, [])

  useEffect(() => {
    return () => {
      stopPolling()
    }
  }, [stopPolling])

  const handleStatus = useCallback(
    (status: WorkflowStatus): void => {
      setWorkflow(status)
      if (status.status === 'pending_approval') {
        setPanelState('pending_approval')
        setAnnouncement('报告草稿已生成，等待审批。')
        stopPolling()
      } else if (status.status === 'completed') {
        setPanelState('completed')
        setAnnouncement('报告已生成。')
        stopPolling()
      } else if (status.status === 'rejected') {
        setPanelState('rejected')
        setAnnouncement('报告已拒绝。')
        stopPolling()
      } else if (status.status === 'failed') {
        setPanelState('failed')
        setAnnouncement('工作流执行失败。')
        stopPolling()
      } else {
        setPanelState('polling')
      }
    },
    [stopPolling],
  )

  const startPolling = useCallback(
    (threadId: string): void => {
      stopPolling()
      abortController.current = new AbortController()

      const poll = async (): Promise<void> => {
        if (isFetchingRef.current) return
        isFetchingRef.current = true
        try {
          const status = await client.getStatus(threadId, abortController.current?.signal)
          handleStatus(status)
          if (status.status === 'running' && abortController.current?.signal.aborted === false) {
            pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS)
          }
        } catch {
          // Polling errors are silently ignored; user can manually refresh.
          if (abortController.current?.signal.aborted === false) {
            pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS)
          }
        } finally {
          isFetchingRef.current = false
        }
      }

      void poll()
    },
    [client, handleStatus, stopPolling],
  )

  const handleUpload = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (!pdfFile || !apiKeyConfigured) return

    stopPolling()
    setPanelState('uploading')
    setErrorMessage(null)
    setAnnouncement('正在上传 PDF 并启动工作流…')

    try {
      const status = await client.uploadPdf(pdfFile, topic)
      handleStatus(status)
      if (status.status === 'running') {
        startPolling(status.threadId)
      }
    } catch (error) {
      setPanelState('idle')
      setErrorMessage(safeErrorMessage(error))
      setAnnouncement('上传失败，请检查文件和凭据后重试。')
    }
  }

  const handleApprove = async (): Promise<void> => {
    if (!workflow) return
    setActionLoading(true)
    setErrorMessage(null)
    setAnnouncement('正在批准并生成最终报告…')
    try {
      const status = await client.approve(workflow.threadId)
      handleStatus(status)
    } catch (error) {
      setErrorMessage(safeErrorMessage(error))
      setAnnouncement('审批操作失败。')
    } finally {
      setActionLoading(false)
    }
  }

  const handleReject = async (): Promise<void> => {
    if (!workflow) return
    const trimmed = feedback.trim()
    if (!trimmed) {
      setErrorMessage('拒绝反馈不能为空。')
      return
    }
    setActionLoading(true)
    setErrorMessage(null)
    setAnnouncement('正在拒绝并重新分析…')
    try {
      const status = await client.reject(workflow.threadId, trimmed)
      handleStatus(status)
      if (status.status === 'running') {
        startPolling(status.threadId)
      }
      setFeedback('')
    } catch (error) {
      setErrorMessage(safeErrorMessage(error))
      setAnnouncement('拒绝操作失败。')
    } finally {
      setActionLoading(false)
    }
  }

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0] ?? null
    setPdfFile(file)
    setErrorMessage(null)
  }

  const handleReset = (): void => {
    stopPolling()
    setPdfFile(null)
    setTopic('')
    setWorkflow(null)
    setPanelState('idle')
    setErrorMessage(null)
    setFeedback('')
    setActionLoading(false)
    setAnnouncement('已重置，可上传新 PDF。')
  }

  const canUpload = apiKeyConfigured && pdfFile !== null && panelState === 'idle'
  const isBusy = panelState === 'uploading' || panelState === 'polling' || actionLoading

  return (
    <div className="workflowPanel">
      <section className="hero workflowHero">
        <div>
          <p className="eyebrow">WORKSPACE · PDF WORKFLOW</p>
          <h1>PDF 报告工作流</h1>
          <p className="heroCopy">
            上传 PDF 文档，后端自动解析、检索上下文、分析内容并生成报告草稿；审批后输出最终报告。
          </p>
        </div>
        <div className="heroActions">
          <div className="statusPill">
            {panelState === 'idle'
              ? '待上传'
              : panelState === 'uploading'
                ? '上传中'
                : workflow
                  ? statusLabels[workflow.status]
                  : '处理中'}
          </div>
          {workflow ? (
            <button type="button" className="secondaryButton" onClick={handleReset}>
              新建任务
            </button>
          ) : null}
        </div>
      </section>

      <div className="srOnly" aria-live="polite" aria-atomic="true" role="status">
        {announcement}
      </div>

      {errorMessage ? (
        <div className="errorBanner" role="alert">
          <span>{errorMessage}</span>
          <button
            type="button"
            className="errorDismiss"
            aria-label="关闭错误提示"
            onClick={() => setErrorMessage(null)}
          >
            ×
          </button>
        </div>
      ) : null}

      <div className="workflowGrid">
        <section className="panel workflowUploadPanel" aria-label="上传与配置">
          <div className="panelHeader">
            <h2>上传 PDF</h2>
          </div>
          <form className="workflowUploadForm" onSubmit={handleUpload}>
            <label htmlFor="workflow-pdf" className="workflowFileLabel">
              选择 PDF 文件
            </label>
            <input
              id="workflow-pdf"
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
              disabled={isBusy}
              aria-describedby="workflow-pdf-hint"
            />
            <p id="workflow-pdf-hint" className="workflowHint">
              仅支持 PDF 格式，文件过大时后端会返回错误。
            </p>

            {pdfFile ? (
              <p className="workflowFileName">
                已选择：<strong>{pdfFile.name}</strong>（{Math.round(pdfFile.size / 1024)} KB）
              </p>
            ) : null}

            <label htmlFor="workflow-topic" className="workflowFileLabel">
              报告主题（可选）
            </label>
            <input
              id="workflow-topic"
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例如：财务审计报告"
              disabled={isBusy}
              maxLength={1000}
            />

            <button type="submit" disabled={!canUpload || isBusy}>
              {panelState === 'uploading' ? '上传中…' : '开始生成报告'}
            </button>

            {!apiKeyConfigured ? (
              <p className="workflowHint workflowHintWarning">
                未配置 API Key，请先在工作台侧边栏设置用户 Key。
              </p>
            ) : null}
          </form>
        </section>

        <section className="panel workflowStatusPanel" aria-label="工作流状态">
          <div className="panelHeader">
            <h2>执行状态</h2>
          </div>

          {workflow ? (
            <div className="workflowStatusContent">
              <div className="workflowMeta">
                <div>
                  <span className="metricLabel">任务标识</span>
                  <span className="workflowThreadId">{workflow.threadId}</span>
                </div>
                <div>
                  <span className="metricLabel">当前阶段</span>
                  <span className={`workflowStage stage-${workflow.stage}`}>
                    {stageLabels[workflow.stage]}
                  </span>
                </div>
                {workflow.filename ? (
                  <div>
                    <span className="metricLabel">文件名</span>
                    <span>{workflow.filename}</span>
                  </div>
                ) : null}
                {workflow.reportTopic ? (
                  <div>
                    <span className="metricLabel">主题</span>
                    <span>{workflow.reportTopic}</span>
                  </div>
                ) : null}
                {workflow.pageCount !== null ? (
                  <div>
                    <span className="metricLabel">页数</span>
                    <span>{workflow.pageCount}</span>
                  </div>
                ) : null}
                {workflow.references !== null ? (
                  <div>
                    <span className="metricLabel">引用数</span>
                    <span>{workflow.references}</span>
                  </div>
                ) : null}
                {workflow.model ? (
                  <div>
                    <span className="metricLabel">模型</span>
                    <span>{workflow.model}</span>
                  </div>
                ) : null}
                {workflow.promptTokens !== null || workflow.completionTokens !== null ? (
                  <div>
                    <span className="metricLabel">Token 用量</span>
                    <span>
                      {workflow.promptTokens ?? '-'} / {workflow.completionTokens ?? '-'}
                    </span>
                  </div>
                ) : null}
              </div>

              {workflow.draftSummary ? (
                <div className="workflowDraft">
                  <span className="metricLabel">分析摘要</span>
                  <p className="workflowDraftText">{workflow.draftSummary}</p>
                </div>
              ) : null}

              {workflow.retrievalWarning ? (
                <div className="workflowWarning">
                  <span className="metricLabel">检索警告</span>
                  <p>{workflow.retrievalWarning}</p>
                </div>
              ) : null}

              {panelState === 'pending_approval' ? (
                <div className="workflowApproval">
                  <span className="metricLabel">审批操作</span>
                  <div className="workflowApprovalActions">
                    <button
                      type="button"
                      onClick={handleApprove}
                      disabled={actionLoading}
                      aria-label="批准生成最终报告"
                    >
                      {actionLoading ? '处理中…' : '批准生成报告'}
                    </button>
                    <div className="workflowRejectGroup">
                      <label htmlFor="workflow-feedback" className="srOnly">
                        拒绝反馈
                      </label>
                      <input
                        id="workflow-feedback"
                        type="text"
                        value={feedback}
                        onChange={(e) => setFeedback(e.target.value)}
                        placeholder="输入修改建议，例如：补充风险章节"
                        disabled={actionLoading}
                        maxLength={4000}
                      />
                      <button
                        type="button"
                        className="secondaryButton"
                        onClick={handleReject}
                        disabled={actionLoading}
                        aria-label="拒绝草稿并携带反馈重新分析"
                      >
                        {actionLoading ? '处理中…' : '拒绝并修改'}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

              {panelState === 'completed' && workflow.report ? (
                <div className="workflowReport">
                  <span className="metricLabel">最终报告</span>
                  <pre className="workflowReportText">{workflow.report}</pre>
                </div>
              ) : null}

              {panelState === 'failed' ? (
                <div className="workflowFailure">
                  <span className="metricLabel">失败信息</span>
                  {workflow.errorCode ? <p>错误码：{workflow.errorCode}</p> : null}
                  {workflow.errorMessage ? (
                    <p className="safeError">{workflow.errorMessage}</p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="workflowEmpty">
              <p>暂无运行中的工作流。</p>
              <span>上传 PDF 后状态将显示在这里。</span>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
