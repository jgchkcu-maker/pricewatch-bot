from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from pricewatch.prices import PriceEvent, rolling_stats


@dataclass(frozen=True, slots=True)
class DealDecision:
    is_baseline: bool
    is_new_low: bool
    previous_min: Decimal | None
    current_price: Decimal
    delta: Decimal | None
    delta_percent: Decimal | None


def evaluate_verified_price(
    history: Sequence[PriceEvent],
    new_price: Decimal,
    *,
    observed_at: datetime,
    window_days: int = 7,
) -> DealDecision:
    """Evaluate a newly verified public price against the previous rolling window."""

    # Reuse PriceEvent validation for positive price and timezone-aware timestamps.
    PriceEvent(price=new_price, observed_at=observed_at)
    stats = rolling_stats(history, observed_at, window_days=window_days)
    previous_min = stats.minimum
    if previous_min is None:
        return DealDecision(
            is_baseline=True,
            is_new_low=False,
            previous_min=None,
            current_price=new_price,
            delta=None,
            delta_percent=None,
        )

    delta = previous_min - new_price
    percent = (delta / previous_min * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return DealDecision(
        is_baseline=False,
        is_new_low=new_price < previous_min,
        previous_min=previous_min,
        current_price=new_price,
        delta=delta,
        delta_percent=percent,
    )
