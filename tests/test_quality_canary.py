# ruff: noqa: I001
import asyncio
from decimal import Decimal

from pricewatch.marketplaces import OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.quality_canary import run_marketplace_canary
from pricewatch.search_plan import SearchPlan


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Apple AirPods Pro 3",
        primary_query="AirPods Pro 3",
        product_type="wireless earbuds",
        required_tokens=("airpods", "pro", "3"),
        identity_attributes={},
    )


def candidate(listing_id: str, title: str) -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id=listing_id,
        variation_id=f"option-{listing_id}",
        seller_id=f"seller-{listing_id}",
        title=title,
        url=f"https://www.wildberries.ru/catalog/{listing_id}/detail.aspx",
    )


class FixtureAdapter:
    marketplace = "wildberries"

    def __init__(self) -> None:
        self.candidates = [
            candidate("1", "AirPods Pro 3"),
            candidate("2", "AirPods Pro 3"),
            candidate("3", "Чехол для AirPods Pro 3"),
            candidate("4", "AirPods Pro 3 копия 1:1"),
            candidate("5", "AirPods Pro 3"),
        ]
        self.snapshots = {
            "1": ("19990", "AirPods Pro 3", True),
            "2": ("827", "AirPods Pro 3", True),
            "3": ("799", "Чехол для AirPods Pro 3", True),
            "4": ("827", "AirPods Pro 3 копия 1:1", True),
            "5": ("19990", "AirPods Pro 3", False),
        }

    async def search(self, query, *, limit=50, page=1, category_path=None):
        return self.candidates[:limit]

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        price, title, available = self.snapshots[locator.listing_id]
        return OfferSnapshot(
            locator=locator,
            title=title,
            price=Decimal(price),
            available=available,
            price_source="detail",
        )


class EmptyAdapter(FixtureAdapter):
    async def search(self, query, *, limit=50, page=1, category_path=None):
        return []


def test_fixture_canary_partitions_every_exact_verified_offer() -> None:
    result = asyncio.run(
        run_marketplace_canary(
            plan(),
            FixtureAdapter(),
            limit=10,
            trusted_prices=(Decimal("18990"), Decimal("19990"), Decimal("20990")),
        )
    )

    assert result.raw_count == 5
    assert result.verified_count == 5
    assert result.identity_rejected_count == 0
    assert result.trusted_count == 1
    assert result.quarantined_count == 1
    assert result.quality_rejected_count == 2
    assert result.unavailable_count == 1
    assert (
        result.trusted_count
        + result.quarantined_count
        + result.quality_rejected_count
        + result.unavailable_count
        == result.verified_count
    )
    assert result.reason_code_counts["price_outlier"] == 1
    assert result.reason_code_counts["accessory_only"] == 1
    assert result.reason_code_counts["explicit_counterfeit"] == 1
    assert result.reason_code_counts["unavailable"] == 1


def test_fixture_canary_treats_healthy_empty_search_as_zero() -> None:
    result = asyncio.run(run_marketplace_canary(plan(), EmptyAdapter(), limit=10))

    assert result.raw_count == 0
    assert result.verified_count == 0
    assert result.trusted_count == 0
    assert result.quarantined_count == 0
    assert result.quality_rejected_count == 0
    assert result.unavailable_count == 0
