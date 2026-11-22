"""FastAPI application factory for DocQA-lite.

The app owns one SQLite file: the schema is created on startup by the lifespan
handler, and every route lives in :mod:`app.routes`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config
from app.db import init_db
from app.routes import router
from app.schemas import HealthResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(app.state.db_path)
    yield


def create_app(db_path: str | None = None) -> FastAPI:
    """Build an app bound to one database file.

    ``db_path`` is explicit so tests can point at a per-test tmp_path database;
    production uses ``config.DB_PATH``.
    """
    app = FastAPI(title="DocQA-lite", version="0.2.0", lifespan=lifespan)
    app.state.db_path = str(db_path if db_path is not None else config.DB_PATH)
    app.include_router(router)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True)

    return app


app = create_app()
