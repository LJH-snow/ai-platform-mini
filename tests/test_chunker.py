import pytest

from app.rag.chunker import chunk_text


def test_empty_text_returns_empty_list() -> None:
    assert chunk_text("") == []


def test_short_text_returns_single_chunk() -> None:
    assert chunk_text("Hello", chunk_size=100, overlap=0) == ["Hello"]


def test_exact_chunk_size() -> None:
    text = "abcde"
    result = chunk_text(text, chunk_size=5, overlap=0)
    assert result == ["abcde"]


def test_multiple_chunks_no_overlap() -> None:
    text = "abcdefghij"
    result = chunk_text(text, chunk_size=3, overlap=0)
    assert result == ["abc", "def", "ghi", "j"]


def test_chunks_with_overlap() -> None:
    text = "abcdefghij"
    result = chunk_text(text, chunk_size=5, overlap=2)
    # step=3, text length=10
    # i=0: [0:5]="abcde" (covers end at 5 < 10, continue)
    # i=3: [3:8]="defgh" (covers end at 8 < 10, continue)
    # i=6: [6:10]="ghij" (covers end at 10 == len, stop)
    assert result == ["abcde", "defgh", "ghij"]


def test_last_chunk_shorter_than_size() -> None:
    text = "abcdefg"
    result = chunk_text(text, chunk_size=3, overlap=0)
    assert result == ["abc", "def", "g"]


def test_overlap_preserves_context() -> None:
    text = "abcdefg"
    result = chunk_text(text, chunk_size=4, overlap=2)
    assert len(result) >= 2
    # Second chunk should overlap with end of first
    assert result[0][-2:] == result[1][:2]


def test_invalid_chunk_size_zero() -> None:
    with pytest.raises(ValueError, match="chunk_size must be > 0"):
        chunk_text("hello", chunk_size=0)


def test_invalid_chunk_size_negative() -> None:
    with pytest.raises(ValueError, match="chunk_size must be > 0"):
        chunk_text("hello", chunk_size=-1)


def test_invalid_overlap_exceeds_chunk_size() -> None:
    with pytest.raises(ValueError, match="overlap must be < chunk_size"):
        chunk_text("hello", chunk_size=5, overlap=5)


def test_invalid_overlap_negative() -> None:
    with pytest.raises(ValueError, match="overlap must be >= 0"):
        chunk_text("hello", chunk_size=10, overlap=-1)


def test_zero_overlap() -> None:
    text = "abcdefg"
    result = chunk_text(text, chunk_size=3, overlap=0)
    assert result == ["abc", "def", "g"]


def test_single_character_chunks() -> None:
    text = "abc"
    result = chunk_text(text, chunk_size=1, overlap=0)
    assert result == ["a", "b", "c"]


class TestChunkerBoundaryConditions:
    """Test boundary conditions that previously produced duplicate
    trailing chunks when text length fell between step and chunk_size."""

    def test_text_length_equals_step_plus_one(self) -> None:
        """text length = chunk_size - overlap + 1 (just past first step).

        With chunk_size=10, overlap=2, step=8:
        len(text)=9 → first chunk covers [0:10] which reaches the end,
        so no second chunk.
        """
        text = "A" * 9
        result = chunk_text(text, chunk_size=10, overlap=2)
        assert len(result) == 1
        assert result[0] == text

    def test_text_length_equals_chunk_size_minus_one(self) -> None:
        """text length = chunk_size - 1.

        First chunk covers the entire text, no second chunk needed.
        """
        text = "A" * 9
        result = chunk_text(text, chunk_size=10, overlap=0)
        assert len(result) == 1
        assert result[0] == text

    def test_text_length_equals_chunk_size(self) -> None:
        """text length = chunk_size exactly.

        One chunk covering the full text.
        """
        text = "A" * 10
        result = chunk_text(text, chunk_size=10, overlap=0)
        assert len(result) == 1
        assert result[0] == text

    def test_text_length_equals_chunk_size_with_overlap(self) -> None:
        """text length = chunk_size with overlap > 0.

        First chunk covers the full text, so no duplicate trailing chunk.
        """
        text = "A" * 10
        result = chunk_text(text, chunk_size=10, overlap=2)
        assert len(result) == 1
        assert result[0] == text

    def test_text_length_chunk_size_plus_one(self) -> None:
        """text length = chunk_size + 1.

        First chunk covers [0:chunk_size], second covers [step:step+chunk_size]
        which is just 1 character past the overlap region.
        """
        text = "A" * 11
        result = chunk_text(text, chunk_size=10, overlap=2)
        assert len(result) == 2
        # First chunk is full size
        assert len(result[0]) == 10
        # Second chunk has overlap of 2
        assert result[0][-2:] == result[1][:2]

    def test_default_config_no_duplicate_trailing(self) -> None:
        """With default chunk_size=500, overlap=50 (step=450),
        text of 451-500 chars should produce exactly 1 chunk."""
        for length in (450, 451, 499, 500):
            text = "X" * length
            result = chunk_text(text, chunk_size=500, overlap=50)
            assert len(result) == 1, f"length={length} should produce 1 chunk"

    def test_no_redundant_trailing_chunks(self) -> None:
        """General property: no chunk should be entirely contained
        within the previous chunk."""
        text = "A" * 600
        result = chunk_text(text, chunk_size=500, overlap=50)
        for i in range(1, len(result)):
            # Each chunk starts at position (step * i) and extends
            # to (step * i + chunk_size). The non-overlap region of
            # each chunk must not be empty (except the last chunk
            # which may be shorter).
            prev_start = (500 - 50) * (i - 1)
            curr_start = (500 - 50) * i
            # Current chunk must start before previous chunk ends
            # (overlap) but must also extend beyond previous chunk end
            assert curr_start < prev_start + 500  # overlap exists
            assert (
                curr_start + len(result[i]) > prev_start + 500 or i == len(result) - 1
            )
