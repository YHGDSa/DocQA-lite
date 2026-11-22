"""Request and response models. T10 adds the QA surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = True


class UploadResponse(BaseModel):
    id: int
    filename: str
    size: int
    chunks: int
    duplicated: bool


class DocumentInfo(BaseModel):
    id: int
    filename: str
    size: int
    chunks: int
    created_at: str


class DocumentList(BaseModel):
    items: list[DocumentInfo]
    total: int


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)


class SearchHitModel(BaseModel):
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


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitModel]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class Citation(BaseModel):
    index: int
    chunk_id: int
    filename: str
    line_start: int
    line_end: int
    quote: str


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    refused: bool
    latency_ms: int
