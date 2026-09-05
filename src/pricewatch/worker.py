from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from pricewatch.learning_persistence import PostgresLearningStateStore
from pricewatch.marketplaces import (
    MarketplaceAdapter,
    OfferIdentityError,
    ParserDriftError,
    SearchCandidate,
)
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.scan import candidate_identity, scan_once
from pricewatch.transport import (
    MarketplaceAccessError,
    MarketplaceRateLimitedError,
    MarketplaceTransportError,
)
from pricewatch.verification import verify_candidate


class WorkerRepository(Protocol):
    async def claim_due_products(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TrackedProductRecord, ...]: ...

    async def known_listings(
        self,
        product_id: int,
        marketplace: str,
    ) -> tuple[SearchCandidate, ...]: ...

    async def complete_scan(
        self,
        product_id: int,
        *,
        now: datetime,
        success: bool,
        interval_seconds: int,
        retry_after_seconds: int | None = None,
    ) -> None: ...

    async def record_taxonomy_positive(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
    ) -> None: ...


class VerifiedOfferRecorder(Protocol):
    async def record_verified_offer(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        snapshot: Any,
        *,
        verified_at: datetime,
        allow_alerts: bool = True,
    ) -> Any: ...


class LearningStore(Protocol):
    async def load_engine(self, scope_key: str) -> HybridMatchEngine: ...

    async def save_verified_update(self, scope_key: str, engine, evidence) -> None: ...


class PriceWorker:
    def __init__(
        self,
        *,
        repository: WorkerRepository,
        verified_store: VerifiedOfferRecorder,
        learning_store: LearningStore | PostgresLearningStateStore,
        adapters: Mapping[str, MarketplaceAdapter],
        worker_id: str,
        batch_size: int = 20,
        lease_seconds: int = 180,
        interval_seconds: int = 240,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if batch_size <= 0 or lease_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("worker timing and batch settings must be positive")
        self._repository = repository
        self._verified_store = verified_store
        self._learning_store = learning_store
        self._adapters = dict(adapters)
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds

    async def run_once(self, now: datetime) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        products = await self._repository.claim_due_products(
            worker_id=self._worker_id,
            now=now,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        for product in products:
            await self._process_product(product, now)
        return len(products)

    async def _process_product(self, product: TrackedProductRecord, now: datetime) -> None:
        scope_key = f"product:{product.identity_fingerprint}"
        engine = await self._learning_store.load_engine(scope_key)
        successful_marketplaces = 0
        retry_after_seconds: int | None = None

        for adapter in self._adapters.values():
            try:
                await self._scan_marketplace(
                    product,
                    adapter,
                    engine,
                    scope_key=scope_key,
                    now=now,
                )
                successful_marketplaces += 1
            except MarketplaceRateLimitedError as exc:
                retry_after_seconds = max(
                    retry_after_seconds or 0,
                    exc.retry_after_seconds or 600,
                )
            except (MarketplaceAccessError, MarketplaceTransportError, ParserDriftError):
                retry_after_seconds = max(retry_after_seconds or 0, 900)

        success = successful_marketplaces > 0
        await self._repository.complete_scan(
            product.id,
            now=now,
            success=success,
            interval_seconds=self._interval_seconds,
            retry_after_seconds=None if success else retry_after_seconds,
        )

    async def _scan_marketplace(
        self,
        product: TrackedProductRecord,
        adapter: MarketplaceAdapter,
        engine: HybridMatchEngine,
        *,
        scope_key: str,
        now: datetime,
    ) -> None:
        allow_alerts = product.last_successful_scan_at is not None
        known = await self._repository.known_listings(product.id, adapter.marketplace)
        attempted: set[tuple[str, str, str | None, str | None]] = set()

        for known_candidate in known:
            attempted.add(candidate_identity(known_candidate))
            await self._verify_and_record(
                product,
                known_candidate,
                adapter,
                engine,
                scope_key=scope_key,
                source_queries=(),
                now=now,
                allow_alerts=allow_alerts,
            )

        cycle = int(now.timestamp() // self._interval_seconds)
        outcome = await scan_once(
            product.search_plan,
            adapter,
            cycle=cycle,
            match_engine=engine,
        )
        for candidate in (*outcome.accepted, *outcome.ambiguous):
            identity = candidate_identity(candidate)
            if identity in attempted:
                continue
            attempted.add(identity)
            await self._verify_and_record(
                product,
                candidate,
                adapter,
                engine,
                scope_key=scope_key,
                source_queries=outcome.queries,
                now=now,
                allow_alerts=allow_alerts,
            )

    async def _verify_and_record(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        adapter: MarketplaceAdapter,
        engine: HybridMatchEngine,
        *,
        scope_key: str,
        source_queries: tuple[str, ...],
        now: datetime,
        allow_alerts: bool,
    ) -> bool:
        try:
            snapshot = await verify_candidate(
                product.search_plan,
                candidate,
                adapter,
                match_engine=engine,
                source_queries=source_queries,
                learning_store=self._learning_store,
                learning_scope_key=scope_key,
            )
        except OfferIdentityError:
            return False

        await self._verified_store.record_verified_offer(
            product,
            candidate,
            snapshot,
            verified_at=now,
            allow_alerts=allow_alerts,
        )
        if product.product_type is not None and candidate.taxonomy is not None:
            await self._repository.record_taxonomy_positive(product, candidate)
        return True
