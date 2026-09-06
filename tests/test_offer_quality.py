from decimal import Decimal

from pricewatch.marketplaces import (
    OfferCondition,
    OfferLocator,
    OfferQualitySignals,
    OfferSnapshot,
    SearchCandidate,
)
from pricewatch.offer_quality import (
    OfferQualityContext,
    OfferQualityReason,
    OfferQualityStatus,
    evaluate_offer_quality,
)
from pricewatch.search_plan import SearchPlan


def airpods_plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Apple AirPods Pro 3",
        primary_query="apple airpods pro 3",
        product_type="wireless earbuds",
        identity_attributes={"brand": "apple", "model": "airpods pro 3"},
    )


def new_plan() -> SearchPlan:
    return airpods_plan()


def case_plan() -> SearchPlan:
    return SearchPlan(
        canonical_name="Чехол AirPods Pro 3",
        primary_query="чехол airpods pro 3",
        product_type="case",
        identity_attributes={"for_model": "airpods pro 3"},
    )


def exact_candidate(title: str) -> SearchCandidate:
    return SearchCandidate(
        marketplace="wildberries",
        listing_id="100",
        variation_id="200",
        seller_id="seller-1",
        title=title,
    )


def exact_snapshot(
    price: str,
    *,
    title: str = "AirPods Pro 3",
    condition: OfferCondition = OfferCondition.UNKNOWN,
    available: bool = True,
    authenticity_badges: tuple[str, ...] = (),
) -> OfferSnapshot:
    return OfferSnapshot(
        locator=OfferLocator(
            marketplace="wildberries",
            listing_id="100",
            variation_id="200",
            seller_id="seller-1",
        ),
        title=title,
        price=Decimal(price),
        available=available,
        quality_signals=OfferQualitySignals(
            condition=condition,
            authenticity_badges=authenticity_badges,
        ),
    )


def normal_reference() -> OfferQualityContext:
    return OfferQualityContext(
        trusted_prices=(Decimal("18990"), Decimal("19990"), Decimal("20990"))
    )


def test_exact_normal_offer_is_trusted() -> None:
    decision = evaluate_offer_quality(
        new_plan(), exact_candidate("AirPods Pro 3"), exact_snapshot("19990"), normal_reference()
    )
    assert decision.status is OfferQualityStatus.TRUSTED


def test_extreme_low_is_quarantined_before_confirmation() -> None:
    decision = evaluate_offer_quality(
        new_plan(), exact_candidate("AirPods Pro 3"), exact_snapshot("827"), normal_reference()
    )
    assert decision.status is OfferQualityStatus.QUARANTINED
    assert OfferQualityReason.PRICE_OUTLIER in decision.reason_codes
    assert decision.reference_price == Decimal("19990")
    assert decision.price_ratio == Decimal("827") / Decimal("19990")
    assert decision.confirmation_count == 1


def test_explicit_accessory_is_rejected_for_main_product() -> None:
    decision = evaluate_offer_quality(
        airpods_plan(),
        exact_candidate("Чехол для AirPods Pro 3"),
        exact_snapshot("799", title="Чехол для AirPods Pro 3"),
        normal_reference(),
    )
    assert decision.status is OfferQualityStatus.REJECTED
    assert OfferQualityReason.ACCESSORY_ONLY in decision.reason_codes


def test_same_word_case_is_allowed_when_target_is_case() -> None:
    decision = evaluate_offer_quality(
        case_plan(),
        exact_candidate("Чехол AirPods Pro 3"),
        exact_snapshot("799", title="Чехол AirPods Pro 3"),
        OfferQualityContext(trusted_prices=(Decimal("699"), Decimal("799"), Decimal("899"))),
    )
    assert decision.status is OfferQualityStatus.TRUSTED
    assert OfferQualityReason.ACCESSORY_ONLY not in decision.reason_codes


def test_main_product_with_case_bundle_is_not_accessory_only() -> None:
    decision = evaluate_offer_quality(
        airpods_plan(),
        exact_candidate("AirPods Pro 3 с чехлом"),
        exact_snapshot("19990", title="AirPods Pro 3 с чехлом"),
        normal_reference(),
    )
    assert decision.status is OfferQualityStatus.TRUSTED


def test_explicit_copy_is_rejected_but_low_price_alone_is_not() -> None:
    copy_decision = evaluate_offer_quality(
        airpods_plan(),
        exact_candidate("AirPods Pro 3 копия 1:1"),
        exact_snapshot("827", title="AirPods Pro 3 копия 1:1"),
        normal_reference(),
    )
    low_decision = evaluate_offer_quality(
        airpods_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("827"),
        OfferQualityContext(trusted_prices=(Decimal("19990"),)),
    )
    assert copy_decision.status is OfferQualityStatus.REJECTED
    assert OfferQualityReason.EXPLICIT_COUNTERFEIT in copy_decision.reason_codes
    assert low_decision.status is OfferQualityStatus.QUARANTINED
    assert OfferQualityReason.PRICE_OUTLIER in low_decision.reason_codes
    assert OfferQualityReason.EXPLICIT_COUNTERFEIT not in low_decision.reason_codes


def test_missing_reviews_do_not_reject_normal_exact_offer() -> None:
    snapshot = exact_snapshot("19990")
    decision = evaluate_offer_quality(
        new_plan(), exact_candidate("AirPods Pro 3"), snapshot, normal_reference()
    )
    assert decision.status is OfferQualityStatus.TRUSTED


def test_used_card_conflicts_with_new_plan() -> None:
    snapshot = exact_snapshot("9990", condition=OfferCondition.USED)
    decision = evaluate_offer_quality(
        new_plan(), exact_candidate("AirPods Pro 3"), snapshot, normal_reference()
    )
    assert decision.status is OfferQualityStatus.REJECTED
    assert OfferQualityReason.CONDITION_CONFLICT in decision.reason_codes


def test_second_stable_outlier_confirmation_can_be_trusted() -> None:
    decision = evaluate_offer_quality(
        new_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("827"),
        OfferQualityContext(
            trusted_prices=(Decimal("18990"), Decimal("19990"), Decimal("20990")),
            prior_status=OfferQualityStatus.QUARANTINED,
            prior_confirmation_count=1,
        ),
    )
    assert decision.status is OfferQualityStatus.TRUSTED
    assert decision.confirmation_count == 2


def test_no_price_baseline_requires_exact_confirmation() -> None:
    first = evaluate_offer_quality(
        new_plan(), exact_candidate("AirPods Pro 3"), exact_snapshot("19990"), OfferQualityContext()
    )
    assert first.status is OfferQualityStatus.QUARANTINED
    assert OfferQualityReason.NO_PRICE_BASELINE in first.reason_codes
    assert OfferQualityReason.NEEDS_CONFIRMATION in first.reason_codes
    assert first.confirmation_count == 1

    second = evaluate_offer_quality(
        new_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("19990"),
        OfferQualityContext(
            prior_status=OfferQualityStatus.QUARANTINED,
            prior_confirmation_count=first.confirmation_count,
        ),
    )
    assert second.status is OfferQualityStatus.TRUSTED
    assert second.confirmation_count == 2


def test_official_authenticity_badge_can_bootstrap_without_price_baseline() -> None:
    decision = evaluate_offer_quality(
        new_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("19990", authenticity_badges=("оригинал",)),
        OfferQualityContext(),
    )
    assert decision.status is OfferQualityStatus.TRUSTED


def test_unavailable_offer_is_not_trusted() -> None:
    decision = evaluate_offer_quality(
        new_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("19990", available=False),
        normal_reference(),
    )
    assert decision.status is OfferQualityStatus.UNAVAILABLE
    assert decision.reason_codes == (OfferQualityReason.UNAVAILABLE,)


def test_missing_detail_title_is_rejected() -> None:
    decision = evaluate_offer_quality(
        new_plan(),
        exact_candidate("AirPods Pro 3"),
        exact_snapshot("19990", title=""),
        normal_reference(),
    )
    assert decision.status is OfferQualityStatus.REJECTED
    assert OfferQualityReason.MISSING_TITLE in decision.reason_codes
