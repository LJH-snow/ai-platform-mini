# PDF RAG Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从真实 PDF 上传到 PostgreSQL/pgvector RAG 入库，并在 AI Platform Mini 前端提供可演示的知识库页面。

**Architecture:** 新增受限 PDF 提取器、RAG ingestion service 和进程内异步 worker；service 复用现有 chunker、Ollama embedder、PgVectorStore。API 按 API Key hash 隔离文档，只暴露安全摘要和有界文本预览，不改变现有 Chat/Agent RAG 查询链路。

**Tech Stack:** FastAPI UploadFile, pypdf, python-multipart, SQLAlchemy async, pgvector, React 19, TypeScript, Vitest, Playwright。

---

### Task 1: PDF 解析和入库领域服务

**Files:**
- Create: `app/rag/pdf_extractor.py`
- Create: `app/rag/ingestion.py`
- Modify: `app/core/settings.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `app/rag/pg_vector_store.py`
- Modify: `app/rag/vector_store.py`
- Test: `tests/test_pdf_extractor.py`
- Test: `tests/test_rag_ingestion.py`

- [ ] 增加 PDF 大小、页数、文本长度配置。
- [ ] 使用 `pypdf.PdfReader` 从 bytes 提取文本，拒绝非 PDF、加密 PDF、空文本和超限输入。
- [ ] 实现 ingestion service，计算 SHA-256、调用 chunker/embedder/store，并返回文档摘要。
- [ ] 为 PgVectorStore 增加安全文档摘要查询和 chunk count 聚合。
- [ ] 使用 fake embedder/store 覆盖成功、空文本、重复内容和 Embedding 数量不一致。

### Task 2: RAG 文档 API

**Files:**
- Create: `app/schemas/rag.py`
- Modify: `app/api/rag.py`
- Modify: `app/core/container.py`
- Modify: `app/main.py`
- Test: `tests/test_rag_documents_api.py`

- [ ] 增加 `POST /api/v1/rag/documents` 和 `GET /api/v1/rag/documents`。
- [ ] 复用现有普通用户 Key 和限流依赖，未认证请求不触达 RAG service。
- [ ] 映射上传、冲突、RAG 未启用和存储错误到既有异常边界。
- [ ] 覆盖鉴权、PDF MIME/签名、成功入库、列表和安全错误响应。

### Task 3: 前端知识库页面

**Files:**
- Create: `frontend/src/knowledge/client.ts`
- Create: `frontend/src/knowledge/types.ts`
- Create: `frontend/src/knowledge/KnowledgeBase.tsx`
- Create: `frontend/src/knowledge/knowledge.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Modify: `frontend/src/App.test.tsx`

- [ ] 实现 multipart 上传 client 和文档列表 client，统一处理 400/409/413/503。
- [ ] 增加知识库页面、文件选择/拖拽、状态提示、文档列表和进入 RAG 对话动作。
- [ ] 平台导航增加知识库入口，上传成功后刷新列表。
- [ ] 覆盖无 Key、文件类型、上传成功、冲突错误和移动端布局。

### Task 4: 异步队列、删除、预览与集成验证

**Files:**
- Modify: `README.md`
- Modify: `frontend/README.md`
- Modify: `.env.example`

- [ ] 运行 ruff、mypy、pytest（使用隔离的 memory/RAG-disabled 配置）和全部前端检查。
- [ ] 使用真实 PostgreSQL/pgvector/Ollama 环境验证 PDF 上传后的文档列表和 RAG 检索。
- [ ] 使用 Playwright 验证 Dashboard → 知识库 → 上传状态 → 文档列表 → RAG 对话入口。
- [ ] 更新 HR 演示步骤，明确 PDF 上传是真实异步入库，并展示删除和文本预览能力。
