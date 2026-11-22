"""DocQA-lite configuration.

T8 keeps this file deliberately small: paths, upload limits and the chunking
knobs T9 will use. No LLM setting is read here -- T8 does not call a model,
and no key is required, logged or copied.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SQLite file. Tests override this by passing db_path= to create_app().
DB_PATH = os.environ.get("DOCQA_DB_PATH", str(BASE_DIR / "data" / "docqa.db"))

# Upload limits (enforced from T9 on).
MAX_FILE_MB = int(os.environ.get("DOCQA_MAX_FILE_MB", "10"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

# Chunking knobs: char-based windows with overlap, shaped after the reference
# card in docs/REFERENCE-B.md. The chunker itself lands in T9.
CHUNK_CHAR_SIZE = int(os.environ.get("DOCQA_CHUNK_CHAR_SIZE", "800"))
CHUNK_CHAR_OVERLAP = int(os.environ.get("DOCQA_CHUNK_CHAR_OVERLAP", "120"))
