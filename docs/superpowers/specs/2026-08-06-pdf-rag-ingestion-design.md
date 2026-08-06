# PDF 上传与 RAG 文档入库设计

## 目标

为 AI Platform Mini 增加真实 PDF 知识库入口，让普通用户可以通过前端上传 PDF，后端完成文本提取、分块、Ollama Embedding 和 PostgreSQL/pgvector 入库，并在前端看到已经入库的文档和分块数量。

## 设计边界

- 上传接口使用普通用户 API Key 和现有限流依赖。
- 只接受 PDF；限制文件大小、页数和提取文本总长度，避免无界资源消耗。
- 使用 `pypdf` 提取文本，不执行 PDF 内嵌脚本或外部资源。
- 文档内容 SHA-256 复用现有 `RagDocument.content_sha256` 约束；同内容返回冲突，同文件名的新内容沿用现有“替换旧文档”语义。
- 分块、Embedding 和写入复用现有 `chunk_text`、`OllamaEmbedder` 和 `PgVectorStore.add_document`。
- 入库接口返回 `202` 和进程内任务 ID；后台 worker 执行提取、向量化和写入，前端轮询真实任务状态，不伪造后端进度百分比。
- 提供文档列表接口，返回文件名、文档 ID、创建时间、文本字符数和分块数量；不返回完整正文和向量。

## 后端接口

### `POST /api/v1/rag/documents`

`multipart/form-data`，字段名 `file`。

成功返回：

```json
{
  "task_id": "uuid",
  "status": "queued",
  "document_id": null,
  "filename": "project-design.pdf",
  "error": null
}
```

任务完成后通过 `GET /api/v1/rag/tasks/{task_id}` 获取状态，并重新读取文档列表。

### 文档删除与文本预览

- `DELETE /api/v1/rag/documents/{document_id}`：仅删除当前 API Key 所属文档及其 chunks，成功返回 `204`。
- `GET /api/v1/rag/documents/{document_id}/preview`：仅返回当前 API Key 所属文档的有界提取文本，不返回原始 PDF 或向量。
- 文档和 chunk 使用 UUID；API Key 只保存为 SHA-256 hash 作为租户边界。

### `GET /api/v1/rag/documents`

返回安全的文档摘要列表，按创建时间倒序。

### 错误边界

- `400`：不是 PDF、PDF 加密、没有可提取文本、页数或文本长度超过限制。
- `409`：内容重复。
- `413`：文件超过大小限制。
- `422`：multipart 字段缺失或格式错误。
- `503`：RAG 未启用、Embedding 服务不可用或 pgvector 不可用。

## 前端页面

在现有平台壳层增加“知识库”导航/页面：

- 上传区域：拖拽和文件选择均可，明确只接受 PDF 和大小限制。
- 上传状态：只展示真实的请求阶段，不展示虚假百分比。
- 文档列表：展示文件名、状态、分块数、文本长度和入库时间。
- 重复或解析失败时显示安全错误，可再次上传。
- “进入 RAG 对话”按钮跳转到现有对话工作台，并预填知识库问题。

## 非目标

- 不实现跨进程持久化队列、断点续传或原始 PDF 落盘；PDF bytes 仅在 worker 处理期间暂存于内存。
- 页面内预览指提取文本预览，不渲染或保存原始 PDF。
- 不把向量暴露给前端。
