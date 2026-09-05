import asyncio

import httpx

from pricewatch.marketplaces import SearchRequest
from pricewatch.transport import (
    HttpJsonFetcher,
    MarketplaceAccessError,
    MarketplaceRateLimitedError,
    MarketplaceTransportError,
)


def run_fetch(response: httpx.Response, *, max_response_bytes: int = 1024) -> dict:
    async def handler(request: httpx.Request) -> httpx.Response:
        return response

    async def scenario() -> dict:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
            fetcher = HttpJsonFetcher(client, max_response_bytes=max_response_bytes)
            return await fetcher.get_json(
                SearchRequest("https://example.test/search", {"query": "xiaomi"})
            )

    return asyncio.run(scenario())


def test_fetcher_returns_top_level_json_object() -> None:
    payload = run_fetch(httpx.Response(200, json={"products": []}))
    assert payload == {"products": []}


def test_fetcher_classifies_rate_limit_without_retrying() -> None:
    try:
        run_fetch(httpx.Response(429, headers={"Retry-After": "12"}, text="slow down"))
    except MarketplaceRateLimitedError as exc:
        assert exc.retry_after_seconds == 12
    else:
        raise AssertionError("429 must be classified as rate limited")


def test_fetcher_classifies_access_denied_and_redirect_as_access_error() -> None:
    for response in (
        httpx.Response(403, text="forbidden"),
        httpx.Response(307, headers={"Location": "https://example.test/challenge"}),
    ):
        try:
            run_fetch(response)
        except MarketplaceAccessError:
            pass
        else:
            raise AssertionError(f"{response.status_code} must be classified as access error")


def test_fetcher_classifies_html_200_as_access_error() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        text="<html><title>Verify you are human</title></html>",
    )
    try:
        run_fetch(response)
    except MarketplaceAccessError:
        pass
    else:
        raise AssertionError("HTML challenge page must not be parsed as marketplace JSON")


def test_fetcher_rejects_oversized_or_non_object_json() -> None:
    try:
        run_fetch(httpx.Response(200, content=b"{" + b"x" * 2000 + b"}"), max_response_bytes=100)
    except MarketplaceTransportError as exc:
        assert "large" in str(exc)
    else:
        raise AssertionError("oversized response must fail")

    try:
        run_fetch(httpx.Response(200, json=[1, 2, 3]))
    except MarketplaceTransportError as exc:
        assert "object" in str(exc)
    else:
        raise AssertionError("top-level JSON arrays must fail")
