"""Chunk retrieval: FTS5 trigram + bm25, with a LIKE fallback for short queries.

FTS5's trigram tokenizer cannot match a phrase shorter than three characters
(there is no trigram to look up), so one- and two-character queries take the
LIKE path instead of silently returning nothing.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: Below this length the trigram index has nothing to look up.
TRIGRAM_MIN_CHARS = 3

_FTS_SQL = """
SELECT c.id AS chunk_id,
       c.doc_id AS doc_id,
       d.filename AS filename,
       c.text AS text,
       c.char_start AS char_start,
       c.char_end AS char_end,
       c.line_start AS line_start,
       c.line_end AS line_end,
       bm25(chunks_fts) AS rank
FROM chunks_fts
JOIN chunks c ON c.id = chunks_fts.rowid
JOIN documents d ON d.id = c.doc_id
WHERE chunks_fts MATCH ?
  AND d.deleted_at IS NULL
ORDER BY rank ASC
LIMIT ?
"""

_SHORT_SQL = """
SELECT c.id AS chunk_id,
       c.doc_id AS doc_id,
       d.filename AS filename,
       c.text AS text,
       c.char_start AS char_start,
       c.char_end AS char_end,
       c.line_start AS line_start,
       c.line_end AS line_end
FROM chunks c
JOIN documents d ON d.id = c.doc_id
WHERE c.text LIKE ?
  AND d.deleted_at IS NULL
ORDER BY c.id
LIMIT ?
"""


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    doc_id: int
    filename: str
    text: str
    score: float
    overlap: float
    char_start: int
    char_end: int
    line_start: int
    line_end: int


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _overlap(query: str, text: str) -> float:
    """Character-bigram Jaccard overlap between the query and one chunk.

    bm25 ranks a whole chunk; this tells the caller how much of the *query*
    actually appears in it. A single-character query has no bigram, so it
    degrades to a containment check.
    """
    q = query.strip()
    if not q:
        return 0.0
    if len(q) == 1:
        return 1.0 if q.lower() in text.lower() else 0.0
    q_grams = _bigrams(q.lower())
    text_grams = _bigrams(text.lower())
    if not q_grams or not text_grams:
        return 0.0
    return len(q_grams & text_grams) / len(q_grams | text_grams)


def _hit(row: sqlite3.Row, query: str, score: float) -> SearchHit:
    text = row["text"]
    return SearchHit(
        chunk_id=int(row["chunk_id"]),
        doc_id=int(row["doc_id"]),
        filename=row["filename"],
        text=text,
        score=score,
        overlap=_overlap(query, text),
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        line_start=int(row["line_start"]),
        line_end=int(row["line_end"]),
    )


def search_chunks(
    conn: sqlite3.Connection, query: str, top_k: int = 5
) -> list[SearchHit]:
    """Return the best ``top_k`` chunks for ``query``.

    Blank queries return an empty list; the route is responsible for turning
    that into a 422. Every statement is parameterised -- no SQL is built from
    user input.
    """
    q = query.strip()
    if not q:
        return []
    limit = max(1, int(top_k))

    if len(q) < TRIGRAM_MIN_CHARS:
        rows = conn.execute(_SHORT_SQL, (f"%{q}%", limit)).fetchall()
        return [_hit(row, q, 1.0) for row in rows]

    # Quoted phrase: FTS5 treats bare punctuation and operators (AND, OR, NEAR,
    # "-") as syntax, and a handbook question may legitimately contain them.
    # Doubling an embedded quote is FTS5's escape.
    fts_query = '"' + q.replace('"', '""') + '"'
    rows = conn.execute(_FTS_SQL, (fts_query, limit)).fetchall()
    # bm25() returns a negative number where more negative is better, so the
    # public score is its negation: bigger is better.
    return [_hit(row, q, -float(row["rank"])) for row in rows]
