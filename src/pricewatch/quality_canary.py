from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal

from pricewatch.marketplaces import MarketplaceAdapter, OfferIdentityError
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.offer_quality import (
    OfferQualityContext,
    OfferQualityPolicy,
    OfferQualityStatus,
    evaluate_offer_quality,
)
from pricewatch.scan import scan_once
from pricewatch.search_plan import SearchPlan
from pricewatch.verification import verify_candidate


@dataclass(frozen=True, slots=True)
class QualityCanaryResult:
    marketplace: str
    raw_count: int
    accepted_count: int
    ambiguous_count: int
    search_rejected_count: int
    taxonomy_rejected_count: int
    duplicate_count: int
    verified_count: int
    identity_rejected_count: int
    trusted_count: int
    quarantined_count: int
    quality_rejected_count: int
    unavailable_count: int
    reason_code_counts: dict[str, int]

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


async def run_marketplace_canary(
    plan: SearchPlan,
    adapter: MarketplaceAdapter,
    *,
    limit: int = 10,
    trusted_prices: tuple[Decimal, ...] = (),
    quality_policy: OfferQualityPolicy | None = None,
) -> QualityCanaryResult:
    """Run search, exact verification and quality classification without persistence.

    The canary deliberately accepts no repository or store object. It can mutate only
    the in-memory matching engine used during this one diagnostic pass.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    engine = HybridMatchEngine()
    outcome = await scan_once(
        plan,
        adapter,
        cycle=0,
        limit=limit,
        match_engine=engine,
    )
    policy = quality_policy or OfferQualityPolicy()
    reference_prices = tuple(trusted_prices)
    reason_counts: Counter[str] = Counter()
    identity_rejected_count = 0
    trusted_count = 0
    quarantined_count = 0
    quality_rejected_count = 0
    unavailable_count = 0
    verified_count = 0

    for candidate in (*outcome.accepted, *outcome.ambiguous):
        try:
            snapshot = await verify_candidate(
                plan,
                candidate,
                adapter,
                match_engine=engine,
                source_queries=outcome.queries,
            )
        except OfferIdentityError:
            identity_rejected_count += 1
            continue

        verified_count += 1
        decision = evaluate_offer_quality(
            plan,
            candidate,
            snapshot,
            OfferQualityContext(trusted_prices=reference_prices),
            policy,
        )
        reason_counts.update(reason.value for reason in decision.reason_codes)
        if decision.status is OfferQualityStatus.TRUSTED:
            trusted_count += 1
        elif decision.status is OfferQualityStatus.QUARANTINED:
            quarantined_count += 1
        elif decision.status is OfferQualityStatus.UNAVAILABLE:
            unavailable_count += 1
        else:
            quality_rejected_count += 1

    return QualityCanaryResult(
        marketplace=adapter.marketplace,
        raw_count=outcome.raw_count,
        accepted_count=len(outcome.accepted),
        ambiguous_count=len(outcome.ambiguous),
        search_rejected_count=outcome.rejected_count,
        taxonomy_rejected_count=outcome.taxonomy_rejected_count,
        duplicate_count=outcome.duplicate_count,
        verified_count=verified_count,
        identity_rejected_count=identity_rejected_count,
        trusted_count=trusted_count,
        quarantined_count=quarantined_count,
        quality_rejected_count=quality_rejected_count,
        unavailable_count=unavailable_count,
        reason_code_counts=dict(sorted(reason_counts.items())),
    )
