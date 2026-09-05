from __future__ import annotations

from decimal import Decimal
from typing import Any

from pricewatch.marketplaces import ParserDriftError, SearchCandidate


def _kopecks_to_rubles(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    price = Decimal(str(value)) / Decimal(100)
    return price if price > 0 else None


def _string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_search_payload(payload: dict[str, Any]) -> list[SearchCandidate]:
    """Parse a Wildberries catalog search response into neutral candidates.

    WB search currently returns product cards in the top-level ``products`` list.
    Each ``sizes`` entry can carry its own option id and price, so we preserve it
    as a separate variation candidate instead of silently collapsing variants.
    """
    products = payload.get("products")
    if not isinstance(products, list):
        raise ParserDriftError("Wildberries search payload has no products list")

    candidates: list[SearchCandidate] = []
    for product in products:
        if not isinstance(product, dict):
            raise ParserDriftError("Wildberries products item is not an object")

        listing_id = _string(product.get("id"))
        title = _string(product.get("name"))
        if not listing_id or not title:
            raise ParserDriftError("Wildberries product is missing id or name")

        brand = _string(product.get("brand"))
        seller_id = _string(product.get("supplierId"))
        seller_name = _string(product.get("supplier"))
        total_quantity = product.get("totalQuantity")
        available = total_quantity > 0 if isinstance(total_quantity, int | float) else None
        attributes = {"brand": brand} if brand else {}
        url = f"https://www.wildberries.ru/catalog/{listing_id}/detail.aspx"

        sizes = product.get("sizes")
        if sizes is None:
            sizes = []
        if not isinstance(sizes, list):
            raise ParserDriftError("Wildberries product sizes is not a list")

        if not sizes:
            candidates.append(
                SearchCandidate(
                    marketplace="wildberries",
                    listing_id=listing_id,
                    title=title,
                    attributes=attributes,
                    url=url,
                    seller_id=seller_id,
                    seller_name=seller_name,
                    available=available,
                    price_source="search",
                )
            )
            continue

        for size in sizes:
            if not isinstance(size, dict):
                raise ParserDriftError("Wildberries size item is not an object")
            price_block = size.get("price")
            if price_block is None:
                price_block = {}
            if not isinstance(price_block, dict):
                raise ParserDriftError("Wildberries size price is not an object")

            candidates.append(
                SearchCandidate(
                    marketplace="wildberries",
                    listing_id=listing_id,
                    variation_id=_string(size.get("optionId")),
                    title=title,
                    attributes=attributes,
                    url=url,
                    seller_id=seller_id,
                    seller_name=seller_name,
                    price=_kopecks_to_rubles(price_block.get("product")),
                    original_price=_kopecks_to_rubles(price_block.get("basic")),
                    available=available,
                    price_source="search",
                )
            )

    return candidates
