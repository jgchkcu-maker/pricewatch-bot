import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricewatch.adapters.ozon import OzonSearchAdapter, parse_offer_payload
from pricewatch.marketplaces import (
    OfferCondition,
    OfferIdentityError,
    OfferLocator,
    ParserDriftError,
    SearchRequest,
)

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
        marketplace="ozon",
        listing_id="123456789",
        variation_id="123456789",
        url="https://www.ozon.ru/product/xiaomi-pad-7-123456789/",
    )


def test_parse_ozon_offer_uses_public_price_and_keeps_card_price_conditional() -> None:
    snapshot = parse_offer_payload(fixture("ozon_detail_minimal.json"), locator())

    assert snapshot.locator == locator()
    assert snapshot.title == "Xiaomi Планшет Pad 7 8 ГБ 256 ГБ"
    assert snapshot.price == Decimal("30990")
    assert snapshot.original_price == Decimal("35990")
    assert snapshot.conditional_prices == {"ozon_card": Decimal("29490")}
    assert snapshot.available is True
    assert snapshot.price_source == "card"
    assert "8 ГБ" in snapshot.attributes.values()
    assert "256 ГБ" in snapshot.attributes.values()


def test_ozon_offer_extracts_optional_quality_signals() -> None:
    snapshot = parse_offer_payload(fixture("ozon_detail_minimal.json"), locator())

    assert snapshot.quality_signals.seller_name == "Example Store"
    assert snapshot.quality_signals.seller_rating == Decimal("4.7")
    assert snapshot.quality_signals.seller_review_count is None
    assert snapshot.quality_signals.condition is OfferCondition.UNKNOWN
    assert snapshot.quality_signals.authenticity_badges == ("оригинал",)
    assert snapshot.quality_signals.identifiers == {}
    assert snapshot.quality_signals.image_count == 3


def test_ozon_offer_reads_product_rating_widget() -> None:
    snapshot = parse_offer_payload(fixture("ozon_detail_minimal.json"), locator())

    assert snapshot.rating == Decimal("4.9")
    assert snapshot.review_count == 731


def test_ozon_missing_or_malformed_optional_metadata_does_not_block_price() -> None:
    payload = fixture("ozon_detail_minimal.json")
    del payload["widgetStates"]["webCurrentSeller-106-default-1"]
    del payload["widgetStates"]["webReviewProductScore-107-default-1"]
    del payload["widgetStates"]["labelListV2-108-default-1"]
    payload["widgetStates"]["webCurrentSeller-999-default-1"] = "not-json"
    payload["widgetStates"]["labelListV2-999-default-1"] = "not-json"

    snapshot = parse_offer_payload(payload, locator())

    assert snapshot.locator == locator()
    assert snapshot.price == Decimal("30990")
    assert snapshot.available is True
    assert snapshot.rating is None
    assert snapshot.review_count is None
    assert snapshot.quality_signals.seller_name is None
    assert snapshot.quality_signals.seller_rating is None
    assert snapshot.quality_signals.authenticity_badges == ()
    assert snapshot.quality_signals.condition is OfferCondition.UNKNOWN


def test_ozon_offer_rejects_sku_mismatch() -> None:
    payload = fixture("ozon_detail_minimal.json")
    payload["widgetStates"]["webGallery-100-default-1"] = json.dumps({"sku": "999999999"})

    with pytest.raises(OfferIdentityError):
        parse_offer_payload(payload, locator())


def test_ozon_offer_fails_closed_when_price_widget_disappears() -> None:
    payload = fixture("ozon_detail_minimal.json")
    del payload["widgetStates"]["webPrice-102-default-1"]

    with pytest.raises(ParserDriftError):
        parse_offer_payload(payload, locator())


def test_ozon_offer_rejects_conflicting_availability_signals() -> None:
    payload = fixture("ozon_detail_minimal.json")
    payload["widgetStates"]["webSale-103-default-1"] = json.dumps(
        {"offer": {"isAvailable": False}}
    )

    with pytest.raises(ParserDriftError):
        parse_offer_payload(payload, locator())


def test_ozon_adapter_fetch_offer_builds_exact_pdp_request() -> None:
    fetcher = RecordingFetcher(fixture("ozon_detail_minimal.json"))
    adapter = OzonSearchAdapter(fetcher)

    snapshot = asyncio.run(adapter.fetch_offer(locator()))

    assert snapshot.price == Decimal("30990")
    request = fetcher.requests[0]
    assert request.url == "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
    assert request.params["url"] == "/product/123456789/"
