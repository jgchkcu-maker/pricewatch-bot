import asyncio
from decimal import Decimal

from pricewatch.marketplaces import SearchCandidate
from pricewatch.match_learning import (
    HardNegativeBucket,
    HybridMatchEngine,
    LearningEvidenceSource,
)
from pricewatch.scan import scan_once
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import MarketplaceTaxonomy, TaxonomyObservationAccumulator
from pricewatch.transport import MarketplaceRateLimitedError


class FakeSearchAdapter:
    marketplace = "wildberries"

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]:
        assert query in {
            "xiaomi pad 7 8 256",
            "xiaomi pad7 8 256",
            "сяоми пад 7 8 256",
        }
        assert category_path is None
        return [
            SearchCandidate(
                marketplace="wildberries",
                listing_id="1",
                variation_id="11",
                title="Xiaomi Pad7 8ГБ 256ГБ",
                attributes={"brand": "Xiaomi"},
                taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
                price=Decimal("31990"),
                price_source="search",
            ),
            SearchCandidate(
                marketplace="wildberries",
                listing_id="2",
                variation_id="22",
                title="Xiaomi Pad 7 8ГБ 256ГБ премиум аксессуар",
                taxonomy=MarketplaceTaxonomy(subject_id="203", entity="Чехлы"),
                price=Decimal("990"),
                price_source="search",
            ),
            SearchCandidate(
                marketplace="wildberries",
                listing_id="3",
                variation_id="33",
                title="Xiaomi Pad 7 256GB",
                attributes={"brand": "Xiaomi"},
                taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
                price=Decimal("29990"),
                price_source="search",
            ),
        ]


class FakeOzonSearchAdapter:
    marketplace = "ozon"

    def __init__(self) -> None:
        self.category_paths: list[str | None] = []

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]:
        self.category_paths.append(category_path)
        return []


class RateLimitedSecondQueryAdapter(FakeSearchAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]:
        self.calls += 1
        if self.calls == 2:
            raise MarketplaceRateLimitedError("burst limited", retry_after_seconds=600)
        return await super().search(
            query,
            limit=limit,
            page=page,
            category_path=category_path,
        )


class SingleQueryBudgetAdapter(FakeSearchAdapter):
    max_search_queries_per_scan = 1

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
        page: int = 1,
        category_path: str | None = None,
    ) -> list[SearchCandidate]:
        self.queries.append(query)
        return await super().search(
            query,
            limit=limit,
            page=page,
            category_path=category_path,
        )


def make_plan(*, product_type: str = "tablet") -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="Xiaomi Pad 7 8/256",
        product_type=product_type,
        aliases=("Xiaomi Pad7 8 256", "Сяоми Пад 7 8 256"),
        required_tokens=("xiaomi",),
        excluded_terms=("case", "pad 7 pro"),
        identity_attributes={
            "model": "pad 7",
            "ram": "8 gb",
            "storage": "256 gb",
        },
    )


def test_scan_splits_taxonomy_rejected_and_ambiguous_candidates() -> None:
    outcome = asyncio.run(scan_once(make_plan(), FakeSearchAdapter(), cycle=0))

    assert outcome.queries == ("xiaomi pad 7 8 256",)
    assert [(item.listing_id, item.variation_id) for item in outcome.accepted] == [("1", "11")]
    assert [(item.listing_id, item.variation_id) for item in outcome.ambiguous] == [("3", "33")]
    assert outcome.rejected_count == 1
    assert outcome.taxonomy_rejected_count == 1
    assert outcome.accepted[0].price == Decimal("31990")


def test_scan_keeps_primary_and_adds_rotating_alias_without_duplicate_offers() -> None:
    outcome = asyncio.run(scan_once(make_plan(), FakeSearchAdapter(), cycle=1))

    assert outcome.queries == ("xiaomi pad 7 8 256", "xiaomi pad7 8 256")
    assert outcome.raw_count == 6
    assert outcome.duplicate_count == 3
    assert len(outcome.accepted) == 1
    assert len(outcome.ambiguous) == 1
    assert outcome.rejected_count == 1
    assert outcome.taxonomy_rejected_count == 1


def test_scan_preserves_successful_primary_results_if_later_alias_is_rate_limited() -> None:
    adapter = RateLimitedSecondQueryAdapter()

    outcome = asyncio.run(scan_once(make_plan(), adapter, cycle=1))

    assert adapter.calls == 2
    assert outcome.queries == ("xiaomi pad 7 8 256",)
    assert [(item.listing_id, item.variation_id) for item in outcome.accepted] == [("1", "11")]
    assert len(outcome.ambiguous) == 1


def test_scan_respects_marketplace_single_query_budget_without_losing_alias_rotation() -> None:
    adapter = SingleQueryBudgetAdapter()

    outcome = asyncio.run(scan_once(make_plan(), adapter, cycle=1))

    assert adapter.queries == ["xiaomi pad7 8 256"]
    assert outcome.queries == ("xiaomi pad7 8 256",)
    assert len(outcome.accepted) == 1


def test_scan_passes_known_ozon_category_scope_to_adapter() -> None:
    adapter = FakeOzonSearchAdapter()

    asyncio.run(scan_once(make_plan(), adapter, cycle=0))

    assert adapter.category_paths == ["/category/planshety-15525/"]


def test_scan_keeps_global_search_for_unknown_ozon_product_type() -> None:
    adapter = FakeOzonSearchAdapter()

    asyncio.run(scan_once(make_plan(product_type="rare experimental device"), adapter, cycle=0))

    assert adapter.category_paths == [None]


def test_scan_collects_learning_evidence_without_training_model() -> None:
    engine = HybridMatchEngine()
    before = dict(engine.model.weights)

    outcome = asyncio.run(
        scan_once(
            make_plan(),
            FakeSearchAdapter(),
            cycle=0,
            match_engine=engine,
        )
    )

    assert engine.model.weights == before
    assert len(engine.evidence) == 3
    assert all(item.source is LearningEvidenceSource.SEARCH for item in engine.evidence)
    assert any(
        item.bucket is HardNegativeBucket.TAXONOMY_CONFLICT
        for item in engine.hard_negatives
    )
    assert len(engine.uncertain_queue.items()) == 1
    assert engine.query_performance.score(outcome.queries[0]) > 0


def test_scan_does_not_train_taxonomy_from_unverified_search_accepts() -> None:
    observations = TaxonomyObservationAccumulator()

    asyncio.run(
        scan_once(
            make_plan(),
            FakeSearchAdapter(),
            cycle=0,
            taxonomy_observations=observations,
        )
    )

    assert observations.propose("tablet", "wildberries", minimum_distinct=1) is None


def test_scan_uses_verified_query_performance_to_choose_alias() -> None:
    engine = HybridMatchEngine()
    engine.query_performance.record_discovery(
        "xiaomi pad7 8 256",
        candidate_ids={"10"},
        accepted_ids={"10"},
    )
    engine.query_performance.record_discovery(
        "сяоми пад 7 8 256",
        candidate_ids={"20"},
        accepted_ids={"20"},
    )
    engine.query_performance.record_verified("xiaomi pad7 8 256", "10", matched=False)
    engine.query_performance.record_verified("сяоми пад 7 8 256", "20", matched=True)

    outcome = asyncio.run(
        scan_once(
            make_plan(),
            FakeSearchAdapter(),
            cycle=5,
            match_engine=engine,
        )
    )

    assert outcome.queries == ("xiaomi pad 7 8 256", "сяоми пад 7 8 256")
