"""Chunk retrieval: FTS5 trigram + bm25, with a LIKE fallback for short queries.

FTS5's trigram tokenizer cannot match a phrase shorter than three characters
(there is no trigram to look up), so one- and two-character queries take the
LIKE path instead of silently returning nothing.

``search_chunks`` is for literal lookups (``POST /search``). ``search_for_question``
is what ``POST /ask`` uses: a natural question is not a substring of the
handbook, so after the exact-phrase attempt it falls back to matching the
question's character bigrams.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: Below this length the trigram index has nothing to look up.
TRIGRAM_MIN_CHARS = 3

#: Upper bound on the number of bigram terms one question may contribute, so a
#: 500-character question cannot build a thousand-term SQL statement.
MAX_QUESTION_TERMS = 24

#: Hard cap on the candidate rows a question scan may pull back before ranking.
MAX_QUESTION_CANDIDATES = 200

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
WHERE c.text LIKE ? ESCAPE '\\'
  AND d.deleted_at IS NULL
ORDER BY c.id
LIMIT ?
"""

_SELECT_COLUMNS = """
       c.id AS chunk_id,
       c.doc_id AS doc_id,
       d.filename AS filename,
       c.text AS text,
       c.char_start AS char_start,
       c.char_end AS char_end,
       c.line_start AS line_start,
       c.line_end AS line_end
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


def _like_pattern(value: str) -> str:
    """Escape LIKE metacharacters so ``%`` and ``_`` match themselves.

    Backslash first: escaping it after the others would double the escapes
    this function just added.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _overlap(query: str, text: str) -> float:
    """Fraction of the query's character bigrams that occur in the chunk.

    This is coverage, not Jaccard: dividing by the union makes a chunk score
    lower merely for being long -- a two-character query against a 24-character
    chunk scores 0.043, which is below the refusal threshold and says "not
    relevant" about a chunk that clearly is. Coverage answers the question the
    refusal gate actually asks: how much of what was asked is present here.
    """
    q = query.strip()
    if not q:
        return 0.0
    if len(q) == 1:
        return 1.0 if q.lower() in text.lower() else 0.0
    q_grams = _bigrams(q.lower())
    if not q_grams:
        return 0.0
    return len(q_grams & _bigrams(text.lower())) / len(q_grams)


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
        rows = conn.execute(_SHORT_SQL, (f"%{_like_pattern(q)}%", limit)).fetchall()
        return [_hit(row, q, 1.0) for row in rows]

    # Quoted phrase: FTS5 treats bare punctuation and operators (AND, OR, NEAR,
    # "-") as syntax, and a handbook question may legitimately contain them.
    # Doubling an embedded quote is FTS5's escape.
    fts_query = '"' + q.replace('"', '""') + '"'
    rows = conn.execute(_FTS_SQL, (fts_query, limit)).fetchall()
    # bm25() returns a negative number where more negative is better, so the
    # public score is its negation: bigger is better.
    return [_hit(row, q, -float(row["rank"])) for row in rows]


def _question_terms(question: str) -> list[str]:
    """Distinct two-character windows of a question, punctuation dropped."""
    q = question.strip()
    terms: list[str] = []
    seen: set[str] = set()
    for index in range(len(q) - 1):
        term = q[index : index + 2]
        if not all(ch.isalnum() for ch in term):
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= MAX_QUESTION_TERMS:
            break
    return terms


def search_for_question(
    conn: sqlite3.Connection, question: str, top_k: int = 5
) -> list[SearchHit]:
    """Retrieve evidence for a natural-language question.

    A question such as "年假多少天" is not a substring of "员工年假 10 天可结转",
    so the exact-phrase search above finds nothing. The fallback matches the
    question's character bigrams with LIKE and ranks chunks by how many
    distinct bigrams they contain. The corpus is a few dozen documents, so the
    scan is cheap; precision is not this function's job -- the model answer and
    the citation check in the ask path are.
    """
    hits = search_chunks(conn, question, top_k)
    if hits:
        return hits

    terms = _question_terms(question)
    if not terms:
        return []

    # One statement with a fixed number of placeholders; the terms are bound,
    # never interpolated.
    where = " OR ".join(["c.text LIKE ? ESCAPE '\\'"] * len(terms))
    sql = (
        f"SELECT {_SELECT_COLUMNS}"
        " FROM chunks c JOIN documents d ON d.id = c.doc_id"
        f" WHERE ({where}) AND d.deleted_at IS NULL"
        " LIMIT ?"
    )
    params = [f"%{_like_pattern(term)}%" for term in terms]
    params.append(MAX_QUESTION_CANDIDATES)
    rows = conn.execute(sql, params).fetchall()

    lowered = [(row, row["text"].lower()) for row in rows]
    ranked = sorted(
        (
            (sum(1 for term in terms if term.lower() in text), row)
            for row, text in lowered
        ),
        key=lambda pair: (-pair[0], pair[1]["chunk_id"]),
    )
    return [
        _hit(row, question, float(matches))
        for matches, row in ranked[: max(1, int(top_k))]
    ]
