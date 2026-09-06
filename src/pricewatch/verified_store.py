from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pricewatch.deals import DealDecision, evaluate_verified_price
from pricewatch.marketplaces import OfferSnapshot, SearchCandidate
from pricewatch.prices import PriceEvent
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.runtime_repository import ConnectionFactory


@dataclass(frozen=True, slots=True)
class VerifiedOfferWriteResult:
    marketplace_listing_id: int
    event_id: int | None
    decision: DealDecision | None
    outbox_count: int


def _decimal_map_payload(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {str(key): str(value) for key, value in sorted(values.items())}


def _decode_json_map(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise ValueError("expected JSON object")
    if not isinstance(decoded, dict):
        raise ValueError("expected JSON object")
    return {str(key): str(item) for key, item in decoded.items()}


def _taxonomy_payload(candidate: SearchCandidate) -> dict[str, str | None] | None:
    taxonomy = candidate.taxonomy
    if taxonomy is None:
        return None
    return {
        "subject_id": taxonomy.subject_id,
        "parent_id": taxonomy.parent_id,
        "entity": taxonomy.entity,
        "category_path": taxonomy.category_path,
    }


def _reviews_url(marketplace: str, listing_id: str) -> str | None:
    """Build a review destination only for a verified, numeric marketplace id."""
    normalized_id = listing_id.strip()
    if not normalized_id.isdigit():
        return None
    normalized_marketplace = marketplace.casefold()
    if normalized_marketplace == "ozon":
        return f"https://www.ozon.ru/product/{normalized_id}/reviews/"
    if normalized_marketplace == "wildberries":
        return f"https://www.wildberries.ru/catalog/{normalized_id}/feedbacks"
    return None


def _rating_alert_payload(snapshot: OfferSnapshot) -> dict[str, Any]:
    rating = snapshot.rating
    review_count = snapshot.review_count
    if rating is None or review_count is None or isinstance(review_count, bool):
        return {}
    if not (Decimal("0") < rating <= Decimal("5")) or review_count <= 0:
        return {}
    reviews_url = _reviews_url(snapshot.locator.marketplace, snapshot.locator.listing_id)
    if reviews_url is None:
        return {}
    return {
        "rating": str(rating),
        "review_count": review_count,
        "reviews_url": reviews_url,
    }


class VerifiedOfferStore:
    """Atomically persist trusted detail state, price events and buyer alerts."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def record_verified_offer(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        snapshot: OfferSnapshot,
        *,
        verified_at: datetime,
        allow_alerts: bool = True,
    ) -> VerifiedOfferWriteResult:
        if candidate.marketplace != snapshot.locator.marketplace:
            raise ValueError("candidate and verified snapshot marketplace must match")
        if candidate.listing_id != snapshot.locator.listing_id:
            raise ValueError("candidate and verified snapshot listing must match")

        # Validate trusted price and timestamp before opening the transaction.
        PriceEvent(price=snapshot.price, observed_at=verified_at)
        conditional = _decimal_map_payload(snapshot.conditional_prices)
        conditional_json = json.dumps(conditional, ensure_ascii=False, sort_keys=True)
        attributes_json = json.dumps(dict(snapshot.attributes), ensure_ascii=False, sort_keys=True)
        taxonomy_json = json.dumps(
            _taxonomy_payload(candidate),
            ensure_ascii=False,
            sort_keys=True,
        )
        url = snapshot.locator.url or candidate.url
        variation_id = snapshot.locator.variation_id or candidate.variation_id or ""

        async with self._connection_factory() as connection:
            listing_cursor = await connection.execute(
                """
                INSERT INTO marketplace_listing (
                    tracked_product_id, marketplace, listing_id, variation_id,
                    seller_id, seller_name, canonical_url, title, attributes, taxonomy,
                    active, last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, TRUE, %s)
                ON CONFLICT (tracked_product_id, marketplace, listing_id, variation_id)
                DO UPDATE SET
                    seller_id = EXCLUDED.seller_id,
                    seller_name = EXCLUDED.seller_name,
                    canonical_url = EXCLUDED.canonical_url,
                    title = EXCLUDED.title,
                    attributes = EXCLUDED.attributes,
                    taxonomy = EXCLUDED.taxonomy,
                    active = TRUE,
                    last_seen_at = EXCLUDED.last_seen_at
                RETURNING id
                """,
                (
                    product.id,
                    candidate.marketplace,
                    candidate.listing_id,
                    variation_id,
                    snapshot.locator.seller_id or candidate.seller_id,
                    candidate.seller_name,
                    url,
                    snapshot.title,
                    attributes_json,
                    taxonomy_json,
                    verified_at,
                ),
            )
            listing_row = await listing_cursor.fetchone()
            if listing_row is None:
                raise RuntimeError("marketplace listing upsert returned no row")
            marketplace_listing_id = int(listing_row[0])

            state_cursor = await connection.execute(
                """
                SELECT public_price, conditional_prices::text, original_price, available
                FROM listing_state
                WHERE marketplace_listing_id = %s
                FOR UPDATE
                """,
                (marketplace_listing_id,),
            )
            state = await state_cursor.fetchone()
            state_unchanged = False
            if state is not None:
                state_public = Decimal(str(state[0])) if state[0] is not None else None
                state_conditional = _decode_json_map(state[1])
                state_original = Decimal(str(state[2])) if state[2] is not None else None
                state_available = bool(state[3]) if state[3] is not None else None
                state_unchanged = (
                    state_public == snapshot.price
                    and state_conditional == conditional
                    and state_original == snapshot.original_price
                    and state_available == snapshot.available
                )

            verification_meta = json.dumps(
                {"source": snapshot.price_source, "verified": True},
                sort_keys=True,
            )
            if state_unchanged:
                await connection.execute(
                    """
                    UPDATE listing_state
                    SET verified_at = %s,
                        verification_meta = %s::jsonb,
                        updated_at = NOW()
                    WHERE marketplace_listing_id = %s
                    """,
                    (verified_at, verification_meta, marketplace_listing_id),
                )
                await connection.commit()
                return VerifiedOfferWriteResult(
                    marketplace_listing_id=marketplace_listing_id,
                    event_id=None,
                    decision=None,
                    outbox_count=0,
                )

            cutoff = verified_at - timedelta(days=7)
            history_cursor = await connection.execute(
                """
                SELECT public_price, verified_at
                FROM price_event
                WHERE tracked_product_id = %s
                  AND public_price IS NOT NULL
                  AND verified_at >= %s
                  AND verified_at <= %s
                ORDER BY verified_at ASC
                """,
                (product.id, cutoff, verified_at),
            )
            history_rows = await history_cursor.fetchall()
            history = [
                PriceEvent(price=Decimal(str(row[0])), observed_at=row[1])  # type: ignore[arg-type]
                for row in history_rows
            ]
            decision = evaluate_verified_price(
                history,
                snapshot.price,
                observed_at=verified_at,
            )

            await connection.execute(
                """
                INSERT INTO listing_state (
                    marketplace_listing_id, public_price, conditional_prices,
                    original_price, available, verified_at, verification_meta, updated_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, NOW())
                ON CONFLICT (marketplace_listing_id) DO UPDATE
                SET public_price = EXCLUDED.public_price,
                    conditional_prices = EXCLUDED.conditional_prices,
                    original_price = EXCLUDED.original_price,
                    available = EXCLUDED.available,
                    verified_at = EXCLUDED.verified_at,
                    verification_meta = EXCLUDED.verification_meta,
                    updated_at = NOW()
                """,
                (
                    marketplace_listing_id,
                    snapshot.price,
                    conditional_json,
                    snapshot.original_price,
                    snapshot.available,
                    verified_at,
                    verification_meta,
                ),
            )
            event_cursor = await connection.execute(
                """
                INSERT INTO price_event (
                    tracked_product_id, marketplace_listing_id, public_price,
                    conditional_prices, available, verified_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id
                """,
                (
                    product.id,
                    marketplace_listing_id,
                    snapshot.price,
                    conditional_json,
                    snapshot.available,
                    verified_at,
                ),
            )
            event_row = await event_cursor.fetchone()
            if event_row is None:
                raise RuntimeError("price event insert returned no row")
            event_id = int(event_row[0])

            outbox_count = 0
            if allow_alerts and decision.is_new_low:
                rating_payload = _rating_alert_payload(snapshot)
                subscribers_cursor = await connection.execute(
                    """
                    SELECT u.id, u.chat_id, s.id
                    FROM subscription s
                    JOIN telegram_user u ON u.id = s.user_id
                    WHERE s.tracked_product_id = %s
                      AND s.status = 'active'
                      AND u.delivery_enabled = TRUE
                    """,
                    (product.id,),
                )
                subscribers = await subscribers_cursor.fetchall()
                for user_id, chat_id, subscription_id in subscribers:
                    payload: dict[str, Any] = {
                        "chat_id": int(chat_id),
                        "product_name": product.canonical_name,
                        "marketplace": candidate.marketplace,
                        "public_price": str(snapshot.price),
                        "previous_min": str(decision.previous_min),
                        "delta": str(decision.delta),
                        "delta_percent": str(decision.delta_percent),
                        "url": url,
                        "verified_at": verified_at.isoformat(),
                        "conditional_prices": conditional,
                        **rating_payload,
                    }
                    serialized_payload = json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    dedup_key = (
                        f"new-low:{product.id}:{marketplace_listing_id}:"
                        f"{event_id}:{int(subscription_id)}"
                    )
                    await connection.execute(
                        """
                        INSERT INTO notification_outbox (
                            dedup_key, user_id, subscription_id, tracked_product_id,
                            notification_type, payload
                        )
                        VALUES (%s, %s, %s, %s, 'new_low', %s::jsonb)
                        ON CONFLICT (dedup_key) DO NOTHING
                        """,
                        (
                            dedup_key,
                            int(user_id),
                            int(subscription_id),
                            product.id,
                            serialized_payload,
                        ),
                    )
                    outbox_count += 1

            await connection.commit()
            return VerifiedOfferWriteResult(
                marketplace_listing_id=marketplace_listing_id,
                event_id=event_id,
                decision=decision,
                outbox_count=outbox_count,
            )
