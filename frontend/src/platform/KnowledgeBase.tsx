import type { DragEvent, JSX } from 'react'
import { useCallback, useEffect, useState } from 'react'
import type { KnowledgeClient } from './knowledge.ts'
import { isKnowledgeDocument, KnowledgeApiError, waitForKnowledgeTask } from './knowledge.ts'
import type {
  KnowledgeDocument,
  KnowledgeDocumentPreview,
  KnowledgeTask,
} from './knowledge-types.ts'
import type { RagRuntimeStatus } from './rag-status.ts'

type KnowledgeBaseProps = {
  apiKeyConfigured: boolean
  ragStatus: RagRuntimeStatus
  client: Pick<KnowledgeClient, 'listDocuments' | 'uploadPdf'> &
    Partial<Pick<KnowledgeClient, 'getTask' | 'deleteDocument' | 'getDocumentPreview'>>
  maxUploadBytes: number
  onOpenRagChat: () => void
}

type UploadState = 'idle' | 'uploading' | 'success'
type ListState = 'loading' | 'ready' | 'error'

const formatBytes = (bytes: number): string => {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const formatDate = (value: string | null): string => {
  if (!value) return '后端未提供时间'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间格式不可用' : date.toLocaleString('zh-CN')
}

const getRagStatusTitle = (status: RagRuntimeStatus): string => {
  switch (status.kind) {
    case 'loading':
      return '正在检查 RAG 状态'
    case 'ready':
      return '知识库已就绪'
    case 'disabled':
      return 'RAG 未启用'
    case 'database_unavailable':
      return '知识库数据库不可用'
    case 'embedding_unavailable':
      return 'Embedding 服务不可用'
    case 'unavailable':
      return 'RAG 暂不可用'
    case 'error':
      return 'RAG 状态未知'
  }
}

const getRagStatusNotice = (status: RagRuntimeStatus): string => {
  switch (status.kind) {
    case 'loading':
      return '正在检查 RAG 服务状态…'
    case 'ready':
      return 'RAG 已启用，可以上传和查看当前 API Key 的知识库文档。'
    case 'disabled':
      return 'RAG 未启用，无法使用知识库。'
    case 'database_unavailable':
      return '知识库数据库不可用，暂无法确认或操作知识库。'
    case 'embedding_unavailable':
      return 'Embedding 服务不可用，暂无法上传或检索文档。'
    case 'unavailable':
      return 'RAG 服务暂不可用，请稍后重试。'
    case 'error':
      return '无法确认 RAG 服务状态，健康检查请求失败。'
  }
}

export function KnowledgeBase(props: KnowledgeBaseProps): JSX.Element {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([])
  const ragReady = props.ragStatus.kind === 'ready'
  const canUseKnowledge = props.apiKeyConfigured && ragReady
  const [listState, setListState] = useState<ListState>(canUseKnowledge ? 'loading' : 'ready')
  const [uploadState, setUploadState] = useState<UploadState>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('知识库页面已准备就绪。')
  const [preview, setPreview] = useState<KnowledgeDocumentPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const ragNotice = props.apiKeyConfigured ? getRagStatusNotice(props.ragStatus) : null
  const ragNoticeTitle = props.apiKeyConfigured ? getRagStatusTitle(props.ragStatus) : null
  const documentCountLabel = !ragReady
    ? '不可用'
    : listState === 'loading'
      ? '读取中'
      : `${documents.length} 个`

  const refresh = useCallback(async (): Promise<void> => {
    if (!canUseKnowledge) {
      setDocuments([])
      setListState('ready')
      return
    }
    setListState('loading')
    try {
      const nextDocuments = await props.client.listDocuments()
      if (!nextDocuments.every(isKnowledgeDocument)) {
        throw new Error('知识库返回的数据格式不完整。')
      }
      setDocuments(nextDocuments)
      setListState('ready')
      setErrorMessage(null)
    } catch (error) {
      setListState('error')
      setErrorMessage(error instanceof Error ? error.message : '知识库列表加载失败。')
    }
  }, [canUseKnowledge, props.client])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const uploadFile = async (file: File): Promise<void> => {
    setUploadState('idle')
    setErrorMessage(null)
    if (!props.apiKeyConfigured) {
      setErrorMessage('请先在管理员后台配置普通用户 API Key。')
      setAnnouncement('请先配置普通用户 API Key。')
      return
    }
    if (!ragReady) {
      setErrorMessage(null)
      setAnnouncement('知识库暂不可用，无法上传文档。')
      return
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setErrorMessage('目前只支持上传 PDF 文件。')
      setAnnouncement('目前只支持上传 PDF 文件。')
      return
    }
    if (file.size > props.maxUploadBytes) {
      setErrorMessage(`文件超过 ${formatBytes(props.maxUploadBytes)} 限制。`)
      setAnnouncement(`文件超过 ${formatBytes(props.maxUploadBytes)} 限制。`)
      return
    }

    setUploadState('uploading')
    setErrorMessage(null)
    setAnnouncement('正在提取 PDF 文本、生成向量并写入知识库。')
    try {
      const result = await props.client.uploadPdf(file)
      if (isKnowledgeDocument(result)) {
        setDocuments((current) => [
          result,
          ...current.filter(
            (item) => item.document_id !== result.document_id && item.filename !== result.filename,
          ),
        ])
        setListState('ready')
        setUploadState('success')
        setAnnouncement(`文档 ${result.filename} 已完成入库。`)
      } else {
        if (!props.client.getTask) {
          throw new Error('知识库任务状态接口不可用。')
        }
        const finalTask = await waitForKnowledgeTask(
          { getTask: props.client.getTask },
          result as KnowledgeTask,
          (task) => {
            const statusText =
              task.status === 'queued'
                ? '已排队，等待入库 worker。'
                : task.status === 'processing'
                  ? '正在提取文本、生成向量并写入知识库。'
                  : '正在确认文档索引结果。'
            setAnnouncement(statusText)
          },
        )
        await refresh()
        setUploadState('success')
        setAnnouncement(`文档 ${finalTask.filename} 已完成入库。`)
      }
    } catch (error) {
      setUploadState('idle')
      setErrorMessage(
        error instanceof KnowledgeApiError ? error.message : 'PDF 入库失败，请稍后重试。',
      )
      setAnnouncement(error instanceof Error ? error.message : 'PDF 入库失败。')
    }
  }

  const removeDocument = async (document: KnowledgeDocument): Promise<void> => {
    if (!props.client.deleteDocument) return
    setErrorMessage(null)
    try {
      await props.client.deleteDocument(document.document_id)
      setDocuments((current) => current.filter((item) => item.document_id !== document.document_id))
      setPreview((current) => (current?.document_id === document.document_id ? null : current))
      setAnnouncement(`文档 ${document.filename} 已删除。`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '文档删除失败。')
    }
  }

  const showPreview = async (document: KnowledgeDocument): Promise<void> => {
    if (!props.client.getDocumentPreview) return
    setPreviewLoading(true)
    setErrorMessage(null)
    try {
      setPreview(await props.client.getDocumentPreview(document.document_id))
      setAnnouncement(`已打开 ${document.filename} 的文本预览。`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '文档预览失败。')
    } finally {
      setPreviewLoading(false)
    }
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault()
    if (!canUseKnowledge || uploadState === 'uploading') return
    const file = event.dataTransfer.files.item(0)
    if (file) void uploadFile(file)
  }

  return (
    <section className="platformPage knowledgePage" aria-labelledby="knowledge-title">
      <div className="pageIntro compactPageIntro">
        <div>
          <p className="pageKicker">03 · RAG KNOWLEDGE BASE</p>
          <h1 id="knowledge-title">让文档成为可检索的上下文。</h1>
          <p className="pageLead">
            上传真实 PDF，系统会提取文本、切分内容、调用 Ollama Embedding，并写入
            pgvector。这里不展示原文和向量，只展示可审计的入库结果。
          </p>
        </div>
        <div className="introBadge">
          <span
            className={canUseKnowledge ? 'introBadgeDot' : 'introBadgeDot introBadgeDotPending'}
            aria-hidden="true"
          />
          <span>Knowledge Base</span>
          <strong>
            {canUseKnowledge
              ? 'Ready to ingest'
              : !props.apiKeyConfigured
                ? 'Key required'
                : props.ragStatus.kind === 'loading'
                  ? 'Checking RAG'
                  : props.ragStatus.kind === 'disabled'
                    ? 'RAG disabled'
                    : props.ragStatus.kind === 'error'
                      ? 'Status unknown'
                      : 'RAG unavailable'}
          </strong>
        </div>
      </div>

      {!props.apiKeyConfigured ? (
        <div className="knowledgeNotice">请先配置普通用户 API Key，再上传和查看知识库文档。</div>
      ) : ragNotice ? (
        <div className="knowledgeNotice">{ragNotice}</div>
      ) : null}
      <div className="knowledgeLayout">
        <section className="knowledgeUploadCard" aria-labelledby="upload-title">
          <div className="knowledgeCardHeader">
            <div>
              <p className="sectionKicker">INGESTION PIPELINE</p>
              <h2 id="upload-title">上传 PDF 并入库</h2>
            </div>
            <span className="knowledgeLimit">上限 {formatBytes(props.maxUploadBytes)}</span>
          </div>
          <div
            className={
              uploadState === 'uploading'
                ? 'knowledgeDropzone knowledgeDropzoneBusy'
                : 'knowledgeDropzone'
            }
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDrop}
            aria-disabled={!canUseKnowledge || uploadState === 'uploading'}
            aria-busy={uploadState === 'uploading'}
          >
            <div className="knowledgeDropIcon" aria-hidden="true">
              PDF
            </div>
            <strong>
              {uploadState === 'uploading' ? '正在入库，请稍候…' : '拖拽 PDF 到这里，或选择文件'}
            </strong>
            <span>仅支持可提取文本的 PDF，不保存原始文件。</span>
            <label className="primaryButton knowledgeChooseButton">
              选择 PDF 文件
              <input
                type="file"
                accept=".pdf,.txt,.md,.markdown,.docx,.xlsx,.html,.htm"
                disabled={!canUseKnowledge || uploadState === 'uploading'}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  event.target.value = ''
                  if (file) void uploadFile(file)
                }}
              />
            </label>
          </div>
          <div className="knowledgePipeline" aria-label="PDF 入库流程">
            <span>1 · 提取文本</span>
            <i aria-hidden="true">→</i>
            <span>2 · 生成 Embedding</span>
            <i aria-hidden="true">→</i>
            <span>3 · 写入 pgvector</span>
          </div>
          <p className="srOnly" aria-live="polite">
            {announcement}
          </p>
          {errorMessage ? (
            <div className="errorMessage knowledgeError" role="alert">
              {errorMessage}
            </div>
          ) : null}
          {uploadState === 'success' ? (
            <div className="successNotice">入库完成。现在可以去对话工作台验证 RAG 检索。</div>
          ) : null}
          <button
            type="button"
            className="secondaryButton knowledgeChatButton"
            onClick={props.onOpenRagChat}
            disabled={!canUseKnowledge}
          >
            去知识库问答 →
          </button>
        </section>

        <section className="knowledgeDocumentsCard" aria-labelledby="documents-title">
          <div className="knowledgeCardHeader">
            <div>
              <p className="sectionKicker">INDEXED DOCUMENTS</p>
              <h2 id="documents-title">已入库文档</h2>
            </div>
            <span className="knowledgeCount">{documentCountLabel}</span>
          </div>
          {!props.apiKeyConfigured ? (
            <div className="knowledgeEmptyState">
              <strong>需要 API Key</strong>
              <p>请先配置普通用户 API Key，再上传和查看知识库文档。</p>
            </div>
          ) : !ragReady ? (
            <div className="knowledgeEmptyState">
              <strong>{ragNoticeTitle}</strong>
              <p>{ragNotice}</p>
            </div>
          ) : listState === 'loading' ? (
            <div className="knowledgeEmptyState">正在读取已入库文档…</div>
          ) : listState === 'error' ? (
            <div className="knowledgeEmptyState">
              <strong>文档列表加载失败</strong>
              <p>当前无法确认知识库内容，请重试。</p>
              <button type="button" className="secondaryButton" onClick={() => void refresh()}>
                重试列表请求
              </button>
            </div>
          ) : documents.length === 0 ? (
            <div className="knowledgeEmptyState">
              <strong>当前知识库暂无文档</strong>
              <p>上传一份项目说明、接口文档或产品 PRD，马上就能演示真实 RAG 流程。</p>
            </div>
          ) : (
            <div className="knowledgeDocumentList">
              {documents.map((document) => (
                <article className="knowledgeDocument" key={document.document_id}>
                  <div className="knowledgeDocumentTopline">
                    <strong>{document.filename}</strong>
                    {document.safety_verdict === 'suspicious' && (
                      <span className="safetyBadge" title="命中注入规则，strict 模式下不参与检索">
                        疑似注入
                      </span>
                    )}
                    <span>已索引</span>
                  </div>
                  <div className="knowledgeFacts">
                    <span>
                      <small>文本</small>
                      {document.text_characters.toLocaleString()} 字符
                    </span>
                    <span>
                      <small>分块</small>
                      {document.chunk_count} chunks
                    </span>
                    <span>
                      <small>模型</small>
                      {document.embedding_model}
                    </span>
                    <span>
                      <small>入库时间</small>
                      {formatDate(document.created_at)}
                    </span>
                  </div>
                  <code title={document.content_sha256}>
                    SHA {document.content_sha256.slice(0, 12)}…
                  </code>
                  <div className="knowledgeDocumentActions">
                    <button
                      type="button"
                      className="secondaryButton"
                      onClick={() => void showPreview(document)}
                      disabled={!props.client.getDocumentPreview || previewLoading}
                    >
                      {previewLoading ? '读取中…' : '预览文本'}
                    </button>
                    <button
                      type="button"
                      className="secondaryButton dangerButton"
                      onClick={() => void removeDocument(document)}
                      disabled={!props.client.deleteDocument}
                    >
                      删除
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
      {preview ? (
        <section className="knowledgePreviewCard" aria-labelledby="preview-title">
          <div className="knowledgeCardHeader">
            <div>
              <p className="sectionKicker">EXTRACTED TEXT</p>
              <h2 id="preview-title">{preview.filename} · 文本预览</h2>
            </div>
            <button type="button" className="secondaryButton" onClick={() => setPreview(null)}>
              关闭预览
            </button>
          </div>
          <pre>{preview.text ?? preview.content ?? ''}</pre>
          {preview.truncated ? <p className="formHint">预览已截断，原始 PDF 不会保存。</p> : null}
        </section>
      ) : null}
    </section>
  )
}
