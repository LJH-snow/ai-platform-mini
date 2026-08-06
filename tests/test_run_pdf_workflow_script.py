import importlib.util
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from app.rag.pdf_extractor import ExtractedPdf
from app.rag.service import PreparedRAGRequest, RAGReference
from app.schemas.chat import ChatRequest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_pdf_workflow.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_pdf_workflow", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ModuleType, module)


class FakeRAGService:
    def __init__(self, references: tuple[RAGReference, ...] = ()) -> None:
        self._references = references

    async def prepare(
        self,
        request: ChatRequest,
        *,
        owner_key_hash: str,
    ) -> PreparedRAGRequest:
        return PreparedRAGRequest(
            enhanced_request=request,
            references=self._references,
        )


class FakeProvider:
    def __init__(self) -> None:
        self.chat_count = 0

    @property
    def default_model(self) -> str:
        return "fake-model"

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.chat_count += 1
        return {
            "model": "fake-model",
            "message": {"role": "assistant", "content": "Fake report"},
            "done": True,
        }

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        yield self._response()

    async def list_models(self) -> dict[str, Any]:
        return {"models": [{"name": self.default_model}]}

    async def close(self) -> None:
        return None

    def _response(self) -> dict[str, Any]:
        return {
            "model": "fake-model",
            "message": {"role": "assistant", "content": "Fake report"},
            "done": True,
        }


class FakePdfExtractor:
    def __init__(self, *, max_pages: int, max_text_characters: int) -> None:
        self.max_pages = max_pages
        self.max_text_characters = max_text_characters

    async def extract(self, path: Path) -> ExtractedPdf:
        return ExtractedPdf(filename=path.name, text="PDF body", page_count=1)


def make_reference() -> RAGReference:
    return RAGReference(
        document_id="doc-1",
        chunk_id="chunk-1",
        chunk_index=0,
        content="Retrieved context",
        distance=0.1,
    )


def install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    references: tuple[RAGReference, ...] = (),
) -> FakeProvider:
    rag_service = FakeRAGService(references=references)
    provider = FakeProvider()
    monkeypatch.setattr(module, "provide_rag_service", lambda: rag_service)
    monkeypatch.setattr(module, "provide_llm_provider", lambda: provider)
    monkeypatch.setattr(module, "PdfFileExtractor", FakePdfExtractor)
    return provider


def make_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    return pdf_path


async def test_cli_approve_generates_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load_script()
    install_fakes(monkeypatch, script, references=(make_reference(),))
    pdf_path = make_pdf(tmp_path)
    output_path = tmp_path / "report.md"
    args = script._build_parser().parse_args(
        [
            str(pdf_path),
            "--owner-key-hash",
            "a" * 64,
            "--approve",
            "--output",
            str(output_path),
        ]
    )

    exit_code = await script.run_workflow(args)

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["status"] == "completed"
    assert summary["thread_id"]
    assert summary["report_path"] == str(output_path)
    assert output_path.exists()


async def test_cli_reject_loops_until_max_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load_script()
    provider = install_fakes(monkeypatch, script, references=(make_reference(),))
    pdf_path = make_pdf(tmp_path)
    output_path = tmp_path / "report.md"
    args = script._build_parser().parse_args(
        [
            str(pdf_path),
            "--owner-key-hash",
            "a" * 64,
            "--reject-with-feedback",
            "revise it",
            "--max-revisions",
            "2",
            "--output",
            str(output_path),
        ]
    )

    exit_code = await script.run_workflow(args)

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 2
    assert summary["status"] == "rejected"
    assert summary["revision_count"] == 2
    assert provider.chat_count == 2
    assert not output_path.exists()


async def test_cli_interactive_empty_input_pauses_and_keeps_thread_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load_script()
    install_fakes(monkeypatch, script)
    pdf_path = make_pdf(tmp_path)
    output_path = tmp_path / "report.md"
    args = script._build_parser().parse_args(
        [
            str(pdf_path),
            "--owner-key-hash",
            "a" * 64,
            "--output",
            str(output_path),
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    exit_code = await script.run_workflow(args)

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert summary["status"] == "pending_approval"
    assert summary["thread_id"]
    assert not output_path.exists()


def test_cli_invalid_decision_raises_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_script()
    args = script._build_parser().parse_args(
        ["sample.pdf", "--owner-key-hash", "a" * 64]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: '{"decision": "maybe"}')

    with pytest.raises(SystemExit, match="Decision must be"):
        script._resolve_decision(args)


def test_cli_non_object_decision_raises_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_script()
    args = script._build_parser().parse_args(
        ["sample.pdf", "--owner-key-hash", "a" * 64]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "[]")

    with pytest.raises(SystemExit, match="Decision must be a JSON object"):
        script._resolve_decision(args)
