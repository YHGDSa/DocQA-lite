"""Character-window chunking that keeps source offsets.

Offsets are Python string indices, never bytes, so ``text[char_start:char_end]``
always slices the original document back out -- that is what makes a citation
verifiable in T10. Line numbers are 1-based and inclusive on both ends.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    seq: int
    text: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def _line_starts(text: str) -> list[int]:
    """Index of the first character of every line (line 1 starts at 0)."""
    return [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[Chunk]:
    """Sliding character windows over ``text``.

    ``char_end`` is exclusive. The window is never stripped, because stripping
    would move the offsets away from the characters they describe; the only
    normalisation is skipping a text that has no non-whitespace character at
    all (an empty document produces no chunks rather than one empty chunk).
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")
    if overlap < 0:
        raise ValueError(f"overlap must be non-negative, got {overlap}")
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be < size ({size})")
    if not text or not text.strip():
        return []

    starts = _line_starts(text)
    total = len(text)
    chunks: list[Chunk] = []
    start = 0
    while start < total:
        end = min(start + size, total)
        # bisect_right gives "how many line starts are <= position", which is
        # exactly the 1-based number of the line containing that position.
        line_start = bisect_right(starts, start)
        line_end = bisect_right(starts, max(start, end - 1))
        chunks.append(
            Chunk(
                seq=len(chunks),
                text=text[start:end],
                char_start=start,
                char_end=end,
                line_start=line_start,
                line_end=line_end,
            )
        )
        if end == total:
            break
        start = end - overlap
    return chunks
