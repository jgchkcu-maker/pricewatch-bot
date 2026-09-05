from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


class ParserDriftError(ValueError):
    """Raised when a marketplace response no longer has the expected schema."""


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


class MarketplaceAdapter(Protocol):
    marketplace: str

    async def search(self, query: str, *, limit: int = 50) -> list[SearchCandidate]: ...

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot: ...
