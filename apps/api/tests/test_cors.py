import httpx

ALLOWED = "https://askdata.example"
DISALLOWED = "https://evil.example"


def _cors_headers(response: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in response.headers.items() if k.lower().startswith("access-control-")}


def _preflight(origin: str, method: str = "POST", headers: str = "content-type") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    }


async def test_preflight_allowed_origin(client: httpx.AsyncClient) -> None:
    response = await client.options("/healthz", headers=_preflight(ALLOWED))

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    assert "Access-Control-Allow-Credentials" not in response.headers


async def test_preflight_disallowed_origin_is_403_without_cors_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.options("/healthz", headers=_preflight(DISALLOWED))

    assert response.status_code == 403
    assert response.json()["code"] == "cors_rejected"
    assert _cors_headers(response) == {}


async def test_simple_get_disallowed_origin_has_no_cors_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": DISALLOWED})

    assert response.status_code == 200
    assert _cors_headers(response) == {}


async def test_simple_get_allowed_origin_gets_origin_back(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": ALLOWED})

    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED


async def test_preflight_put_rejected(client: httpx.AsyncClient) -> None:
    response = await client.options("/healthz", headers=_preflight(ALLOWED, method="PUT"))

    assert response.status_code == 403
    assert response.json()["code"] == "cors_rejected"
    assert _cors_headers(response) == {}


async def test_preflight_unlisted_header_rejected(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/healthz", headers=_preflight(ALLOWED, headers="authorization")
    )

    assert response.status_code == 403
    assert _cors_headers(response) == {}


async def test_rejected_preflight_has_request_id_and_security_headers(
    client: httpx.AsyncClient,
) -> None:
    response = await client.options("/healthz", headers=_preflight(DISALLOWED))

    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"


async def test_retry_after_is_exposed(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": ALLOWED})

    exposed = {
        h.strip().lower() for h in response.headers["Access-Control-Expose-Headers"].split(",")
    }
    assert exposed == {"x-request-id", "retry-after"}
