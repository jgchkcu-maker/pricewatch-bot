from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from collections.abc import Iterable


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def _require_positive(price: Decimal) -> None:
    if price <= 0:
        raise ValueError("price must be positive")


@dataclass(frozen=True, slots=True)
class PriceEvent:
    price: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_positive(self.price)
        _require_aware(self.observed_at)


@dataclass(frozen=True, slots=True)
class CurrentPriceState:
    price: Decimal
    last_observed_at: datetime

    def __post_init__(self) -> None:
        _require_positive(self.price)
        _require_aware(self.last_observed_at)


@dataclass(frozen=True, slots=True)
class RollingPriceStats:
    count: int
    minimum: Decimal | None
    median: Decimal | None


def apply_observation(
    state: CurrentPriceState | None,
    price: Decimal,
    observed_at: datetime,
) -> tuple[CurrentPriceState, PriceEvent | None]:
    _require_positive(price)
    _require_aware(observed_at)

    new_state = CurrentPriceState(price=price, last_observed_at=observed_at)
    if state is not None and state.price == price:
        return new_state, None

    return new_state, PriceEvent(price=price, observed_at=observed_at)


def rolling_stats(
    events: Iterable[PriceEvent],
    now: datetime,
    window_days: int = 7,
) -> RollingPriceStats:
    _require_aware(now)
    if window_days <= 0:
        raise ValueError("window_days must be positive")

    cutoff = now - timedelta(days=window_days)
    prices = [event.price for event in events if cutoff <= event.observed_at <= now]
    if not prices:
        return RollingPriceStats(count=0, minimum=None, median=None)

    return RollingPriceStats(
        count=len(prices),
        minimum=min(prices),
        median=median(prices),
    )
