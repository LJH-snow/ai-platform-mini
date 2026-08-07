"""Tokenization tests: Chinese, error codes, mixed text, stdlib fallback."""

from __future__ import annotations

from pytest import MonkeyPatch

from app.rag.tokenize import tokenize_keywords


def test_chinese_bigram_tokenization() -> None:
    tokens = tokenize_keywords("报销政策是什么")
    assert "报销" in tokens
    assert "政策" in tokens


def test_error_code_stays_single_token() -> None:
    tokens = tokenize_keywords("查询 E10023 错误码的解决方案")
    assert "E10023" in tokens
    assert not any(token.startswith("E100") for token in tokens if token != "E10023")


def test_mixed_chinese_english_and_codes() -> None:
    tokens = tokenize_keywords("混合 test E10023 和中文")
    assert "E10023" in tokens
    assert "test" in tokens
    assert any("中文" in token or "中" in token for token in tokens)


def test_serial_codes_in_english_text() -> None:
    tokens = tokenize_keywords("Error E10024 occurred in the report")
    assert "E10024" in tokens
    assert "occurred" in tokens


def test_empty_text_yields_no_tokens() -> None:
    assert tokenize_keywords("") == []
    assert tokenize_keywords("   ") == []


def test_stdlib_fallback_tokenizes_cjk_unigrams(monkeypatch: MonkeyPatch) -> None:
    import app.rag.tokenize as tokenize_module

    monkeypatch.setattr(tokenize_module, "_JIEBA_AVAILABLE", False)
    monkeypatch.setattr(tokenize_module, "_jieba", None)

    tokens = tokenize_keywords("报销政策 E10023 解决方案")
    assert tokens == ["报", "销", "政", "策", "E10023", "解", "决", "方", "案"]


def test_stdlib_fallback_keeps_ascii_runs(monkeypatch: MonkeyPatch) -> None:
    import app.rag.tokenize as tokenize_module

    monkeypatch.setattr(tokenize_module, "_JIEBA_AVAILABLE", False)
    monkeypatch.setattr(tokenize_module, "_jieba", None)

    assert tokenize_keywords("2026 Q1 report") == ["2026", "Q1", "report"]
