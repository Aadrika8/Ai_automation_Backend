"""Regression: an unhandled 500 must still carry CORS headers, or the browser
turns it into an opaque 'Failed to fetch' the frontend can't read."""
from app import repositories


async def test_unhandled_500_has_cors_headers(client, manager_headers, monkeypatch):
    async def boom():
        raise RuntimeError("simulated backend failure")

    monkeypatch.setattr(repositories, "list_apps", boom)
    res = await client.get(
        "/api/apps",
        headers={**manager_headers, "Origin": "http://localhost:5173"},
    )
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error"}
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
