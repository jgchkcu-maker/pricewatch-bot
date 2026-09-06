from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from pricewatch.learning_persistence import PostgresLearningStateStore
from pricewatch.marketplaces import (
    MarketplaceAdapter,
    OfferIdentityError,
    OfferSnapshot,
    ParserDriftError,
    SearchCandidate,
)
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.offer_quality import (
    OfferQualityContext,
    OfferQualityDecision,
    OfferQualityPolicy,
    OfferQualityStatus,
    evaluate_offer_quality,
)
from pricewatch.runtime_models import TrackedProductRecord
from pricewatch.scan import candidate_identity, scan_once
from pricewatch.transport import (
    MarketplaceAccessError,
    MarketplaceRateLimitedError,
    MarketplaceTransportError,
)
from pricewatch.verification import verify_candidate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MarketplaceScanStats:
    marketplace: str
    raw_count: int
    accepted_count: int
    ambiguous_count: int
    rejected_count: int
    taxonomy_rejected_count: int
    duplicate_count: int
    verified_count: int
    identity_rejected_count: int
    quality_rejected_count: int
    quarantined_count: int
    unavailable_count: int
    trusted_count: int
    reason_code_counts: dict[str, int]


class WorkerRepository(Protocol):
    async def claim_due_products(
        self,
        *,
        worker_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> tuple[TrackedProductRecord, ...]: ...

    async def list_known_candidates(
        self,
        product_id: int,
        marketplace: str,
    ) -> tuple[SearchCandidate, ...]: ...

    async def list_trusted_price_reference(
        self,
        product_id: int,
        marketplace: str,
        *,
        since: datetime,
    ) -> tuple[Decimal, ...]: ...

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
        snapshot: OfferSnapshot,
        *,
        verified_at: datetime,
        allow_alerts: bool = True,
    ) -> Any: ...

    async def record_quarantined_offer(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        snapshot: OfferSnapshot,
        decision: OfferQualityDecision,
        *,
        verified_at: datetime,
    ) -> Any: ...

    async def record_quality_rejection(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        snapshot: OfferSnapshot,
        decision: OfferQualityDecision,
        *,
        verified_at: datetime,
    ) -> Any: ...


class LearningStore(Protocol):
    async def load_engine(self, scope_key: str) -> HybridMatchEngine: ...

    async def save_verified_update(self, scope_key: str, engine, evidence) -> None: ...


def _prior_quality_status(candidate: SearchCandidate) -> OfferQualityStatus | None:
    if candidate.quality_status is None:
        return None
    try:
        return OfferQualityStatus(candidate.quality_status)
    except ValueError:
        return None


def _scan_stats(
    marketplace: str,
    outcome,
    decisions: list[OfferQualityDecision | None],
) -> MarketplaceScanStats:
    verified = [decision for decision in decisions if decision is not None]
    reason_counts: Counter[str] = Counter()
    for decision in verified:
        reason_counts.update(reason.value for reason in decision.reason_codes)

    return MarketplaceScanStats(
        marketplace=marketplace,
        raw_count=outcome.raw_count,
        accepted_count=len(outcome.accepted),
        ambiguous_count=len(outcome.ambiguous),
        rejected_count=outcome.rejected_count,
        taxonomy_rejected_count=outcome.taxonomy_rejected_count,
        duplicate_count=outcome.duplicate_count,
        verified_count=len(verified),
        identity_rejected_count=len(decisions) - len(verified),
        quality_rejected_count=sum(
            decision.status is OfferQualityStatus.REJECTED for decision in verified
        ),
        quarantined_count=sum(
            decision.status is OfferQualityStatus.QUARANTINED for decision in verified
        ),
        unavailable_count=sum(
            decision.status is OfferQualityStatus.UNAVAILABLE for decision in verified
        ),
        trusted_count=sum(
            decision.status is OfferQualityStatus.TRUSTED for decision in verified
        ),
        reason_code_counts=dict(sorted(reason_counts.items())),
    )


def _log_scan_completed(stats: MarketplaceScanStats) -> None:
    logger.info(
        "marketplace scan completed",
        extra={
            "marketplace_scan_stats": stats,
            "marketplace": stats.marketplace,
            "raw_count": stats.raw_count,
            "accepted_count": stats.accepted_count,
            "ambiguous_count": stats.ambiguous_count,
            "rejected_count": stats.rejected_count,
            "taxonomy_rejected_count": stats.taxonomy_rejected_count,
            "duplicate_count": stats.duplicate_count,
            "verified_count": stats.verified_count,
            "identity_rejected_count": stats.identity_rejected_count,
            "quality_rejected_count": stats.quality_rejected_count,
            "quarantined_count": stats.quarantined_count,
            "unavailable_count": stats.unavailable_count,
            "trusted_count": stats.trusted_count,
            "quality_reason_counts": stats.reason_code_counts,
        },
    )


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
        quality_policy: OfferQualityPolicy | None = None,
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
        self._quality_policy = quality_policy or OfferQualityPolicy()

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
        if not success and product.last_successful_scan_at is None:
            retry_after_seconds = min(retry_after_seconds or 300, 300)
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
        trusted_prices = list(
            await self._repository.list_trusted_price_reference(
                product.id,
                adapter.marketplace,
                since=now - timedelta(days=7),
            )
        )
        known = await self._repository.list_known_candidates(product.id, adapter.marketplace)
        attempted: set[tuple[str, str, str | None, str | None]] = set()
        decisions: list[OfferQualityDecision | None] = []

        for known_candidate in known:
            attempted.add(candidate_identity(known_candidate))
            decisions.append(
                await self._verify_and_record(
                    product,
                    known_candidate,
                    adapter,
                    engine,
                    trusted_prices=trusted_prices,
                    scope_key=scope_key,
                    source_queries=(),
                    now=now,
                    allow_alerts=allow_alerts,
                )
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
            decisions.append(
                await self._verify_and_record(
                    product,
                    candidate,
                    adapter,
                    engine,
                    trusted_prices=trusted_prices,
                    scope_key=scope_key,
                    source_queries=outcome.queries,
                    now=now,
                    allow_alerts=allow_alerts,
                )
            )

        _log_scan_completed(_scan_stats(adapter.marketplace, outcome, decisions))

    async def _verify_and_record(
        self,
        product: TrackedProductRecord,
        candidate: SearchCandidate,
        adapter: MarketplaceAdapter,
        engine: HybridMatchEngine,
        *,
        trusted_prices: list[Decimal],
        scope_key: str,
        source_queries: tuple[str, ...],
        now: datetime,
        allow_alerts: bool,
    ) -> OfferQualityDecision | None:
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
            return None

        context = OfferQualityContext(
            trusted_prices=tuple(trusted_prices),
            prior_status=_prior_quality_status(candidate),
            prior_confirmation_count=candidate.quality_observation_count,
        )
        decision = evaluate_offer_quality(
            product.search_plan,
            candidate,
            snapshot,
            context,
            self._quality_policy,
        )

        if decision.status is OfferQualityStatus.TRUSTED:
            await self._verified_store.record_verified_offer(
                product,
                candidate,
                snapshot,
                verified_at=now,
                allow_alerts=allow_alerts,
            )
            trusted_prices.append(snapshot.price)
            if product.product_type is not None and candidate.taxonomy is not None:
                await self._repository.record_taxonomy_positive(product, candidate)
        elif decision.status is OfferQualityStatus.QUARANTINED:
            await self._verified_store.record_quarantined_offer(
                product,
                candidate,
                snapshot,
                decision,
                verified_at=now,
            )
        else:
            await self._verified_store.record_quality_rejection(
                product,
                candidate,
                snapshot,
                decision,
                verified_at=now,
            )

        return decision
