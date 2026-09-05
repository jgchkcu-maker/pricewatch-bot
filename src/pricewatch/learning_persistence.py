from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any, Protocol

from pricewatch.marketplaces import SearchCandidate
from pricewatch.match_learning import (
    HardNegative,
    HardNegativeBucket,
    HybridMatchEngine,
    LearningEvidence,
    OnlineMatchModel,
    QueryPerformance,
    UncertainMatch,
)
from pricewatch.taxonomy import MarketplaceTaxonomy

LEARNING_STATE_SCHEMA_VERSION = 1

_CREATE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS pricewatch_learning_state (
    scope_key TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

_CREATE_EVIDENCE_TABLE = """
CREATE TABLE IF NOT EXISTS pricewatch_learning_evidence (
    id BIGSERIAL PRIMARY KEY,
    scope_key TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    variation_id TEXT,
    source TEXT NOT NULL,
    verified_label BOOLEAN NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

_CREATE_EVIDENCE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pricewatch_learning_evidence_scope_created
ON pricewatch_learning_evidence (scope_key, created_at DESC)
""".strip()

_UPSERT_STATE = """
INSERT INTO pricewatch_learning_state (scope_key, schema_version, payload, updated_at)
VALUES (%s, %s, %s::jsonb, NOW())
ON CONFLICT (scope_key) DO UPDATE
SET schema_version = EXCLUDED.schema_version,
    payload = EXCLUDED.payload,
    updated_at = NOW()
""".strip()

_SELECT_STATE = """
SELECT schema_version, payload::text
FROM pricewatch_learning_state
WHERE scope_key = %s
""".strip()

_INSERT_EVIDENCE = """
INSERT INTO pricewatch_learning_evidence (
    scope_key,
    marketplace,
    listing_id,
    variation_id,
    source,
    verified_label,
    payload
)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
""".strip()


class _Cursor(Protocol):
    async def fetchone(self) -> tuple[object, ...] | None: ...


class _Connection(Protocol):
    async def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _Cursor: ...

    async def commit(self) -> None: ...


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[_Connection]: ...


def _decimal_to_json(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _decimal_from_json(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _taxonomy_to_payload(taxonomy: MarketplaceTaxonomy | None) -> dict[str, object] | None:
    if taxonomy is None:
        return None
    return {
        "subject_id": taxonomy.subject_id,
        "parent_id": taxonomy.parent_id,
        "entity": taxonomy.entity,
        "category_path": taxonomy.category_path,
    }


def _taxonomy_from_payload(payload: object) -> MarketplaceTaxonomy | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("candidate taxonomy state must be an object")
    return MarketplaceTaxonomy(
        subject_id=payload.get("subject_id"),
        parent_id=payload.get("parent_id"),
        entity=payload.get("entity"),
        category_path=payload.get("category_path"),
    )


def _candidate_to_payload(candidate: SearchCandidate) -> dict[str, object]:
    return {
        "marketplace": candidate.marketplace,
        "listing_id": candidate.listing_id,
        "title": candidate.title,
        "attributes": dict(candidate.attributes),
        "taxonomy": _taxonomy_to_payload(candidate.taxonomy),
        "url": candidate.url,
        "variation_id": candidate.variation_id,
        "seller_id": candidate.seller_id,
        "seller_name": candidate.seller_name,
        "price": _decimal_to_json(candidate.price),
        "original_price": _decimal_to_json(candidate.original_price),
        "available": candidate.available,
        "price_source": candidate.price_source,
    }


def _candidate_from_payload(payload: object) -> SearchCandidate:
    if not isinstance(payload, Mapping):
        raise ValueError("candidate state must be an object")
    attributes = payload.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise ValueError("candidate attributes state must be an object")
    return SearchCandidate(
        marketplace=str(payload["marketplace"]),
        listing_id=str(payload["listing_id"]),
        title=str(payload["title"]),
        attributes={str(key): str(value) for key, value in attributes.items()},
        taxonomy=_taxonomy_from_payload(payload.get("taxonomy")),
        url=str(payload["url"]) if payload.get("url") is not None else None,
        variation_id=(
            str(payload["variation_id"])
            if payload.get("variation_id") is not None
            else None
        ),
        seller_id=(
            str(payload["seller_id"]) if payload.get("seller_id") is not None else None
        ),
        seller_name=(
            str(payload["seller_name"])
            if payload.get("seller_name") is not None
            else None
        ),
        price=_decimal_from_json(payload.get("price")),
        original_price=_decimal_from_json(payload.get("original_price")),
        available=(
            bool(payload["available"]) if payload.get("available") is not None else None
        ),
        price_source=(
            str(payload["price_source"])
            if payload.get("price_source") is not None
            else None
        ),
    )


def encode_engine_state(engine: HybridMatchEngine) -> dict[str, Any]:
    query_stats = {
        query: {
            "runs": stats.runs,
            "candidate_ids": sorted(stats.candidate_ids),
            "accepted_ids": sorted(stats.accepted_ids),
            "verified_matches": sorted(stats.verified_matches),
            "verified_rejects": sorted(stats.verified_rejects),
        }
        for query, stats in engine.query_performance._stats.items()
    }
    uncertain = [
        {
            "product_name": item.product_name,
            "candidate": _candidate_to_payload(item.candidate),
            "probability": item.probability,
            "priority": item.priority,
            "source_queries": list(item.source_queries),
        }
        for item in engine.uncertain_queue.items()
    ]
    hard_negatives = [
        {
            "candidate": _candidate_to_payload(item.candidate),
            "bucket": item.bucket.value,
            "reason": item.reason,
        }
        for item in engine.hard_negatives
    ]
    return {
        "schema_version": LEARNING_STATE_SCHEMA_VERSION,
        "accept_threshold": engine.accept_threshold,
        "reject_threshold": engine.reject_threshold,
        "evidence_limit": engine.evidence.maxlen,
        "model": {
            "learning_rate": engine.model.learning_rate,
            "weights": dict(engine.model.weights),
        },
        "query_performance": query_stats,
        "uncertain_queue": uncertain,
        "hard_negatives": hard_negatives,
    }


def _require_mapping(payload: object, field: str) -> Mapping[object, object]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field} must be an object")
    return payload


def decode_engine_state(payload: Mapping[str, Any]) -> HybridMatchEngine:
    version = payload.get("schema_version")
    if version != LEARNING_STATE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported learning state schema: {version}; "
            f"expected {LEARNING_STATE_SCHEMA_VERSION}"
        )

    model_payload = _require_mapping(payload.get("model"), "model")
    learning_rate = float(model_payload.get("learning_rate", 0.05))
    model = OnlineMatchModel(learning_rate=learning_rate)
    weights = _require_mapping(model_payload.get("weights"), "model.weights")
    expected_weight_keys = set(model.weights)
    restored_weight_keys = {str(key) for key in weights}
    if restored_weight_keys != expected_weight_keys:
        raise ValueError("learning model weight schema does not match current model")
    model.weights = {str(key): float(value) for key, value in weights.items()}

    engine = HybridMatchEngine(
        accept_threshold=float(payload.get("accept_threshold", 0.98)),
        reject_threshold=float(payload.get("reject_threshold", 0.05)),
        evidence_limit=int(payload.get("evidence_limit", 4096)),
        model=model,
    )

    query_payload = _require_mapping(
        payload.get("query_performance", {}),
        "query_performance",
    )
    for raw_query, raw_stats in query_payload.items():
        stats_payload = _require_mapping(raw_stats, "query_performance entry")
        engine.query_performance._stats[str(raw_query)] = QueryPerformance(
            runs=int(stats_payload.get("runs", 0)),
            candidate_ids={str(value) for value in stats_payload.get("candidate_ids", [])},
            accepted_ids={str(value) for value in stats_payload.get("accepted_ids", [])},
            verified_matches={
                str(value) for value in stats_payload.get("verified_matches", [])
            },
            verified_rejects={
                str(value) for value in stats_payload.get("verified_rejects", [])
            },
        )

    uncertain_payload = payload.get("uncertain_queue", [])
    if not isinstance(uncertain_payload, list):
        raise ValueError("uncertain_queue must be an array")
    for raw_item in uncertain_payload:
        item_payload = _require_mapping(raw_item, "uncertain_queue item")
        candidate = _candidate_from_payload(item_payload.get("candidate"))
        item = UncertainMatch(
            product_name=str(item_payload["product_name"]),
            candidate=candidate,
            probability=float(item_payload["probability"]),
            priority=float(item_payload["priority"]),
            source_queries=tuple(str(value) for value in item_payload.get("source_queries", [])),
        )
        key = (candidate.marketplace, candidate.listing_id, candidate.variation_id)
        engine.uncertain_queue._items[key] = item

    negatives_payload = payload.get("hard_negatives", [])
    if not isinstance(negatives_payload, list):
        raise ValueError("hard_negatives must be an array")
    for raw_item in negatives_payload:
        item_payload = _require_mapping(raw_item, "hard_negatives item")
        candidate = _candidate_from_payload(item_payload.get("candidate"))
        bucket = HardNegativeBucket(str(item_payload["bucket"]))
        negative = HardNegative(
            candidate=candidate,
            bucket=bucket,
            reason=str(item_payload["reason"]),
        )
        engine.hard_negatives.append(negative)
        engine._hard_negative_keys.add(
            (candidate.marketplace, candidate.listing_id, candidate.variation_id, bucket)
        )

    return engine


def _evidence_payload(evidence: LearningEvidence) -> dict[str, object]:
    return {
        "product_name": evidence.product_name,
        "features": evidence.features.as_mapping(),
        "probability": evidence.probability,
        "decision": evidence.decision.value,
        "reason": evidence.reason,
        "source_queries": list(evidence.source_queries),
    }


def _validate_scope_key(scope_key: str) -> str:
    normalized = scope_key.strip()
    if not normalized:
        raise ValueError("scope_key must not be empty")
    return normalized


def _serialize_state(payload: Mapping[str, Any]) -> str:
    version = payload.get("schema_version")
    if version != LEARNING_STATE_SCHEMA_VERSION:
        raise ValueError("cannot persist unsupported learning state schema")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _serialize_verified_evidence(evidence: LearningEvidence) -> str:
    if evidence.verified_label is None:
        raise ValueError("only verified learning evidence may be persisted")
    return json.dumps(
        _evidence_payload(evidence),
        ensure_ascii=False,
        sort_keys=True,
    )


async def _write_state(
    connection: _Connection,
    scope: str,
    payload: Mapping[str, Any],
) -> None:
    await connection.execute(
        _UPSERT_STATE,
        (scope, LEARNING_STATE_SCHEMA_VERSION, _serialize_state(payload)),
    )


async def _write_verified_evidence(
    connection: _Connection,
    scope: str,
    evidence: LearningEvidence,
) -> None:
    await connection.execute(
        _INSERT_EVIDENCE,
        (
            scope,
            evidence.marketplace,
            evidence.listing_id,
            evidence.variation_id,
            evidence.source.value,
            evidence.verified_label,
            _serialize_verified_evidence(evidence),
        ),
    )


class PostgresLearningStateStore:
    """Persist one learning-engine state per scope without touching the search hot path.

    `connection_factory` is intentionally driver-neutral. It must yield an async connection with
    `execute()` and `commit()` methods, which makes the store compatible with a psycopg-style
    connection factory while keeping the core package dependency-free.
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def initialize(self) -> None:
        async with self._connection_factory() as connection:
            await connection.execute(_CREATE_STATE_TABLE)
            await connection.execute(_CREATE_EVIDENCE_TABLE)
            await connection.execute(_CREATE_EVIDENCE_INDEX)
            await connection.commit()

    async def save(self, scope_key: str, payload: Mapping[str, Any]) -> None:
        scope = _validate_scope_key(scope_key)
        async with self._connection_factory() as connection:
            await _write_state(connection, scope, payload)
            await connection.commit()

    async def load(self, scope_key: str) -> dict[str, Any] | None:
        scope = _validate_scope_key(scope_key)
        async with self._connection_factory() as connection:
            cursor = await connection.execute(_SELECT_STATE, (scope,))
            row = await cursor.fetchone()
        if row is None:
            return None

        row_version, raw_payload = row
        if int(row_version) != LEARNING_STATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported persisted learning state schema: {row_version}")
        if isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        elif isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        else:
            raise ValueError("persisted learning state payload must be JSON object or text")
        if not isinstance(payload, dict):
            raise ValueError("persisted learning state root must be an object")
        if payload.get("schema_version") != int(row_version):
            raise ValueError("persisted learning state schema metadata is inconsistent")
        return payload

    async def load_engine(self, scope_key: str) -> HybridMatchEngine:
        payload = await self.load(scope_key)
        if payload is None:
            return HybridMatchEngine()
        return decode_engine_state(payload)

    async def append_verified_evidence(
        self,
        scope_key: str,
        evidence: LearningEvidence,
    ) -> None:
        scope = _validate_scope_key(scope_key)
        async with self._connection_factory() as connection:
            await _write_verified_evidence(connection, scope, evidence)
            await connection.commit()

    async def save_verified_update(
        self,
        scope_key: str,
        engine: HybridMatchEngine,
        evidence: LearningEvidence,
    ) -> None:
        """Atomically persist updated adaptive state and its verified provenance record."""
        scope = _validate_scope_key(scope_key)
        payload = encode_engine_state(engine)
        async with self._connection_factory() as connection:
            await _write_state(connection, scope, payload)
            await _write_verified_evidence(connection, scope, evidence)
            await connection.commit()
