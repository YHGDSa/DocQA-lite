"""Ask with citations, refusal without evidence, out-of-range citation and SSE.

Every test monkeypatches ``app.llm.chat`` / ``app.llm.stream_chat``: pytest
never touches the network and never needs an API key.
"""

import json
import sqlite3

import pytest

from app import config

HANDBOOK = "员工手册\n员工年假 10 天可结转，需提前申请。\n"


def _upload(client, name: str, content: str):
    return client.post(
        "/documents/upload",
        files={"file": (name, content.encode("utf-8"), "text/markdown")},
    )


def _qa_rows(client):
    conn = sqlite3.connect(client.app.state.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM qa_logs ORDER BY id").fetchall()
    finally:
        conn.close()


def _sse_events(response):
    """Parse an SSE body into ``[(event, payload), ...]``."""
    events = []
    name = None
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return events


def _configure_llm(monkeypatch):
    """Pretend a model is configured; the chat call itself is still mocked."""
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "not-a-real-key")
    monkeypatch.setattr(config, "LLM_MODEL", "test-model")


def test_ask_with_evidence_has_citations(client, monkeypatch):
    assert _upload(client, "handbook.md", HANDBOOK).status_code == 201

    seen = {}

    async def fake_chat(messages, temperature=0.0):
        seen["messages"] = messages
        return "员工年假 10 天可结转 [1]"

    monkeypatch.setattr("app.llm.chat", fake_chat)

    r = client.post("/ask", json={"question": "年假多少天"})
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is False
    assert "10 天" in body["answer"]
    assert len(body["citations"]) == 1

    citation = body["citations"][0]
    assert citation["index"] == 1
    assert citation["filename"] == "handbook.md"
    assert citation["line_start"] > 0
    assert citation["line_end"] >= citation["line_start"]
    assert citation["quote"]

    # The citation points at a chunk that really exists in this database, and
    # its quote is a prefix of that chunk's text.
    conn = sqlite3.connect(client.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT text FROM chunks WHERE id = ?", (citation["chunk_id"],)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0].startswith(citation["quote"])

    # The model was shown that chunk with its source line.
    user_message = seen["messages"][1]["content"]
    assert "[1] (handbook.md 第" in user_message
    assert "年假多少天" in user_message

    rows = _qa_rows(client)
    assert len(rows) == 1
    assert rows[0]["refused"] == 0
    assert rows[0]["question"] == "年假多少天"
    assert "10 天" in rows[0]["answer"]
    assert '"chunk_id"' in rows[0]["citations_json"]


def test_ask_without_evidence_refuses(client, monkeypatch):
    assert _upload(client, "handbook.md", "员工年假 10 天。").status_code == 201

    async def boom(messages, temperature=0.0):
        raise AssertionError("LLM must not be called when there is no evidence")

    monkeypatch.setattr("app.llm.chat", boom)

    r = client.post("/ask", json={"question": "公司有没有股票期权"})
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["answer"] == config.REFUSAL_TEXT
    assert body["citations"] == []

    # Passing the test proves the mock never ran: the refusal came before any
    # model call, so this works with no API key at all.
    rows = _qa_rows(client)
    assert len(rows) == 1
    assert rows[0]["refused"] == 1
    assert rows[0]["citations_json"] == "[]"


@pytest.mark.parametrize("model_answer", [
    "年假 10 天 [9]",
    "年假 10 天 [1]，可以无限累积 [1,2]。",
    f"年假 100 天 [1]。{config.REFUSAL_TEXT}",
])
def test_invalid_citations_or_mixed_refusal_refuse(client, monkeypatch, model_answer):
    assert _upload(client, "handbook.md", "员工年假 10 天可结转。").status_code == 201

    async def fake_chat(messages, temperature=0.0):
        return model_answer

    monkeypatch.setattr("app.llm.chat", fake_chat)

    r = client.post("/ask", json={"question": "年假多少天"})
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["answer"] == config.REFUSAL_TEXT
    assert body["citations"] == []

    rows = _qa_rows(client)
    assert len(rows) == 1
    assert rows[0]["refused"] == 1


def test_ask_stream_events(client, monkeypatch):
    assert _upload(client, "handbook.md", HANDBOOK).status_code == 201
    _configure_llm(monkeypatch)

    async def fake_stream(messages, temperature=0.0):
        for piece in ("员工", "年假", " [1]"):
            yield piece

    monkeypatch.setattr("app.llm.stream_chat", fake_stream)

    with client.stream(
        "POST", "/ask/stream", json={"question": "年假多少天"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        events = _sse_events(response)

    assert events, "no SSE frames received"
    assert events[0][0] == "citations"
    preview = events[0][1]["citations"]
    assert len(preview) >= 1
    assert preview[0]["index"] == 1
    assert preview[0]["filename"] == "handbook.md"
    assert preview[0]["quote"]

    tokens = [payload["text"] for name, payload in events if name == "token"]
    assert len(tokens) >= 2
    assert "".join(tokens) == "员工年假 [1]"
    assert events[-1][0] == "done"
    assert events[-1][1]["refused"] is False


def test_ask_stream_refuses_without_evidence(client, monkeypatch):
    assert _upload(client, "handbook.md", "员工年假 10 天。").status_code == 201

    async def boom(messages, temperature=0.0):
        raise AssertionError("stream_chat must not be called without evidence")
        yield ""  # pragma: no cover

    monkeypatch.setattr("app.llm.stream_chat", boom)

    with client.stream(
        "POST", "/ask/stream", json={"question": "公司有没有股票期权"}
    ) as response:
        assert response.status_code == 200
        events = _sse_events(response)

    assert events[0][0] == "citations"
    assert events[0][1]["citations"] == []
    assert events[1][0] == "token"
    assert events[1][1]["text"] == config.REFUSAL_TEXT
    assert events[-1][0] == "done"
    assert events[-1][1]["refused"] is True
