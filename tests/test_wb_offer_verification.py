import asyncio
import json
from decimal import Decimal
from pathlib import Path

from pricewatch.adapters.wildberries import WildberriesSearchAdapter, parse_offer_payload
from pricewatch.marketplaces import OfferLocator, SearchRequest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingFetcher:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[SearchRequest] = []

    async def get_json(self, request: SearchRequest) -> dict:
        self.requests.append(request)
        return self.payload


def test_parse_offer_payload_selects_exact_variation_and_card_price() -> None:
    locator = OfferLocator(
        marketplace="wildberries",
        listing_id="123456789",
        seller_id="4242",
        variation_id="987654",
        url="https://www.wildberries.ru/catalog/123456789/detail.aspx",
    )

    snapshot = parse_offer_payload(fixture("wb_card_minimal.json"), locator)

    assert snapshot.locator == locator
    assert snapshot.title == "Планшет Xiaomi Pad 7 8ГБ 256ГБ"
    assert snapshot.price == Decimal("30990")
    assert snapshot.original_price == Decimal("35990")
    assert snapshot.available is True
    assert snapshot.price_source == "card"


def test_parse_offer_payload_uses_product_rating_not_supplier_rating() -> None:
    locator = OfferLocator(
        marketplace="wildberries",
        listing_id="123456789",
        seller_id="4242",
        variation_id="987654",
        url="https://www.wildberries.ru/catalog/123456789/detail.aspx",
    )

    snapshot = parse_offer_payload(fixture("wb_card_minimal.json"), locator)

    assert snapshot.rating == Decimal("4.8")
    assert snapshot.review_count == 12436
    assert snapshot.rating != Decimal("3.1")


def test_wb_adapter_fetch_offer_builds_card_v4_request() -> None:
    fetcher = RecordingFetcher(fixture("wb_card_minimal.json"))
    adapter = WildberriesSearchAdapter(fetcher, dest="-1257786")
    locator = OfferLocator(
        marketplace="wildberries",
        listing_id="123456789",
        variation_id="987654",
    )

    snapshot = asyncio.run(adapter.fetch_offer(locator))

    assert snapshot.price == Decimal("30990")
    request = fetcher.requests[0]
    assert request.url == "https://card.wb.ru/cards/v4/detail"
    assert request.params["nm"] == "123456789"
    assert request.params["dest"] == "-1257786"
    assert request.params["curr"] == "rub"
