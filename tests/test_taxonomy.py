from pricewatch.taxonomy import (
    MarketplaceTaxonomy,
    TaxonomyConstraint,
    TaxonomyGateStatus,
    TaxonomyObservationAccumulator,
    TaxonomyRegistry,
    taxonomy_gate,
)

from pricewatch.marketplaces import SearchCandidate


def test_known_wb_subject_rejects_accessory_category() -> None:
    constraint = TaxonomyConstraint(
        marketplace="wildberries",
        product_type="tablet",
        subject_ids=frozenset({"107"}),
    )
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="1",
        title="Чехол для Xiaomi Pad 7",
        taxonomy=MarketplaceTaxonomy(subject_id="203", entity="Чехлы"),
    )

    decision = taxonomy_gate(candidate, constraint)

    assert decision.status is TaxonomyGateStatus.REJECT


def test_known_wb_subject_accepts_target_category() -> None:
    constraint = TaxonomyConstraint(
        marketplace="wildberries",
        product_type="tablet",
        subject_ids=frozenset({"107"}),
    )
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="1",
        title="Xiaomi Pad 7",
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
    )

    decision = taxonomy_gate(candidate, constraint)

    assert decision.status is TaxonomyGateStatus.PASS


def test_missing_taxonomy_is_unknown_not_reject() -> None:
    constraint = TaxonomyConstraint(
        marketplace="wildberries",
        product_type="tablet",
        subject_ids=frozenset({"107"}),
    )
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="1",
        title="Xiaomi Pad 7",
    )

    decision = taxonomy_gate(candidate, constraint)

    assert decision.status is TaxonomyGateStatus.UNKNOWN


def test_registry_contains_evidence_backed_tablet_seeds() -> None:
    registry = TaxonomyRegistry.with_default_seeds()

    wb = registry.resolve("tablet", "wildberries")
    ozon = registry.resolve("tablet", "ozon")

    assert wb is not None
    assert wb.subject_ids == frozenset({"107"})
    assert ozon is not None
    assert ozon.category_path == "/category/planshety-15525/"
    assert registry.resolve("coffee grinder", "ozon") is None


def _candidate(listing_id: str, subject_id: str) -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id=listing_id,
        title="Xiaomi Pad 7",
        taxonomy=MarketplaceTaxonomy(subject_id=subject_id, entity="Планшеты"),
    )


def test_taxonomy_learning_needs_three_distinct_accepted_listings() -> None:
    accumulator = TaxonomyObservationAccumulator()
    accumulator.observe("tablet", _candidate("1", "107"))
    accumulator.observe("tablet", _candidate("2", "107"))

    assert accumulator.propose("tablet", "wildberries") is None

    accumulator.observe("tablet", _candidate("3", "107"))
    proposed = accumulator.propose("tablet", "wildberries")

    assert proposed is not None
    assert proposed.subject_ids == frozenset({"107"})


def test_taxonomy_learning_deduplicates_listing_ids() -> None:
    accumulator = TaxonomyObservationAccumulator()
    for _ in range(5):
        accumulator.observe("tablet", _candidate("1", "107"))

    assert accumulator.propose("tablet", "wildberries") is None


def test_taxonomy_learning_refuses_tied_competing_signatures() -> None:
    accumulator = TaxonomyObservationAccumulator()
    for listing_id in ("1", "2", "3"):
        accumulator.observe("tablet", _candidate(listing_id, "107"))
    for listing_id in ("4", "5", "6"):
        accumulator.observe("tablet", _candidate(listing_id, "203"))

    assert accumulator.propose("tablet", "wildberries") is None
