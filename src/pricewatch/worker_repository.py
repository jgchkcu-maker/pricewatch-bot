from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from pricewatch.marketplaces import SearchCandidate
from pricewatch.runtime_models import (
    TrackedProductRecord,
    search_plan_from_payload,
)
from pricewatch.runtime_repository import ConnectionFactory
from pricewatch.taxonomy import MarketplaceTaxonomy


def _json_object(value: object) -> dict[str, Any]:
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
    return {str(key): item for key, item in decoded.items()}


def _product_from_row(row: tuple[object, ...]) -> TrackedProductRecord:
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


def _taxonomy_from_json(value: object) -> MarketplaceTaxonomy | None:
    payload = _json_object(value)
    if not payload:
        return None
    return MarketplaceTaxonomy(
        subject_id=str(payload["subject_id"]) if payload.get("subject_id") is not None else None,
        parent_id=str(payload["parent_id"]) if payload.get("parent_id") is not None else None,
        entity=str(payload["entity"]) if payload.get("entity") is not None else None,
        category_path=(
            str(payload["category_path"])
            if payload.get("category_path") is not None
            else None
        ),
    )


class PostgresWorkerRepository:
    """Worker-facing PostgreSQL operations for claims, listings and taxonomy evidence."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def claim_due_products(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TrackedProductRecord, ...]:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if limit <= 0 or lease_seconds <= 0:
            raise ValueError("limit and lease_seconds must be positive")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        lease_until = now + timedelta(seconds=lease_seconds)
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT p.id, p.canonical_name, p.product_type, p.identity_fingerprint,
                       p.search_plan::text, p.lifecycle_state, p.subscriber_count,
                       p.next_scan_at, p.last_successful_scan_at
                FROM tracked_product p
                LEFT JOIN worker_lease l ON l.tracked_product_id = p.id
                WHERE p.lifecycle_state = 'active'
                  AND p.subscriber_count > 0
                  AND p.next_scan_at <= %s
                  AND (l.tracked_product_id IS NULL OR l.lease_until <= %s)
                ORDER BY p.next_scan_at ASC
                FOR UPDATE OF p SKIP LOCKED
                LIMIT %s
                """,
                (now, now, limit),
            )
            rows = await cursor.fetchall()
            products = tuple(_product_from_row(row) for row in rows)
            for product in products:
                await connection.execute(
                    """
                    INSERT INTO worker_lease (
                        tracked_product_id, worker_id, lease_until, updated_at
                    )
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (tracked_product_id) DO UPDATE
                    SET worker_id = EXCLUDED.worker_id,
                        lease_until = EXCLUDED.lease_until,
                        updated_at = NOW()
                    """,
                    (product.id, worker_id, lease_until),
                )
            await connection.commit()
        return products

    async def known_listings(
        self,
        product_id: int,
        marketplace: str,
    ) -> tuple[SearchCandidate, ...]:
        normalized_marketplace = marketplace.strip().casefold()
        if not normalized_marketplace:
            raise ValueError("marketplace must not be empty")
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT marketplace, listing_id, variation_id, seller_id, seller_name,
                       canonical_url, title, attributes::text, taxonomy::text
                FROM marketplace_listing
                WHERE tracked_product_id = %s
                  AND marketplace = %s
                  AND active = TRUE
                ORDER BY last_seen_at DESC
                """,
                (product_id, normalized_marketplace),
            )
            rows = await cursor.fetchall()

        result: list[SearchCandidate] = []
        for row in rows:
            attributes_payload = _json_object(row[7])
            result.append(
                SearchCandidate(
                    marketplace=str(row[0]),
                    listing_id=str(row[1]),
                    variation_id=str(row[2]) if row[2] else None,
                    seller_id=str(row[3]) if row[3] is not None else None,
                    seller_name=str(row[4]) if row[4] is not None else None,
                    url=str(row[5]) if row[5] is not None else None,
                    title=str(row[6]),
                    attributes={
                        str(key): str(value) for key, value in attributes_payload.items()
                    },
                    taxonomy=_taxonomy_from_json(row[8]),
                )
            )
        return tuple(result)

    async def complete_scan(
        self,
        product_id: int,
        *,
        now: datetime,
        success: bool,
        interval_seconds: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if retry_after_seconds is not None and retry_after_seconds <= 0:
            raise ValueError("retry_after_seconds must be positive when supplied")
        delay = interval_seconds if success else (retry_after_seconds or max(interval_seconds, 900))
        next_scan_at = now + timedelta(seconds=delay)

        async with self._connection_factory() as connection:
            if success:
                await connection.execute(
                    """
                    UPDATE tracked_product
                    SET next_scan_at = %s,
                        last_successful_scan_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_scan_at, now, product_id),
                )
            else:
                await connection.execute(
                    """
                    UPDATE tracked_product
                    SET next_scan_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_scan_at, product_id),
                )
            await connection.execute(
                "DELETE FROM worker_lease WHERE tracked_product_id = %s",
                (product_id,),
            )
            await connection.commit()

    async def record_taxonomy_positive(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
    ) -> None:
        if product.product_type is None or candidate.taxonomy is None:
            return
        taxonomy = candidate.taxonomy
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                INSERT INTO taxonomy_evidence (
                    tracked_product_id, product_type, marketplace, listing_id,
                    subject_id, parent_id, entity, category_path,
                    strength, verified_label
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'detail_strong', TRUE)
                """,
                (
                    product.id,
                    product.product_type,
                    candidate.marketplace,
                    candidate.listing_id,
                    taxonomy.subject_id,
                    taxonomy.parent_id,
                    taxonomy.entity,
                    taxonomy.category_path,
                ),
            )
            await connection.commit()
