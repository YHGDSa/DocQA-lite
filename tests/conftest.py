"""Per-test database and TestClient.

Each test gets its own SQLite file under pytest's tmp_path, so tests never
share state and never touch the real data/docqa.db. Nothing here opens a
network connection.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as test_client:
        yield test_client
