from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from pricewatch.marketplaces import ParserDriftError, SearchCandidate

_OZON_ORIGIN = "https://www.ozon.ru"


def _parse_rub_price(value: object) -> Decimal | None:
    if not isinstance(value, str):
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
        raise ParserDriftError("Ozon tile grid widget is neither JSON text nor object")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ParserDriftError("Ozon tile grid widget contains malformed JSON") from exc
    if not isinstance(decoded, dict):
        raise ParserDriftError("Ozon tile grid widget did not decode to an object")
    return decoded


def _tile_text_and_prices(item: dict[str, Any]) -> tuple[str | None, Decimal | None, Decimal | None]:
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
            automation_id = test_info.get("automatizationId") if isinstance(test_info, dict) else None
            if isinstance(text, str) and (atom.get("id") == "name" or automation_id == "tile-name"):
                title = text.strip() or None

    return title, current, original


def parse_search_payload(payload: dict[str, Any]) -> list[SearchCandidate]:
    """Parse Ozon ``tileGridDesktop-*`` composer widgets into search candidates."""
    widget_states = payload.get("widgetStates")
    if not isinstance(widget_states, dict):
        raise ParserDriftError("Ozon search payload has no widgetStates object")

    candidates: list[SearchCandidate] = []
    seen: set[tuple[str, str]] = set()

    for key, raw_widget in widget_states.items():
        if not isinstance(key, str) or not key.startswith("tileGridDesktop-"):
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
