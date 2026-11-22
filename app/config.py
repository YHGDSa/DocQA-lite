"""DocQA-lite configuration.

Paths, upload limits and the chunking knobs. No LLM setting is read here --
T8/T9 do not call a model, and no key is required, read or logged.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite file. Tests override this by passing db_path= to create_app().
DB_PATH = os.environ.get("DOCQA_DB_PATH", str(BASE_DIR / "data" / "docqa.db"))

# Upload cap for the 2-week version: small enough that one request cannot
# exhaust the box. The route reads at most MAX_FILE_BYTES + 1 bytes, so an
# oversized upload is rejected without buffering the whole file.
MAX_FILE_MB = int(os.environ.get("DOCQA_MAX_FILE_MB", "2"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

# Chunking: 400 characters with 50 characters of overlap. These are OUR
# numbers, not LightRAG's defaults (1200 tokens / 100 tokens overlap):
# docs/REFERENCE-B.md only borrows the sliding-window + chunk-metadata +
# citation idea. A 400/50 character window keeps a whole handbook section
# inside one FTS5 hit on a corpus of a few dozen documents.
CHUNK_CHAR_SIZE = int(os.environ.get("DOCQA_CHUNK_CHAR_SIZE", "400"))
CHUNK_CHAR_OVERLAP = int(os.environ.get("DOCQA_CHUNK_CHAR_OVERLAP", "50"))

# --- LLM (T10) -------------------------------------------------------------
# Read from the process environment only. DocQA-lite never loads a .env file,
# never prints these values, and never writes them anywhere; pytest never calls
# the model (app.llm.chat is monkeypatched in tests).
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()

# Refusal gate. Retrieval quality is measured by how much of the question the
# chunk actually covers (see retrieval._overlap), not by bm25's score, whose
# magnitude swings with corpus size and chunk length (see DECISIONS D24).
REFUSAL_OVERLAP_THRESHOLD = float(
    os.environ.get("DOCQA_REFUSAL_OVERLAP_THRESHOLD", "0.05")
)

#: One fixed sentence, so "no evidence" is testable and never model-flavoured.
REFUSAL_TEXT = "未在文档中找到依据。"
