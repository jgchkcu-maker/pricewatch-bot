from __future__ import annotations

import json
from typing import Any

import httpx

from pricewatch.marketplaces import SearchRequest


class MarketplaceTransportError(RuntimeError):
    """Marketplace request failed for a non-access-control reason."""


class MarketplaceAccessError(MarketplaceTransportError):
    """Marketplace refused the request or returned an access/challenge response."""


class MarketplaceRateLimitedError(MarketplaceAccessError):
    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = int(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class HttpJsonFetcher:
    """Small, non-retrying JSON transport for marketplace adapters.

    Retry/backoff belongs to the scheduler so a blocked marketplace cannot create
    an uncontrolled retry storm inside every individual request.
    """

    def __init__(self, client: httpx.AsyncClient, *, max_response_bytes: int = 5_000_000) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._client = client
        self._max_response_bytes = max_response_bytes

    async def get_json(self, request: SearchRequest) -> dict[str, Any]:
        try:
            response = await self._client.get(
                request.url,
                params=request.params,
                headers=request.headers,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise MarketplaceTransportError("marketplace request timed out") from exc
        except httpx.RequestError as exc:
            raise MarketplaceTransportError("marketplace network request failed") from exc

        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            raise MarketplaceRateLimitedError("marketplace rate limited request", retry_after)
        if response.status_code in {401, 403}:
            raise MarketplaceAccessError(f"marketplace denied access: HTTP {response.status_code}")
        if 300 <= response.status_code < 400:
            raise MarketplaceAccessError(
                f"marketplace returned redirect: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise MarketplaceTransportError(
                f"marketplace returned HTTP {response.status_code}"
            )

        content = response.content
        if len(content) > self._max_response_bytes:
            raise MarketplaceTransportError("marketplace response is too large")

        content_type = response.headers.get("content-type", "").casefold()
        stripped = content.lstrip()
        if "text/html" in content_type or stripped.startswith((b"<html", b"<!doctype")):
            raise MarketplaceAccessError("marketplace returned HTML instead of JSON")

        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MarketplaceTransportError("marketplace returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise MarketplaceTransportError("marketplace JSON root must be an object")
        return payload
