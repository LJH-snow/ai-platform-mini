def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into fixed-size character chunks with overlap.

    Args:
        text: Input text to split.
        chunk_size: Maximum characters per chunk. Must be > 0.
        overlap: Number of overlapping characters between consecutive chunks.
            Must be >= 0 and < chunk_size.

    Returns:
        List of text chunks. Empty list for empty input.

    Raises:
        ValueError: If chunk_size <= 0 or overlap >= chunk_size.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be > 0, got {chunk_size}")
    if overlap < 0:
        raise ValueError(f"overlap must be >= 0, got {overlap}")
    if overlap >= chunk_size:
        raise ValueError(f"overlap must be < chunk_size, got {overlap} >= {chunk_size}")
    if not text:
        return []

    step = chunk_size - overlap
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunk = text[i : i + chunk_size]
        chunks.append(chunk)
        # If this chunk already reaches or passes the end of text,
        # no need to advance — the next window would be entirely
        # contained within this one (pure overlap duplication).
        if i + chunk_size >= len(text):
            break
        i += step
    return chunks
