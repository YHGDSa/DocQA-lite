"""T9: chunk search, source offsets and the short-query fallback."""

FILLER = "无关段落。" * 90  # 450 characters -> the keyword lands in chunk 2
KEYWORD = "年假 10 天"
SOURCE = FILLER + "\n" + KEYWORD + "，需提前申请。\n" + "结尾段落。"


def _upload(client, name: str, content: str):
    return client.post(
        "/documents/upload",
        files={"file": (name, content.encode("utf-8"), "text/markdown")},
    )


def test_search_returns_hits_with_offsets(client):
    assert _upload(client, "handbook.md", SOURCE).status_code == 201

    r = client.post("/search", json={"query": KEYWORD, "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == KEYWORD
    assert len(body["hits"]) >= 1

    hit = body["hits"][0]
    # The offsets must slice the exact chunk text out of the original file.
    assert hit["text"] == SOURCE[hit["char_start"] : hit["char_end"]]
    assert KEYWORD in hit["text"]
    keyword_line = SOURCE[: SOURCE.index(KEYWORD)].count("\n") + 1
    assert hit["line_start"] <= keyword_line <= hit["line_end"]
    assert hit["score"] > 0
    assert hit["overlap"] > 0


def test_search_short_query_falls_back_to_like(client):
    text = "员工手册\n年假 10 天，需提前申请。"
    assert _upload(client, "handbook.md", text).status_code == 201

    # Two characters: the trigram index has nothing to look up, LIKE answers.
    r = client.post("/search", json={"query": "年假"})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) == 1
    hit = hits[0]
    assert "年假" in hit["text"]
    assert hit["text"] == text[hit["char_start"] : hit["char_end"]]
    assert hit["score"] == 1.0
    assert 0 < hit["overlap"] <= 1

    blank = client.post("/search", json={"query": ""})
    assert blank.status_code == 422

    # LIKE metacharacters must match themselves, not act as wildcards. Both new
    # documents contain a "0", but only one contains the literal "0%".
    assert _upload(client, "pricing.md", "折扣 10% 起，需审批。").status_code == 201
    assert _upload(client, "orders.md", "订单号 2024-001。").status_code == 201

    percent = client.post("/search", json={"query": "0%"})
    assert percent.status_code == 200
    percent_hits = percent.json()["hits"]
    assert len(percent_hits) == 1
    assert percent_hits[0]["filename"] == "pricing.md"
    assert "10%" in percent_hits[0]["text"]

    # Three characters go through FTS5, which is literal by construction; the
    # assertion keeps the two paths honest about the same string.
    literal = client.post("/search", json={"query": "10%"})
    assert literal.status_code == 200
    literal_hits = literal.json()["hits"]
    assert len(literal_hits) == 1
    assert literal_hits[0]["filename"] == "pricing.md"
