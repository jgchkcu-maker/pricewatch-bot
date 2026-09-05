from __future__ import annotations

from typing import Protocol

from pricewatch.marketplaces import (
    MarketplaceOfferAdapter,
    OfferIdentityError,
    OfferLocator,
    OfferSnapshot,
    SearchCandidate,
)
from pricewatch.match_learning import (
    HybridMatchEngine,
    LearningEvidence,
    LearningEvidenceSource,
)
from pricewatch.matching import MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import TaxonomyGateStatus, TaxonomyObservationAccumulator


class VerifiedLearningStore(Protocol):
    async def save_verified_update(
        self,
        scope_key: str,
        engine: HybridMatchEngine,
        evidence: LearningEvidence,
    ) -> None: ...


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
    learning_store: VerifiedLearningStore | None = None,
    learning_scope_key: str | None = None,
) -> OfferSnapshot:
    """Fetch the concrete offer, recheck identity, and learn only from verified evidence.

    The detail card's deterministic identity evidence is authoritative. The online scorer is
    allowed to be uncalibrated and is trained toward verified truth; it never supplies its own
    training label. Missing detail evidence is inconclusive and never becomes a negative label.
    """
    if candidate.marketplace != adapter.marketplace:
        raise ValueError("candidate marketplace does not match verification adapter")
    if (learning_store is None) != (learning_scope_key is None):
        raise ValueError("learning_store and learning_scope_key must be provided together")
    if learning_scope_key is not None and not learning_scope_key.strip():
        raise ValueError("learning_scope_key must not be empty")

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
    deterministic = match_candidate(plan, verified_candidate)
    decision = engine.classify(
        plan,
        verified_candidate,
        taxonomy_status=TaxonomyGateStatus.UNKNOWN,
        source_queries=source_queries,
    )

    # Exact detail verification supplies the label. Probability is evidence, not authority.
    # Hybrid hard vetoes remain authoritative because they encode contradictions such as an
    # identifier or capacity mismatch that a soft scorer must never override.
    matched = deterministic.status is MatchStatus.ACCEPT and not decision.hard_vetoes
    contradicted = deterministic.status is MatchStatus.REJECT or bool(decision.hard_vetoes)

    if matched:
        engine.learn_verified(
            plan,
            verified_candidate,
            decision,
            matched=True,
            source=LearningEvidenceSource.DETAIL,
            source_queries=source_queries,
        )
    elif contradicted:
        engine.learn_verified(
            plan,
            verified_candidate,
            decision,
            matched=False,
            source=LearningEvidenceSource.DETAIL,
            source_queries=source_queries,
        )
    else:
        raise OfferIdentityError(
            "offer verification failed product identity recheck: detail evidence is ambiguous"
        )

    evidence = engine.evidence[-1]
    if learning_store is not None and learning_scope_key is not None:
        await learning_store.save_verified_update(
            learning_scope_key,
            engine,
            evidence,
        )

    if not matched:
        reason = decision.hard_vetoes[0] if decision.hard_vetoes else deterministic.reason
        raise OfferIdentityError(f"offer verification failed product identity recheck: {reason}")

    if (
        taxonomy_observations is not None
        and plan.product_type is not None
        and candidate.taxonomy is not None
    ):
        taxonomy_observations.observe(plan.product_type, candidate)

    return snapshot
