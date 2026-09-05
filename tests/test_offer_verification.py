import asyncio
from decimal import Decimal

from pricewatch.marketplaces import OfferIdentityError, OfferLocator, OfferSnapshot, SearchCandidate
from pricewatch.match_learning import HybridMatchEngine, LearningEvidenceSource
from pricewatch.search_plan import SearchPlan
from pricewatch.taxonomy import MarketplaceTaxonomy, TaxonomyObservationAccumulator
from pricewatch.verification import verify_candidate


class FakeOfferAdapter:
    marketplace = "wildberries"

    def __init__(self, title: str, price: Decimal) -> None:
        self.title = title
        self.price = price

    async def fetch_offer(self, locator: OfferLocator) -> OfferSnapshot:
        return OfferSnapshot(
            locator=locator,
            title=self.title,
            price=self.price,
            available=True,
            price_source="card",
        )


def plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Xiaomi Pad 7 8/256",
        product_type="tablet",
        primary_query="xiaomi pad 7 8 256",
        required_tokens=("xiaomi",),
        excluded_terms=("pad 7 pro", "чехол"),
        identity_attributes={"model": "pad 7", "ram": "8 gb", "storage": "256 gb"},
    )


def search_candidate() -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="123",
        variation_id="456",
        seller_id="789",
        title="Xiaomi Pad 7 8ГБ 256ГБ",
        taxonomy=MarketplaceTaxonomy(subject_id="107", entity="Планшеты"),
        price=Decimal("29990"),
        price_source="search",
    )


def test_verification_uses_concrete_offer_and_rechecks_identity() -> None:
    snapshot = asyncio.run(
        verify_candidate(
            plan(),
            search_candidate(),
            FakeOfferAdapter("Xiaomi Pad7 8ГБ 256ГБ", Decimal("29490")),
        )
    )

    assert snapshot.price == Decimal("29490")
    assert snapshot.price_source == "card"
    assert snapshot.locator.variation_id == "456"


def test_verification_rejects_detail_card_that_no_longer_matches_product() -> None:
    try:
        asyncio.run(
            verify_candidate(
                plan(),
                search_candidate(),
                FakeOfferAdapter("Xiaomi Pad 7 Pro 8ГБ 256ГБ", Decimal("19990")),
            )
        )
    except OfferIdentityError as exc:
        assert "verification" in str(exc)
    else:
        raise AssertionError("mismatched detail card must not become a verified offer")


def test_detail_verification_trains_matcher_and_taxonomy_only_after_success() -> None:
    engine = HybridMatchEngine()
    observations = TaxonomyObservationAccumulator()
    before = dict(engine.model.weights)

    asyncio.run(
        verify_candidate(
            plan(),
            search_candidate(),
            FakeOfferAdapter("Xiaomi Pad7 8ГБ 256ГБ", Decimal("29490")),
            match_engine=engine,
            source_queries=("xiaomi pad 7 8 256",),
            taxonomy_observations=observations,
        )
    )

    assert engine.model.weights != before
    evidence = engine.evidence[-1]
    assert evidence.source is LearningEvidenceSource.DETAIL
    assert evidence.verified_label is True
    proposed = observations.propose("tablet", "wildberries", minimum_distinct=1)
    assert proposed is not None
    assert proposed.subject_ids == frozenset({"107"})


def test_failed_detail_identity_recheck_records_verified_negative() -> None:
    engine = HybridMatchEngine()

    try:
        asyncio.run(
            verify_candidate(
                plan(),
                search_candidate(),
                FakeOfferAdapter("Xiaomi Pad 7 Pro 8ГБ 256ГБ", Decimal("19990")),
                match_engine=engine,
                source_queries=("xiaomi pad 7 8 256",),
            )
        )
    except OfferIdentityError:
        pass
    else:
        raise AssertionError("mismatched detail card must not become a verified offer")

    evidence = engine.evidence[-1]
    assert evidence.source is LearningEvidenceSource.DETAIL
    assert evidence.verified_label is False


def test_verified_detail_identity_overrides_uncalibrated_probability() -> None:
    engine = HybridMatchEngine()
    engine.model.weights = {key: 0.0 for key in engine.model.weights}
    engine.model.weights["intercept"] = -100.0

    snapshot = asyncio.run(
        verify_candidate(
            plan(),
            search_candidate(),
            FakeOfferAdapter("Xiaomi Pad7 8ГБ 256ГБ", Decimal("29490")),
            match_engine=engine,
            source_queries=("xiaomi pad 7 8 256",),
        )
    )

    assert snapshot.price == Decimal("29490")
    evidence = engine.evidence[-1]
    assert evidence.source is LearningEvidenceSource.DETAIL
    assert evidence.verified_label is True
