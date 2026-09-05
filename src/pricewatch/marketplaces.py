from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class ParserDriftError(ValueError):
    """Raised when a marketplace response no longer has the expected schema."""


class OfferIdentityError(ValueError):
    """A detail response could not verify the exact requested offer/variation."""


@dataclass(frozen=True, slots=True)
class SearchRequest:
    url: str
    params: Mapping[str, str] = field(default_factory=dict)


class JsonFetcher(Protocol):
    async def get_json(self, request: SearchRequest) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    marketplace: str
    listing_id: str
    title: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None
    variation_id: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    available: bool | None = None
    price_source: str | None = None


@dataclass(frozen=True, slots=True)
class OfferLocator:
    marketplace: str
    listing_id: str
    seller_id: str | None = None
    variation_id: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class OfferSnapshot:
    locator: OfferLocator
    title: str
    price: Decimal
    available: bool
    attributes: Mapping[str, str] = field(default_factory=dict)
    original_price: Decimal | None = None
    conditional_prices: Mapping[str, Decimal] = field(default_factory=dict)
    price_source: str = "offer"


class MarketplaceSearchAdapter(Protocol):
    marketplace: str

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
    ) -> list[SearchCandidate]: ...


class MarketplaceOfferAdapter(Protocol):
    marketplace: str

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot: ...


class MarketplaceAdapter(MarketplaceSearchAdapter, MarketplaceOfferAdapter, Protocol):
    """Full adapter contract for marketplaces that implement search and offer fetch."""
