from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pricewatch.taxonomy import MarketplaceTaxonomy


class ParserDriftError(ValueError):
    """Raised when a marketplace response no longer has the expected schema."""


class OfferIdentityError(ValueError):
    """A detail response could not verify the exact requested offer/variation."""


class OfferCondition(StrEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OfferQualitySignals:
    seller_name: str | None = None
    seller_rating: Decimal | None = None
    seller_review_count: int | None = None
    condition: OfferCondition = OfferCondition.UNKNOWN
    authenticity_badges: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)
    image_count: int | None = None


@dataclass(frozen=True, slots=True)
class SearchRequest:
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)


class JsonFetcher(Protocol):
    async def get_json(self, request: SearchRequest) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    marketplace: str
    listing_id: str
    title: str
    attributes: Mapping[str, str] = field(default_factory=dict)
    taxonomy: MarketplaceTaxonomy | None = None
    url: str | None = None
    variation_id: str | None = None
    seller_id: str | None = None
    seller_name: str | None = None
    price: Decimal | None = None
    original_price: Decimal | None = None
    available: bool | None = None
    price_source: str | None = None
    quality_status: str | None = None
    quality_observation_count: int = 0


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
    rating: Decimal | None = None
    review_count: int | None = None
    quality_signals: OfferQualitySignals = field(default_factory=OfferQualitySignals)


class MarketplaceSearchAdapter(Protocol):
    marketplace: str

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]: ...


class MarketplaceOfferAdapter(Protocol):
    marketplace: str

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot: ...


class MarketplaceAdapter(MarketplaceSearchAdapter, MarketplaceOfferAdapter, Protocol):
    """Full adapter contract for marketplaces that implement search and offer fetch."""
