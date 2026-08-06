from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

from app.exceptions.base import KnowledgeBaseEmptyError
from app.providers.base import LLMProvider
from app.rag.pdf_extractor import ExtractedPdf
from app.rag.service import PreparedRAGRequest, RAGReference, RAGService
from app.schemas.chat import ChatRequest
from app.workflows.pdf_report import (
    PDFReportState,
    PDFReportWorkflow,
    ProviderRouterReportModel,
    RagServiceReportRetriever,
    ReportCompletion,
    ReportModel,
    RetrievedContext,
    build_run_summary,
)


class FakePdfExtractor:
    def __init__(self, extracted: ExtractedPdf) -> None:
        self._extracted = extracted
        self.paths: list[Path] = []

    async def extract(self, path: Path) -> ExtractedPdf:
        self.paths.append(path)
        return self._extracted


class FakeRetriever:
    def __init__(
        self,
        references: Sequence[RAGReference] = (),
        warning: str | None = None,
    ) -> None:
        self._references = tuple(references)
        self._warning = warning
        self.queries: list[str] = []
        self.owner_key_hashes: list[str] = []

    async def retrieve(self, query: str, *, owner_key_hash: str) -> RetrievedContext:
        self.queries.append(query)
        self.owner_key_hashes.append(owner_key_hash)
        return RetrievedContext(
            query=query,
            references=self._references,
            warning=self._warning,
        )


class FakeModel:
    def __init__(self, content: str = "Fake analysis") -> None:
        self._content = content
        self.messages: list[list[tuple[str, str]]] = []
        self.models: list[str | None] = []

    async def complete(
        self,
        messages: Sequence[tuple[str, str]],
        *,
        model: str | None = None,
    ) -> ReportCompletion:
        self.messages.append(list(messages))
        self.models.append(model)
        return ReportCompletion(
            content=self._content,
            model=model or "fake-model",
            prompt_tokens=11,
            completion_tokens=7,
        )


class FakeRAGService:
    def __init__(
        self,
        references: tuple[RAGReference, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._references = references
        self._error = error
        self.queries: list[str] = []
        self.owner_key_hashes: list[str] = []

    async def prepare(
        self,
        request: ChatRequest,
        *,
        owner_key_hash: str,
    ) -> PreparedRAGRequest:
        self.queries.append(request.message)
        self.owner_key_hashes.append(owner_key_hash)
        if self._error is not None:
            raise self._error
        return PreparedRAGRequest(
            enhanced_request=request,
            references=self._references,
        )


class FakeProvider:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.payloads: list[dict[str, Any]] = []

    @property
    def default_model(self) -> str:
        return "fake-default"

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return self._response

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        yield self._response

    async def list_models(self) -> dict[str, Any]:
        return {"models": [{"name": self.default_model}]}

    async def close(self) -> None:
        return None


def make_reference(
    *,
    document_id: str = "doc-1",
    chunk_id: str = "chunk-1",
    content: str = "Retrieved context",
) -> RAGReference:
    return RAGReference(
        document_id=document_id,
        chunk_id=chunk_id,
        chunk_index=0,
        content=content,
        distance=0.1,
    )


def make_workflow(
    tmp_path: Path,
    *,
    references: Sequence[RAGReference] = (),
    warning: str | None = None,
) -> tuple[PDFReportWorkflow, FakePdfExtractor, FakeRetriever, FakeModel, Path]:
    extractor = FakePdfExtractor(
        ExtractedPdf(filename="sample.pdf", text="PDF body text", page_count=2)
    )
    retriever = FakeRetriever(references=references, warning=warning)
    model = FakeModel()
    workflow = PDFReportWorkflow(
        extractor=extractor,
        retriever=retriever,
        model=model,
        max_document_characters=1_000,
        max_reference_characters=500,
    )
    output_path = tmp_path / "report.md"
    return workflow, extractor, retriever, model, output_path


def make_input(
    output_path: Path,
    *,
    require_approval: bool = False,
    topic: str | None = "General review",
) -> PDFReportState:
    return {
        "pdf_path": "/tmp/sample.pdf",
        "report_topic": topic,
        "owner_key_hash": "a" * 64,
        "output_path": str(output_path),
        "require_approval": require_approval,
    }


def test_graph_compiles_with_memory_checkpointer() -> None:
    workflow, _, _, _, _ = make_workflow(Path("/tmp"))

    graph = workflow.build()

    assert graph is not None


async def test_offline_nodes_run_without_approval(tmp_path: Path) -> None:
    reference = make_reference()
    workflow, extractor, retriever, model, output_path = make_workflow(
        tmp_path,
        references=(reference,),
    )
    graph = workflow.build()
    config: RunnableConfig = {"configurable": {"thread_id": "offline-1"}}

    result = await graph.ainvoke(make_input(output_path), config)

    assert result["report_path"] == str(output_path)
    assert output_path.exists()
    report = output_path.read_text(encoding="utf-8")
    assert "Fake analysis" in report
    assert "General review" in report
    assert retriever.queries == ["General review"]
    assert retriever.owner_key_hashes == ["a" * 64]
    assert extractor.paths == [Path("/tmp/sample.pdf")]
    assert len(model.messages) == 1
    assert "PDF body text" in model.messages[0][1][1]
    assert "Retrieved context" in model.messages[0][1][1]
    assert result["approval"] == "approved"


async def test_interrupt_waits_for_approval_then_resumes(tmp_path: Path) -> None:
    workflow, _, _, model, output_path = make_workflow(
        tmp_path,
        references=(make_reference(),),
    )
    graph = workflow.build()
    config: RunnableConfig = {"configurable": {"thread_id": "approval-1"}}

    paused = await graph.ainvoke(make_input(output_path, require_approval=True), config)

    interrupts = paused["__interrupt__"]
    assert len(interrupts) == 1
    assert interrupts[0].value["type"] == "report_approval"
    assert "report_path" not in paused
    assert len(model.messages) == 1

    resumed = await graph.ainvoke(
        Command(resume={"decision": "approved", "feedback": ""}),
        config,
    )

    assert resumed["report_path"] == str(output_path)
    assert output_path.exists()
    assert resumed["approval"] == "approved"


async def test_rejected_approval_reanalyzes_with_feedback(tmp_path: Path) -> None:
    workflow, _, _, model, output_path = make_workflow(
        tmp_path,
        references=(make_reference(),),
    )
    graph = workflow.build()
    config: RunnableConfig = {"configurable": {"thread_id": "reject-1"}}

    paused = await graph.ainvoke(make_input(output_path, require_approval=True), config)
    assert "report_path" not in paused

    paused_again = await graph.ainvoke(
        Command(resume={"decision": "rejected", "feedback": "add risk section"}),
        config,
    )

    assert "report_path" not in paused_again
    assert paused_again["__interrupt__"]
    assert len(model.messages) == 2
    assert "add risk section" in model.messages[1][1][1]

    final = await graph.ainvoke(
        Command(resume={"decision": "approved", "feedback": ""}),
        config,
    )

    assert final["report_path"] == str(output_path)
    assert final["revision_count"] == 1


async def test_rejection_can_end_without_report(tmp_path: Path) -> None:
    workflow, _, _, _, output_path = make_workflow(tmp_path)
    graph = workflow.build()
    config: RunnableConfig = {"configurable": {"thread_id": "reject-end-1"}}
    initial = make_input(output_path, require_approval=True)
    initial["max_revisions"] = 1

    paused = await graph.ainvoke(initial, config)
    assert "report_path" not in paused

    final = await graph.ainvoke(
        Command(resume={"decision": "rejected", "feedback": "stop"}),
        config,
    )

    assert "report_path" not in final
    assert final["approval"] == "rejected"
    assert not output_path.exists()


async def test_rag_retriever_reuses_rag_service_prepare() -> None:
    reference = make_reference()
    fake_rag_service = FakeRAGService(references=(reference,))
    retriever = RagServiceReportRetriever(cast(RAGService, fake_rag_service))

    context = await retriever.retrieve("question", owner_key_hash="a" * 64)

    assert context.query == "question"
    assert context.references == (reference,)
    assert fake_rag_service.queries == ["question"]
    assert fake_rag_service.owner_key_hashes == ["a" * 64]


async def test_rag_retriever_maps_empty_kb_to_warning() -> None:
    fake_rag_service = FakeRAGService(error=KnowledgeBaseEmptyError("empty"))
    retriever = RagServiceReportRetriever(cast(RAGService, fake_rag_service))

    context = await retriever.retrieve("question", owner_key_hash="a" * 64)

    assert context.references == ()
    assert context.warning == "empty"


async def test_provider_router_model_parses_provider_response() -> None:
    provider = FakeProvider(
        {
            "model": "gpt-test",
            "message": {"role": "assistant", "content": "Generated report"},
            "done": True,
            "prompt_eval_count": 12,
            "eval_count": 4,
        }
    )
    model: ReportModel = ProviderRouterReportModel(cast(LLMProvider, provider))

    completion = await model.complete([("user", "analyze")], model="gpt-test")

    assert completion.content == "Generated report"
    assert completion.model == "gpt-test"
    assert completion.prompt_tokens == 12
    assert completion.completion_tokens == 4
    assert provider.payloads[0]["model"] == "gpt-test"
    assert provider.payloads[0]["messages"] == [{"role": "user", "content": "analyze"}]


async def test_provider_router_model_rejects_missing_message() -> None:
    provider = FakeProvider({})
    model: ReportModel = ProviderRouterReportModel(cast(LLMProvider, provider))

    with pytest.raises(ValueError, match="message"):
        await model.complete([("user", "analyze")])


async def test_provider_router_model_rejects_missing_content() -> None:
    provider = FakeProvider({"message": {"role": "assistant"}})
    model: ReportModel = ProviderRouterReportModel(cast(LLMProvider, provider))

    with pytest.raises(ValueError, match="content"):
        await model.complete([("user", "analyze")])


def test_format_references_enforces_content_and_total_limits() -> None:
    workflow, _, _, _, _ = make_workflow(Path("/tmp"))
    references = (
        make_reference(document_id="doc-1", content="A" * 500),
        make_reference(document_id="doc-2", content="B" * 500),
    )

    formatted = workflow._format_references(
        references,
        max_content_characters=100,
        max_total_characters=250,
    )

    assert "A" * 100 in formatted
    assert "B" not in formatted
    assert len(formatted) <= 250


def test_format_references_truncates_first_entry_to_total_limit() -> None:
    workflow, _, _, _, _ = make_workflow(Path("/tmp"))

    formatted = workflow._format_references(
        (make_reference(content="A" * 500),),
        max_content_characters=100,
        max_total_characters=50,
    )

    assert len(formatted) == 50


def test_build_run_summary_reports_completed_status() -> None:
    state: PDFReportState = {
        "pdf_path": "/tmp/sample.pdf",
        "filename": "sample.pdf",
        "page_count": 2,
        "references": [make_reference()],
        "model_name": "fake-model",
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "approval": "approved",
        "report_path": "/tmp/report.md",
    }

    summary = build_run_summary(state)

    assert summary["status"] == "completed"
    assert summary["references"] == 1
    assert summary["report_path"] == "/tmp/report.md"


def test_build_run_summary_marks_pending_approval_with_thread_id() -> None:
    state: PDFReportState = {
        "pdf_path": "/tmp/sample.pdf",
        "filename": "sample.pdf",
    }

    summary = build_run_summary(state, thread_id="thread-1", paused=True)

    assert summary["status"] == "pending_approval"
    assert summary["thread_id"] == "thread-1"


def test_build_run_summary_marks_rejected() -> None:
    state: PDFReportState = {
        "approval": "rejected",
        "revision_count": 2,
    }

    summary = build_run_summary(state)

    assert summary["status"] == "rejected"
    assert summary["revision_count"] == 2


def test_build_run_summary_marks_not_generated() -> None:
    summary = build_run_summary({})

    assert summary["status"] == "not_generated"
    assert summary["thread_id"] is None
