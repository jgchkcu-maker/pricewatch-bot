import json
from decimal import Decimal
from pathlib import Path

import pytest

from pricewatch.adapters.ozon import parse_search_payload as parse_ozon_search
from pricewatch.adapters.wildberries import parse_search_payload as parse_wb_search
from pricewatch.marketplaces import ParserDriftError

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_wb_search_payload_parses_listing_variant_price_and_seller() -> None:
    candidates = parse_wb_search(_fixture("wb_search_minimal.json"))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.marketplace == "wildberries"
    assert candidate.listing_id == "123456789"
    assert candidate.variation_id == "987654"
    assert candidate.title == "Планшет Xiaomi Pad 7 8ГБ 256ГБ"
    assert candidate.price == Decimal("31990")
    assert candidate.original_price == Decimal("35990")
    assert candidate.seller_id == "4242"
    assert candidate.seller_name == "Example Seller"
    assert candidate.available is True
    assert candidate.price_source == "search"


def test_wb_empty_products_is_healthy_empty_result() -> None:
    assert parse_wb_search({"metadata": {}, "products": []}) == []


def test_wb_missing_products_is_parser_drift() -> None:
    with pytest.raises(ParserDriftError, match="products"):
        parse_wb_search({"metadata": {}})


def test_ozon_search_payload_parses_tile_price_title_and_url() -> None:
    candidates = parse_ozon_search(_fixture("ozon_search_minimal.json"))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.marketplace == "ozon"
    assert candidate.listing_id == "3497076095"
    assert candidate.variation_id == "3497076095"
    assert candidate.title == "Xiaomi Pad 7 8 ГБ 256 ГБ"
    assert candidate.price == Decimal("31990")
    assert candidate.original_price == Decimal("35990")
    assert candidate.url == "https://www.ozon.ru/product/xiaomi-pad-7-8-256-3497076095/"
    assert candidate.price_source == "search"


def test_ozon_no_tile_grid_is_healthy_empty_result() -> None:
    assert parse_ozon_search({"widgetStates": {"searchResultsError-1": "{}"}}) == []


def test_ozon_malformed_tile_grid_is_parser_drift() -> None:
    with pytest.raises(ParserDriftError, match="tile grid"):
        parse_ozon_search({"widgetStates": {"tileGridDesktop-1": "not-json"}})


def test_ozon_missing_widget_states_is_parser_drift() -> None:
    with pytest.raises(ParserDriftError, match="widgetStates"):
        parse_ozon_search({})
