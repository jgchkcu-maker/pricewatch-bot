from __future__ import annotations

from dataclasses import dataclass

from pricewatch.marketplaces import MarketplaceSearchAdapter, SearchCandidate
from pricewatch.matching import MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan, query_for_cycle


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    marketplace: str
    query: str
    raw_count: int
    accepted: tuple[SearchCandidate, ...]
    ambiguous: tuple[SearchCandidate, ...]
    rejected_count: int
    duplicate_count: int


def candidate_identity(candidate: SearchCandidate) -> tuple[str, str, str | None, str | None]:
    return (
        candidate.marketplace,
        candidate.listing_id,
        candidate.variation_id,
        candidate.seller_id,
    )


async def scan_once(
    plan: SearchPlan,
    adapter: MarketplaceSearchAdapter,
    *,
    cycle: int,
    limit: int = 100,
) -> ScanOutcome:
    """Run one cheap discovery scan and classify every unique search candidate."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    query = query_for_cycle(plan, cycle)
    raw_candidates = await adapter.search(query, limit=limit)

    unique_candidates: list[SearchCandidate] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    duplicate_count = 0
    for candidate in raw_candidates:
        identity = candidate_identity(candidate)
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        unique_candidates.append(candidate)

    accepted: list[SearchCandidate] = []
    ambiguous: list[SearchCandidate] = []
    rejected_count = 0

    for candidate in unique_candidates:
        decision = match_candidate(plan, candidate)
        if decision.status is MatchStatus.ACCEPT:
            accepted.append(candidate)
        elif decision.status is MatchStatus.AMBIGUOUS:
            ambiguous.append(candidate)
        else:
            rejected_count += 1

    return ScanOutcome(
        marketplace=adapter.marketplace,
        query=query,
        raw_count=len(raw_candidates),
        accepted=tuple(accepted),
        ambiguous=tuple(ambiguous),
        rejected_count=rejected_count,
        duplicate_count=duplicate_count,
    )
