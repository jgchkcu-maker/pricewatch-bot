import asyncio
from decimal import Decimal

from pricewatch.marketplaces import OfferIdentityError, OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.search_plan import SearchPlan
from pricewatch.verification import verify_candidate


class FakeOfferAdapter:
    marketplace = "wildberries"

    def __init__(self, title: str, price: Decimal) -> None:
        self.title = title
        self.price = price

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        return OfferSnapshot(
            locator=locator,
            title=self.title,
            price=self.price,
            available=True,
            price_source="card",
        )


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        product_type="tablet",
        primary_query="xiaomi pad 7 8 256",
        required_tokens=("xiaomi",),
        excluded_terms=("pad 7 pro", "чехол"),
        identity_attributes={"model": "pad 7", "ram": "8 gb", "storage": "256 gb"},
    )


def search_candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="123",
        variation_id="456",
        seller_id="789",
        title="Xiaomi Pad 7 8ГБ 256ГБ",
        price=Decimal("29990"),
        price_source="search",
    )


def test_verification_uses_concrete_offer_and_rechecks_identity() -> None:
    snapshot = asyncio.run(
        verify_candidate(
            plan(),
            search_candidate(),
            FakeOfferAdapter("Xiaomi Pad7 8ГБ 256ГБ", Decimal("29490")),
        )
    )

    assert snapshot.price == Decimal("29490")
    assert snapshot.price_source == "card"
    assert snapshot.locator.variation_id == "456"


def test_verification_rejects_detail_card_that_no_longer_matches_product() -> None:
    try:
        asyncio.run(
            verify_candidate(
                plan(),
                search_candidate(),
                FakeOfferAdapter("Xiaomi Pad 7 Pro 8ГБ 256ГБ", Decimal("19990")),
            )
        )
    except OfferIdentityError as exc:
        assert "verification" in str(exc)
    else:
        raise AssertionError("mismatched detail card must not become a verified offer")
