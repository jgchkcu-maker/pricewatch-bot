from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pricewatch.marketplaces import (
    JsonFetcher,
    OfferIdentityError,
    OfferLocator,
    OfferQualitySignals,
    OfferSnapshot,
    ParserDriftError,
    SearchCandidate,
    SearchRequest,
)
from pricewatch.taxonomy import MarketplaceTaxonomy

_WB_SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"
_WB_CARD_URL = "https://card.wb.ru/cards/v4/detail"
_WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.wildberries.ru/",
}


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


def _rating(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        rating = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return rating if Decimal("0") < rating <= Decimal("5") else None


def _review_count(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        return int(text) if text.isdigit() else None
    return None


def _product_for_listing(payload: dict[str, Any], listing_id: str) -> dict[str, Any] | None:
    products = payload.get("products")
    if not isinstance(products, list):
        return None
    for product in products:
        if isinstance(product, dict) and _string(product.get("id")) == listing_id:
            return product
    return None


def _product_rating(
    payload: dict[str, Any],
    listing_id: str,
) -> tuple[Decimal | None, int | None]:
    """Read product-level review metadata for one exact WB card.

    ``supplierRating`` is intentionally never consulted: it belongs to the
    seller, while alerts must show the rating of the concrete product card.
    """
    product = _product_for_listing(payload, listing_id)
    if product is None:
        return None, None
    rating = _rating(product.get("reviewRating"))
    if rating is None:
        rating = _rating(product.get("rating"))
    return rating, _review_count(product.get("feedbacks"))


def _quality_signals(
    payload: dict[str, Any],
    candidate: SearchCandidate,
) -> OfferQualitySignals:
    """Read optional seller metadata from the same exact WB product object.

    Unknown native shapes intentionally remain unknown. Product review fields
    stay separate from seller rating, and no authenticity/condition signal is
    inferred when the captured payload does not expose one.
    """
    product = _product_for_listing(payload, candidate.listing_id)
    if product is None:
        return OfferQualitySignals()
    return OfferQualitySignals(
        seller_name=_string(product.get("supplier")),
        seller_rating=_rating(product.get("supplierRating")),
    )


def _taxonomy(product: dict[str, Any]) -> MarketplaceTaxonomy | None:
    subject_id = _string(product.get("subjectId"))
    parent_id = _string(product.get("subjectParentId"))
    entity = _string(product.get("entity"))
    if not subject_id and not parent_id and not entity:
        return None
    return MarketplaceTaxonomy(
        subject_id=subject_id,
        parent_id=parent_id,
        entity=entity,
    )


def parse_search_payload(payload: dict[str, Any]) -> list[SearchCandidate]:
    """Parse a Wildberries catalog/card response into neutral candidates.

    WB product payloads currently expose product-level data plus one or more
    ``sizes`` options. Each option remains a separate variation candidate.
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
        taxonomy = _taxonomy(product)
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
                    taxonomy=taxonomy,
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
                    taxonomy=taxonomy,
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


def parse_offer_payload(payload: dict[str, Any], locator: OfferLocator) -> OfferSnapshot:
    candidates = parse_search_payload(payload)
    matching = [candidate for candidate in candidates if candidate.listing_id == locator.listing_id]

    if locator.variation_id is not None:
        matching = [
            candidate for candidate in matching if candidate.variation_id == locator.variation_id
        ]

    if locator.seller_id is not None:
        matching = [
            candidate
            for candidate in matching
            if candidate.seller_id is None or candidate.seller_id == locator.seller_id
        ]

    if len(matching) != 1:
        raise OfferIdentityError(
            "Wildberries card did not resolve to exactly one requested offer variation"
        )

    candidate = matching[0]
    if candidate.price is None:
        raise ParserDriftError("Wildberries verified card candidate has no product price")

    rating, review_count = _product_rating(payload, candidate.listing_id)
    return OfferSnapshot(
        locator=locator,
        title=candidate.title,
        price=candidate.price,
        available=bool(candidate.available),
        attributes=candidate.attributes,
        original_price=candidate.original_price,
        price_source="card",
        rating=rating,
        review_count=review_count,
        quality_signals=_quality_signals(payload, candidate),
    )


class WildberriesSearchAdapter:
    marketplace = "wildberries"
    # WB starts returning 429 when the search host sees back-to-back catalog
    # requests from the same cloud egress. Keep one rotating discovery query
    # per scan; known listing detail polling remains independent and frequent.
    max_search_queries_per_scan = 1

    def __init__(self, fetcher: JsonFetcher, *, dest: str = "-1257786") -> None:
        self._fetcher = fetcher
        self._dest = dest

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if page <= 0:
            raise ValueError("page must be positive")
        if category_path is not None:
            raise ValueError("Wildberries does not use Ozon-style category paths")

        request = SearchRequest(
            url=_WB_SEARCH_URL,
            params={
                "appType": "1",
                "curr": "rub",
                "dest": self._dest,
                "locale": "ru",
                "query": query,
                "resultset": "catalog",
                "page": str(page),
                "spp": "30",
            },
            headers=_WB_HEADERS,
        )
        payload = await self._fetcher.get_json(request)
        return parse_search_payload(payload)[:limit]

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        if locator.marketplace != self.marketplace:
            raise ValueError("offer locator marketplace does not match Wildberries adapter")

        request = SearchRequest(
            url=_WB_CARD_URL,
            params={
                "appType": "1",
                "curr": "rub",
                "dest": self._dest,
                "locale": "ru",
                "spp": "30",
                "nm": locator.listing_id,
            },
            headers=_WB_HEADERS,
        )
        payload = await self._fetcher.get_json(request)
        return parse_offer_payload(payload, locator)
