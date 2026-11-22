"""T8 health + T9 upload, dedupe and listing."""

from app.chunking import chunk_text
from app.config import CHUNK_CHAR_OVERLAP, CHUNK_CHAR_SIZE, MAX_FILE_BYTES

HANDBOOK = "第一行\n第二行 年假 10 天\n第三行"


def _upload(client, name: str = "handbook.md", content: str | bytes = HANDBOOK):
    data = content.encode("utf-8") if isinstance(content, str) else content
    return client.post(
        "/documents/upload",
        files={"file": (name, data, "text/markdown")},
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_upload_md_creates_chunks(client):
    r = _upload(client)
    assert r.status_code == 201
    body = r.json()
    assert body["duplicated"] is False
    assert body["filename"] == "handbook.md"
    assert body["size"] == len(HANDBOOK.encode("utf-8"))
    assert body["chunks"] == len(
        chunk_text(HANDBOOK, CHUNK_CHAR_SIZE, CHUNK_CHAR_OVERLAP)
    )
    assert body["chunks"] > 0

    listed = client.get("/documents")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == body["id"]
    assert payload["items"][0]["filename"] == "handbook.md"
    assert payload["items"][0]["chunks"] == body["chunks"]


def test_upload_duplicate_returns_same_doc(client):
    first = _upload(client)
    assert first.status_code == 201
    first_body = first.json()

    second = _upload(client)
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["duplicated"] is True
    assert second_body["id"] == first_body["id"]
    assert second_body["chunks"] == first_body["chunks"]

    # No second row, no second set of chunks.
    assert client.get("/documents").json()["total"] == 1


def test_upload_rejects_unsupported_ext(client):
    r = _upload(client, name="handbook.pdf", content="年假 10 天")
    assert r.status_code == 400
    assert client.get("/documents").json()["total"] == 0


def test_upload_rejects_oversize(client):
    r = _upload(client, name="big.txt", content=b"a" * (MAX_FILE_BYTES + 1))
    assert r.status_code == 413
    assert client.get("/documents").json()["total"] == 0


def test_list_documents(client):
    short_text = "只有一段。"
    long_text = "段" * 900  # 400/50 character windows -> exactly 3 chunks

    assert _upload(client, "short.md", short_text).status_code == 201
    assert _upload(client, "long.txt", long_text).status_code == 201

    payload = client.get("/documents").json()
    assert payload["total"] == 2
    # id DESC: the long document was uploaded second.
    assert [item["filename"] for item in payload["items"]] == ["long.txt", "short.md"]
    assert [item["chunks"] for item in payload["items"]] == [3, 1]
    assert [item["size"] for item in payload["items"]] == [
        len(long_text.encode("utf-8")),
        len(short_text.encode("utf-8")),
    ]
