from decimal import Decimal

import pytest

from pricewatch.marketplaces import SearchCandidate
from pricewatch.scan import scan_once
from pricewatch.search_plan import SearchPlan


class FakeSearchAdapter:
    marketplace = "wildberries"

    async def search(self, query: str, *, limit: int = 50) -> list[SearchCandidate]:
        assert query in {"xiaomi pad 7 8 256", "xiaomi pad7 8 256"}
        return [
            SearchCandidate(
                marketplace="wildberries",
                listing_id="1",
                variation_id="11",
                title="Xiaomi Pad7 8ГБ 256ГБ",
                attributes={"brand": "Xiaomi"},
                price=Decimal("31990"),
                price_source="search",
            ),
            SearchCandidate(
                marketplace="wildberries",
                listing_id="2",
                variation_id="22",
                title="Чехол для Xiaomi Pad 7 8/256",
                price=Decimal("990"),
                price_source="search",
            ),
            SearchCandidate(
                marketplace="wildberries",
                listing_id="3",
                variation_id="33",
                title="Xiaomi Pad 7 256GB",
                attributes={"brand": "Xiaomi"},
                price=Decimal("29990"),
                price_source="search",
            ),
        ]


def _plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="Xiaomi Pad 7 8/256",
        aliases=("Xiaomi Pad7 8 256",),
        required_tokens=("xiaomi",),
        excluded_terms=("чехол", "case", "pad 7 pro"),
        identity_attributes={
            "model": "pad 7",
            "ram": "8 gb",
            "storage": "256 gb",
        },
    )


@pytest.mark.asyncio
async def test_scan_splits_accepted_rejected_and_ambiguous_candidates() -> None:
    outcome = await scan_once(_plan(), FakeSearchAdapter(), cycle=0)

    assert outcome.query == "xiaomi pad 7 8 256"
    assert [(item.listing_id, item.variation_id) for item in outcome.accepted] == [("1", "11")]
    assert [(item.listing_id, item.variation_id) for item in outcome.ambiguous] == [("3", "33")]
    assert outcome.rejected_count == 1
    assert outcome.accepted[0].price == Decimal("31990")


@pytest.mark.asyncio
async def test_scan_uses_rotating_alias_on_odd_cycle() -> None:
    outcome = await scan_once(_plan(), FakeSearchAdapter(), cycle=1)
    assert outcome.query == "xiaomi pad7 8 256"
