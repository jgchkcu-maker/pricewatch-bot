from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pricewatch.prices import CurrentPriceState, PriceEvent, apply_observation, rolling_stats


def test_initial_observation_creates_event() -> None:
    observed_at = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    state, event = apply_observation(None, Decimal("29990"), observed_at)

    assert state == CurrentPriceState(price=Decimal("29990"), last_observed_at=observed_at)
    assert event == PriceEvent(price=Decimal("29990"), observed_at=observed_at)


def test_unchanged_price_updates_last_seen_without_new_event() -> None:
    first = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    second = first + timedelta(minutes=4)
    existing = CurrentPriceState(price=Decimal("29990"), last_observed_at=first)

    state, event = apply_observation(existing, Decimal("29990.00"), second)

    assert state.price == Decimal("29990.00")
    assert state.last_observed_at == second
    assert event is None


def test_changed_price_emits_single_event() -> None:
    first = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    second = first + timedelta(minutes=4)
    existing = CurrentPriceState(price=Decimal("29990"), last_observed_at=first)

    state, event = apply_observation(existing, Decimal("28990"), second)

    assert state.price == Decimal("28990")
    assert event == PriceEvent(price=Decimal("28990"), observed_at=second)


def test_rolling_stats_only_use_last_seven_days() -> None:
    now = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    events = [
        PriceEvent(Decimal("50000"), now - timedelta(days=8)),
        PriceEvent(Decimal("34000"), now - timedelta(days=6)),
        PriceEvent(Decimal("30000"), now - timedelta(days=3)),
        PriceEvent(Decimal("32000"), now - timedelta(days=1)),
    ]

    stats = rolling_stats(events, now)

    assert stats.count == 3
    assert stats.minimum == Decimal("30000")
    assert stats.median == Decimal("32000")


def test_prices_require_positive_values_and_timezone_aware_datetimes() -> None:
    aware = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="positive"):
        apply_observation(None, Decimal("0"), aware)
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_observation(None, Decimal("100"), datetime(2026, 9, 5, 5, 0))
