from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversations.memory_repository import InMemoryConversationRepository
from app.conversations.repository import ConversationRepository
from app.conversations.service import ConversationService
from app.core.settings import RAG_EMBEDDING_DIMENSIONS, get_settings
from app.providers.base import LLMProvider
from app.providers.factory import create_llm_provider
from app.quota.memory_repository import InMemoryQuotaRepository
from app.quota.models import QuotaConfig
from app.quota.repository import QuotaRepository
from app.quota.service import QuotaService
from app.ratelimit.base import RateLimiter
from app.ratelimit.memory import MemorySlidingWindowLimiter
from app.ratelimit.service import RateLimitService
from app.usage.collector import UsageCollector
from app.usage.memory_repository import InMemoryUsageRepository
from app.usage.repository import UsageRepository
from app.usage.service import UsageService
from app.workflows.repository import WorkflowRunRepository

if TYPE_CHECKING:
    from app.agent_config.service import AgentDefinitionService
    from app.evals.agent_benchmark import AgentBenchmarkRunner
    from app.mcp.manager import MCPToolManager
    from app.prompts.service import PromptRegistryService
    from app.rag.embedder import Embedder
    from app.rag.ingestion import RAGIngestionService
    from app.rag.queue import RAGIngestionQueue
    from app.rag.service import RAGService
    from app.rag.vector_store import VectorStore
    from app.services.agent_run_record_service import AgentRunRecordService
    from app.services.agent_service import AgentService
    from app.services.chat_service import ChatService
    from app.services.workflow_service import PDFReportWorkflowService
    from app.workflows.checkpointer import PostgresWorkflowCheckpointer


@lru_cache
def provide_llm_provider() -> LLMProvider:
    return create_llm_provider()


@lru_cache
def provide_session_factory() -> async_sessionmaker[AsyncSession]:
    from app.db.session import create_async_session_factory

    return create_async_session_factory()


@lru_cache
def provide_agent_run_record_service() -> AgentRunRecordService | None:
    from app.db.init import get_engine
    from app.services.agent_run_record_service import AgentRunRecordService

    if get_engine() is None:
        return None
    return AgentRunRecordService(provide_session_factory())


@lru_cache
def provide_usage_repository() -> UsageRepository:
    settings = get_settings()
    if settings.auth_storage == "postgres":
        from app.usage.postgres_repository import PostgresUsageRepository

        session_factory = provide_session_factory()
        return PostgresUsageRepository(session_factory)
    return InMemoryUsageRepository()


@lru_cache
def provide_usage_service() -> UsageService:
    return UsageService(repository=provide_usage_repository())


@lru_cache
def provide_usage_collector() -> UsageCollector:
    return UsageCollector(provide_usage_service())


@lru_cache
def provide_conversation_repository() -> ConversationRepository:
    settings = get_settings()
    if settings.conversation_storage == "postgres":
        from app.conversations.postgres_repository import (
            PostgresConversationRepository,
        )

        return PostgresConversationRepository(provide_session_factory())
    return InMemoryConversationRepository()


@lru_cache
def provide_conversation_service() -> ConversationService:
    return ConversationService(repository=provide_conversation_repository())


@lru_cache
def provide_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return MemorySlidingWindowLimiter(
        limit=settings.rate_limit_per_minute,
        window_seconds=60,
    )


@lru_cache
def provide_rate_limit_service() -> RateLimitService:
    return RateLimitService(limiter=provide_rate_limiter())


@lru_cache
def provide_quota_service() -> QuotaService:
    settings = get_settings()
    config = QuotaConfig(
        daily_token_limit=settings.quota_daily_tokens or None,
        monthly_token_limit=settings.quota_monthly_tokens or None,
        default_reserve_tokens=512,
        reservation_ttl_seconds=settings.quota_reservation_ttl_seconds,
        reservation_renewal_seconds=settings.quota_reservation_renewal_seconds,
    )
    usage_repo = provide_usage_repository()
    if settings.auth_storage == "postgres":
        from app.quota.postgres_repository import PostgresQuotaRepository

        session_factory = provide_session_factory()
        quota_repo: QuotaRepository = PostgresQuotaRepository(
            session_factory, usage_repo
        )
    else:
        quota_repo = InMemoryQuotaRepository(usage_repo)
    return QuotaService(
        usage_repository=usage_repo,
        quota_repository=quota_repo,
        config=config,
    )


@lru_cache
def provide_embedder() -> Embedder | None:
    """Provide the embedding backend for RAG.

    The deterministic MockEmbedder backs the mock LLM provider so the
    E2E chain (PDF -> chunks -> retrieval -> answer) runs with zero
    external dependencies.
    """
    from app.rag.ollama_embedder import OllamaEmbedder

    settings = get_settings()
    if not settings.rag_enabled:
        return None
    if settings.llm_provider == "mock":
        from app.evals.mock_embedder import MockEmbedder

        return MockEmbedder(dimensions=settings.rag_embedding_dimensions)
    return OllamaEmbedder(
        base_url=settings.ollama_base_url,
        model=settings.rag_embedding_model,
        dimensions=settings.rag_embedding_dimensions,
        timeout_seconds=settings.rag_embedding_timeout_seconds,
    )


@lru_cache
def provide_vector_store() -> VectorStore | None:
    """Provide the active retriever according to RAG_SEARCH_MODE.

    ``vector`` returns the pure PgVectorStore (byte-identical legacy
    behavior); ``hybrid``/``keyword`` wrap it in a HybridRetriever that
    composes semantic + jieba keyword rankings via RRF.
    """
    from app.rag.hybrid import HybridRetriever
    from app.rag.pg_vector_store import PgVectorStore
    from app.rag.reranker import create_reranker

    settings = get_settings()
    if not settings.rag_enabled:
        return None
    session_factory = provide_session_factory()
    store = PgVectorStore(
        session_factory=session_factory,
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=RAG_EMBEDDING_DIMENSIONS,
        safety_mode=settings.rag_safety_mode,
    )
    if settings.rag_search_mode == "vector":
        return store
    reranker = create_reranker(
        settings.reranker_api_key.get_secret_value(),
        model=settings.reranker_model,
        timeout_seconds=settings.reranker_timeout_seconds,
    )
    return HybridRetriever(
        store,
        rrf_k=settings.rag_rrf_k,
        mode=settings.rag_search_mode,
        reranker=reranker,
    )


@lru_cache
def provide_rag_service() -> RAGService | None:
    from app.rag.service import RAGService

    embedder = provide_embedder()
    vector_store = provide_vector_store()
    if embedder is None or vector_store is None:
        return None
    settings = get_settings()
    chat_service = provide_chat_service()
    return RAGService(
        embedder=embedder,
        vector_store=vector_store,
        chat_service=chat_service,
        top_k=settings.rag_top_k,
        max_context_chars=settings.rag_max_context_chars,
        max_distance=settings.rag_max_distance,
    )


@lru_cache
def provide_rag_ingestion_service() -> RAGIngestionService | None:
    from app.rag.ingestion import RAGIngestionService

    embedder = provide_embedder()
    vector_store = provide_vector_store()
    if embedder is None or vector_store is None:
        return None
    settings = get_settings()
    return RAGIngestionService(
        embedder=embedder,
        vector_store=vector_store,
        embedding_model=settings.rag_embedding_model,
        embedding_dimensions=settings.rag_embedding_dimensions,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
        max_pages=settings.rag_max_pdf_pages,
        max_text_characters=settings.rag_max_document_characters,
        safety_mode=settings.rag_safety_mode,
    )


@lru_cache
def provide_rag_ingestion_queue() -> RAGIngestionQueue | None:
    from app.rag.queue import RAGIngestionQueue

    ingestion_service = provide_rag_ingestion_service()
    if ingestion_service is None:
        return None
    return RAGIngestionQueue(ingestion_service)


@lru_cache
def provide_workflow_checkpointer() -> PostgresWorkflowCheckpointer:
    from app.workflows.checkpointer import PostgresWorkflowCheckpointer

    return PostgresWorkflowCheckpointer(get_settings().database_url.get_secret_value())


@lru_cache
def provide_workflow_run_repository() -> WorkflowRunRepository:
    settings = get_settings()
    if settings.workflow_storage == "postgres":
        from app.workflows.postgres_repository import PostgresWorkflowRunRepository

        return PostgresWorkflowRunRepository(provide_session_factory())
    from app.workflows.memory_repository import InMemoryWorkflowRunRepository

    return InMemoryWorkflowRunRepository()


@lru_cache
def provide_workflow_service() -> PDFReportWorkflowService | None:
    from langgraph.checkpoint.base import BaseCheckpointSaver

    from app.services.workflow_service import PDFReportWorkflowService
    from app.workflows.pdf_report import (
        PdfFileExtractor,
        PDFReportWorkflow,
        ProviderRouterReportModel,
        RagServiceReportRetriever,
    )

    settings = get_settings()
    rag_service = provide_rag_service()
    if rag_service is None:
        return None

    if settings.workflow_storage == "postgres":
        checkpointer: BaseCheckpointSaver = provide_workflow_checkpointer().saver
    else:
        from langgraph.checkpoint.memory import InMemorySaver

        from app.workflows.serde import create_workflow_serde

        checkpointer = InMemorySaver(serde=create_workflow_serde())

    workflow = PDFReportWorkflow(
        extractor=PdfFileExtractor(
            max_pages=settings.rag_max_pdf_pages,
            max_text_characters=settings.rag_max_document_characters,
        ),
        retriever=RagServiceReportRetriever(rag_service),
        model=ProviderRouterReportModel(provide_llm_provider()),
        checkpointer=checkpointer,
    )
    return PDFReportWorkflowService(
        workflow=workflow,
        checkpointer=checkpointer,
        run_repository=provide_workflow_run_repository(),
        work_dir=Path("output/workflows"),
        max_upload_bytes=settings.rag_max_upload_bytes,
    )


@lru_cache
def provide_mcp_manager() -> MCPToolManager:
    """Build the MCP manager from the explicit application allowlist."""

    from app.mcp.manager import MCPToolManager

    return MCPToolManager(get_settings().get_mcp_server_configs())


@lru_cache
def provide_chat_service() -> ChatService:
    from app.services.chat_service import ChatService

    return ChatService(provider=provide_llm_provider())


@lru_cache
def provide_agent_service() -> AgentService:
    from app.services.agent_service import AgentService
    from app.tools.calculator import CalculatorTool
    from app.tools.protocols import Tool
    from app.tools.registry import ToolRegistry

    mcp_manager = provide_mcp_manager()
    tools: list[Tool] = [CalculatorTool()]
    rag_service = provide_rag_service()
    if rag_service is not None:
        from app.tools.knowledge_search import KnowledgeSearchTool

        tools.append(KnowledgeSearchTool(rag_service=rag_service))
    tools.extend(mcp_manager.list_tools())

    return AgentService(
        chat_service=provide_chat_service(),
        quota_service=provide_quota_service(),
        usage_collector=provide_usage_collector(),
        tool_registry=ToolRegistry(tools),
        granted_permissions=mcp_manager.granted_permissions(),
        prompt_registry=provide_prompt_registry(),
        agent_definition_service=provide_agent_definition_service(),
    )


@lru_cache
def provide_prompt_registry() -> PromptRegistryService:
    """Provide the PromptRegistryService with appropriate storage backend."""
    from app.prompts.repository import (
        InMemoryPromptRepository,
        PostgresPromptRepository,
        PromptRepository,
    )
    from app.prompts.service import PromptRegistryService

    settings = get_settings()
    if settings.auth_storage == "postgres":
        repo: PromptRepository = PostgresPromptRepository(provide_session_factory())
    else:
        repo = InMemoryPromptRepository()
    return PromptRegistryService(repository=repo)


@lru_cache
def provide_agent_definition_service() -> AgentDefinitionService:
    """Provide AgentDefinitionService with appropriate storage backend."""
    from app.agent_config.repository import (
        AgentDefinitionRepository,
        InMemoryAgentDefinitionRepository,
        PostgresAgentDefinitionRepository,
    )
    from app.agent_config.service import AgentDefinitionService

    settings = get_settings()
    if settings.auth_storage == "postgres":
        repo: AgentDefinitionRepository = PostgresAgentDefinitionRepository(
            provide_session_factory()
        )
    else:
        repo = InMemoryAgentDefinitionRepository()
    # Build a lightweight ToolRegistry from seeds — must NOT call
    # provide_agent_service() here to avoid a circular import.
    from app.tools.calculator import CalculatorTool
    from app.tools.registry import ToolRegistry

    tool_registry = ToolRegistry([CalculatorTool()])
    rag_service = provide_rag_service()
    if rag_service is not None:
        from app.tools.knowledge_search import KnowledgeSearchTool

        tool_registry.register(KnowledgeSearchTool(rag_service=rag_service))
    # MCP tools are also valid agent whitelist entries; register them so
    # create/update tool_names validation matches the runtime registry.
    # provide_mcp_manager() only depends on Settings, so no cycle here.
    for tool in provide_mcp_manager().list_tools():
        tool_registry.register(tool)
    return AgentDefinitionService(
        repository=repo,
        tool_registry=tool_registry,
        prompt_registry=provide_prompt_registry(),
    )


@lru_cache
def provide_agent_benchmark_runner() -> AgentBenchmarkRunner:
    """Provide the AgentBenchmarkRunner backed by the real AgentService."""
    from app.evals.agent_benchmark import AgentBenchmarkRunner
    from app.evals.benchmark_repository import (
        InMemoryBenchmarkRunRepository,
        PostgresBenchmarkRunRepository,
    )

    settings = get_settings()
    from app.evals.benchmark_repository import BenchmarkRunRepository

    repo: BenchmarkRunRepository
    if settings.auth_storage == "postgres":
        repo = PostgresBenchmarkRunRepository(provide_session_factory())
    else:
        repo = InMemoryBenchmarkRunRepository()
    return AgentBenchmarkRunner(
        agent_service=provide_agent_service(),
        agent_definition_service=provide_agent_definition_service(),
        run_repository=repo,
    )


def clear_container_cache() -> None:
    """Clear all lru_cache'd provider factories across the application.

    Must be called after closing resources (embedder, provider, db)
    during lifespan shutdown to prevent a subsequent lifespan from
    reusing stale, already-closed objects.

    Clears caches in both ``app.core.container`` and
    ``app.auth.dependencies`` to ensure that services holding
    database session factories (e.g. APIKeyService in PostgreSQL
    mode) are not reused after the engine has been disposed.
    """
    from app.auth.dependencies import (
        _admin_key_hashes as auth_admin_key_hashes,
    )
    from app.auth.dependencies import (
        provide_api_key_service as auth_provide_api_key_service,
    )

    # Clear in reverse dependency order: dependents before their deps.
    provide_agent_benchmark_runner.cache_clear()
    provide_agent_definition_service.cache_clear()
    provide_prompt_registry.cache_clear()
    provide_agent_service.cache_clear()
    provide_agent_run_record_service.cache_clear()
    provide_workflow_service.cache_clear()
    provide_workflow_run_repository.cache_clear()
    provide_workflow_checkpointer.cache_clear()
    provide_mcp_manager.cache_clear()
    provide_rag_service.cache_clear()
    provide_rag_ingestion_queue.cache_clear()
    provide_rag_ingestion_service.cache_clear()
    provide_vector_store.cache_clear()
    provide_embedder.cache_clear()
    provide_chat_service.cache_clear()
    provide_quota_service.cache_clear()
    provide_rate_limit_service.cache_clear()
    provide_rate_limiter.cache_clear()
    provide_usage_collector.cache_clear()
    provide_usage_service.cache_clear()
    provide_usage_repository.cache_clear()
    provide_conversation_service.cache_clear()
    provide_conversation_repository.cache_clear()
    provide_session_factory.cache_clear()
    provide_llm_provider.cache_clear()
    # Auth module caches — may hold stale session factories after
    # dispose_db() in PostgreSQL mode.  Guard against None (e.g. if
    # the module was patched during testing).
    auth_provide_api_key_service_cache = getattr(
        auth_provide_api_key_service, "cache_clear", None
    )
    if auth_provide_api_key_service_cache is not None:
        auth_provide_api_key_service_cache()
    auth_admin_key_hashes_cache = getattr(auth_admin_key_hashes, "cache_clear", None)
    if auth_admin_key_hashes_cache is not None:
        auth_admin_key_hashes_cache()

    try:
        from app.api.auth import _clear_auth_service_caches

        _clear_auth_service_caches()
    except Exception:
        pass
