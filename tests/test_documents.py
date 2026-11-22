"""T8: the app boots, the schema is created, and /health answers."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
