"""HTTP surface for T9: upload, list, search.

QA, citations and refusal are T10; there is deliberately no /ask yet.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile

from app import config
from app.chunking import chunk_text
from app.db import connect
from app.retrieval import search_chunks
from app.schemas import (
    DocumentInfo,
    DocumentList,
    SearchHitModel,
    SearchRequest,
    SearchResponse,
    UploadResponse,
)

router = APIRouter()

#: The 2-week version reads plain text only; PDF/DOCX parsing is out of scope.
ALLOWED_SUFFIXES = {".md", ".txt"}


def get_db(request: Request):
    """One SQLite connection per request, closed when the request ends."""
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_chunks(conn: sqlite3.Connection, doc_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE doc_id = ?", (doc_id,)
    ).fetchone()
    return int(row["n"])


@router.post("/documents/upload", response_model=UploadResponse)
def upload_document(
    response: Response,
    file: UploadFile = File(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> UploadResponse:
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="file must have a name")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type {suffix or '(none)'}: only .md and .txt are accepted",
        )

    # Read one byte past the cap: enough to detect an oversized upload without
    # buffering the whole file into memory first.
    data = file.file.read(config.MAX_FILE_BYTES + 1)
    if len(data) > config.MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413, detail=f"file exceeds {config.MAX_FILE_MB} MB"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"file is not valid UTF-8 (byte {exc.start})"
        ) from exc

    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "SELECT id, filename, size FROM documents"
        " WHERE sha256 = ? AND deleted_at IS NULL",
        (digest,),
    ).fetchone()
    if existing is not None:
        # Same bytes, same document: report it instead of indexing it twice.
        response.status_code = 200
        doc_id = int(existing["id"])
        return UploadResponse(
            id=doc_id,
            filename=existing["filename"],
            size=int(existing["size"]),
            chunks=_count_chunks(conn, doc_id),
            duplicated=True,
        )

    chunks = chunk_text(text, config.CHUNK_CHAR_SIZE, config.CHUNK_CHAR_OVERLAP)
    with conn:
        cursor = conn.execute(
            "INSERT INTO documents (filename, sha256, size, deleted_at, created_at)"
            " VALUES (?, ?, ?, NULL, ?)",
            (filename, digest, len(data), _utcnow()),
        )
        doc_id = int(cursor.lastrowid)
        # The chunks_ai trigger keeps chunks_fts in sync inside this transaction.
        conn.executemany(
            "INSERT INTO chunks"
            " (doc_id, seq, text, char_start, char_end, line_start, line_end)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (doc_id, c.seq, c.text, c.char_start, c.char_end, c.line_start, c.line_end)
                for c in chunks
            ],
        )

    response.status_code = 201
    return UploadResponse(
        id=doc_id,
        filename=filename,
        size=len(data),
        chunks=len(chunks),
        duplicated=False,
    )


@router.get("/documents", response_model=DocumentList)
def list_documents(conn: sqlite3.Connection = Depends(get_db)) -> DocumentList:
    rows = conn.execute(
        "SELECT d.id AS id, d.filename AS filename, d.size AS size,"
        " d.created_at AS created_at,"
        " (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id) AS chunks"
        " FROM documents d"
        " WHERE d.deleted_at IS NULL"
        " ORDER BY d.id DESC"
    ).fetchall()
    items = [
        DocumentInfo(
            id=int(row["id"]),
            filename=row["filename"],
            size=int(row["size"]),
            chunks=int(row["chunks"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]
    return DocumentList(items=items, total=len(items))


@router.post("/search", response_model=SearchResponse)
def search(
    payload: SearchRequest, conn: sqlite3.Connection = Depends(get_db)
) -> SearchResponse:
    query = payload.query.strip()
    if not query:
        # Passed min_length, but was only whitespace.
        raise HTTPException(status_code=422, detail="query must not be blank")
    hits = search_chunks(conn, query, payload.top_k)
    return SearchResponse(
        query=query,
        hits=[
            SearchHitModel(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                filename=hit.filename,
                text=hit.text,
                score=hit.score,
                overlap=hit.overlap,
                char_start=hit.char_start,
                char_end=hit.char_end,
                line_start=hit.line_start,
                line_end=hit.line_end,
            )
            for hit in hits
        ],
    )
