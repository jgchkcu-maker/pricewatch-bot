from pricewatch.marketplaces import SearchCandidate
from pricewatch.match_learning import (
    HardNegativeBucket,
    HybridMatchEngine,
    LearningEvidenceSource,
    QueryPerformanceTracker,
)
from pricewatch.matching import MatchStatus
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import MarketplaceTaxonomy, TaxonomyGateStatus


def make_plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        primary_query="Xiaomi Pad 7 8 256",
        product_type="tablet",
        required_tokens=("xiaomi",),
        excluded_terms=("чехол", "case", "pad 7 pro"),
        identity_attributes={
            "model": "pad 7",
            "ram": "8 gb",
            "storage": "256 gb",
        },
    )


def exact_candidate(listing_id: str = "1") -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id=listing_id,
        title="Xiaomi Pad 7 8GB 256GB",
        attributes={
            "brand": "Xiaomi",
            "model": "Pad 7",
            "ram": "8 GB",
            "storage": "256 GB",
        },
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
    )


def test_exact_identity_evidence_is_high_confidence_accept() -> None:
    engine = HybridMatchEngine()

    decision = engine.classify(
        make_plan(),
        exact_candidate(),
        taxonomy_status=TaxonomyGateStatus.PASS,
    )

    assert decision.status is MatchStatus.ACCEPT
    assert decision.probability >= engine.accept_threshold
    assert decision.hard_vetoes == ()
    assert decision.features.identity_coverage == 1.0


def test_close_variant_with_conflicting_capacity_is_hard_reject() -> None:
    engine = HybridMatchEngine()
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="2",
        title="Xiaomi Pad 7 12GB 256GB",
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
    )

    decision = engine.classify(
        make_plan(),
        candidate,
        taxonomy_status=TaxonomyGateStatus.PASS,
    )

    assert decision.status is MatchStatus.REJECT
    assert any("variant" in veto for veto in decision.hard_vetoes)
    assert engine.hard_negatives[-1].bucket is HardNegativeBucket.VARIANT_CONFLICT


def test_exact_identifier_conflict_cannot_be_overridden_by_title_similarity() -> None:
    engine = HybridMatchEngine()
    plan = SearchPlan(
        canonical_name="Sony WH-1000XM5",
        primary_query="Sony WH 1000XM5",
        product_type="headphones",
        required_tokens=("sony",),
        identity_attributes={"model": "wh 1000xm5", "gtin": "1234567890123"},
    )
    candidate = SearchCandidate(
        marketplace="ozon",
        listing_id="3",
        title="Sony WH-1000XM5 Wireless Headphones",
        attributes={"model": "WH-1000XM5", "gtin": "9999999999999"},
    )

    decision = engine.classify(plan, candidate)

    assert decision.status is MatchStatus.REJECT
    assert any("identifier" in veto for veto in decision.hard_vetoes)
    assert engine.hard_negatives[-1].bucket is HardNegativeBucket.IDENTIFIER_CONFLICT


def test_ambiguous_candidate_enters_active_learning_queue() -> None:
    engine = HybridMatchEngine()
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="4",
        title="Xiaomi Pad 7 256GB",
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
    )

    decision = engine.classify(
        make_plan(),
        candidate,
        taxonomy_status=TaxonomyGateStatus.PASS,
        source_queries=("xiaomi pad 7 8 256",),
    )

    assert decision.status is MatchStatus.AMBIGUOUS
    queued = engine.uncertain_queue.items()
    assert len(queued) == 1
    assert queued[0].candidate.listing_id == "4"
    assert queued[0].priority > 0


def test_search_observation_never_changes_online_model_weights() -> None:
    engine = HybridMatchEngine()
    before = dict(engine.model.weights)
    decision = engine.classify(
        make_plan(),
        exact_candidate(),
        taxonomy_status=TaxonomyGateStatus.PASS,
    )

    engine.record_search_evidence(
        make_plan(),
        exact_candidate(),
        decision,
        source_queries=("xiaomi pad 7 8 256",),
    )

    assert engine.model.weights == before
    assert engine.evidence[-1].verified_label is None
    assert engine.evidence[-1].source is LearningEvidenceSource.SEARCH


def test_verified_detail_label_updates_model_and_preserves_provenance() -> None:
    engine = HybridMatchEngine()
    candidate = exact_candidate()
    decision = engine.classify(
        make_plan(),
        candidate,
        taxonomy_status=TaxonomyGateStatus.PASS,
    )
    before = dict(engine.model.weights)

    engine.learn_verified(
        make_plan(),
        candidate,
        decision,
        matched=True,
        source=LearningEvidenceSource.DETAIL,
        source_queries=("xiaomi pad 7 8 256", "xiaomi pad7 8 256"),
    )

    assert engine.model.weights != before
    evidence = engine.evidence[-1]
    assert evidence.verified_label is True
    assert evidence.source is LearningEvidenceSource.DETAIL
    assert evidence.source_queries == ("xiaomi pad 7 8 256", "xiaomi pad7 8 256")


def test_accessory_reject_is_mined_but_does_not_train() -> None:
    engine = HybridMatchEngine()
    before = dict(engine.model.weights)
    candidate = SearchCandidate(
        marketplace="wildberries",
        listing_id="5",
        title="Чехол для Xiaomi Pad 7 8/256",
        taxonomy=MarketplaceTaxonomy(subject_id="203", entity="Чехлы"),
    )

    decision = engine.classify(make_plan(), candidate)

    assert decision.status is MatchStatus.REJECT
    assert engine.hard_negatives[-1].bucket is HardNegativeBucket.ACCESSORY
    assert engine.model.weights == before


def test_query_performance_prefers_verified_unique_yield_and_penalizes_rejects() -> None:
    tracker = QueryPerformanceTracker()
    tracker.record_discovery(
        "good alias",
        candidate_ids={"1", "2", "3"},
        accepted_ids={"1", "2"},
    )
    tracker.record_verified("good alias", "1", matched=True)
    tracker.record_verified("good alias", "2", matched=True)

    tracker.record_discovery(
        "bad alias",
        candidate_ids={"4", "5", "6", "7"},
        accepted_ids={"4", "5"},
    )
    tracker.record_verified("bad alias", "4", matched=False)
    tracker.record_verified("bad alias", "5", matched=False)

    assert tracker.score("good alias") > tracker.score("bad alias")
    assert tracker.rank(("bad alias", "good alias"))[0] == "good alias"
