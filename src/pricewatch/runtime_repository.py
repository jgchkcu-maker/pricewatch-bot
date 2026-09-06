from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pricewatch.marketplaces import SearchCandidate
from pricewatch.runtime_models import (
    SubscriptionRecord,
    TrackedProductRecord,
    UserProductSummary,
    identity_fingerprint,
    search_plan_from_payload,
    search_plan_to_payload,
)
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import MarketplaceTaxonomy


class _Cursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...

    async def fetchall(self) -> list[tuple[object, ...]]: ...


class _Connection(Protocol):
    async def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _Cursor: ...

    async def commit(self) -> None: ...


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[_Connection]: ...


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise ValueError("expected JSON object")
    if not isinstance(decoded, dict):
        raise ValueError("expected JSON object")
    return decoded


def _tracked_product_from_row(row: tuple[object, ...]) -> TrackedProductRecord:
    return TrackedProductRecord(
        id=int(row[0]),
        canonical_name=str(row[1]),
        product_type=str(row[2]) if row[2] is not None else None,
        identity_fingerprint=str(row[3]),
        search_plan=search_plan_from_payload(_json_object(row[4])),
        lifecycle_state=str(row[5]),
        subscriber_count=int(row[6]),
        next_scan_at=row[7],  # type: ignore[arg-type]
        last_successful_scan_at=row[8],  # type: ignore[arg-type]
    )


def _subscription_from_row(row: tuple[object, ...]) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=int(row[0]),
        user_id=int(row[1]),
        tracked_product_id=int(row[2]),
        status=str(row[3]),
    )


class RuntimeRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def ensure_user(self, *, telegram_user_id: int, chat_id: int) -> int:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO telegram_user (telegram_user_id, chat_id, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (telegram_user_id) DO UPDATE
                SET chat_id = EXCLUDED.chat_id,
                    updated_at = NOW()
                RETURNING id
                """,
                (telegram_user_id, chat_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("telegram user upsert returned no row")
            await connection.commit()
            return int(row[0])

    async def upsert_tracked_product(self, plan: SearchPlan) -> TrackedProductRecord:
        fingerprint = identity_fingerprint(plan)
        serialized = json.dumps(search_plan_to_payload(plan), ensure_ascii=False, sort_keys=True)
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO tracked_product (
                    canonical_name,
                    product_type,
                    identity_fingerprint,
                    search_plan,
                    lifecycle_state,
                    next_scan_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s::jsonb, 'active', NOW(), NOW())
                ON CONFLICT (identity_fingerprint) DO UPDATE
                SET updated_at = NOW()
                RETURNING id, canonical_name, product_type, identity_fingerprint,
                          search_plan::text, lifecycle_state, subscriber_count,
                          next_scan_at, last_successful_scan_at
                """,
                (plan.canonical_name, plan.product_type, fingerprint, serialized),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("tracked product upsert returned no row")
            await connection.commit()
            return _tracked_product_from_row(row)

    async def _refresh_product_subscription_state(
        self,
        connection: _Connection,
        product_id: int,
    ) -> None:
        await connection.execute(
            """
            UPDATE tracked_product
            SET subscriber_count = (
                    SELECT COUNT(*)
                    FROM subscription
                    WHERE tracked_product_id = %s AND status = 'active'
                ),
                lifecycle_state = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM subscription
                        WHERE tracked_product_id = %s AND status = 'active'
                    ) THEN 'active'
                    ELSE 'paused_no_subscribers'
                END,
                next_scan_at = CASE
                    WHEN EXISTS (
                        SELECT 1 FROM subscription
                        WHERE tracked_product_id = %s AND status = 'active'
                    ) THEN LEAST(next_scan_at, NOW())
                    ELSE next_scan_at
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (product_id, product_id, product_id, product_id),
        )

    async def subscribe(self, *, user_id: int, product_id: int) -> SubscriptionRecord:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO subscription (user_id, tracked_product_id, status, updated_at)
                VALUES (%s, %s, 'active', NOW())
                ON CONFLICT (user_id, tracked_product_id) DO UPDATE
                SET status = 'active',
                    updated_at = NOW()
                RETURNING id, user_id, tracked_product_id, status
                """,
                (user_id, product_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("subscription upsert returned no row")
            await self._refresh_product_subscription_state(connection, product_id)
            await connection.commit()
            return _subscription_from_row(row)

    async def set_subscription_status(self, subscription_id: int, status: str) -> None:
        if status not in {"active", "paused"}:
            raise ValueError("subscription status must be active or paused")
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                UPDATE subscription
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING tracked_product_id
                """,
                (status, subscription_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise KeyError("subscription not found")
            await self._refresh_product_subscription_state(connection, int(row[0]))
            await connection.commit()

    async def pause_subscription(self, subscription_id: int) -> None:
        await self.set_subscription_status(subscription_id, "paused")

    async def resume_subscription(self, subscription_id: int) -> None:
        await self.set_subscription_status(subscription_id, "active")

    async def delete_subscription(self, *, user_id: int, subscription_id: int) -> None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM subscription
                WHERE id = %s AND user_id = %s
                RETURNING tracked_product_id
                """,
                (subscription_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise KeyError("subscription not found")

            product_id = int(row[0])
            await self._refresh_product_subscription_state(connection, product_id)
            remaining_cursor = await connection.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM subscription
                    WHERE tracked_product_id = %s
                )
                """,
                (product_id,),
            )
            remaining_row = await remaining_cursor.fetchone()
            has_remaining_subscriptions = bool(remaining_row and remaining_row[0])
            if not has_remaining_subscriptions:
                await connection.execute(
                    "DELETE FROM price_event WHERE tracked_product_id = %s",
                    (product_id,),
                )
            await connection.commit()

    async def list_user_products(self, user_id: int) -> tuple[UserProductSummary, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT s.id, s.user_id, s.tracked_product_id, s.status,
                       p.id, p.canonical_name, p.product_type, p.identity_fingerprint,
                       p.search_plan::text, p.lifecycle_state, p.subscriber_count,
                       p.next_scan_at, p.last_successful_scan_at,
                       current_offer.public_price::text,
                       current_offer.marketplace,
                       current_offer.canonical_url,
                       current_offer.verified_at,
                       rolling.seven_day_min_price::text
                FROM subscription s
                JOIN tracked_product p ON p.id = s.tracked_product_id
                LEFT JOIN LATERAL (
                    SELECT current_state.public_price,
                           ml.marketplace,
                           ml.canonical_url,
                           current_state.verified_at
                    FROM marketplace_listing ml
                    JOIN listing_state current_state
                      ON current_state.marketplace_listing_id = ml.id
                    WHERE ml.tracked_product_id = p.id
                      AND ml.active = TRUE
                      AND ml.quality_status = 'trusted'
                      AND current_state.quality_status = 'trusted'
                      AND current_state.public_price IS NOT NULL
                      AND current_state.available IS NOT FALSE
                    ORDER BY current_state.public_price ASC,
                             current_state.verified_at DESC
                    LIMIT 1
                ) current_offer ON TRUE
                LEFT JOIN LATERAL (
                    SELECT MIN(pe.public_price) AS seven_day_min_price
                    FROM price_event pe
                    WHERE pe.tracked_product_id = p.id
                      AND pe.quality_status = 'trusted'
                      AND pe.public_price IS NOT NULL
                      AND pe.verified_at >= NOW() - INTERVAL '7 days'
                ) rolling ON TRUE
                WHERE s.user_id = %s
                ORDER BY s.created_at ASC
                """,
                (user_id,),
            )
            rows = await cursor.fetchall()
        result: list[UserProductSummary] = []
        for row in rows:
            subscription = _subscription_from_row(row[:4])
            product = _tracked_product_from_row(row[4:13])
            result.append(
                UserProductSummary(
                    subscription=subscription,
                    product=product,
                    public_price=str(row[13]) if row[13] is not None else None,
                    marketplace=str(row[14]) if row[14] is not None else None,
                    listing_url=str(row[15]) if row[15] is not None else None,
                    verified_at=row[16],  # type: ignore[arg-type]
                    seven_day_min_price=(
                        str(row[17]) if row[17] is not None else None
                    ),
                )
            )
        return tuple(result)

    async def save_pending_confirmation(
        self,
        *,
        confirmation_id: str,
        user_id: int,
        raw_input: str,
        plan: SearchPlan,
        ttl_minutes: int = 15,
    ) -> None:
        if ttl_minutes <= 0:
            raise ValueError("ttl_minutes must be positive")
        expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        payload = json.dumps(search_plan_to_payload(plan), ensure_ascii=False, sort_keys=True)
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                INSERT INTO pending_product_confirmation (
                    id, user_id, raw_input, search_plan, expires_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (id) DO UPDATE
                SET raw_input = EXCLUDED.raw_input,
                    search_plan = EXCLUDED.search_plan,
                    expires_at = EXCLUDED.expires_at
                """,
                (confirmation_id, user_id, raw_input, payload, expires_at),
            )
            await connection.commit()

    async def get_pending_confirmation(
        self,
        confirmation_id: str,
        *,
        consume: bool = False,
    ) -> tuple[int, str, SearchPlan] | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT user_id, raw_input, search_plan::text
                FROM pending_product_confirmation
                WHERE id = %s AND expires_at > NOW()
                """,
                (confirmation_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            if consume:
                await connection.execute(
                    "DELETE FROM pending_product_confirmation WHERE id = %s",
                    (confirmation_id,),
                )
                await connection.commit()
        return int(row[0]), str(row[1]), search_plan_from_payload(_json_object(row[2]))

    async def prune_price_events(self, *, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=8)
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "DELETE FROM price_event WHERE verified_at < %s RETURNING id",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            await connection.commit()
        return len(rows)

    async def disable_user_delivery(self, user_id: int) -> None:
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                UPDATE telegram_user
                SET delivery_enabled = FALSE, updated_at = NOW()
                WHERE id = %s
                """,
                (user_id,),
            )
            await connection.commit()

    async def get_user_id_by_telegram(self, telegram_user_id: int) -> int | None:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                "SELECT id FROM telegram_user WHERE telegram_user_id = %s",
                (telegram_user_id,),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else None

    async def active_subscribers(self, product_id: int) -> list[tuple[int, int, int]]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT u.id, u.chat_id, s.id
                FROM subscription s
                JOIN telegram_user u ON u.id = s.user_id
                WHERE s.tracked_product_id = %s
                  AND s.status = 'active'
                  AND u.delivery_enabled = TRUE
                """,
                (product_id,),
            )
            rows = await cursor.fetchall()
        return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]

    async def recent_public_prices(
        self,
        product_id: int,
        *,
        since: datetime,
    ) -> list[tuple[Decimal, datetime]]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT public_price, verified_at
                FROM price_event
                WHERE tracked_product_id = %s
                  AND quality_status = 'trusted'
                  AND public_price IS NOT NULL
                  AND verified_at >= %s
                ORDER BY verified_at ASC
                """,
                (product_id, since),
            )
            rows = await cursor.fetchall()
        return [(Decimal(str(row[0])), row[1]) for row in rows]  # type: ignore[list-item]

    async def claim_due_products(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TrackedProductRecord, ...]:
        worker = worker_id.strip()
        if not worker:
            raise ValueError("worker_id must not be empty")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        lease_until = now + timedelta(seconds=lease_seconds)

        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                WITH due AS (
                    SELECT p.id
                    FROM tracked_product p
                    LEFT JOIN worker_lease wl ON wl.tracked_product_id = p.id
                    WHERE p.lifecycle_state = 'active'
                      AND p.subscriber_count > 0
                      AND p.next_scan_at <= %s
                      AND (wl.tracked_product_id IS NULL OR wl.lease_until <= %s)
                    ORDER BY p.next_scan_at ASC, p.id ASC
                    FOR UPDATE OF p SKIP LOCKED
                    LIMIT %s
                ), leased AS (
                    INSERT INTO worker_lease (
                        tracked_product_id, worker_id, lease_until, updated_at
                    )
                    SELECT id, %s, %s, NOW()
                    FROM due
                    ON CONFLICT (tracked_product_id) DO UPDATE
                    SET worker_id = EXCLUDED.worker_id,
                        lease_until = EXCLUDED.lease_until,
                        updated_at = NOW()
                    RETURNING tracked_product_id
                )
                SELECT p.id, p.canonical_name, p.product_type, p.identity_fingerprint,
                       p.search_plan::text, p.lifecycle_state, p.subscriber_count,
                       p.next_scan_at, p.last_successful_scan_at
                FROM tracked_product p
                JOIN leased ON leased.tracked_product_id = p.id
                ORDER BY p.next_scan_at ASC, p.id ASC
                """,
                (now, now, limit, worker, lease_until),
            )
            rows = await cursor.fetchall()
            await connection.commit()
        return tuple(_tracked_product_from_row(row) for row in rows)

    async def list_known_candidates(
        self,
        product_id: int,
        marketplace: str,
    ) -> tuple[SearchCandidate, ...]:
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT ml.marketplace, ml.listing_id, ml.variation_id,
                       ml.seller_id, ml.seller_name, ml.canonical_url,
                       ml.title, ml.attributes::text, ml.taxonomy::text
                FROM marketplace_listing ml
                WHERE ml.tracked_product_id = %s
                  AND ml.marketplace = %s
                  AND ml.active = TRUE
                ORDER BY ml.id ASC
                """,
                (product_id, marketplace),
            )
            rows = await cursor.fetchall()

        candidates: list[SearchCandidate] = []
        for row in rows:
            attributes = _json_object(row[7])
            taxonomy: MarketplaceTaxonomy | None = None
            if row[8] is not None:
                raw_taxonomy = _json_object(row[8])
                taxonomy = MarketplaceTaxonomy(
                    subject_id=(
                        str(raw_taxonomy["subject_id"])
                        if raw_taxonomy.get("subject_id") is not None
                        else None
                    ),
                    parent_id=(
                        str(raw_taxonomy["parent_id"])
                        if raw_taxonomy.get("parent_id") is not None
                        else None
                    ),
                    entity=(
                        str(raw_taxonomy["entity"])
                        if raw_taxonomy.get("entity") is not None
                        else None
                    ),
                    category_path=(
                        str(raw_taxonomy["category_path"])
                        if raw_taxonomy.get("category_path") is not None
                        else None
                    ),
                )
            candidates.append(
                SearchCandidate(
                    marketplace=str(row[0]),
                    listing_id=str(row[1]),
                    variation_id=str(row[2]) if row[2] else None,
                    seller_id=str(row[3]) if row[3] is not None else None,
                    seller_name=str(row[4]) if row[4] is not None else None,
                    url=str(row[5]) if row[5] is not None else None,
                    title=str(row[6]),
                    attributes={str(key): str(value) for key, value in attributes.items()},
                    taxonomy=taxonomy,
                )
            )
        return tuple(candidates)
