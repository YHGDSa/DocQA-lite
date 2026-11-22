"""T10: ask with citations, refusal without evidence, out-of-range citation.

Every test monkeypatches ``app.llm.chat``: pytest never touches the network and
never needs an API key.
"""

import sqlite3

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


def test_citation_out_of_range_refuses(client, monkeypatch):
    assert _upload(client, "handbook.md", "员工年假 10 天可结转。").status_code == 201

    async def fake_chat(messages, temperature=0.0):
        return "年假 10 天 [9]"

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
