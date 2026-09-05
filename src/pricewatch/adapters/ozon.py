from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from pricewatch.marketplaces import (
    JsonFetcher,
    OfferIdentityError,
    OfferLocator,
    OfferSnapshot,
    ParserDriftError,
    SearchCandidate,
    SearchRequest,
)

_OZON_ORIGIN = "https://www.ozon.ru"
_OZON_COMPOSER_URL = f"{_OZON_ORIGIN}/api/composer-api.bx/page/json/v2"


def _parse_rub_price(value: object) -> Decimal | None:
    """Parse Ozon display-price strings without guessing numeric units."""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9,.-]", "", value).replace(",", ".")
    if not compact:
        return None
    try:
        price = Decimal(compact)
    except InvalidOperation:
        return None
    return price if price > 0 else None


def _canonical_url(link: object, sku: str) -> str:
    if not isinstance(link, str) or not link.strip():
        return f"{_OZON_ORIGIN}/product/{sku}/"

    parsed = urlsplit(link.strip())
    path = parsed.path or f"/product/{sku}/"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{_OZON_ORIGIN}{path}"


def _decode_widget(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ParserDriftError("Ozon widget is neither JSON text nor object")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ParserDriftError("Ozon widget contains malformed JSON") from exc
    if not isinstance(decoded, dict):
        raise ParserDriftError("Ozon widget did not decode to an object")
    return decoded


def _widget_name(key: str) -> str:
    """Return the stable semantic widget name, excluding volatile instance ids."""
    return key.split("-", 1)[0]


def _widget_states(payload: dict[str, Any]) -> dict[str, Any]:
    states = payload.get("widgetStates")
    if not isinstance(states, dict):
        raise ParserDriftError("Ozon payload has no widgetStates object")
    return states


def _widgets(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, raw_widget in _widget_states(payload).items():
        if isinstance(key, str) and _widget_name(key) == name:
            result.append(_decode_widget(raw_widget))
    return result


def _required_widget(payload: dict[str, Any], name: str) -> dict[str, Any]:
    matches = _widgets(payload, name)
    if not matches:
        raise ParserDriftError(f"Ozon detail payload is missing required {name} widget")
    return matches[0]


def _rich_text(nodes: object) -> str:
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        value = node.get("text", node.get("content"))
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(parts)


def _detail_attributes(payload: dict[str, Any]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for widget in _widgets(payload, "webShortCharacteristics"):
        rows = widget.get("characteristics")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            title_block = row.get("title")
            if isinstance(title_block, dict):
                title = _rich_text(title_block.get("textRs"))
            elif isinstance(title_block, str):
                title = title_block.strip()
            else:
                title = ""

            values = row.get("values")
            value = _rich_text(values)
            if title and value:
                attributes[title] = value
    return attributes


def _availability(payload: dict[str, Any], price_widget: dict[str, Any]) -> bool:
    signals: list[bool] = []
    price_available = price_widget.get("isAvailable")
    if isinstance(price_available, bool):
        signals.append(price_available)

    for sale in _widgets(payload, "webSale"):
        offer = sale.get("offer")
        if isinstance(offer, dict) and isinstance(offer.get("isAvailable"), bool):
            signals.append(offer["isAvailable"])

    if not signals:
        raise ParserDriftError("Ozon detail payload has no explicit availability signal")
    if any(value != signals[0] for value in signals[1:]):
        raise ParserDriftError("Ozon detail payload has conflicting availability signals")
    return signals[0]


def _verify_detail_sku(payload: dict[str, Any], locator: OfferLocator) -> str:
    expected = locator.listing_id.strip()
    if locator.variation_id is not None and locator.variation_id.strip() != expected:
        raise OfferIdentityError("Ozon variation id must equal the concrete SKU")

    gallery = _required_widget(payload, "webGallery")
    raw_gallery_sku = gallery.get("sku")
    gallery_sku = str(raw_gallery_sku).strip() if raw_gallery_sku is not None else ""
    if not gallery_sku:
        raise ParserDriftError("Ozon webGallery widget has no SKU")
    if gallery_sku != expected:
        raise OfferIdentityError(
            f"Ozon detail SKU mismatch: requested {expected}, received {gallery_sku}"
        )
    return gallery_sku


def _tile_text_and_prices(
    item: dict[str, Any],
) -> tuple[str | None, Decimal | None, Decimal | None]:
    title: str | None = None
    current: Decimal | None = None
    original: Decimal | None = None

    main_state = item.get("mainState")
    if not isinstance(main_state, list):
        raise ParserDriftError("Ozon tile has no mainState list")

    for atom in main_state:
        if not isinstance(atom, dict):
            continue
        atom_type = atom.get("type")

        if atom_type == "priceV2":
            price_v2 = atom.get("priceV2")
            if not isinstance(price_v2, dict):
                continue
            rows = price_v2.get("price")
            if not isinstance(rows, list):
                continue
            fallback_prices: list[Decimal] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                parsed = _parse_rub_price(row.get("text"))
                if parsed is None:
                    continue
                fallback_prices.append(parsed)
                style = row.get("textStyle")
                if style == "PRICE":
                    current = parsed
                elif style == "ORIGINAL_PRICE":
                    original = parsed
            if current is None and fallback_prices:
                current = fallback_prices[0]

        if atom_type == "textDS":
            text_ds = atom.get("textDS")
            if not isinstance(text_ds, dict):
                continue
            text = text_ds.get("text")
            test_info = text_ds.get("testInfo")
            automation_id = (
                test_info.get("automatizationId") if isinstance(test_info, dict) else None
            )
            is_name = atom.get("id") == "name" or automation_id == "tile-name"
            if isinstance(text, str) and is_name:
                title = text.strip() or None

    return title, current, original


def parse_search_payload(payload: dict[str, Any]) -> list[SearchCandidate]:
    """Parse Ozon ``tileGridDesktop-*`` composer widgets into search candidates."""
    candidates: list[SearchCandidate] = []
    seen: set[tuple[str, str]] = set()

    for key, raw_widget in _widget_states(payload).items():
        if not isinstance(key, str) or _widget_name(key) != "tileGridDesktop":
            continue

        widget = _decode_widget(raw_widget)
        items = widget.get("items")
        if not isinstance(items, list):
            raise ParserDriftError("Ozon tile grid widget has no items list")

        for item in items:
            if not isinstance(item, dict):
                raise ParserDriftError("Ozon tile grid item is not an object")

            raw_sku = item.get("sku", item.get("id"))
            if raw_sku is None:
                raise ParserDriftError("Ozon tile is missing sku/id")
            sku = str(raw_sku).strip()
            if not sku:
                raise ParserDriftError("Ozon tile has an empty sku/id")

            title, price, original_price = _tile_text_and_prices(item)
            if not title:
                raise ParserDriftError("Ozon tile is missing tile-name text")

            identity = (sku, sku)
            if identity in seen:
                continue
            seen.add(identity)

            action = item.get("action")
            link = action.get("link") if isinstance(action, dict) else None
            candidates.append(
                SearchCandidate(
                    marketplace="ozon",
                    listing_id=sku,
                    variation_id=sku,
                    title=title,
                    url=_canonical_url(link, sku),
                    price=price,
                    original_price=original_price,
                    price_source="search",
                )
            )

    return candidates


def parse_offer_payload(payload: dict[str, Any], locator: OfferLocator) -> OfferSnapshot:
    """Verify one concrete Ozon SKU from a PDP composer response.

    The normal public price is deliberately kept separate from Ozon-card/bank pricing.
    Search previews are not trusted here: identity, availability and price are read again
    from the concrete product page before the result can become a verified alert.
    """
    if locator.marketplace != "ozon":
        raise ValueError("Ozon parser received a non-Ozon locator")

    sku = _verify_detail_sku(payload, locator)
    heading = _required_widget(payload, "webProductHeading")
    title_value = heading.get("title", heading.get("text"))
    title = title_value.strip() if isinstance(title_value, str) else ""
    if not title:
        raise ParserDriftError("Ozon webProductHeading widget has no title")

    price_widget = _required_widget(payload, "webPrice")
    available = _availability(payload, price_widget)
    if not available:
        raise OfferIdentityError("Ozon offer became unavailable during verification")

    public_price = _parse_rub_price(price_widget.get("price"))
    if public_price is None:
        raise ParserDriftError("Ozon available offer has no valid public price")

    original_price = _parse_rub_price(price_widget.get("originalPrice"))
    if original_price is not None and original_price <= public_price:
        original_price = None

    conditional_prices: dict[str, Decimal] = {}
    card_price = _parse_rub_price(price_widget.get("cardPrice"))
    if card_price is not None:
        conditional_prices["ozon_card"] = card_price

    verified_locator = OfferLocator(
        marketplace="ozon",
        listing_id=sku,
        seller_id=locator.seller_id,
        variation_id=sku,
        url=locator.url or f"{_OZON_ORIGIN}/product/{sku}/",
    )
    return OfferSnapshot(
        locator=verified_locator,
        title=title,
        price=public_price,
        original_price=original_price,
        conditional_prices=conditional_prices,
        available=True,
        attributes=_detail_attributes(payload),
        price_source="card",
    )


class OzonSearchAdapter:
    marketplace = "ozon"

    def __init__(self, fetcher: JsonFetcher, *, composer_url: str = _OZON_COMPOSER_URL) -> None:
        self._fetcher = fetcher
        self._composer_url = composer_url

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
    ) -> list[SearchCandidate]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if page <= 0:
            raise ValueError("page must be positive")

        inner_path = f"/search/?text={query}&page={page}"
        request = SearchRequest(url=self._composer_url, params={"url": inner_path})
        payload = await self._fetcher.get_json(request)
        return parse_search_payload(payload)[:limit]

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        if locator.marketplace != self.marketplace:
            raise ValueError("offer locator marketplace does not match Ozon adapter")
        sku = locator.listing_id.strip()
        if not sku.isdigit():
            raise ValueError("Ozon listing id must be a numeric SKU")

        request = SearchRequest(
            url=self._composer_url,
            params={"url": f"/product/{sku}/"},
        )
        payload = await self._fetcher.get_json(request)
        return parse_offer_payload(payload, locator)
