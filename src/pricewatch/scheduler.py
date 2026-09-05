from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def next_scan_at(last_scan_at: datetime, interval_minutes: int = 4) -> datetime:
    _require_aware(last_scan_at)
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    return last_scan_at + timedelta(minutes=interval_minutes)


@dataclass(frozen=True, slots=True)
class TrackedProductSchedule:
    product_id: str
    next_scan_at: datetime

    def __post_init__(self) -> None:
        if not self.product_id:
            raise ValueError("product_id must not be empty")
        _require_aware(self.next_scan_at)


def due_product_ids(
    schedules: Iterable[TrackedProductSchedule],
    now: datetime,
) -> tuple[str, ...]:
    _require_aware(now)
    due: list[str] = []
    seen: set[str] = set()
    for schedule in schedules:
        if schedule.next_scan_at <= now and schedule.product_id not in seen:
            due.append(schedule.product_id)
            seen.add(schedule.product_id)
    return tuple(due)
