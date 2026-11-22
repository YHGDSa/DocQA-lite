"""Citation extraction, validation and the fixed refusal text.

The model is asked to end every claim with a bare ``[n]``. Anything else --
no marker, a malformed marker like ``[1,2]``, or a marker pointing outside the
chunks that were actually shown -- is treated as an unsupported answer and
downgraded to the fixed refusal (see README, "SSE 与引用校验").
"""

from __future__ import annotations

import re

from app.config import REFUSAL_TEXT
from app.retrieval import SearchHit
from app.schemas import Citation

CITATION_RE = re.compile(r"\[(\d+)\]")


def extract_ids(answer: str) -> list[int]:
    """Every ``[n]`` in the answer, in order, duplicates kept."""
    return [int(match) for match in CITATION_RE.findall(answer or "")]


def validate_answer(answer: str, hits: list[SearchHit]) -> tuple[bool, list[int]]:
    """Check an answer against the chunks that were shown to the model.

    Returns ``(valid, ids)``:
    - empty answer -> ``(False, [])`` (nothing was claimed, nothing is citable);
    - the model refused by itself -> ``(True, [])``;
    - no ``[n]`` -> ``(False, [])`` (an uncited claim is not supported);
    - any ``n`` outside ``1..len(hits)`` -> ``(False, ids)``;
    - otherwise ``(True, sorted unique ids)``.
    """
    text = (answer or "").strip()
    if not text:
        return False, []
    if REFUSAL_TEXT in text:
        return True, []
    ids = extract_ids(text)
    if not ids:
        return False, []
    if any(index < 1 or index > len(hits) for index in ids):
        return False, ids
    return True, sorted(set(ids))


def build_citation(index: int, hit: SearchHit) -> Citation:
    """Project one hit into the citation shape returned by ``POST /ask``."""
    return Citation(
        index=index,
        chunk_id=hit.chunk_id,
        filename=hit.filename,
        line_start=hit.line_start,
        line_end=hit.line_end,
        quote=hit.text[:200],
    )
