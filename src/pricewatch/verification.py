from __future__ import annotations

from pricewatch.marketplaces import (
    MarketplaceOfferAdapter,
    OfferIdentityError,
    OfferLocator,
    OfferSnapshot,
    SearchCandidate,
)
from pricewatch.match_learning import HybridMatchEngine, LearningEvidenceSource
from pricewatch.matching import MatchStatus
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import TaxonomyGateStatus, TaxonomyObservationAccumulator


def candidate_locator(candidate: SearchCandidate) -> OfferLocator:
    return OfferLocator(
        marketplace=candidate.marketplace,
        listing_id=candidate.listing_id,
        seller_id=candidate.seller_id,
        variation_id=candidate.variation_id,
        url=candidate.url,
    )


async def verify_candidate(
    plan: SearchPlan,
    candidate: SearchCandidate,
    adapter: MarketplaceOfferAdapter,
    *,
    match_engine: HybridMatchEngine | None = None,
    source_queries: tuple[str, ...] = (),
    taxonomy_observations: TaxonomyObservationAccumulator | None = None,
) -> OfferSnapshot:
    """Fetch the concrete offer, recheck identity, and learn only from verified evidence."""
    if candidate.marketplace != adapter.marketplace:
        raise ValueError("candidate marketplace does not match verification adapter")

    engine = match_engine or HybridMatchEngine()
    snapshot = await adapter.fetch_offer(candidate_locator(candidate))
    verified_candidate = SearchCandidate(
        marketplace=snapshot.locator.marketplace,
        listing_id=snapshot.locator.listing_id,
        variation_id=snapshot.locator.variation_id,
        seller_id=snapshot.locator.seller_id,
        title=snapshot.title,
        attributes=snapshot.attributes,
        taxonomy=candidate.taxonomy,
        url=snapshot.locator.url,
        price=snapshot.price,
        original_price=snapshot.original_price,
        available=snapshot.available,
        price_source=snapshot.price_source,
    )
    decision = engine.classify(
        plan,
        verified_candidate,
        taxonomy_status=TaxonomyGateStatus.UNKNOWN,
        source_queries=source_queries,
    )
    matched = decision.status is MatchStatus.ACCEPT
    engine.learn_verified(
        plan,
        verified_candidate,
        decision,
        matched=matched,
        source=LearningEvidenceSource.DETAIL,
        source_queries=source_queries,
    )

    if not matched:
        raise OfferIdentityError(
            f"offer verification failed product identity recheck: {decision.reason}"
        )

    if (
        taxonomy_observations is not None
        and plan.product_type is not None
        and candidate.taxonomy is not None
    ):
        taxonomy_observations.observe(plan.product_type, candidate)

    return snapshot
