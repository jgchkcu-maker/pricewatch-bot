from __future__ import annotations

from dataclasses import dataclass

from pricewatch.marketplaces import MarketplaceSearchAdapter, SearchCandidate
from pricewatch.matching import MatchStatus, match_candidate
from pricewatch.search_plan import SearchPlan, queries_for_cycle
from pricewatch.taxonomy import (
    TaxonomyGateStatus,
    TaxonomyObservationAccumulator,
    TaxonomyRegistry,
    taxonomy_gate,
)


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    marketplace: str
    queries: tuple[str, ...]
    raw_count: int
    accepted: tuple[SearchCandidate, ...]
    ambiguous: tuple[SearchCandidate, ...]
    rejected_count: int
    taxonomy_rejected_count: int
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
    taxonomy_registry: TaxonomyRegistry | None = None,
    taxonomy_observations: TaxonomyObservationAccumulator | None = None,
) -> ScanOutcome:
    """Run one fast discovery scan and classify every unique search candidate."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    registry = taxonomy_registry or TaxonomyRegistry.with_default_seeds()
    constraint = registry.resolve(plan.product_type, adapter.marketplace)
    category_path = constraint.category_path if constraint is not None else None

    queries = queries_for_cycle(plan, cycle)
    raw_candidates: list[SearchCandidate] = []
    for query in queries:
        raw_candidates.extend(
            await adapter.search(
                query,
                limit=limit,
                category_path=category_path,
            )
        )

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
    taxonomy_rejected_count = 0

    for candidate in unique_candidates:
        taxonomy_decision = taxonomy_gate(candidate, constraint)
        if taxonomy_decision.status is TaxonomyGateStatus.REJECT:
            rejected_count += 1
            taxonomy_rejected_count += 1
            continue

        decision = match_candidate(plan, candidate)
        if decision.status is MatchStatus.ACCEPT:
            accepted.append(candidate)
            if taxonomy_observations is not None and plan.product_type is not None:
                taxonomy_observations.observe(plan.product_type, candidate)
        elif decision.status is MatchStatus.AMBIGUOUS:
            ambiguous.append(candidate)
        else:
            rejected_count += 1

    return ScanOutcome(
        marketplace=adapter.marketplace,
        queries=queries,
        raw_count=len(raw_candidates),
        accepted=tuple(accepted),
        ambiguous=tuple(ambiguous),
        rejected_count=rejected_count,
        taxonomy_rejected_count=taxonomy_rejected_count,
        duplicate_count=duplicate_count,
    )
