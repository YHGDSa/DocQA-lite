"""SQLite connection helpers and the schema every phase shares.

The column set is frozen: T9 (upload/chunk/search) and T10 (QA/citations) read
these tables as-is, so a later phase adds tables or migration code instead of
editing the columns below.

One deviation from the originally drafted DDL is documented inline: SQLite
refuses ``CREATE INDEX`` on a virtual table, so the FTS5 index cannot be
created. FTS5 keeps its own index internally, so nothing is lost.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  filename   TEXT NOT NULL,
  sha256     TEXT NOT NULL UNIQUE,
  size       INTEGER NOT NULL,
  deleted_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  text       TEXT NOT NULL,
  char_start INTEGER NOT NULL,
  char_end   INTEGER NOT NULL,
  line_start INTEGER NOT NULL,
  line_end   INTEGER NOT NULL,
  UNIQUE (doc_id, seq)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='id',
  tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS qa_logs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  question       TEXT NOT NULL,
  answer         TEXT NOT NULL,
  citations_json TEXT NOT NULL,
  refused        INTEGER NOT NULL DEFAULT 0,
  latency_ms     INTEGER,
  created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, seq);

-- Not created: "CREATE INDEX idx_chunks_fts_rowid ON chunks_fts(rowid)".
-- SQLite rejects it with "virtual tables may not be indexed"; an FTS5 table
-- already maintains its own index, so no lookup path is missing.
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open one connection with the pragmas the schema relies on.

    ``check_same_thread=False`` because FastAPI may run a sync dependency or
    endpoint in a worker thread; each request still gets its own connection.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """Create the schema, creating the parent directory if needed.

    Idempotent: every statement is ``IF NOT EXISTS``, so calling it on an
    existing database is a no-op.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
