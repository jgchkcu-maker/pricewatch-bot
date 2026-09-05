from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pricewatch.deals import evaluate_verified_price

from pricewatch.prices import PriceEvent


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def event(price: str, *, days_ago: int = 0) -> PriceEvent:
    return PriceEvent(price=Decimal(price), observed_at=NOW - timedelta(days=days_ago))


def test_first_verified_observation_is_baseline_without_alert() -> None:
    decision = evaluate_verified_price([], Decimal("29490"), observed_at=NOW)

    assert decision.is_baseline is True
    assert decision.is_new_low is False
    assert decision.previous_min is None
    assert decision.current_price == Decimal("29490")


def test_lower_verified_price_is_new_rolling_low() -> None:
    decision = evaluate_verified_price(
        [event("29490"), event("30990", days_ago=2)],
        Decimal("24990"),
        observed_at=NOW,
    )

    assert decision.is_baseline is False
    assert decision.is_new_low is True
    assert decision.previous_min == Decimal("29490")
    assert decision.delta == Decimal("4500")
    assert decision.delta_percent == Decimal("15.26")


def test_higher_price_does_not_emit_new_low() -> None:
    decision = evaluate_verified_price(
        [event("24990", days_ago=1)],
        Decimal("25990"),
        observed_at=NOW,
    )

    assert decision.is_new_low is False
    assert decision.previous_min == Decimal("24990")
    assert decision.delta == Decimal("-1000")


def test_expired_old_minimum_does_not_count_in_current_window() -> None:
    decision = evaluate_verified_price(
        [event("19990", days_ago=8), event("29990", days_ago=2)],
        Decimal("28990"),
        observed_at=NOW,
    )

    assert decision.previous_min == Decimal("29990")
    assert decision.is_new_low is True


def test_new_price_must_be_positive_and_timestamp_aware() -> None:
    try:
        evaluate_verified_price([], Decimal("0"), observed_at=NOW)
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("zero verified price must be rejected")

    try:
        evaluate_verified_price([], Decimal("1"), observed_at=datetime(2026, 9, 5))
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive verified timestamp must be rejected")
