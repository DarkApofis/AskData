import httpx


async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_healthz_rejects_post(client: httpx.AsyncClient) -> None:
    response = await client.post("/healthz")

    assert response.status_code == 405


async def test_unknown_path_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
