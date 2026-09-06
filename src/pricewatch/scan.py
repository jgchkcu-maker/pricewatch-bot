from __future__ import annotations

from dataclasses import dataclass

from pricewatch.marketplaces import MarketplaceSearchAdapter, SearchCandidate
from pricewatch.match_learning import HybridMatchEngine
from pricewatch.matching import MatchStatus
from pricewatch.search_plan import SearchPlan, queries_for_cycle
from pricewatch.taxonomy import (
    TaxonomyGateStatus,
    TaxonomyObservationAccumulator,
    TaxonomyRegistry,
    taxonomy_gate,
)
from pricewatch.transport import (
    MarketplaceAccessError,
    MarketplaceRateLimitedError,
    MarketplaceTransportError,
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


def _queries_for_scan(
    plan: SearchPlan,
    cycle: int,
    engine: HybridMatchEngine,
) -> tuple[str, ...]:
    scheduled = queries_for_cycle(plan, cycle)
    if len(scheduled) == 1 or not plan.aliases:
        return scheduled

    # queries_for_cycle adds one alias every second cycle. Keep that cadence, but once verified
    # evidence exists let the query learner choose the useful alias while still exploring.
    alias_slot = (cycle + 1) // 2 - 1
    selected = engine.query_performance.select_alias(plan.aliases, slot=alias_slot)
    if selected is None or selected == plan.primary_query:
        return (plan.primary_query,)
    return (plan.primary_query, selected)


def _apply_marketplace_query_budget(
    queries: tuple[str, ...],
    *,
    cycle: int,
    adapter: MarketplaceSearchAdapter,
) -> tuple[str, ...]:
    raw_budget = getattr(adapter, "max_search_queries_per_scan", None)
    if raw_budget is None:
        return queries
    if isinstance(raw_budget, bool) or not isinstance(raw_budget, int) or raw_budget <= 0:
        raise ValueError("max_search_queries_per_scan must be a positive integer")
    if len(queries) <= raw_budget:
        return queries
    if raw_budget == 1:
        # Scheduled alias cycles contain (primary, alias). Alternate naturally:
        # even cycles keep the primary-only schedule, odd cycles exercise the
        # learned alias without issuing a second burst request.
        return (queries[cycle % len(queries)],)
    return queries[:raw_budget]


async def scan_once(
    plan: SearchPlan,
    adapter: MarketplaceSearchAdapter,
    *,
    cycle: int,
    limit: int = 100,
    taxonomy_registry: TaxonomyRegistry | None = None,
    taxonomy_observations: TaxonomyObservationAccumulator | None = None,
    match_engine: HybridMatchEngine | None = None,
) -> ScanOutcome:
    """Run one fast discovery scan and classify every unique search candidate.

    Search-level observations may populate diagnostics, query metrics, uncertain queues, and
    hard-negative buckets, but they never train matcher weights or taxonomy mappings. Those
    updates are reserved for exact detail verification.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    # Kept in the signature for callers that already own one accumulator. Discovery must not
    # write to it: otherwise marketplace search noise could poison taxonomy learning.
    del taxonomy_observations

    registry = taxonomy_registry or TaxonomyRegistry.with_default_seeds()
    constraint = registry.resolve(plan.product_type, adapter.marketplace)
    category_path = constraint.category_path if constraint is not None else None
    engine = match_engine or HybridMatchEngine()

    scheduled_queries = _queries_for_scan(plan, cycle, engine)
    queries = _apply_marketplace_query_budget(
        scheduled_queries,
        cycle=cycle,
        adapter=adapter,
    )
    raw_candidates: list[SearchCandidate] = []
    completed_queries: list[str] = []
    query_candidate_ids: dict[str, set[str]] = {}
    source_queries_by_identity: dict[
        tuple[str, str, str | None, str | None], list[str]
    ] = {}

    for query in queries:
        try:
            query_candidates = await adapter.search(
                query,
                limit=limit,
                category_path=category_path,
            )
        except (
            MarketplaceRateLimitedError,
            MarketplaceAccessError,
            MarketplaceTransportError,
        ):
            # Do not throw away a valid primary result just because an optional
            # follow-up alias hit a transient marketplace access limit.
            if raw_candidates:
                break
            raise

        completed_queries.append(query)
        raw_candidates.extend(query_candidates)
        query_ids = query_candidate_ids.setdefault(query, set())
        for candidate in query_candidates:
            identity = candidate_identity(candidate)
            query_ids.add(candidate.listing_id)
            source_queries = source_queries_by_identity.setdefault(identity, [])
            if query not in source_queries:
                source_queries.append(query)

    effective_queries = tuple(completed_queries)
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
    accepted_ids_by_query: dict[str, set[str]] = {
        query: set() for query in effective_queries
    }

    for candidate in unique_candidates:
        identity = candidate_identity(candidate)
        source_queries = tuple(source_queries_by_identity.get(identity, ()))
        taxonomy_decision = taxonomy_gate(candidate, constraint)
        if taxonomy_decision.status is TaxonomyGateStatus.REJECT:
            taxonomy_rejected_count += 1

        decision = engine.classify(
            plan,
            candidate,
            taxonomy_status=taxonomy_decision.status,
            source_queries=source_queries,
        )
        engine.record_search_evidence(
            plan,
            candidate,
            decision,
            source_queries=source_queries,
        )

        if decision.status is MatchStatus.ACCEPT:
            accepted.append(candidate)
            for query in source_queries:
                accepted_ids_by_query[query].add(candidate.listing_id)
        elif decision.status is MatchStatus.AMBIGUOUS:
            ambiguous.append(candidate)
        else:
            rejected_count += 1

    for query in effective_queries:
        engine.query_performance.record_discovery(
            query,
            candidate_ids=query_candidate_ids.get(query, set()),
            accepted_ids=accepted_ids_by_query.get(query, set()),
        )

    return ScanOutcome(
        marketplace=adapter.marketplace,
        queries=effective_queries,
        raw_count=len(raw_candidates),
        accepted=tuple(accepted),
        ambiguous=tuple(ambiguous),
        rejected_count=rejected_count,
        taxonomy_rejected_count=taxonomy_rejected_count,
        duplicate_count=duplicate_count,
    )
