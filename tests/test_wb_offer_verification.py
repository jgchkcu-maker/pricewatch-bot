import asyncio
import json
from decimal import Decimal
from pathlib import Path

from pricewatch.adapters.wildberries import WildberriesSearchAdapter, parse_offer_payload
from pricewatch.marketplaces import OfferCondition, OfferLocator, SearchRequest

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


def locator() -> OfferLocator:
    return OfferLocator(
        marketplace="wildberries",
        listing_id="123456789",
        seller_id="4242",
        variation_id="987654",
        url="https://www.wildberries.ru/catalog/123456789/detail.aspx",
    )


def test_parse_offer_payload_selects_exact_variation_and_card_price() -> None:
    snapshot = parse_offer_payload(fixture("wb_card_minimal.json"), locator())

    assert snapshot.locator == locator()
    assert snapshot.title == "Планшет Xiaomi Pad 7 8ГБ 256ГБ"
    assert snapshot.price == Decimal("30990")
    assert snapshot.original_price == Decimal("35990")
    assert snapshot.available is True
    assert snapshot.price_source == "card"


def test_parse_offer_payload_uses_product_rating_not_supplier_rating() -> None:
    snapshot = parse_offer_payload(fixture("wb_card_minimal.json"), locator())

    assert snapshot.rating == Decimal("4.8")
    assert snapshot.review_count == 12436
    assert snapshot.rating != Decimal("3.1")


def test_wb_offer_extracts_seller_quality_from_exact_product() -> None:
    snapshot = parse_offer_payload(fixture("wb_card_minimal.json"), locator())

    assert snapshot.quality_signals.seller_name == "Example Seller"
    assert snapshot.quality_signals.seller_rating == Decimal("3.1")
    assert snapshot.quality_signals.seller_review_count is None
    assert snapshot.quality_signals.condition is OfferCondition.UNKNOWN
    assert snapshot.quality_signals.authenticity_badges == ()
    assert snapshot.quality_signals.identifiers == {}
    assert snapshot.quality_signals.image_count is None
    assert snapshot.rating != snapshot.quality_signals.seller_rating


def test_wb_missing_optional_seller_quality_does_not_block_exact_price() -> None:
    payload = fixture("wb_card_minimal.json")
    product = payload["products"][0]
    del product["supplierRating"]
    del product["supplier"]

    snapshot = parse_offer_payload(payload, locator())

    assert snapshot.price == Decimal("30990")
    assert snapshot.rating == Decimal("4.8")
    assert snapshot.review_count == 12436
    assert snapshot.quality_signals.seller_name is None
    assert snapshot.quality_signals.seller_rating is None
    assert snapshot.quality_signals.authenticity_badges == ()


def test_wb_quality_signals_come_from_same_exact_product() -> None:
    payload = fixture("wb_card_minimal.json")
    payload["products"].insert(
        0,
        {
            "id": 999999999,
            "brand": "Other",
            "name": "Other product",
            "supplier": "Wrong Seller",
            "supplierId": 9999,
            "supplierRating": 5.0,
            "reviewRating": 5.0,
            "feedbacks": 1,
            "totalQuantity": 1,
            "sizes": [
                {
                    "optionId": 111,
                    "price": {"basic": 100000, "product": 90000},
                }
            ],
        },
    )

    snapshot = parse_offer_payload(payload, locator())

    assert snapshot.quality_signals.seller_name == "Example Seller"
    assert snapshot.quality_signals.seller_rating == Decimal("3.1")
    assert snapshot.rating == Decimal("4.8")


def test_wb_adapter_fetch_offer_builds_card_v4_request() -> None:
    fetcher = RecordingFetcher(fixture("wb_card_minimal.json"))
    adapter = WildberriesSearchAdapter(fetcher, dest="-1257786")
    offer_locator = OfferLocator(
        marketplace="wildberries",
        listing_id="123456789",
        variation_id="987654",
    )

    snapshot = asyncio.run(adapter.fetch_offer(offer_locator))

    assert snapshot.price == Decimal("30990")
    request = fetcher.requests[0]
    assert request.url == "https://card.wb.ru/cards/v4/detail"
    assert request.params["nm"] == "123456789"
    assert request.params["dest"] == "-1257786"
    assert request.params["curr"] == "rub"
