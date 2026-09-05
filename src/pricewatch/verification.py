from __future__ import annotations

from pricewatch.marketplaces import (
    MarketplaceOfferAdapter,
    OfferIdentityError,
    OfferLocator,
    OfferSnapshot,
    SearchCandidate,
)
from pricewatch.matching import MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan


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
) -> OfferSnapshot:
    """Fetch the concrete offer and re-run product identity before trusting its price."""
    if candidate.marketplace != adapter.marketplace:
        raise ValueError("candidate marketplace does not match verification adapter")

    snapshot = await adapter.fetch_offer(candidate_locator(candidate))
    verified_candidate = SearchCandidate(
        marketplace=snapshot.locator.marketplace,
        listing_id=snapshot.locator.listing_id,
        variation_id=snapshot.locator.variation_id,
        seller_id=snapshot.locator.seller_id,
        title=snapshot.title,
        attributes=snapshot.attributes,
        url=snapshot.locator.url,
        price=snapshot.price,
        original_price=snapshot.original_price,
        available=snapshot.available,
        price_source=snapshot.price_source,
    )
    decision = match_candidate(plan, verified_candidate)
    if decision.status is not MatchStatus.ACCEPT:
        raise OfferIdentityError(
            f"offer verification failed product identity recheck: {decision.reason}"
        )
    return snapshot
