from collections.abc import AsyncIterator

import httpx
import pytest

from askdata_api.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
